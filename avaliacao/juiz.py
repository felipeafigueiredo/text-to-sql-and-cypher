"""O juiz: o Claude (modelo de fronteira), pelo Claude Code em modo não interativo, julga cada resposta contra o
gabarito, com acesso somente leitura ao mesmo servidor MCP dos agentes.

Por que o Claude Code e não uma API (T65): a especificação pede um juiz de fronteira com acesso somente leitura ao
servidor MCP, não uma API. O Claude Code é um cliente MCP e roda com a assinatura do grupo, sem custo de API; as
camadas gratuitas das APIs só liberam modelos pequenos (P5).

Cada julgamento é uma chamada `claude -p`, isolada:
  - só o nosso servidor MCP (--mcp-config + --strict-mcp-config) e só as 5 ferramentas dele; as ferramentas do
    próprio Claude Code (terminal, arquivos, web) ficam desligadas (--tools ""); o servidor recusa escrita nos dois bancos;
  - sem as configurações, hooks e memória do usuário (--setting-sources ""), sem sessão salva;
  - veredito estruturado (--json-schema) e o registro de cada chamada de ferramenta (--output-format stream-json);
  - se o servidor MCP não conectar, o julgamento para: sem ferramentas o modelo julgaria de memória.

Entrada: execucoes.jsonl do avaliador (pergunta, gabarito e resposta de cada execução). Roda na máquina com os bancos
(local, com o mesmo recorte do Colab). Saídas, gravadas a cada julgamento, com retomada:
  - juiz.csv: execução, veredito (correta | parcial | incorreta), justificativa, consultas e custo do juiz;
  - juiz.jsonl: o mesmo, com cada consulta do juiz (argumentos e resultado).

No sistema 3, quando o verificador devolveu a resposta, o juiz julga também a primeira resposta do agente (linha
com qual = "anterior"): é o que mostra quantas devoluções mudaram o veredito (T53).

Uso (as variáveis AIRBNB_* apontam para os bancos, como no servidor):
    python avaliacao/juiz.py resultados --python-servidor <python com as libs do servidor>
"""
import argparse
import ast
import asyncio
import csv
import datetime
import json
import os
import shutil
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVIDOR = os.path.join(RAIZ, "servidor", "server.py")
MODELO_JUIZ = os.environ.get("AIRBNB_JUIZ_MODELO", "sonnet")
NOME_MCP = "airbnb"
FERRAMENTAS = ["consultar_sql", "consultar_grafo", "buscar_entidade", "detalhes_anuncio", "comparar_anuncios"]
MAX_CONSULTAS = 6                 # pedido no prompt; o Claude Code não tem limite de voltas nesta versão
MAX_RESULTADO = 4000              # caracteres de cada resultado de ferramenta guardados no juiz.jsonl
TEMPO_MAXIMO = 600                # segundos por julgamento

ESQUEMA_VEREDITO = {
    "type": "object",
    "properties": {
        "veredito": {"type": "string", "enum": ["correta", "parcial", "incorreta"]},
        "justificativa": {"type": "string",
                          "description": "1 a 3 frases: o que a resposta acertou ou errou, citando os números e nomes"},
    },
    "required": ["veredito", "justificativa"],
}

PROMPT_JUIZ = """Você é o juiz de uma avaliação de sistemas que respondem perguntas sobre os anúncios do Airbnb em São \
Paulo (dados do Inside Airbnb, setembro de 2026), consultando um banco relacional e um banco de grafo. Você recebe a \
pergunta, o gabarito (calculado nos bancos por consultas escritas à mão, com o critério do que conta como certo) e a \
resposta de um sistema.

Critérios:
- correta: responde ao que foi perguntado e traz os números e nomes do gabarito. Arredondamento, formato do número \
(4.91 ou 4,91; 3.421 ou 3421) e informação extra correta não tiram ponto.
- parcial: acerta parte e erra ou omite outra parte do que a pergunta pede (ex.: a pergunta pede duas coisas e só uma \
está certa; o gabarito diz que dar só um dos números está incompleto).
- incorreta: o número ou nome principal está errado; traz dado inventado; diz que algo não existe quando existe; \
troca o item perguntado por outro; recusa uma pergunta que os dados respondem; ou responde uma pergunta fora do escopo \
em vez de recusar.

Siga o critério escrito no gabarito quando houver (perguntas capciosas, item inexistente, fora do escopo).

Ferramentas: você tem acesso somente leitura aos mesmos bancos, pelas ferramentas do servidor MCP. Use-as quando a \
resposta afirmar algo que o gabarito não cobre (um número, nome ou fato a mais): confira antes de dar o veredito, \
porque informação extra errada torna a resposta incorreta ou parcial. Não refaça o gabarito. No máximo \
{max_consultas} consultas.

Julgue só a resposta; não importa como o sistema chegou a ela. Responda em português.

Os esquemas dos bancos, para escrever as consultas:

{esquemas}"""

