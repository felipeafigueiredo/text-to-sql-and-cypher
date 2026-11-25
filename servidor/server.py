"""Servidor MCP do projeto: anúncios do Airbnb em São Paulo (Inside Airbnb) em dois bancos.

- PostgreSQL: atributos, números e comodidades (anúncios, anfitriões, bairros, avaliações, hóspedes).
- Neo4j: relações (quem anuncia o quê, onde fica cada anúncio, quem se hospedou onde, bairros vizinhos).

Ferramentas (o modelo decide quando chamar):
    consultar_sql       SELECT somente leitura no PostgreSQL
    consultar_grafo     consulta Cypher somente leitura no Neo4j
    buscar_entidade     acha anúncio, anfitrião, hóspede, bairro ou comodidade pelo nome (resolve homônimos e inexistentes)
    detalhes_anuncio    todos os dados de um anúncio
    comparar_anuncios   compara anúncios e calcula o melhor em cada critério   <- ferramenta de cálculo
Resources (a aplicação lê):  esquema://sql, esquema://grafo
Prompt (o usuário escolhe):  analisar_bairro

Toda chamada errada devolve um erro de execução (isError) com uma mensagem que diz o que corrigir,
para o modelo tentar de novo dentro do loop. Resultado vazio não é erro.

Configuração por variáveis de ambiente: AIRBNB_PG_DSN, AIRBNB_NEO4J_URI, AIRBNB_NEO4J_USUARIO, AIRBNB_NEO4J_SENHA.
Roda pelo transporte stdio:  python servidor/server.py
"""
import datetime
import decimal
import os
import re
from typing import Annotated, Literal

import psycopg
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from neo4j import READ_ACCESS, GraphDatabase, Query
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from pydantic import Field

MAX_LINHAS = 50                       # teto de linhas devolvidas ao modelo por consulta
TIMEOUT_S = 15                        # teto de tempo por consulta (o PostgreSQL também impõe pelo usuário leitor)
PG_DSN = os.environ.get("AIRBNB_PG_DSN", "host=localhost port=5432 dbname=airbnb user=leitor password=leitor")
NEO4J_URI = os.environ.get("AIRBNB_NEO4J_URI", "bolt://localhost:7687")
NEO4J_AUTH = (os.environ.get("AIRBNB_NEO4J_USUARIO", "neo4j"), os.environ.get("AIRBNB_NEO4J_SENHA", "airbnb-sao-paulo"))

NOTAS = ["rating", "accuracy", "cleanliness", "checkin", "communication", "location", "value"]
TABELAS_SQL = {
    "neighbourhood": "name, group_name",
    "host": "host_id, name, is_superhost, identity_verified, listings_count",
    "listing": "listing_id, host_id, neighbourhood, name, latitude, longitude, property_type, room_type, accommodates, "
               "bedrooms, beds, bathrooms, price, minimum_nights, availability_365, number_of_reviews, first_review, "
               "last_review, " + ", ".join(f"review_scores_{n}" for n in NOTAS) +
               ", estimated_occupancy_l365d, estimated_revenue_l365d",
    "listing_amenity": "listing_id, amenity",
    "guest": "guest_id, name",
    "review": "review_id, listing_id, guest_id, date",
}
ROTULOS_GRAFO = {"Host", "Listing", "Guest", "Neighbourhood"}
RELACOES_GRAFO = {"HOSTS", "LOCATED_IN", "REVIEWED", "BORDERS"}
PROPRIEDADES_GRAFO = {"host_id", "listing_id", "guest_id", "name"}

