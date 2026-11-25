"""Sistema 2 · agente único + roteamento: um roteador escolhe o banco e um especialista responde.

Hipótese (seção 4 do PLANO): com menos contexto por agente — um só esquema e só as ferramentas do seu banco —
o modelo pequeno escreve consultas melhores.

    START ─► roteador ─┬─ sql ─────────────► especialista SQL ──────────────────► END
                       ├─ cypher ──────────► especialista grafo ────────────────► END
                       ├─ ambos ───────────► especialista grafo ─► especialista SQL ─► END
                       └─ fora_do_escopo ──► recusa fixa (sem modelo, sem ferramenta) ─► END

- O roteador tem saída estruturada (Rota) e vê só uma descrição curta de cada banco, não os esquemas.
- Cada especialista é o agente único (grafo_agente) com o esquema e as ferramentas de um banco só.
- "ambos" vai sempre grafo → SQL: nestes dados o que só o grafo tem são as relações (bairros vizinhos, quem anuncia
  ou avaliou o quê), e os números (preço, nota, comodidade, data) só o SQL tem (T6). O grafo resolve a parte de
  relações e passa os nomes/ids; o SQL calcula e escreve a resposta final, sem um redator a mais.
- Se a saída do roteador não vier no formato, a rota fica "falhou" e a pergunta segue como "ambos", que tem os dois
  bancos. A rota escolhida vai para a medição (coluna rota) para medir o acerto do roteador.
"""
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from agentes import agente_unico as au
from agentes.comum import Sistema, chamar, medicao, modelo, resposta_final

FERRAMENTAS = {
    "sql": ("consultar_sql", "buscar_entidade", "detalhes_anuncio", "comparar_anuncios"),
    "grafo": ("consultar_grafo", "buscar_entidade"),
}

RECUSA = ("Não posso responder: os dados são só dos anúncios do Airbnb em São Paulo (anúncios, preços, notas, "
          "comodidades, anfitriões, hóspedes, avaliações e bairros).")


class Rota(BaseModel):
    """Para qual banco a pergunta vai."""
    banco: Literal["sql", "cypher", "ambos", "fora_do_escopo"] = Field(description="o banco que responde a pergunta")
    motivo: str = Field(description="o motivo, em uma frase curta")


# Nenhum exemplo é pergunta da avaliação.
PROMPT_ROTEADOR = """Você encaminha perguntas sobre os anúncios do Airbnb em São Paulo para o banco de dados certo.

- sql (PostgreSQL): tudo que é número ou atributo — preço, nota, comodidades, tipo de imóvel, capacidade, \
datas e contagens de avaliações, ocupação, receita, dados de um anúncio ou anfitrião, rankings de bairros por \
esses números. Também serve para conferir se um anúncio ou anfitrião existe.
- cypher (Neo4j): relações — bairros que fazem divisa (vizinhos, vizinhos dos vizinhos), em quais bairros um \
anfitrião anuncia, quais hóspedes avaliaram os anúncios de um anfitrião, hóspedes em comum. O grafo não tem preço, \
nota, comodidade nem data.
- ambos: a pergunta precisa de uma relação do grafo E de um número do SQL (por exemplo, um número calculado nos \
bairros vizinhos de outro bairro).
- fora_do_escopo: a pergunta não é sobre os anúncios do Airbnb em São Paulo.

Exemplos:
"Qual é o preço médio dos estúdios na Bela Vista?" → sql
"Quantos anúncios aceitam animais de estimação?" → sql
"Quais bairros fazem divisa com o Tatuapé?" → cypher
"Quais hóspedes avaliaram anúncios de dois anfitriões diferentes?" → cypher
"Qual é o anúncio mais barato nos bairros vizinhos do Butantã?" → ambos
"Quem ganhou o jogo do Corinthians ontem?" → fora_do_escopo"""

SO_SQL = "\n\nVocê só tem o banco relacional (PostgreSQL). Responda com ele."
SO_GRAFO = "\n\nVocê só tem o banco de grafo (Neo4j). Responda com ele."

