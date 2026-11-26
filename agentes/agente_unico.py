"""Sistema 1 · agente único: um modelo com os dois esquemas e as 5 ferramentas, no loop modelo ⇄ ferramentas.

É também a peça dos outros dois sistemas (T3): o roteamento e o verificador montam agentes com grafo_agente().

    START ─► modelo ──── pediu ferramenta ───► ferramentas
               ▲                                   │
               └──────────── resultado ────────────┘
               │
               └─► respondeu com texto ─► END
"""
from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import tools_condition

from agentes.comum import Sistema, chamar, modelo, no_ferramentas, resposta_final

# As regras valem para qualquer pergunta sobre estes dados. Nenhum exemplo é pergunta da avaliação.
REGRAS = """Você responde perguntas sobre os anúncios do Airbnb em São Paulo (dados do Inside Airbnb, setembro de 2026): \
anúncios, preços, notas, comodidades, anfitriões, hóspedes, avaliações e bairros, consultando bancos de dados pelas \
ferramentas.

Regras:
1. Busque os dados com as ferramentas. Nunca invente números, nomes ou ids.
2. Não descreva o que vai fazer nem anuncie os próximos passos: chame a ferramenta. Uma resposta em texto encerra \
o atendimento, então só responda em texto quando tiver a resposta final. Se a pergunta precisar de várias \
consultas, faça todas antes de responder.
3. Comodidades, tipos de acomodação e tipos de imóvel estão em inglês nos bancos. Traduza os termos da pergunta: \
por exemplo, "ar-condicionado" é a comodidade 'Air conditioning' e "quarto privativo" é o room_type 'Private room'. \
Os bairros estão sem acento ('Republica', 'Bras'). Se não souber o nome exato de um anúncio, anfitrião, hóspede, \
bairro ou comodidade, use buscar_entidade.
4. Faça contagens, médias e rankings dentro da consulta (COUNT, AVG, ORDER BY ... LIMIT), não de cabeça. \
Se o resultado vier com "truncado": true, você recebeu só parte das linhas: use "total_linhas" ou refaça a \
consulta agregando.
5. Se uma ferramenta devolver erro, leia a dica e corrija a chamada.
6. Se a busca não encontrar o que foi perguntado, responda que não existe nos dados. Não troque por outro item \
parecido e não insista em novas buscas. A exceção é quando a ferramenta disser que o nome existe com outro \
tipo: aí repita a busca com esse tipo.
7. Se a pergunta não for sobre os anúncios do Airbnb em São Paulo (anúncios, anfitriões, hóspedes, avaliações, \
bairros), diga que não pode responder com estes dados, sem chamar ferramentas.
8. Responda em português, de forma direta, com os números e nomes encontrados. Identifique anúncios, anfitriões \
e bairros pelo nome (traga o name na consulta), não só pelo id."""


def prompt_sistema(servidor, bancos=("sql", "grafo"), regras=REGRAS):
    """As regras e os esquemas lidos dos resources do servidor (esquema://sql, esquema://grafo)."""
    return regras + "\n\n" + "\n\n".join(servidor.esquema(b) for b in bancos)


class Estado(TypedDict):
    messages: Annotated[list, add_messages]


def grafo_agente(ferramentas, sistema, nome="agente", max_tokens=1024):
    """O loop modelo ⇄ ferramentas como grafo do LangGraph. nome identifica o agente na medição."""
    llm = modelo(max_tokens).bind_tools(ferramentas)

    async def no_modelo(estado, config):
        return {"messages": [await chamar(llm, [SystemMessage(sistema)] + estado["messages"], config, nome)]}

    g = StateGraph(Estado)
    g.add_node("modelo", no_modelo)
    g.add_node("ferramentas", no_ferramentas(ferramentas, nome))
    g.add_edge(START, "modelo")
    g.add_conditional_edges("modelo", tools_condition, {"tools": "ferramentas", END: END})
    g.add_edge("ferramentas", "modelo")
    return g.compile()


class AgenteUnico(Sistema):
    nome = "agente único"

    def __init__(self, servidor):
        super().__init__(servidor)
        self.grafo = grafo_agente(servidor.ferramentas, prompt_sistema(servidor))

    async def executar(self, pergunta, config):
        out = await self.grafo.ainvoke({"messages": [HumanMessage(pergunta)]}, config)
        return resposta_final(out["messages"]), out["messages"]