JULGAR = """Pergunta: {pergunta}

Gabarito: {gabarito}

Resposta do sistema:
{resposta}"""

COLUNAS = ["execucao", "sistema", "pergunta_id", "qual", "veredito", "acerto", "justificativa",
           "consultas_juiz", "ferramentas_juiz", "chamadas_juiz", "tokens_entrada_juiz", "tokens_saida_juiz",
           "custo_usd_juiz", "segundos_juiz", "modelo_juiz", "erro", "inicio"]


class LimiteAtingido(Exception):
    """A cota da assinatura acabou (janela de horas ou semanal): parar e retomar depois."""


class SemServidor(Exception):
    """O servidor MCP não conectou: sem ferramentas, o juiz julgaria de memória."""


def esquemas():
    """ESQUEMA_SQL e ESQUEMA_GRAFO lidos do server.py sem importá-lo (o servidor tem dependências próprias)."""
    arvore = ast.parse(open(SERVIDOR, encoding="utf-8").read())
    textos = {a.targets[0].id: a.value.value for a in arvore.body
              if isinstance(a, ast.Assign) and isinstance(a.targets[0], ast.Name)
              and a.targets[0].id in ("ESQUEMA_SQL", "ESQUEMA_GRAFO")}
    return textos["ESQUEMA_SQL"] + "\n\n" + textos["ESQUEMA_GRAFO"]


def executavel_claude():
    """O executável do Claude Code. No Windows, o claude.cmd do npm passaria os argumentos pelo cmd, que estraga as
    aspas do JSON: usa o claude.exe que ele chama."""
    caminho = os.environ.get("AIRBNB_CLAUDE") or shutil.which("claude")
    if not caminho:
        raise RuntimeError("Claude Code não encontrado. Instale (npm install -g @anthropic-ai/claude-code), faça login "
                           "e rode de novo, ou aponte AIRBNB_CLAUDE para o executável.")
    if caminho.lower().endswith(".cmd"):
        exe = os.path.join(os.path.dirname(caminho), "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")
        if os.path.exists(exe):
            return exe
    return caminho


def limite(texto):
    t = (texto or "").lower()
    return "429" in t or "rate_limit" in t or ("limit" in t and ("usage" in t or "reached" in t or "reset" in t))


def interpretar(saida):
    """Lê a saída stream-json de um `claude -p`: confere o servidor MCP, junta as consultas com os resultados e pega
    o veredito estruturado e o custo. Função pura, para o teste usar uma saída gravada."""
    eventos = [json.loads(l) for l in saida.splitlines() if l.strip().startswith("{")]
    init = next((e for e in eventos if e.get("type") == "system" and e.get("subtype") == "init"), None)
    if init is None:
        raise RuntimeError("o Claude Code não iniciou a sessão: " + saida[-500:])
    mcp = {s["name"]: s["status"] for s in init.get("mcp_servers", [])}
    if mcp.get(NOME_MCP) != "connected":
        raise SemServidor(f"servidor MCP '{NOME_MCP}' com status {mcp.get(NOME_MCP)}")

    prefixo, pedidos, consultas = f"mcp__{NOME_MCP}__", {}, []
    for e in eventos:
        if e.get("type") not in ("assistant", "user"):
            continue
        for b in e["message"].get("content", []):
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name", "").startswith(prefixo):
                c = dict(ferramenta=b["name"][len(prefixo):], argumentos=b.get("input", {}), erro=False, resultado="")
                pedidos[b["id"]] = c
                consultas.append(c)
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in pedidos:
                conteudo = b.get("content")
                if isinstance(conteudo, list):
                    conteudo = "".join(x.get("text", "") for x in conteudo if isinstance(x, dict))
                pedidos[b["tool_use_id"]].update(erro=bool(b.get("is_error")), resultado=str(conteudo)[:MAX_RESULTADO])

    fim = next((e for e in reversed(eventos) if e.get("type") == "result"), None)
    if fim is None:
        raise RuntimeError("o Claude Code terminou sem resultado: " + saida[-500:])
    rejeitado = any(e.get("type") == "rate_limit_event" and e.get("rate_limit_info", {}).get("status") == "rejected"
                    for e in eventos)
    if fim.get("is_error") and (rejeitado or limite(str(fim.get("result")) + str(fim.get("api_error_status")))):
        raise LimiteAtingido(str(fim.get("result"))[:300])

    uso = fim.get("usage", {})
    j = fim.get("structured_output") or {}
    ok = not fim.get("is_error") and j.get("veredito") in ("correta", "parcial", "incorreta")
    return dict(
        veredito=j["veredito"] if ok else "falhou",
        justificativa=j.get("justificativa", "") if ok else "",
        erro=None if ok else f"sem veredito estruturado: {str(fim.get('result'))[:300]}",
        consultas=consultas, chamadas_juiz=fim.get("num_turns", 0),
        tokens_entrada_juiz=uso.get("input_tokens", 0) + uso.get("cache_creation_input_tokens", 0)
        + uso.get("cache_read_input_tokens", 0),
        tokens_saida_juiz=uso.get("output_tokens", 0), custo_usd_juiz=fim.get("total_cost_usd"),
        modelo_juiz=init.get("model"))


