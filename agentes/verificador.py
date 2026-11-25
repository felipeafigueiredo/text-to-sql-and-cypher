"""Sistema 3 · agente único + verificador: o agente responde e um verificador confere a resposta contra as consultas.

Hipótese (seção 4 do PLANO): um segundo olhar pega erros de sentido que o loop modelo ⇄ ferramentas não pega,
porque nesses erros a consulta roda sem erro e devolve um número, só que não o número pedido.

    START ─► agente único ─► verificador ─┬─ aprovada, ou já devolveu 2 vezes ─► END
                  ▲                       │
                  └──── devolução ────────┘   (a objeção entra na mesma conversa do agente)

- O agente é o do sistema 1 (grafo_agente com os dois esquemas e as 5 ferramentas), sem mudança (T3).
- O código, não o modelo, reúne as consultas que deram certo e os resultados; o verificador recebe a pergunta, essas
  consultas, a resposta e os dois esquemas, e julga duas coisas: (a) as consultas respondem à pergunta feita
  (banco, filtros, agregação)? (b) os números e nomes da resposta batem com os resultados?
- Objeção só com evidência citada. O verificador não olha sintaxe (T4: o loop do agente já corrige) e não contesta
  resultado vazio (T5: na pergunta de item inexistente, vazio é a resposta certa).
- No máximo 2 devoluções; depois disso vale a última resposta (o veredito dela fica registrado). Saída do verificador fora do formato conta como
  aprovada (e fica marcada como "falhou" na medição).
"""
import json
from typing import TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from agentes import agente_unico as au
from agentes.comum import Sistema, chamar, medicao, modelo, resposta_final, texto

MAX_DEVOLUCOES = 2
MAX_RESULTADO = 2500             # caracteres de cada resultado mostrados ao verificador


class Veredito(BaseModel):
    """O veredito do verificador sobre a resposta do agente."""
    aprovada: bool = Field(description="true se as consultas respondem à pergunta e a resposta bate com os resultados")
    objecao: str = Field(default="", description="se não aprovada: o que está errado e o que corrigir, em 1 ou 2 frases")
    evidencia: str = Field(default="", description="se não aprovada: o trecho da consulta ou do resultado que mostra o erro")


# Nenhum exemplo é pergunta da avaliação.
PROMPT_VERIFICADOR = """Você revisa a resposta de um agente que consultou bancos de dados sobre os anúncios do Airbnb \
em São Paulo. Você recebe a pergunta, as consultas que o agente executou com os resultados, e a resposta dele. Os \
esquemas dos bancos estão no fim.

Verifique duas coisas:
1. As consultas respondem à pergunta feita? Confira o banco, os filtros (bairro, comodidade, tipo, período), a \
agregação (contagem, média, ranking) e se o resultado traz o que a pergunta pede. Por exemplo: se a pergunta é sobre \
os vizinhos de um bairro e o resultado traz o próprio bairro, a consulta devolveu o nó errado; se a pergunta é sobre \
as avaliações de 2025 e a consulta usou um contador da vida toda, o período está errado.
2. Os números e nomes da resposta batem com os resultados das consultas? A resposta não pode trazer número ou nome \
que não esteja nos resultados.

Regras:
- Aprove quando as duas coisas estiverem certas. Na dúvida, aprove.
- Só faça objeção se puder citar a evidência: o trecho da consulta ou do resultado que mostra o erro.
- Não comente erros de sintaxe nem consultas que falharam: o agente já os corrige sozinho.
- Resultado vazio não é erro: se a busca não achou o item, a resposta certa é dizer que ele não existe nos dados.
- Se a pergunta não é sobre os anúncios do Airbnb em São Paulo, o certo é recusar sem consultar. Se ela é sobre \
esses dados, responder sem consultar é erro.
- Na objeção, diga o que está errado e o que o agente deve consultar de novo."""

REVISAR = """Pergunta: {pergunta}

Consultas executadas, com os resultados:
{consultas}

Resposta do agente:
{resposta}"""