# A mensagem para o especialista do grafo quando a rota é "ambos": ele resolve só as relações.
PARTE_GRAFO = """Pergunta: {pergunta}

Esta pergunta também precisa do banco relacional, que outro agente vai consultar depois de você. Faça só a parte \
de relações que está no grafo (bairros vizinhos, anúncios de um anfitrião, hóspedes...) e responda com a lista \
completa de nomes ou ids encontrados, sem calcular preços, notas ou outros números que o grafo não tem."""

# E a mensagem para o especialista SQL, com o que veio do grafo.
PARTE_SQL = """Pergunta: {pergunta}

O agente do banco de grafo já levantou esta parte:
{grafo}

Use esses nomes ou ids nas suas consultas, calcule o que falta e responda a pergunta."""


class Estado(TypedDict):
    pergunta: str
    rota: str
    grafo: str                                   # o que o especialista do grafo levantou (rota "ambos")
    resposta: str
    messages: Annotated[list, add_messages]


def roteador_llm():
    """O modelo do roteador. Separado em função para o teste com modelo falso trocar."""
    return modelo(max_tokens=200).with_structured_output(Rota, include_raw=True)


class Roteamento(Sistema):
    nome = "roteamento"

    def __init__(self, servidor):
        super().__init__(servidor)
        self.roteador = roteador_llm()
        self.sql = au.grafo_agente(servidor.so(*FERRAMENTAS["sql"]),
                                   au.prompt_sistema(servidor, bancos=("sql",)) + SO_SQL, nome="sql")
        self.grafo_esp = au.grafo_agente(servidor.so(*FERRAMENTAS["grafo"]),
                                         au.prompt_sistema(servidor, bancos=("grafo",)) + SO_GRAFO, nome="grafo")
        self.grafo = self.montar()

    async def no_roteador(self, estado, config):
        r = await chamar(self.roteador, [SystemMessage(PROMPT_ROTEADOR), HumanMessage(estado["pergunta"])],
                         config, "roteador")
        rota = r["parsed"].banco if r.get("parsed") else "falhou"
        medicao(config).rota = rota
        return {"rota": rota}

    async def no_grafo(self, estado, config):
        ambos = estado["rota"] != "cypher"
        texto = PARTE_GRAFO.format(pergunta=estado["pergunta"]) if ambos else estado["pergunta"]
        out = await self.grafo_esp.ainvoke({"messages": [HumanMessage(texto)]}, config)
        achado = resposta_final(out["messages"])
        return {"messages": out["messages"], **({"grafo": achado} if ambos else {"resposta": achado})}

    async def no_sql(self, estado, config):
        texto = (PARTE_SQL.format(pergunta=estado["pergunta"], grafo=estado.get("grafo") or "(nada)")
                 if estado["rota"] != "sql" else estado["pergunta"])
        out = await self.sql.ainvoke({"messages": [HumanMessage(texto)]}, config)
        return {"messages": out["messages"], "resposta": resposta_final(out["messages"])}

    async def no_fora(self, estado, config):
        return {"messages": [AIMessage(RECUSA)], "resposta": RECUSA}

    def montar(self):
        g = StateGraph(Estado)
        g.add_node("roteador", self.no_roteador)
        g.add_node("sql", self.no_sql)
        g.add_node("grafo", self.no_grafo)
        g.add_node("fora", self.no_fora)
        g.add_edge(START, "roteador")
        g.add_conditional_edges("roteador", lambda e: e["rota"],
                                {"sql": "sql", "cypher": "grafo", "ambos": "grafo", "falhou": "grafo",
                                 "fora_do_escopo": "fora"})
        g.add_conditional_edges("grafo", lambda e: END if e["rota"] == "cypher" else "sql", ["sql", END])
        g.add_edge("sql", END)
        g.add_edge("fora", END)
        return g.compile()

    async def executar(self, pergunta, config):
        out = await self.grafo.ainvoke({"pergunta": pergunta, "rota": "", "grafo": "", "resposta": "",
                                        "messages": [HumanMessage(pergunta)]}, config)
        return out["resposta"], out["messages"]