class Juiz:
    def __init__(self, python_servidor=None, modelo=MODELO_JUIZ, claude=None):
        self.modelo, self.claude = modelo, claude or executavel_claude()
        # o servidor MCP com o mesmo ambiente do servidor dos agentes (bancos e, no Colab, as libs separadas)
        env = {k: v for k, v in os.environ.items() if k.startswith("AIRBNB_")}
        libs = os.environ.get("AIRBNB_SERVIDOR_LIBS")
        if libs:
            env["PYTHONPATH"] = libs
        env.update(FASTMCP_LOG_LEVEL="CRITICAL", FASTMCP_SHOW_SERVER_BANNER="false")
        config = {"mcpServers": {NOME_MCP: {"command": python_servidor or os.environ.get("AIRBNB_SERVIDOR_PYTHON", sys.executable),
                                            "args": [SERVIDOR], "env": env}}}
        self.config = os.path.join(tempfile.mkdtemp(prefix="juiz_"), "mcp.json")
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump(config, f)
        self.sistema = PROMPT_JUIZ.format(max_consultas=MAX_CONSULTAS, esquemas=esquemas())

    def comando(self):
        return [self.claude, "-p", "--output-format", "stream-json", "--verbose",
                "--model", self.modelo, "--system-prompt", self.sistema,
                "--setting-sources", "", "--no-session-persistence",
                "--tools", "", "--mcp-config", self.config, "--strict-mcp-config",
                "--allowedTools", ",".join(f"mcp__{NOME_MCP}__{f}" for f in FERRAMENTAS),
                "--permission-mode", "dontAsk", "--json-schema", json.dumps(ESQUEMA_VEREDITO, ensure_ascii=False)]

    async def julgar(self, pergunta, gabarito, resposta):
        """Devolve o veredito, a justificativa, as consultas feitas e o custo do julgamento."""
        t = time.time()
        prompt = JULGAR.format(pergunta=pergunta, gabarito=gabarito, resposta=resposta or "(vazia)")
        proc = await asyncio.create_subprocess_exec(
            *self.comando(), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env={**os.environ, "MCP_TIMEOUT": "60000"})
        try:
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), TEMPO_MAXIMO)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"o julgamento passou de {TEMPO_MAXIMO} s")
        saida = out.decode("utf-8", errors="replace")
        if not saida.strip():
            erro = err.decode("utf-8", errors="replace")
            if limite(erro):
                raise LimiteAtingido(erro[-300:])
            raise RuntimeError(f"o Claude Code não respondeu (código {proc.returncode}): {erro[-500:]}")
        return {**interpretar(saida), "segundos_juiz": round(time.time() - t, 2)}


def ler_registros(pasta):
    """Os registros completos do avaliador (execucoes.jsonl); se uma execução aparecer duas vezes, vale a última."""
    regs = {}
    with open(os.path.join(pasta, "execucoes.jsonl"), encoding="utf-8") as f:
        for l in f:
            if l.strip():
                r = json.loads(l)
                regs[r["execucao"]] = r
    return list(regs.values())


def a_julgar(registros):
    """O que julgar: a resposta final de cada execução e, no sistema 3 com devolução, a primeira resposta do agente."""
    for r in registros:
        yield r["execucao"], "final", r, r["resposta"]
        if r.get("respostas_anteriores"):
            yield r["execucao"] + "-anterior", "anterior", r, r["respostas_anteriores"][0]