ESQUEMA_SQL = """BANCO RELACIONAL (PostgreSQL) — ferramenta consultar_sql. Cidade única: São Paulo, coleta de setembro de 2026.

neighbourhood(name, group_name)
  96 distritos. name SEM ACENTO, como no dado original: 'Republica', 'Bras', 'Butanta', 'Se', 'Jardim Paulista'.
  Para comparar com o que o usuário escreveu, use unaccent(): WHERE unaccent(name) ILIKE unaccent('Brás').
  group_name: agrupamento de distritos (subprefeitura), ex.: 'Pinheiros', 'Se', 'Vila Mariana'.

host(host_id, name, is_superhost, identity_verified, listings_count)
  name: só o primeiro nome (ou o nome da empresa); há muitos homônimos.
  ATENÇÃO: listings_count conta os anúncios do anfitrião no Airbnb INTEIRO (todas as cidades).
  Para anúncios em São Paulo, conte na tabela listing: SELECT count(*) FROM listing WHERE host_id = ...

listing(listing_id, host_id, neighbourhood, name, latitude, longitude, property_type, room_type, accommodates,
        bedrooms, beds, bathrooms, price, minimum_nights, availability_365, number_of_reviews, first_review, last_review,
        review_scores_rating, review_scores_accuracy, review_scores_cleanliness, review_scores_checkin,
        review_scores_communication, review_scores_location, review_scores_value,
        estimated_occupancy_l365d, estimated_revenue_l365d)
  Um anúncio por linha; o nome (name) NÃO é único. neighbourhood é o name da tabela neighbourhood.
  room_type: 'Entire home/apt', 'Private room', 'Shared room', 'Hotel room'. property_type em inglês ('Entire rental unit'...).
  price: R$ por noite; NULO em ~3,5% dos anúncios. accommodates: capacidade máxima de hóspedes.
  review_scores_*: notas de 0 a 5 dadas pelos hóspedes; NULAS nos anúncios sem avaliação.
  ATENÇÃO: number_of_reviews conta as avaliações da vida toda do anúncio (desde 2011);
  a tabela review só tem as de 2025 em diante.
  estimated_occupancy_l365d: noites ocupadas estimadas no último ano; estimated_revenue_l365d: receita estimada em R$.

listing_amenity(listing_id, amenity)
  Comodidades de cada anúncio, em inglês: 'Wifi', 'Kitchen', 'Pool', 'Air conditioning', 'Gym', 'Pets allowed'...
  Só as presentes em pelo menos 1% dos anúncios.

guest(guest_id, name)
  Hóspedes que avaliaram algum anúncio de 2025 em diante. name: só o primeiro nome; há muitos homônimos.

review(review_id, listing_id, guest_id, date)
  Uma linha por avaliação, de 2025-01-01 a 2026-09. A avaliação NÃO tem nota própria:
  as notas estão em listing.review_scores_*. O mesmo hóspede pode avaliar o mesmo anúncio mais de uma vez.

Os ids (listing_id, host_id, guest_id) são números inteiros grandes: escreva-os sem aspas.
Chaves compartilhadas com o grafo: listing_id, host_id, guest_id e o nome do bairro.
A vizinhança entre bairros NÃO está no SQL: está no grafo."""

ESQUEMA_GRAFO = """BANCO DE GRAFO (Neo4j) — ferramenta consultar_grafo. Cidade única: São Paulo.

Nós:
  (:Host {host_id, name})            anfitrião
  (:Listing {listing_id, name})      anúncio
  (:Guest {guest_id, name})          hóspede que avaliou algum anúncio de 2025 em diante
  (:Neighbourhood {name})            bairro (distrito), sem acento: 'Republica', 'Bras', 'Pinheiros'
Relações:
  (:Host)-[:HOSTS]->(:Listing)                 o anfitrião anuncia o anúncio
  (:Listing)-[:LOCATED_IN]->(:Neighbourhood)   o anúncio fica no bairro
  (:Guest)-[:REVIEWED]->(:Listing)             o hóspede avaliou o anúncio (uma relação por par, sem propriedades)
  (:Neighbourhood)-[:BORDERS]-(:Neighbourhood) bairros vizinhos; consulte SEM direção: (a)-[:BORDERS]-(b)

Os ids são números inteiros: {listing_id: 123}, sem aspas.
O grafo NÃO tem preços, notas, datas, comodidades nem contagens: isso está no SQL.
Para contar vizinhos, conte o nó do outro lado:
  MATCH (l:Listing {listing_id: 123})<-[:REVIEWED]-(g:Guest) RETURN count(DISTINCT g)
Para contar, use count() na própria consulta: as listas devolvidas são cortadas em 50 linhas.
Chaves compartilhadas com o SQL: listing_id, host_id, guest_id e o nome do bairro. Para uma pergunta que precisa
dos dois bancos, consulte o grafo, pegue os ids e use-os no SQL (ou o contrário)."""

