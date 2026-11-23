"""Servidor MCP do projeto: dados do Yelp de Santa Barbara em dois bancos.

- PostgreSQL: atributos, números e categorias (negócios, avaliações, usuários).
- Neo4j: relações (amizades entre usuários e quem avaliou qual negócio).

Ferramentas (o modelo decide quando chamar):
    consultar_sql       SELECT somente leitura no PostgreSQL
    consultar_grafo     consulta Cypher somente leitura no Neo4j
    buscar_entidade     acha negócio, usuário ou categoria pelo nome (resolve homônimos e inexistentes)
    detalhes_negocio    todos os dados de um negócio
    comparar_negocios   compara negócios e calcula o melhor em cada critério   <- ferramenta de cálculo
Resources (a aplicação lê):  esquema://sql, esquema://grafo
Prompt (o usuário escolhe):  analisar_negocio

Toda chamada errada devolve um erro de execução (isError) com uma mensagem que diz o que corrigir,
para o modelo tentar de novo dentro do loop. Resultado vazio não é erro.

Configuração por variáveis de ambiente: YELP_PG_DSN, YELP_NEO4J_URI, YELP_NEO4J_USUARIO, YELP_NEO4J_SENHA.
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
PG_DSN = os.environ.get("YELP_PG_DSN", "host=localhost port=5432 dbname=yelp user=leitor password=leitor")
NEO4J_URI = os.environ.get("YELP_NEO4J_URI", "bolt://localhost:7687")
NEO4J_AUTH = (os.environ.get("YELP_NEO4J_USUARIO", "neo4j"), os.environ.get("YELP_NEO4J_SENHA", "yelp-santa-barbara"))

ID_YELP = re.compile(r"^[A-Za-z0-9_-]{22}$")
TABELAS_SQL = {
    "business": "business_id, name, address, postal_code, latitude, longitude, stars, review_count, is_open, price_range, checkin_count",
    "business_category": "business_id, category",
    "review": "review_id, user_id, business_id, stars, date, useful, funny, cool",
    "users": "user_id, name, review_count, yelping_since, useful, funny, cool, fans, average_stars",
}
ROTULOS_GRAFO = {"User", "Business"}
RELACOES_GRAFO = {"FRIEND", "REVIEWED"}
PROPRIEDADES_GRAFO = {"user_id", "business_id", "name"}

ESQUEMA_SQL = """BANCO RELACIONAL (PostgreSQL) — ferramenta consultar_sql. Cidade única: Santa Barbara, CA.

business(business_id, name, address, postal_code, latitude, longitude, stars, review_count, is_open, price_range, checkin_count)
  stars: nota média do negócio no Yelp, de 1.0 a 5.0 (passos de 0.5)
  review_count: número de avaliações do negócio no Yelp inteiro
  is_open: 1 aberto, 0 fechado
  price_range: faixa de preço de 1 (barato) a 4 (caro); NULO em cerca de metade dos negócios
  checkin_count: número de check-ins registrados
  Há negócios com o mesmo nome (redes); o business_id os distingue.

business_category(business_id, category)
  Um negócio tem várias categorias. Valores em inglês: 'Restaurants', 'Mexican', 'Coffee & Tea', 'Bars'...
  Juntar com business pode repetir o negócio quando o filtro casa mais de uma categoria: use COUNT(DISTINCT ...).

review(review_id, user_id, business_id, stars, date, useful, funny, cool)
  Uma linha por avaliação feita em Santa Barbara. stars: nota da avaliação, inteiro de 1 a 5.
  O mesmo usuário pode avaliar o mesmo negócio mais de uma vez.

users(user_id, name, review_count, yelping_since, useful, funny, cool, fans, average_stars)
  name: só o primeiro nome; há muitos homônimos.
  ATENÇÃO: review_count e average_stars contam o Yelp INTEIRO, não só Santa Barbara.
  Para avaliações em Santa Barbara, conte ou faça a média na tabela review.

Chaves compartilhadas com o grafo: user_id e business_id.
Amizades NÃO estão no SQL: estão no grafo."""

ESQUEMA_GRAFO = """BANCO DE GRAFO (Neo4j) — ferramenta consultar_grafo. Cidade única: Santa Barbara, CA.

Nós:
  (:User {user_id, name})
  (:Business {business_id, name})
Relações:
  (:User)-[:FRIEND]-(:User)          amizade; consulte SEM direção: (a)-[:FRIEND]-(b)
  (:User)-[:REVIEWED]->(:Business)   o usuário avaliou o negócio (uma relação por par, sem propriedades)

