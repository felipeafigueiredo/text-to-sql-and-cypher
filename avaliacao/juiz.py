"""O juiz: um modelo de fronteira (Gemini, pela API) julga cada resposta contra o gabarito, com acesso somente leitura
ao mesmo servidor MCP dos agentes, para conferir o que o gabarito não resolve sozinho.

Entrada: execucoes.jsonl do avaliador (pergunta, gabarito e resposta de cada execução).
Saídas, gravadas a cada julgamento (com retomada, como no avaliador):
  - juiz.csv: execução, veredito (correta | parcial | incorreta), justificativa, consultas e custo do juiz;
  - juiz.jsonl: o mesmo, com cada consulta do juiz (argumentos e resultado).

Fica separado das execuções de propósito: as chamadas ao Gemini não entram na latência nem nos recursos medidos dos
agentes, e o juiz pode ser rodado de novo (outro prompt, outro modelo) sem rodar os agentes outra vez.

No sistema 3, quando o verificador devolveu a resposta, o juiz julga também a primeira resposta do agente (linha
com qual = "anterior"): é o que mostra quantas devoluções mudaram o veredito (T53).

Configuração: GOOGLE_API_KEY (chave do Google AI Studio) e, opcional, AIRBNB_JUIZ_MODELO e AIRBNB_JUIZ_PAUSA
(segundos entre julgamentos, para respeitar o limite de pedidos por minuto da camada gratuita).
"""
import asyncio
import csv
import datetime
import json
import os
import time
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from agentes.comum import texto
from avaliacao.avaliador import ler_registros

MODELO_JUIZ = os.environ.get("AIRBNB_JUIZ_MODELO", "gemini-flash-latest")
PAUSA = float(os.environ.get("AIRBNB_JUIZ_PAUSA", "0"))
MAX_CONSULTAS = 6                 # consultas do juiz por julgamento
MAX_RESULTADO = 4000              # caracteres de cada resultado de ferramenta guardados no juiz.jsonl
TENTATIVAS = 5                    # em erro de limite (429), espera e tenta de novo


class Julgamento(BaseModel):
    """O veredito do juiz sobre uma resposta."""
    veredito: Literal["correta", "parcial", "incorreta"] = Field(description="correta, parcial ou incorreta")
    justificativa: str = Field(description="1 a 3 frases: o que a resposta acertou ou errou, citando os números e nomes")


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

Ferramentas: você tem acesso somente leitura aos mesmos bancos. Use-as só quando o gabarito não resolver: por \
exemplo, a resposta cita um número ou nome que não está no gabarito e pode estar certo ou errado. Não refaça o \
gabarito. No máximo {max_consultas} consultas.

Julgue só a resposta; não importa como o sistema chegou a ela. Responda em português."""

JULGAR = """Pergunta: {pergunta}

Gabarito: {gabarito}

