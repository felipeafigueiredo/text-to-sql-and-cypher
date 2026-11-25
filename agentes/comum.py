"""O que os 3 sistemas compartilham: a conexão com o servidor MCP, o modelo, a medição e a interface answer().

Todo sistema é uma subclasse de Sistema e implementa executar(pergunta, config). A answer() cuida do resto:
mede tempo, tokens, chamadas e recursos, e devolve tudo num dicionário que vira uma linha do CSV.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import psutil
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_mcp_adapters.resources import load_mcp_resources
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_ollama import ChatOllama
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import ToolNode
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELO = os.environ.get("AIRBNB_MODELO", "qwen3:8b")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
NUM_CTX = 16384          # dois esquemas + 5 ferramentas + resultados de até 50 linhas: 8192 estoura em poucas voltas
LIMITE_PASSOS = 30       # passos do LangGraph por pergunta (~14 voltas modelo ⇄ ferramentas)


# ─── servidor MCP ────────────────────────────────────────────────────────────

class Servidor:
    """Uma sessão MCP aberta com o servidor/server.py, que dura enquanto o notebook usar os agentes.

    O adaptador do LangGraph, por padrão, sobe o servidor de novo a cada chamada de ferramenta; aqui a sessão fica
    aberta, para a latência medir o agente e não a partida do servidor. A sessão vive numa tarefa própria porque o
    contexto do cliente MCP precisa abrir e fechar na mesma tarefa (no Colab, cada célula roda numa tarefa diferente).

    python e libs: o servidor roda com as bibliotecas de servidor_libs/ (mcp 2.x), separadas das do notebook (mcp 1.x).

    A sessão é aberta direto pelo SDK do mcp, e não pelo MultiServerMCPClient, para mandar o stderr do servidor para um
    arquivo: o adaptador usa o sys.stderr, que no kernel do Colab não tem fileno() e derruba a partida do servidor.
    """

    URIS = ("esquema://sql", "esquema://grafo")
    LOG = os.path.join(tempfile.gettempdir(), "servidor_mcp.log")

    def __init__(self, python=None, libs=None):
        self.python = python or os.environ.get("AIRBNB_SERVIDOR_PYTHON", sys.executable)
        self.libs = libs if libs is not None else os.environ.get("AIRBNB_SERVIDOR_LIBS", os.path.join(RAIZ, "servidor_libs"))
        self.ferramentas, self.esquemas = [], {}
        self._pronto, self._fim, self._tarefa = asyncio.Event(), asyncio.Event(), None

    def conexao(self):
        env = {k: v for k, v in os.environ.items() if k.startswith("AIRBNB_")}
        if self.libs:
            env["PYTHONPATH"] = self.libs
        # sem log do servidor na tela: o erro de cada ferramenta já volta ao modelo e fica na medição
        env.update(FASTMCP_LOG_LEVEL="CRITICAL", FASTMCP_SHOW_SERVER_BANNER="false")
        return StdioServerParameters(command=self.python, args=[os.path.join(RAIZ, "servidor", "server.py")], env=env)

    async def _dono(self):
        try:
            with open(self.LOG, "w", encoding="utf-8") as log:
                async with stdio_client(self.conexao(), errlog=log) as (leitura, escrita), \
                        ClientSession(leitura, escrita) as sessao:
                    await sessao.initialize()
                    self.ferramentas = await load_mcp_tools(sessao)
                    self.esquemas = {b.metadata["uri"]: b.as_string()
                                     for b in await load_mcp_resources(sessao, uris=list(self.URIS))}
                    self._pronto.set()
                    await self._fim.wait()
        finally:
            self._pronto.set()

    async def abrir(self):
        if self._tarefa is None:
            self._tarefa = asyncio.create_task(self._dono())
            await self._pronto.wait()
            if self._tarefa.done():
                self._tarefa.result()          # repassa o erro de quem não conseguiu abrir
        return self

    async def fechar(self):
        if self._tarefa is not None:
            self._fim.set()
            await self._tarefa
            self._tarefa = None

    def esquema(self, banco):
        return self.esquemas[f"esquema://{banco}"]

    def so(self, *nomes):
        """Só as ferramentas com estes nomes (para especialistas)."""
        return [f for f in self.ferramentas if f.name in nomes]


# ─── modelo ──────────────────────────────────────────────────────────────────

def modelo(max_tokens=1024):
    """O qwen3:8b no Ollama: temperatura 0, semente fixa, sem o modo de raciocínio (T27)."""
    return ChatOllama(model=MODELO, base_url=OLLAMA_URL, temperature=0, seed=42, num_ctx=NUM_CTX,
                      num_predict=max_tokens, reasoning=False, keep_alive="2h")


async def aquecer():
    """Carrega o modelo na GPU antes de medir (T28): a primeira chamada leva minutos e não entra nas métricas."""
    t = time.time()
    await modelo(max_tokens=5).ainvoke("Responda só: ok")
    return round(time.time() - t, 1)


# ─── medição ─────────────────────────────────────────────────────────────────

def texto(conteudo):
    """O texto de uma mensagem ou de um resultado de ferramenta (o adaptador MCP entrega uma lista de blocos)."""
    if isinstance(conteudo, str):
        return conteudo
    return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in conteudo)


def limpar(resposta):
    """Tira um bloco <think> que escape do modelo e espaços nas pontas."""
    return re.sub(r"<think>.*?</think>", "", resposta or "", flags=re.S).strip()


class Medicao:
    """O que aconteceu numa execução: cada chamada ao modelo e cada chamada de ferramenta, de todos os agentes."""

    def __init__(self):
        self.modelo, self.ferramentas = [], []

    def registrar_modelo(self, msg, agente, segundos):
        uso = getattr(msg, "usage_metadata", None) or {}
        self.modelo.append(dict(agente=agente, tokens_entrada=uso.get("input_tokens", 0),
                                tokens_saida=uso.get("output_tokens", 0), segundos=round(segundos, 2)))

    def registrar_ferramenta(self, chamada, resultado, agente, segundos):
        self.ferramentas.append(dict(agente=agente, ferramenta=chamada["name"], argumentos=chamada["args"],
                                     erro=resultado.status == "error", resultado=texto(resultado.content),
                                     segundos=round(segundos, 2)))


def medicao(config):
    return config["configurable"]["medicao"]


async def chamar(llm, mensagens, config, agente):
    """Chama o modelo e registra tokens e tempo. Com with_structured_output(include_raw=True), registra a mensagem crua."""
    t = time.time()
    r = await llm.ainvoke(mensagens)
    medicao(config).registrar_modelo(r["raw"] if isinstance(r, dict) else r, agente, time.time() - t)
    return r


def no_ferramentas(ferramentas, agente):
    """O nó que executa as ferramentas pedidas pelo modelo e registra cada chamada.
    Um erro da ferramenta (isError) volta ao modelo como mensagem, para ele corrigir (T4)."""
    executor = ToolNode(ferramentas, handle_tool_errors=True)

    async def no(estado, config):
        pedidos = {c["id"]: c for c in estado["messages"][-1].tool_calls}
        t = time.time()
        saida = await executor.ainvoke(estado, config)
        dt = (time.time() - t) / max(len(pedidos), 1)
        for m in saida["messages"]:
            medicao(config).registrar_ferramenta(pedidos[m.tool_call_id], m, agente, dt)
        return saida
    return no


class Amostrador:
    """CPU e RAM da máquina (psutil) e GPU (nvidia-smi), amostradas em segundo plano durante uma execução.
    É a máquina inteira: o Ollama e o servidor MCP rodam em processos separados do notebook."""

    def __init__(self, intervalo=0.5):
        self.intervalo, self.amostras = intervalo, []
        self.tem_gpu = shutil.which("nvidia-smi") is not None
        self._parar = threading.Event()

    def _gpu(self):
        try:
            s = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=2).stdout.split("\n")[0].split(",")
            return float(s[0]), float(s[1]) / 1024
        except Exception:
            return None, None

    def _amostrar(self):
        gpu, vram = self._gpu() if self.tem_gpu else (None, None)
        self.amostras.append((psutil.cpu_percent(None), psutil.virtual_memory().used / 2**30, gpu, vram))

    def _laco(self):
        while not self._parar.wait(self.intervalo):
            self._amostrar()
        self._amostrar()                       # uma no fim, para execuções mais curtas que o intervalo

    def __enter__(self):
        psutil.cpu_percent(None)               # zera a referência: a próxima leitura mede a partir daqui
        self._fio = threading.Thread(target=self._laco, daemon=True)
        self._fio.start()
        return self

    def __exit__(self, *_):
        self._parar.set()
        self._fio.join()

    def resumo(self):
        def col(i, f):
            v = [a[i] for a in self.amostras if a[i] is not None]
            return round(f(v), 2) if v else None
        media = lambda v: sum(v) / len(v)
        return dict(cpu_medio=col(0, media), cpu_max=col(0, max), ram_max_gb=col(1, max),
                    gpu_medio=col(2, media), gpu_max=col(2, max), gpu_mem_max_gb=col(3, max))


# ─── a interface comum ───────────────────────────────────────────────────────

class Sistema:
    """Base dos 3 sistemas. Uma subclasse define nome e executar(pergunta, config) -> (resposta, mensagens)."""

    nome = "?"

    def __init__(self, servidor: Servidor):
        self.servidor = servidor

    async def executar(self, pergunta, config):
        raise NotImplementedError

    async def answer(self, pergunta):
        """Responde uma pergunta e devolve a resposta com os dados da execução."""
        m, t, estourou, erro, resposta, mensagens = Medicao(), time.time(), False, None, "", []
        config = {"recursion_limit": LIMITE_PASSOS, "configurable": {"medicao": m}}
        with Amostrador() as amostras:
            try:
                resposta, mensagens = await self.executar(pergunta, config)
            except GraphRecursionError:
                estourou = True
            except Exception as e:                       # uma falha numa pergunta não derruba as outras 59
                erro = f"{type(e).__name__}: {e}"
        return dict(
            sistema=self.nome, pergunta=pergunta, resposta=limpar(resposta),
            ferramentas=[f["ferramenta"] for f in m.ferramentas],
            chamadas_modelo=len(m.modelo), chamadas_ferramenta=len(m.ferramentas),
            erros_ferramenta=sum(f["erro"] for f in m.ferramentas),
            tokens_entrada=sum(c["tokens_entrada"] for c in m.modelo),
            tokens_saida=sum(c["tokens_saida"] for c in m.modelo),
            segundos=round(time.time() - t, 2), **amostras.resumo(),
            estourou_limite=estourou, erro=erro,
            chamadas=m.modelo, consultas=m.ferramentas, mensagens=mensagens)


def resposta_final(mensagens):
    """A última mensagem do modelo que não pede ferramenta."""
    return next((texto(m.content) for m in reversed(mensagens)
                 if isinstance(m, AIMessage) and not m.tool_calls), "")


def mostrar(r, largura=220):
    """O trace de uma execução: cada consulta, o resultado resumido e a resposta."""
    print(f"👤 {r['pergunta']}\n")
    for c in r["consultas"]:
        args = json.dumps(c["argumentos"], ensure_ascii=False)
        print(f"   🔧 [{c['agente']}] {c['ferramenta']}({args[:largura]})")
        print(f"   {'❌' if c['erro'] else '📡'} {c['resultado'].replace(chr(10), ' ')[:largura]}")
    if r["estourou_limite"]:
        print("   ⛔ passou do limite de passos")
    if r["erro"]:
        print(f"   💥 {r['erro']}")
    print(f"\n💬 {r['resposta']}\n")
    gpu = f" · GPU {r['gpu_medio']}% / {r['gpu_mem_max_gb']} GB" if r["gpu_medio"] is not None else ""
    print(f"⏱ {r['segundos']} s · {r['chamadas_modelo']} chamadas ao modelo · {r['chamadas_ferramenta']} ferramentas "
          f"({r['erros_ferramenta']} com erro) · tokens {r['tokens_entrada']} → {r['tokens_saida']} · "
          f"CPU {r['cpu_medio']}% · RAM {r['ram_max_gb']} GB{gpu}")


__all__ = ["Servidor", "Sistema", "modelo", "aquecer", "chamar", "no_ferramentas", "medicao", "texto",
           "resposta_final", "mostrar", "HumanMessage", "ToolMessage"]