O grafo NÃO tem notas, datas, categorias nem contagens: isso está no SQL.
Para contar vizinhos, conte o nó do outro lado:
  MATCH (u:User {user_id: '...'})-[:FRIEND]-(f:User) RETURN count(DISTINCT f)
Para contar, use count() na própria consulta: as listas devolvidas são cortadas em 50 linhas.
Chaves compartilhadas com o SQL: user_id e business_id. Para uma pergunta que precisa dos dois bancos,
consulte o grafo, pegue os ids e use-os no SQL (ou o contrário)."""

mcp = FastMCP("yelp-santa-barbara", instructions=(
    "Dados do Yelp de Santa Barbara, CA, em dois bancos: PostgreSQL (atributos, notas, categorias) e Neo4j "
    "(amizades e quem avaliou o quê). Leia esquema://sql e esquema://grafo antes de escrever consultas."))


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
        dica = f"Tabela inexistente. As tabelas são: {tabelas}. A de usuários se chama users, no plural."
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
        dica = "Função ou operador inexistente para esses tipos. Confira os tipos das colunas (ex.: stars é número)."
    else:
        dica = "Confira a consulta contra o esquema (resource esquema://sql) e tente de novo."
    return ToolError(f"Erro do PostgreSQL: {msg}\nDica: {dica}")


def avisos_cypher(consulta):
    """Rótulos, relações e propriedades que não existem no grafo: a consulta roda, mas volta vazia."""
    sem_texto = re.sub(r"'[^']*'|\"[^\"]*\"", "''", consulta)
    rotulos = set(re.findall(r"\(\s*\w*\s*:\s*`?(\w+)", sem_texto))
    relacoes = set(re.findall(r"\[\s*\w*\s*:\s*`?(\w+)", sem_texto))
    props = set(re.findall(r"\b[A-Za-z_]\w*\.([A-Za-z_]\w*)\b(?!\s*\()", sem_texto))
    props |= set(re.findall(r"[({,]\s*([A-Za-z_]\w*)\s*:\s*(?:''|\$|-?\d)", sem_texto))
    avisos = []
    if rotulos - ROTULOS_GRAFO:
        avisos.append(f"Rótulo inexistente: {', '.join(sorted(rotulos - ROTULOS_GRAFO))}. Os rótulos são User e Business.")
    if relacoes - RELACOES_GRAFO:
        avisos.append(f"Relação inexistente: {', '.join(sorted(relacoes - RELACOES_GRAFO))}. As relações são FRIEND e REVIEWED.")
    if props - PROPRIEDADES_GRAFO:
        extra = sorted(props - PROPRIEDADES_GRAFO)
        texto = f"Propriedade inexistente no grafo: {', '.join(extra)}. Os nós só têm user_id, business_id e name."
        if {"stars", "date", "category", "review_count"} & set(extra):
            texto += " Notas, datas, categorias e contagens estão no SQL (consultar_sql)."
        avisos.append(texto)
    return avisos


# ─── TOOLS ───

@mcp.tool
def consultar_sql(
    consulta: Annotated[str, Field(description="Um único comando SELECT (ou WITH ... SELECT) em PostgreSQL")],
) -> dict:
    """Executa uma consulta SQL de leitura no PostgreSQL: negócios, notas, categorias, avaliações e usuários.
    Use para filtros, contagens, médias, rankings e junções. Devolve no máximo 50 linhas, com o total
    de linhas e se o resultado foi truncado. O esquema está no resource esquema://sql."""
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
    """Executa uma consulta Cypher de leitura no Neo4j: amizades entre usuários (FRIEND) e quem avaliou
    qual negócio (REVIEWED). Use para vizinhos, amigos de amigos, caminhos e conexões. O grafo não tem
    notas nem categorias. Devolve no máximo 50 linhas, com o total e se o resultado foi truncado.
    O esquema está no resource esquema://grafo."""
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
            dica = (f"A consulta passou de {TIMEOUT_S} s. Parta de um nó específico (por user_id ou business_id) "
                    "e limite o tamanho dos caminhos.")
        else:
            dica = "Confira a consulta contra o esquema (resource esquema://grafo) e tente de novo."
        raise ToolError(f"Erro do Neo4j ({codigo}): {msg}\nDica: {dica}")
    r = resultado(linhas, total)
    avisos = avisos_cypher(q)
    if avisos:
        r["avisos_de_esquema"] = avisos
    return r