mcp = FastMCP("airbnb-sao-paulo", instructions=(
    "Anúncios do Airbnb em São Paulo (Inside Airbnb, setembro de 2026) em dois bancos: PostgreSQL (preços, notas, "
    "comodidades, avaliações) e Neo4j (anfitriões, hóspedes em comum, bairros vizinhos). "
    "Leia esquema://sql e esquema://grafo antes de escrever consultas."))


# ─── acesso aos bancos ───

_pg = None
_neo4j = None


def pg():
    global _pg
    if _pg is None or _pg.closed:
        try:
            _pg = psycopg.connect(PG_DSN, autocommit=True, connect_timeout=10)
        except psycopg.OperationalError as e:
            raise ToolError(f"O PostgreSQL não está acessível ({e}). Isto é um problema do ambiente, não da consulta.")
    return _pg


def grafo():
    global _neo4j
    if _neo4j is None:
        _neo4j = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    return _neo4j


def jsonavel(v):
    """Converte o que vem dos bancos em tipos que viram JSON."""
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat(sep=" ") if isinstance(v, datetime.datetime) else v.isoformat()
    if isinstance(v, dict):
        return {k: jsonavel(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonavel(x) for x in v]
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def sql_linhas(consulta, params=()):
    """Consulta interna (escrita por nós, não pelo modelo): devolve lista de dicionários."""
    with pg().cursor() as cur:
        cur.execute(consulta, params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, jsonavel(list(l)))) for l in cur.fetchall()]


def resultado(linhas, total, colunas=None):
    r = {"linhas": linhas, "linhas_devolvidas": len(linhas), "total_linhas": total, "truncado": total > len(linhas)}
    if colunas is not None:
        r = {"colunas": colunas, **r}
    if r["truncado"]:
        r["aviso"] = (f"O resultado tem {total} linhas e só as {len(linhas)} primeiras foram devolvidas. "
                      "Para contar, use count() na consulta; para ver outras linhas, refine o filtro ou use ORDER BY e LIMIT.")
    return r


def erro_sql(e: psycopg.Error) -> ToolError:
    msg = str(e).strip().splitlines()[0]
    tabelas = "; ".join(f"{t}({c})" for t, c in TABELAS_SQL.items())
    if isinstance(e, psycopg.errors.UndefinedTable):
        dica = f"Tabela inexistente. As tabelas são: {tabelas}. Os nomes estão em inglês e no singular."
    elif isinstance(e, psycopg.errors.UndefinedColumn):
        dica = f"Coluna inexistente. As colunas de cada tabela são: {tabelas}."
    elif isinstance(e, psycopg.errors.QueryCanceled):
        dica = (f"A consulta passou de {TIMEOUT_S} s e foi cancelada. Evite produtos cartesianos (junções sem condição), "
                "filtre antes de juntar e use LIMIT.")
    elif isinstance(e, (psycopg.errors.ReadOnlySqlTransaction, psycopg.errors.InsufficientPrivilege)):
        dica = "O banco é somente leitura: só consultas SELECT são permitidas."
    elif isinstance(e, psycopg.errors.SyntaxError):
        dica = "Erro de sintaxe SQL. Confira a consulta e envie de novo."
    elif isinstance(e, psycopg.errors.UndefinedFunction):
        dica = ("Função ou operador inexistente para esses tipos. Confira os tipos das colunas: os ids são inteiros "
                "(sem aspas), price e review_scores_* são números.")
    elif isinstance(e, psycopg.errors.InvalidTextRepresentation):
        dica = "Valor com tipo errado: os ids são inteiros (sem aspas) e as datas vão no formato 'AAAA-MM-DD'."
    else:
        dica = "Confira a consulta contra o esquema (resource esquema://sql) e tente de novo."
    return ToolError(f"Erro do PostgreSQL: {msg}\nDica: {dica}")