DEVOLUCAO = """Um revisor conferiu sua resposta e encontrou um problema:
{objecao}
Evidência: {evidencia}

Refaça as consultas que precisarem de correção e responda de novo, completa, a pergunta: {pergunta}"""


def consultas_feitas(mensagens):
    """As chamadas de ferramenta que deram certo, com argumentos e resultado, na ordem em que aconteceram."""
    pedidos = {c["id"]: c for m in mensagens if isinstance(m, AIMessage) for c in m.tool_calls}
    feitas = []
    for m in mensagens:
        if isinstance(m, ToolMessage) and m.status != "error" and m.tool_call_id in pedidos:
            c, r = pedidos[m.tool_call_id], texto(m.content)
            corte = f"\n(... resultado cortado em {MAX_RESULTADO} caracteres)" if len(r) > MAX_RESULTADO else ""
            feitas.append(f"Consulta {len(feitas) + 1} · {c['name']}\nArgumentos: "
                          f"{json.dumps(c['args'], ensure_ascii=False)}\nResultado: {r[:MAX_RESULTADO]}{corte}")
    return "\n\n".join(feitas) or "(nenhuma consulta)"


class Estado(TypedDict):
    pergunta: str
    conversa: list               # a conversa do agente, que continua a cada devolução
    resposta: str
    aprovada: bool


def verificador_llm():
    """O modelo do verificador. Separado em função para o teste com modelo falso trocar."""
    return modelo(max_tokens=400).with_structured_output(Veredito, include_raw=True)


class Verificador(Sistema):
    nome = "verificador"

    def __init__(self, servidor):
        super().__init__(servidor)
        self.agente = au.grafo_agente(servidor.ferramentas, au.prompt_sistema(servidor))
        self.verificador = verificador_llm()
        self.sistema_verificador = au.prompt_sistema(servidor, regras=PROMPT_VERIFICADOR)
        self.grafo = self.montar()

    async def no_agente(self, estado, config):
        out = await self.agente.ainvoke({"messages": estado["conversa"]}, config)
        return {"conversa": out["messages"], "resposta": resposta_final(out["messages"])}

    async def no_verificador(self, estado, config):
        m = medicao(config)
        pedido = REVISAR.format(pergunta=estado["pergunta"], consultas=consultas_feitas(estado["conversa"]),
                                resposta=estado["resposta"] or "(vazia)")
        r = await chamar(self.verificador, [SystemMessage(self.sistema_verificador), HumanMessage(pedido)],
                         config, "verificador")
        v = r.get("parsed")
        aprovada = True if v is None else v.aprovada
        devolver = not aprovada and sum(x["devolvida"] for x in m.verificacoes) < MAX_DEVOLUCOES
        m.verificacoes.append(dict(aprovada=aprovada, devolvida=devolver, falhou=v is None,
                                   objecao="" if v is None else v.objecao, evidencia="" if v is None else v.evidencia,
                                   resposta=estado["resposta"], apos_consulta=len(m.ferramentas)))
        if not devolver:                 # aprovada, ou reprovada depois de esgotar as devoluções: vale esta resposta
            return {"aprovada": True}
        m.respostas_anteriores.append(estado["resposta"])
        devolucao = HumanMessage(DEVOLUCAO.format(objecao=v.objecao, evidencia=v.evidencia or "(não citada)",
                                                  pergunta=estado["pergunta"]))
        return {"aprovada": False, "conversa": estado["conversa"] + [devolucao]}

    @staticmethod
    def depois_do_verificador(estado):
        return END if estado["aprovada"] else "agente"

    def montar(self):
        g = StateGraph(Estado)
        g.add_node("agente", self.no_agente)
        g.add_node("verificador", self.no_verificador)
        g.add_edge(START, "agente")
        g.add_edge("agente", "verificador")
        g.add_conditional_edges("verificador", self.depois_do_verificador, ["agente", END])
        return g.compile()

    async def executar(self, pergunta, config):
        out = await self.grafo.ainvoke({"pergunta": pergunta, "conversa": [HumanMessage(pergunta)], "resposta": "",
                                        "aprovada": False}, config)
        return out["resposta"], out["conversa"]