@mcp.tool
def buscar_entidade(
    nome: Annotated[str, Field(description="O nome, ou parte dele, a procurar")],
    tipo: Annotated[Literal["negocio", "usuario", "categoria"], Field(description="O que procurar")],
) -> dict:
    """Acha negócios, usuários ou categorias pelo nome (sem diferenciar maiúsculas) e devolve os ids.
    Use antes de detalhes_negocio ou comparar_negocios, e para conferir se algo existe.
    Mostra também quando há vários com o mesmo nome. Categorias estão em inglês (ex.: 'Mexican')."""
    termo = (nome or "").strip()
    if len(termo) < 2:
        raise ToolError("Informe pelo menos 2 caracteres do nome a procurar.")
    padrao = "%" + termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    try:
        if tipo == "negocio":
            achados = sql_linhas(
                "SELECT business_id, name, address, stars, review_count, is_open FROM business WHERE name ILIKE %s "
                "ORDER BY lower(name) = lower(%s) DESC, review_count DESC LIMIT 10", (padrao, termo))
            total = sql_linhas("SELECT count(*) AS n FROM business WHERE name ILIKE %s", (padrao,))[0]["n"]
            parecidos = ("SELECT name FROM (SELECT DISTINCT name FROM business WHERE similarity(name, %s) > 0.3) n "
                         "ORDER BY similarity(name, %s) DESC LIMIT 5")
        elif tipo == "usuario":
            achados = sql_linhas(
                "SELECT user_id, name, review_count, fans, yelping_since FROM users WHERE name ILIKE %s "
                "ORDER BY lower(name) = lower(%s) DESC, review_count DESC LIMIT 10", (padrao, termo))
            total = sql_linhas("SELECT count(*) AS n FROM users WHERE name ILIKE %s", (padrao,))[0]["n"]
            parecidos = ("SELECT name FROM (SELECT DISTINCT name FROM users WHERE similarity(name, %s) > 0.4) n "
                         "ORDER BY similarity(name, %s) DESC LIMIT 5")
        else:
            achados = sql_linhas(
                "SELECT category, count(*) AS negocios FROM business_category WHERE category ILIKE %s "
                "GROUP BY category ORDER BY lower(category) = lower(%s) DESC, count(*) DESC LIMIT 10", (padrao, termo))
            total = sql_linhas("SELECT count(DISTINCT category) AS n FROM business_category WHERE category ILIKE %s",
                               (padrao,))[0]["n"]
            parecidos = ("SELECT category AS name FROM (SELECT DISTINCT category FROM business_category) c "
                         "WHERE similarity(category, %s) > 0.3 ORDER BY similarity(category, %s) DESC LIMIT 5")
    except psycopg.Error as e:
        raise erro_sql(e)

    rotulo = {"negocio": "negócio", "usuario": "usuário", "categoria": "categoria"}[tipo]
    r = {"tipo": tipo, "termo": termo, "encontrados": total, "resultados": achados}
    if total == 0:
        r["mensagem"] = f"Nenhum(a) {rotulo} com '{termo}' no nome em Santa Barbara."
        try:
            sugestoes = [x["name"] for x in sql_linhas(parecidos, (termo, termo))]
        except psycopg.Error as e:
            raise erro_sql(e)
        if sugestoes:
            r["nomes_parecidos"] = sugestoes
        if tipo == "categoria":
            r["mensagem"] += " As categorias estão em inglês (ex.: 'Mexican', 'Coffee & Tea')."
        return r
    if total > len(achados):
        r["aviso"] = f"{total} encontrados; só os {len(achados)} primeiros foram devolvidos. Refine o nome."
    chave = "category" if tipo == "categoria" else "name"
    iguais = [a for a in achados if a[chave].lower() == termo.lower()]
    if tipo != "categoria" and len(iguais) > 1:
        r["homonimos"] = (f"Há {len(iguais)} {rotulo}s chamados exatamente '{termo}'. "
                          "Use o id para escolher; endereço e números ajudam a diferenciar.")
    return r