def avisos_cypher(consulta):
    """Rótulos, relações e propriedades que não existem no grafo, e ids entre aspas: a consulta roda, mas volta vazia."""
    sem_texto = re.sub(r"'[^']*'|\"[^\"]*\"", "''", consulta)
    rotulos = set(re.findall(r"\(\s*\w*\s*:\s*`?(\w+)", sem_texto))
    relacoes = set(re.findall(r"\[\s*\w*\s*:\s*`?(\w+)", sem_texto))
    props = set(re.findall(r"\b[A-Za-z_]\w*\.([A-Za-z_]\w*)\b(?!\s*\()", sem_texto))
    props |= set(re.findall(r"[({,]\s*([A-Za-z_]\w*)\s*:\s*(?:''|\$|-?\d)", sem_texto))
    avisos = []
    if rotulos - ROTULOS_GRAFO:
        avisos.append(f"Rótulo inexistente: {', '.join(sorted(rotulos - ROTULOS_GRAFO))}. "
                      "Os rótulos são Host, Listing, Guest e Neighbourhood.")
    if relacoes - RELACOES_GRAFO:
        avisos.append(f"Relação inexistente: {', '.join(sorted(relacoes - RELACOES_GRAFO))}. "
                      "As relações são HOSTS, LOCATED_IN, REVIEWED e BORDERS.")
    if props - PROPRIEDADES_GRAFO:
        extra = sorted(props - PROPRIEDADES_GRAFO)
        texto = (f"Propriedade inexistente no grafo: {', '.join(extra)}. "
                 "Os nós só têm host_id, listing_id, guest_id e name.")
        if {"price", "date", "amenity", "review_scores_rating", "number_of_reviews", "room_type", "neighbourhood"} & set(extra):
            texto += " Preços, notas, datas, tipos, comodidades e contagens estão no SQL (consultar_sql)."
        avisos.append(texto)
    if re.search(r"_id\s*(?::|=|<>|IN)\s*\[?\s*''", sem_texto):
        avisos.append("Id entre aspas: no grafo os ids são inteiros. Escreva {listing_id: 123}, sem aspas.")
    return avisos


def id_valido(valor, nome):
    """Ids do Airbnb são inteiros positivos (até 19 dígitos)."""
    try:
        i = int(str(valor).strip())
    except (TypeError, ValueError):
        raise ToolError(f"{nome} inválido: '{valor}'. Um {nome} é um número inteiro, ex.: 1234567. "
                        "Para achar o id a partir do nome, use buscar_entidade.")
    if i <= 0 or i >= 2**63:
        raise ToolError(f"{nome} inválido: {valor}. Use o número inteiro devolvido por buscar_entidade.")
    return i


# ─── TOOLS ───