def ja_julgadas(caminho_csv):
    if not os.path.exists(caminho_csv):
        return set()
    with open(caminho_csv, encoding="utf-8", newline="") as f:
        return {l["execucao"] for l in csv.DictReader(f)}


async def julgar_execucoes(juiz, pasta, mostrar=None):
    """Julga o que está em pasta/execucoes.jsonl e grava pasta/juiz.csv e pasta/juiz.jsonl. Devolve quantos julgou.

    Um erro numa execução fica registrado na linha (veredito "falhou") e o laço segue. Cota da assinatura esgotada
    (LimiteAtingido) ou servidor MCP fora do ar (SemServidor) interrompem tudo: rodar de novo retoma de onde parou."""
    caminho_csv, caminho_jsonl = os.path.join(pasta, "juiz.csv"), os.path.join(pasta, "juiz.jsonl")
    feitas, novo = ja_julgadas(caminho_csv), not os.path.exists(caminho_csv)
    julgadas = 0
    with open(caminho_csv, "a", encoding="utf-8", newline="") as fc, open(caminho_jsonl, "a", encoding="utf-8") as fj:
        escritor = csv.DictWriter(fc, fieldnames=COLUNAS)
        if novo:
            escritor.writeheader()
        for eid, qual, reg, resposta in a_julgar(ler_registros(pasta)):
            if eid in feitas:
                continue
            inicio = datetime.datetime.now().isoformat(timespec="seconds")
            try:
                j = await juiz.julgar(reg["pergunta"], reg["gabarito"], resposta)
            except (LimiteAtingido, SemServidor):
                raise
            except Exception as e:
                j = dict(veredito="falhou", justificativa="", erro=f"{type(e).__name__}: {e}", consultas=[],
                         chamadas_juiz=0, tokens_entrada_juiz=0, tokens_saida_juiz=0, custo_usd_juiz=None,
                         modelo_juiz=None, segundos_juiz=0)
            l = {"execucao": eid, "sistema": reg["sistema"], "pergunta_id": reg["pergunta_id"], "qual": qual,
                 "veredito": j["veredito"], "acerto": None if j["veredito"] == "falhou" else j["veredito"] == "correta",
                 "justificativa": j["justificativa"], "consultas_juiz": len(j["consultas"]),
                 "ferramentas_juiz": " | ".join(c["ferramenta"] for c in j["consultas"]),
                 **{k: j[k] for k in ("chamadas_juiz", "tokens_entrada_juiz", "tokens_saida_juiz", "custo_usd_juiz",
                                      "segundos_juiz")},
                 "modelo_juiz": j["modelo_juiz"] or juiz.modelo, "erro": j["erro"], "inicio": inicio}
            fj.write(json.dumps({**l, "resposta": resposta, "consultas": j["consultas"]}, ensure_ascii=False, default=str) + "\n")
            escritor.writerow(l)
            fc.flush(), fj.flush()
            os.fsync(fc.fileno()), os.fsync(fj.fileno())
            julgadas += 1
            if mostrar:
                mostrar(l)
    return julgadas


def imprimir(l):
    marca = {"correta": "✅", "parcial": "🟡", "incorreta": "❌"}.get(l["veredito"], "⚠️")
    print(f"{marca} {l['execucao']:<16} {l['veredito']:<9} · {l['consultas_juiz']} consulta(s) · "
          f"{l['segundos_juiz']:>5.1f} s · {l['justificativa'][:150]}" + (f" · 💥 {l['erro']}" if l["erro"] else ""),
          flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Julga as execuções de uma pasta com o Claude Code.")
    ap.add_argument("pasta", help="pasta com o execucoes.jsonl do avaliador (o juiz.csv e o juiz.jsonl vão para ela)")
    ap.add_argument("--python-servidor", help="o python com as bibliotecas do servidor MCP (padrão: este python)")
    ap.add_argument("--modelo", default=MODELO_JUIZ, help="modelo do Claude Code (padrão: %(default)s)")
    a = ap.parse_args()
    juiz = Juiz(python_servidor=a.python_servidor, modelo=a.modelo)
    try:
        n = asyncio.run(julgar_execucoes(juiz, a.pasta, mostrar=imprimir))
        print(f"\n{n} julgamento(s) agora · juiz.csv em {a.pasta}")
    except LimiteAtingido as e:
        sys.exit(f"\n⏸ cota da assinatura esgotada ({e}). Rode de novo depois: continua de onde parou.")
    except SemServidor as e:
        sys.exit(f"\n⛔ {e}. Confira os bancos, as variáveis AIRBNB_* e o --python-servidor.")