@mcp.tool
def detalhes_negocio(
    business_id: Annotated[str, Field(description="O business_id, com 22 caracteres")],
) -> dict:
    """Todos os dados de um negócio: endereço, nota, faixa de preço, se está aberto, check-ins,
    categorias, e as avaliações presentes no banco (quantidade, média, primeira e última).
    Para achar o business_id a partir do nome, use buscar_entidade."""
    bid = (business_id or "").strip()
    if not ID_YELP.match(bid):
        raise ToolError(f"business_id inválido: '{bid}'. Um business_id tem 22 caracteres (letras, números, '-' e '_'). "
                        "Para achar o id a partir do nome, use buscar_entidade com tipo 'negocio'.")
    try:
        linhas = sql_linhas("SELECT * FROM business WHERE business_id = %s", (bid,))
        if not linhas:
            raise ToolError(f"Nenhum negócio com business_id '{bid}'. Confira o id com buscar_entidade.")
        negocio = linhas[0]
        negocio["categorias"] = [x["category"] for x in sql_linhas(
            "SELECT category FROM business_category WHERE business_id = %s ORDER BY category", (bid,))]
        negocio["avaliacoes_no_banco"] = sql_linhas(
            "SELECT count(*) AS quantidade, round(avg(stars), 2) AS media_das_notas, "
            "min(date)::date AS primeira, max(date)::date AS ultima FROM review WHERE business_id = %s", (bid,))[0]
    except psycopg.Error as e:
        raise erro_sql(e)
    negocio["observacao"] = ("stars e review_count vêm do Yelp; avaliacoes_no_banco conta as avaliações presentes "
                             "na tabela review. price_range nulo quer dizer faixa de preço desconhecida.")
    return negocio


@mcp.tool
def comparar_negocios(
    business_ids: Annotated[list[str], Field(description="De 2 a 5 business_id")],
) -> dict:
    """Compara de 2 a 5 negócios lado a lado e calcula o vencedor de cada critério: maior nota, mais
    avaliações, mais check-ins, mais barato e maior média das avaliações no banco. Os cálculos são
    feitos aqui, não pelo modelo; empates aparecem com mais de um id."""
    ids = list(dict.fromkeys(i.strip() for i in business_ids or []))
    if not 2 <= len(ids) <= 5:
        raise ToolError(f"Envie de 2 a 5 business_id diferentes; recebi {len(ids)}.")
    invalidos = [i for i in ids if not ID_YELP.match(i)]
    if invalidos:
        raise ToolError(f"business_id inválido: {invalidos}. Cada id tem 22 caracteres; use buscar_entidade para achar.")
    try:
        negocios = sql_linhas(
            "SELECT b.business_id, b.name, b.address, b.stars, b.review_count, b.checkin_count, b.price_range, b.is_open, "
            "round(avg(r.stars), 2) AS media_no_banco, count(r.review_id) AS avaliacoes_no_banco "
            "FROM business b LEFT JOIN review r USING (business_id) WHERE b.business_id = ANY(%s) "
            "GROUP BY b.business_id ORDER BY b.name", (ids,))
    except psycopg.Error as e:
        raise erro_sql(e)
    achados = {n["business_id"] for n in negocios}
    ausentes = [i for i in ids if i not in achados]
    if len(negocios) < 2:
        raise ToolError(f"Só {len(negocios)} dos ids existe(m); não há o que comparar. Ids inexistentes: {ausentes}. "
                        "Confira com buscar_entidade.")

    def vencedores(coluna, maior=True):
        valores = [n[coluna] for n in negocios if n[coluna] is not None]
        if not valores:
            return {"valor": None, "business_ids": [], "observacao": "nenhum dos negócios tem esse dado"}
        alvo = max(valores) if maior else min(valores)
        return {"valor": alvo, "business_ids": [n["business_id"] for n in negocios if n[coluna] == alvo]}

    return {
        "negocios": negocios,
        "melhor": {
            "maior_nota": vencedores("stars"),
            "mais_avaliacoes": vencedores("review_count"),
            "mais_checkins": vencedores("checkin_count"),
            "mais_barato": vencedores("price_range", maior=False),
            "maior_media_no_banco": vencedores("media_no_banco"),
        },
        "ausentes": ausentes,
        "observacao": "Em 'melhor', mais de um id quer dizer empate. 'mais_barato' ignora negócios sem faixa de preço.",
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
def analisar_negocio(nome: str) -> str:
    """Pede uma análise de um negócio pelo nome: dados, categorias e comparação com concorrentes."""
    return (f"Analise o negócio '{nome}' em Santa Barbara. Encontre-o pelo nome (se houver mais de um, diga quais), "
            "mostre nota, faixa de preço, categorias e avaliações, e compare-o com dois concorrentes da mesma "
            "categoria principal. Cite o business_id de cada negócio mencionado.")


if __name__ == "__main__":
    mcp.run()