@mcp.tool
def consultar_sql(
    consulta: Annotated[str, Field(description="Um único comando SELECT (ou WITH ... SELECT) em PostgreSQL")],
) -> dict:
    """Executa uma consulta SQL de leitura no PostgreSQL: anúncios, preços, notas, comodidades, anfitriões, bairros,
    avaliações e hóspedes. Use para filtros, contagens, médias, rankings e junções. Devolve no máximo 50 linhas,
    com o total de linhas e se o resultado foi truncado. O esquema está no resource esquema://sql."""
    q = (consulta or "").strip().rstrip(";").strip()
    if not q:
        raise ToolError("A consulta está vazia. Envie um comando SELECT.")
    if ";" in re.sub(r"'[^']*'", "''", q):
        raise ToolError("Envie um único comando SELECT, sem ';' separando comandos.")
    if not re.match(r"(?is)^\s*(select|with)\b", q):
        raise ToolError("Só consultas de leitura são permitidas: o comando deve começar com SELECT ou WITH. "
                        "O banco é somente leitura.")
    try:
        with pg().cursor() as cur:
            cur.execute(q)
            if cur.description is None:
                raise ToolError("A consulta não devolveu colunas. Envie um SELECT que devolva dados.")
            colunas = [c.name for c in cur.description]
            linhas = cur.fetchmany(MAX_LINHAS + 1)
            total = len(linhas)
            if total > MAX_LINHAS:
                cur.execute(f"SELECT count(*) FROM ({q}) AS consulta_original")
                total = cur.fetchone()[0]
    except psycopg.Error as e:
        raise erro_sql(e)
    return resultado([jsonavel(list(l)) for l in linhas[:MAX_LINHAS]], total, colunas)


@mcp.tool
def consultar_grafo(
    consulta: Annotated[str, Field(description="Uma consulta Cypher de leitura (MATCH ... RETURN ...)")],
) -> dict:
    """Executa uma consulta Cypher de leitura no Neo4j: anfitriões e seus anúncios (HOSTS), o bairro de cada anúncio
    (LOCATED_IN), hóspedes que avaliaram cada anúncio (REVIEWED) e bairros vizinhos (BORDERS). Use para vizinhos,
    hóspedes em comum, caminhos e conexões. O grafo não tem preços nem notas. Devolve no máximo 50 linhas, com o
    total e se o resultado foi truncado. O esquema está no resource esquema://grafo."""
    q = (consulta or "").strip().rstrip(";").strip()
    if not q:
        raise ToolError("A consulta está vazia. Envie uma consulta Cypher MATCH ... RETURN.")
    sem_texto = re.sub(r"'[^']*'|\"[^\"]*\"", "''", q)
    escrita = re.search(r"(?i)\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|LOAD\s+CSV)\b", sem_texto)
    if escrita:
        raise ToolError(f"O grafo é somente leitura: '{escrita.group(1).upper()}' não é permitido. "
                        "Use MATCH ... RETURN.")
    try:
        with grafo().session(default_access_mode=READ_ACCESS) as s:
            linhas, total = [], 0
            for registro in s.run(Query(q, timeout=TIMEOUT_S)):
                total += 1
                if total <= MAX_LINHAS:
                    linhas.append(jsonavel(registro.data()))
    except ServiceUnavailable as e:
        raise ToolError(f"O Neo4j não está acessível ({e}). Isto é um problema do ambiente, não da consulta.")
    except Neo4jError as e:
        msg = (e.message or str(e)).strip().splitlines()[0]
        codigo = e.code or ""
        if "Syntax" in codigo:
            dica = "Erro de sintaxe Cypher. Confira parênteses, colchetes e o RETURN, e envie de novo."
        elif "AccessMode" in codigo or "Forbidden" in codigo:
            dica = "O grafo é somente leitura. Use MATCH ... RETURN."
        elif "Timeout" in codigo or "TimedOut" in codigo:
            dica = (f"A consulta passou de {TIMEOUT_S} s. Parta de um nó específico (por listing_id, host_id, guest_id "
                    "ou nome do bairro) e limite o tamanho dos caminhos.")
        else:
            dica = "Confira a consulta contra o esquema (resource esquema://grafo) e tente de novo."
        raise ToolError(f"Erro do Neo4j ({codigo}): {msg}\nDica: {dica}")
    r = resultado(linhas, total)
    avisos = avisos_cypher(q)
    if avisos:
        r["avisos_de_esquema"] = avisos
    return r