Resposta do sistema:
{resposta}"""

FINAL = "Dê agora o veredito final (correta, parcial ou incorreta) e a justificativa."

COLUNAS = ["execucao", "sistema", "pergunta_id", "qual", "veredito", "acerto", "justificativa",
           "consultas_juiz", "ferramentas_juiz", "chamadas_juiz", "tokens_entrada_juiz", "tokens_saida_juiz",
           "segundos_juiz", "modelo_juiz", "erro", "inicio"]


def juiz_llm():
    """O modelo do juiz. Separado em função para o teste com modelo falso trocar."""
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(model=MODELO_JUIZ, temperature=0, max_retries=2, timeout=180)


def limite(e):
    """O erro é de limite da API (pedidos por minuto ou por dia)?"""
    s = f"{type(e).__name__} {e}"
    return "429" in s or "RESOURCE_EXHAUSTED" in s or "ResourceExhausted" in s or "quota" in s.lower()


async def com_espera(chamada):
    """Chama o modelo; em erro de limite, espera (30 s, 60 s, ...) e tenta de novo. Depois de TENTATIVAS, desiste:
    se for a cota do dia, julgar de novo no dia seguinte retoma de onde parou."""
    for i in range(TENTATIVAS):
        try:
            return await chamada()
        except Exception as e:
            if not limite(e) or i == TENTATIVAS - 1:
                raise
            await asyncio.sleep(30 * (i + 1))


def tokens(msg):
    uso = getattr(msg, "usage_metadata", None) or {}
    return uso.get("input_tokens", 0), uso.get("output_tokens", 0)


class Juiz:
    def __init__(self, servidor, llm=None):
        llm = llm or juiz_llm()
        self.modelo = getattr(llm, "model", MODELO_JUIZ)
        self.ferramentas = servidor.ferramentas            # todas só leem: o servidor recusa escrita nos dois bancos
        self.com_ferramentas = llm.bind_tools(self.ferramentas)
        self.estruturado = llm.with_structured_output(Julgamento, include_raw=True)
        self.por_nome = {f.name: f for f in self.ferramentas}
        self.sistema = PROMPT_JUIZ.format(max_consultas=MAX_CONSULTAS)

    async def executar(self, chamada):
        """Uma chamada de ferramenta do juiz. O erro (consulta errada, ferramenta inexistente) volta como mensagem,
        para o juiz corrigir, como no loop dos agentes."""
        f = self.por_nome.get(chamada["name"])
        if f is None:
            return ToolMessage(f"Ferramenta inexistente: {chamada['name']}. Use uma de: {', '.join(self.por_nome)}.",
                               tool_call_id=chamada["id"], status="error")
        try:
            return await f.ainvoke({**chamada, "type": "tool_call"})
        except Exception as e:
            return ToolMessage(f"Erro: {e}", tool_call_id=chamada["id"], status="error")

    async def julgar(self, pergunta, gabarito, resposta):
        """Devolve o veredito, a justificativa, as consultas feitas e o custo do julgamento."""
        t, chamadas, tin, tout, consultas = time.time(), 0, 0, 0, []
        msgs = [SystemMessage(self.sistema),
                HumanMessage(JULGAR.format(pergunta=pergunta, gabarito=gabarito, resposta=resposta or "(vazia)"))]
        while True:
            ai = await com_espera(lambda: self.com_ferramentas.ainvoke(msgs))
            chamadas, (a, b) = chamadas + 1, tokens(ai)
            tin, tout = tin + a, tout + b
            msgs.append(ai)
            if not ai.tool_calls:
                break
            if len(consultas) >= MAX_CONSULTAS:
                # pediu mais do que o limite: responde as chamadas pendentes sem executar e pede o veredito
                msgs.pop()
                msgs.append(AIMessage(content=texto(ai.content) or "(limite de consultas)"))
                break
            for c in ai.tool_calls:
                m = await self.executar(c)
                consultas.append(dict(ferramenta=c["name"], argumentos=c["args"], erro=m.status == "error",
                                      resultado=texto(m.content)[:MAX_RESULTADO]))
                msgs.append(m)
        r = await com_espera(lambda: self.estruturado.ainvoke(msgs + [HumanMessage(FINAL)]))
        chamadas, (a, b) = chamadas + 1, tokens(r["raw"])
        j = r["parsed"]
        return dict(veredito=j.veredito if j else "falhou", justificativa=j.justificativa if j else texto(r["raw"].content),
                    consultas=consultas, chamadas_juiz=chamadas, tokens_entrada_juiz=tin + a,
                    tokens_saida_juiz=tout + b, segundos_juiz=round(time.time() - t, 2))


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

    Um erro numa execução (que não seja de limite) fica registrado na linha, com veredito "falhou", e o laço segue;
    o erro de limite que persiste interrompe tudo (rodar de novo retoma)."""
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
                j, erro = await juiz.julgar(reg["pergunta"], reg["gabarito"], resposta), None
            except Exception as e:
                if limite(e):
                    raise
                j, erro = dict(veredito="falhou", justificativa="", consultas=[], chamadas_juiz=0,
                               tokens_entrada_juiz=0, tokens_saida_juiz=0, segundos_juiz=0), f"{type(e).__name__}: {e}"
            l = {"execucao": eid, "sistema": reg["sistema"], "pergunta_id": reg["pergunta_id"], "qual": qual,
                 "veredito": j["veredito"], "acerto": None if j["veredito"] == "falhou" else j["veredito"] == "correta",
                 "justificativa": j["justificativa"], "consultas_juiz": len(j["consultas"]),
                 "ferramentas_juiz": " | ".join(c["ferramenta"] for c in j["consultas"]),
                 **{k: j[k] for k in ("chamadas_juiz", "tokens_entrada_juiz", "tokens_saida_juiz", "segundos_juiz")},
                 "modelo_juiz": juiz.modelo, "erro": erro, "inicio": inicio}
            fj.write(json.dumps({**l, "resposta": resposta, "consultas": j["consultas"]}, ensure_ascii=False, default=str) + "\n")
            escritor.writerow(l)
            fc.flush(), fj.flush()
            os.fsync(fc.fileno()), os.fsync(fj.fileno())
            julgadas += 1
            if mostrar:
                mostrar(l)
            if PAUSA:
                await asyncio.sleep(PAUSA)
    return julgadas