BUSCAS = {
    # tipo: (rótulo, consulta dos achados, contagem, nomes parecidos quando nada é encontrado)
    "anuncio": ("anúncio",
                "SELECT listing_id, name, neighbourhood, room_type, price, review_scores_rating, number_of_reviews "
                "FROM listing WHERE unaccent(name) ILIKE unaccent(%(p)s) "
                "ORDER BY lower(name) = lower(%(t)s) DESC, number_of_reviews DESC LIMIT 10",
                "SELECT count(*) AS n FROM listing WHERE unaccent(name) ILIKE unaccent(%(p)s)",
                "SELECT name FROM (SELECT DISTINCT name FROM listing WHERE similarity(name, %(t)s) > 0.3) n "
                "ORDER BY similarity(name, %(t)s) DESC LIMIT 5"),
    "anfitriao": ("anfitrião",
                  "SELECT h.host_id, h.name, h.is_superhost, count(l.listing_id) AS anuncios_em_sao_paulo "
                  "FROM host h JOIN listing l USING (host_id) WHERE unaccent(h.name) ILIKE unaccent(%(p)s) "
                  "GROUP BY h.host_id ORDER BY lower(h.name) = lower(%(t)s) DESC, count(l.listing_id) DESC LIMIT 10",
                  "SELECT count(*) AS n FROM host WHERE unaccent(name) ILIKE unaccent(%(p)s)",
                  "SELECT name FROM (SELECT DISTINCT name FROM host WHERE similarity(name, %(t)s) > 0.4) n "
                  "ORDER BY similarity(name, %(t)s) DESC LIMIT 5"),
    "hospede": ("hóspede",
                "SELECT g.guest_id, g.name, count(r.review_id) AS avaliacoes "
                "FROM guest g JOIN review r USING (guest_id) WHERE unaccent(g.name) ILIKE unaccent(%(p)s) "
                "GROUP BY g.guest_id ORDER BY lower(g.name) = lower(%(t)s) DESC, count(r.review_id) DESC LIMIT 10",
                "SELECT count(*) AS n FROM guest WHERE unaccent(name) ILIKE unaccent(%(p)s)",
                "SELECT name FROM (SELECT DISTINCT name FROM guest WHERE similarity(name, %(t)s) > 0.5) n "
                "ORDER BY similarity(name, %(t)s) DESC LIMIT 5"),
    "bairro": ("bairro",
               "SELECT n.name, n.group_name, count(l.listing_id) AS anuncios FROM neighbourhood n "
               "LEFT JOIN listing l ON l.neighbourhood = n.name WHERE unaccent(n.name) ILIKE unaccent(%(p)s) "
               "GROUP BY n.name ORDER BY lower(unaccent(n.name)) = lower(unaccent(%(t)s)) DESC, count(l.listing_id) DESC LIMIT 10",
               "SELECT count(*) AS n FROM neighbourhood WHERE unaccent(name) ILIKE unaccent(%(p)s)",
               "SELECT name FROM neighbourhood WHERE similarity(unaccent(name), unaccent(%(t)s)) > 0.3 "
               "ORDER BY similarity(unaccent(name), unaccent(%(t)s)) DESC LIMIT 5"),
    "comodidade": ("comodidade",
                   "SELECT amenity, count(*) AS anuncios FROM listing_amenity WHERE amenity ILIKE %(p)s "
                   "GROUP BY amenity ORDER BY lower(amenity) = lower(%(t)s) DESC, count(*) DESC LIMIT 10",
                   "SELECT count(DISTINCT amenity) AS n FROM listing_amenity WHERE amenity ILIKE %(p)s",
                   "SELECT amenity AS name FROM (SELECT DISTINCT amenity FROM listing_amenity) c "
                   "WHERE similarity(amenity, %(t)s) > 0.3 ORDER BY similarity(amenity, %(t)s) DESC LIMIT 5"),
}


@mcp.tool
def buscar_entidade(
    nome: Annotated[str, Field(description="O nome, ou parte dele, a procurar")],
    tipo: Annotated[Literal["anuncio", "anfitriao", "hospede", "bairro", "comodidade"], Field(description="O que procurar")],
) -> dict:
    """Acha anúncios, anfitriões, hóspedes, bairros ou comodidades pelo nome (sem diferenciar maiúsculas nem acentos)
    e devolve os ids. Use antes de detalhes_anuncio ou comparar_anuncios, e para conferir se algo existe.
    Mostra também quando há vários com o mesmo nome. Comodidades estão em inglês (ex.: 'Pool', 'Kitchen')."""
    termo = (nome or "").strip()
    if len(termo) < 2:
        raise ToolError("Informe pelo menos 2 caracteres do nome a procurar.")
    rotulo, achar, contar, parecidos = BUSCAS[tipo]
    params = {"p": "%" + termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", "t": termo}
    try:
        achados = sql_linhas(achar, params)
        total = sql_linhas(contar, params)[0]["n"]
    except psycopg.Error as e:
        raise erro_sql(e)

    r = {"tipo": tipo, "termo": termo, "encontrados": total, "resultados": achados}
    if total == 0:
        r["mensagem"] = f"Nenhum(a) {rotulo} com '{termo}' no nome nos dados de São Paulo."
        try:
            sugestoes = [x["name"] for x in sql_linhas(parecidos, params)]
        except psycopg.Error as e:
            raise erro_sql(e)
        if sugestoes:
            r["nomes_parecidos"] = sugestoes
        if tipo == "comodidade":
            r["mensagem"] += " As comodidades estão em inglês (ex.: 'Pool', 'Air conditioning', 'Kitchen')."
        return r
    if total > len(achados):
        r["aviso"] = f"{total} encontrados; só os {len(achados)} primeiros foram devolvidos. Refine o nome."
    chave = "amenity" if tipo == "comodidade" else "name"
    iguais = [a for a in achados if a[chave].lower() == termo.lower()]
    if tipo in ("anuncio", "anfitriao", "hospede") and len(iguais) > 1:
        r["homonimos"] = (f"Há {len(iguais)} {rotulo}s chamados exatamente '{termo}'"
                          + (" (talvez mais, além dos 10 devolvidos)" if total > len(achados) else "")
                          + ". Use o id para escolher; os outros campos ajudam a diferenciar.")
    return r


@mcp.tool
def detalhes_anuncio(
    # int | str: um texto errado chega até aqui e recebe a dica em português, em vez do erro de validação em inglês
    listing_id: Annotated[int | str, Field(description="O listing_id, um número inteiro")],
) -> dict:
    """Todos os dados de um anúncio: bairro, tipo, capacidade, preço, notas, ocupação e receita estimadas,
    anfitrião, comodidades e as avaliações presentes no banco (de 2025 em diante: quantidade, primeira e última).
    Para achar o listing_id a partir do nome, use buscar_entidade."""
    lid = id_valido(listing_id, "listing_id")
    try:
        linhas = sql_linhas("SELECT * FROM listing WHERE listing_id = %s", (lid,))
        if not linhas:
            raise ToolError(f"Nenhum anúncio com listing_id {lid}. Confira o id com buscar_entidade.")
        anuncio = linhas[0]
        anuncio["anfitriao"] = sql_linhas(
            "SELECT h.host_id, h.name, h.is_superhost, h.listings_count AS anuncios_no_airbnb_inteiro, "
            "(SELECT count(*) FROM listing WHERE host_id = h.host_id) AS anuncios_em_sao_paulo "
            "FROM host h WHERE h.host_id = %s", (anuncio["host_id"],))[0]
        anuncio["comodidades"] = [x["amenity"] for x in sql_linhas(
            "SELECT amenity FROM listing_amenity WHERE listing_id = %s ORDER BY amenity", (lid,))]
        anuncio["avaliacoes_no_banco"] = sql_linhas(
            "SELECT count(*) AS quantidade, min(date) AS primeira, max(date) AS ultima "
            "FROM review WHERE listing_id = %s", (lid,))[0]
    except psycopg.Error as e:
        raise erro_sql(e)
    anuncio["observacao"] = ("number_of_reviews conta a vida toda do anúncio; avaliacoes_no_banco conta só as de 2025 "
                             "em diante. price nulo quer dizer preço não informado.")
    return anuncio


@mcp.tool
def comparar_anuncios(
    listing_ids: Annotated[list[int | str], Field(description="De 2 a 5 listing_id (números inteiros)")],
) -> dict:
    """Compara de 2 a 5 anúncios lado a lado e calcula o vencedor de cada critério: maior nota, mais avaliações,
    mais barato, maior capacidade, maior ocupação estimada e maior receita estimada. Os cálculos são feitos aqui,
    não pelo modelo; empates aparecem com mais de um id."""
    ids = list(dict.fromkeys(id_valido(i, "listing_id") for i in listing_ids or []))
    if not 2 <= len(ids) <= 5:
        raise ToolError(f"Envie de 2 a 5 listing_id diferentes; recebi {len(ids)}.")
    try:
        anuncios = sql_linhas(
            "SELECT l.listing_id, l.name, l.neighbourhood, l.room_type, l.accommodates, l.price, l.review_scores_rating, "
            "l.number_of_reviews, l.estimated_occupancy_l365d, l.estimated_revenue_l365d, "
            "(SELECT count(*) FROM review r WHERE r.listing_id = l.listing_id) AS avaliacoes_no_banco "
            "FROM listing l WHERE l.listing_id = ANY(%s) ORDER BY l.listing_id", (ids,))
    except psycopg.Error as e:
        raise erro_sql(e)
    achados = {a["listing_id"] for a in anuncios}
    ausentes = [i for i in ids if i not in achados]
    if len(anuncios) < 2:
        raise ToolError(f"Só {len(anuncios)} dos ids existe(m); não há o que comparar. Ids inexistentes: {ausentes}. "
                        "Confira com buscar_entidade.")

    def vencedores(coluna, maior=True):
        valores = [a[coluna] for a in anuncios if a[coluna] is not None]
        if not valores:
            return {"valor": None, "listing_ids": [], "observacao": "nenhum dos anúncios tem esse dado"}
        alvo = max(valores) if maior else min(valores)
        return {"valor": alvo, "listing_ids": [a["listing_id"] for a in anuncios if a[coluna] == alvo]}

    return {
        "anuncios": anuncios,
        "melhor": {
            "maior_nota": vencedores("review_scores_rating"),
            "mais_avaliacoes": vencedores("number_of_reviews"),
            "mais_barato": vencedores("price", maior=False),
            "maior_capacidade": vencedores("accommodates"),
            "maior_ocupacao": vencedores("estimated_occupancy_l365d"),
            "maior_receita": vencedores("estimated_revenue_l365d"),
        },
        "ausentes": ausentes,
        "observacao": "Em 'melhor', mais de um id quer dizer empate. Critérios ignoram anúncios sem o dado "
                      "(preço ou nota nulos).",
    }


# ─── RESOURCES ───

@mcp.resource("esquema://sql", mime_type="text/plain")
def esquema_sql() -> str:
    """Tabelas, colunas e cuidados do banco PostgreSQL."""
    return ESQUEMA_SQL


@mcp.resource("esquema://grafo", mime_type="text/plain")
def esquema_grafo() -> str:
    """Nós, relações e cuidados do banco Neo4j."""
    return ESQUEMA_GRAFO


# ─── PROMPT ───

@mcp.prompt
def analisar_bairro(nome: str) -> str:
    """Pede uma análise de um bairro de São Paulo: oferta, preços, notas e comparação com os bairros vizinhos."""
    return (f"Analise o bairro '{nome}' de São Paulo. Confirme o nome com buscar_entidade (os nomes estão sem acento), "
            "mostre quantos anúncios ele tem, o preço mediano por noite, a nota média e o tipo de acomodação mais comum, "
            "e compare esses números com os dos bairros vizinhos (relação BORDERS no grafo).")


if __name__ == "__main__":
    mcp.run()
