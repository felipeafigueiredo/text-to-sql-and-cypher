"""As 20 perguntas da avaliação, cada uma com o gabarito calculado dos bancos.

Cada pergunta registra:
- categoria: filtro, item, comparação, cálculo, capciosa, inexistente ou fora do escopo
- banco: onde está a resposta (sql, cypher, ambos ou nenhum)
- ferramentas: a trajetória esperada. Cada item é uma expressão regular, e alguma ferramenta chamada
  precisa casar com cada item. "detalhes_negocio|consultar_sql" aceita qualquer uma das duas.
  Lista vazia: o certo é não chamar ferramenta nenhuma.
- gabarito: função que recebe os bancos (avaliacao.bancos.Bancos) e devolve o texto de referência
  para o juiz, as expressões que a resposta deve conter e as que não pode conter.

O gabarito é calculado na hora, a partir dos bancos carregados: assim o repositório não guarda
dados do Yelp, e as consultas de referência ficam à vista, ao lado de cada pergunta.
Nenhuma destas perguntas aparece como exemplo nos prompts dos agentes.
"""
import re
from decimal import Decimal

PERGUNTAS = []

# Usuários citados por id: no Yelp, o nome do usuário é só o primeiro nome e há muitos homônimos.
# O Marklin é o único com esse nome no recorte, então as perguntas sobre ele usam o nome.
MARKLIN = "8G_G3864kEDCNrDQQHIpkA"
JEMA = "5FgWswuxsksmQnUFP1OtVg"
ROSIE = "LM1Aleq_ozhEslRd2GUGEQ"


def pergunta(id, texto, categoria, banco, ferramentas):
    """Registra uma pergunta; a função decorada calcula o gabarito."""
    def registrar(gabarito):
        PERGUNTAS.append(dict(id=id, pergunta=texto, categoria=categoria, banco=banco,
                              ferramentas=ferramentas, gabarito=gabarito))
        return gabarito
    return registrar


def gab(texto, deve=(), proibido=()):
    return dict(texto=texto, deve=list(deve), proibido=list(proibido))


def num(x):
    """Expressão regular que casa um número escrito em português ou em inglês.
    29897 casa com 29897, 29.897, 29,897 e 29 897. 4.42 casa com 4,42, 4.42 e 4,4 (arredondado).
    4.0 casa com 4, 4,0 e 4.0."""
    if isinstance(x, (Decimal, float)):
        inteiro, frac = f"{x}".split(".") if "." in f"{x}" else (f"{x}", "0")
        if set(frac) == {"0"}:
            return rf"(?<![\d.,]){inteiro}(?:[,.]0+)?(?![\d]|[,.]\d)"
        uma = f"{Decimal(x).quantize(Decimal('0.1'))}".split(".")
        return rf"(?<![\d.,])(?:{inteiro}[,.]{frac}|{uma[0]}[,.]{uma[1]}(?!\d))"
    d = str(x)
    if len(d) > 3:
        return rf"(?<![\d.,]){d[:-3]}[.,\s]?{d[-3:]}(?![\d])"
    return rf"(?<![\d.,]){d}(?![\d]|[,.]\d)"


# A resposta reconhece um empate: "empatam", "empate", "a mesma nota", "notas iguais", "todos têm 4.5"...
EMPATE = r"empat|mesma nota|nota igual|notas iguais|todos (?:os três )?(?:têm|tem)|os três (?:têm|tem)"


def chave(nome):
    """Uma palavra marcante de um nome, para conferir se a resposta o cita (ignora apóstrofo e caixa)."""
    palavras = [p for p in re.findall(r"[A-Za-z]+", nome) if len(p) >= 4 and p.lower() not in ("restaurant", "santa", "barbara")]
    return re.escape(palavras[0] if palavras else nome)


def negocio(db, nome):
    """O business_id de um negócio pelo nome exato; o nome tem de ser único no recorte."""
    linhas = db.sql("SELECT business_id FROM business WHERE name = %(n)s", n=nome)
    assert len(linhas) == 1, f"'{nome}' não é único no recorte ({len(linhas)} negócios)"
    return linhas[0]["business_id"]


def amigos(db, user_id):
    return [r["id"] for r in db.cypher(
        "MATCH (:User {user_id: $u})-[:FRIEND]-(f:User) RETURN DISTINCT f.user_id AS id", u=user_id)]


# ─── SQL ─────────────────────────────────────────────────────────────────────

@pergunta("P01", "Quantos negócios de comida mexicana estão abertos em Santa Barbara?",
          "filtro", "sql", ["consultar_sql"])
def _(db):
    n = db.valor("""SELECT count(DISTINCT b.business_id) FROM business b JOIN business_category c USING (business_id)
                    WHERE c.category = 'Mexican' AND b.is_open = 1""")
    return gab(f"{n} negócios abertos na categoria 'Mexican'.", deve=[num(n)])


@pergunta("P02", "Quais restaurantes abertos da faixa de preço mais cara (4) têm nota 4,5 ou mais?",
          "filtro", "sql", ["consultar_sql"])
def _(db):
    r = db.sql("""SELECT b.name, b.stars FROM business b JOIN business_category c USING (business_id)
                  WHERE c.category = 'Restaurants' AND b.price_range = 4 AND b.is_open = 1 AND b.stars >= 4.5
                  ORDER BY b.name""")
    return gab(f"{len(r)} restaurantes: " + "; ".join(f"{x['name']} ({x['stars']})" for x in r) + ".",
               deve=[chave(x["name"]) for x in r])


@pergunta("P03", "Qual é o endereço, a nota e a faixa de preço da La Super-Rica Taqueria?",
          "item", "sql", ["detalhes_negocio|consultar_sql"])
def _(db):
    x = db.sql("SELECT address, stars, price_range FROM business WHERE business_id = %(b)s",
               b=negocio(db, "La Super-Rica Taqueria"))[0]
    return gab(f"Endereço {x['address']}, nota {x['stars']}, faixa de preço {x['price_range']} (de 1 a 4).",
               deve=[re.escape(x["address"].split()[0]), num(x["stars"])])


@pergunta("P04", "Quantas avaliações o Lure Fish House recebeu em 2019, e qual foi a nota média delas?",
          "item", "sql", ["consultar_sql"])
def _(db):
    x = db.sql("""SELECT count(*) AS n, round(avg(stars), 2) AS media FROM review
                  WHERE business_id = %(b)s AND date >= '2019-01-01' AND date < '2020-01-01'""",
               b=negocio(db, "Lure Fish House"))[0]
    return gab(f"{x['n']} avaliações em 2019, com nota média {x['media']}.", deve=[num(x["n"]), num(x["media"])])


@pergunta("P05", "Entre Lure Fish House, Santo Mezcal e Finney's Crafthouse, qual tem a melhor nota e qual tem mais check-ins?",
          "comparação", "sql", ["comparar_negocios|consultar_sql"])
def _(db):
    ids = [negocio(db, n) for n in ("Lure Fish House", "Santo Mezcal", "Finney's Crafthouse")]
    r = db.sql("SELECT name, stars, checkin_count FROM business WHERE business_id = ANY(%(ids)s) ORDER BY name", ids=ids)
    nota = max(x["stars"] for x in r)
    melhores = [x["name"] for x in r if x["stars"] == nota]
    mais = max(r, key=lambda x: x["checkin_count"])
    empate = (f"Os três empatam na nota, {nota}" if len(melhores) == 3 else f"Melhor nota: {', '.join(melhores)} ({nota})")
    # Com empate, a resposta precisa dizer que empataram: apontar um "melhor" entre notas iguais está errado.
    deve_nota = [EMPATE] if len(melhores) > 1 else [chave(melhores[0])]
    return gab(f"{empate}. Mais check-ins: {mais['name']} ({mais['checkin_count']}). "
               + "; ".join(f"{x['name']}: nota {x['stars']}, {x['checkin_count']} check-ins" for x in r) + ".",
               deve=deve_nota + [chave(mais["name"]), num(mais["checkin_count"])])


@pergunta("P06", "Os restaurantes italianos ou os japoneses têm a nota média maior em Santa Barbara?",
          "comparação", "sql", ["consultar_sql"])
def _(db):
    r = {x["category"]: x for x in db.sql(
        """SELECT c.category, count(*) AS n, round(avg(b.stars), 2) AS media
           FROM business b JOIN business_category c USING (business_id)
           WHERE c.category IN ('Italian', 'Japanese') GROUP BY c.category""")}
    it, jp = r["Italian"], r["Japanese"]
    abertos = {x["category"]: x["media"] for x in db.sql(
        """SELECT c.category, round(avg(b.stars), 2) AS media FROM business b JOIN business_category c USING (business_id)
           WHERE c.category IN ('Italian', 'Japanese') AND b.is_open = 1 GROUP BY c.category""")}
    vencedor = "italianos" if it["media"] > jp["media"] else "japoneses"
    return gab(f"Os {vencedor}: média {it['media']} nos {it['n']} negócios 'Italian' contra {jp['media']} nos {jp['n']} 'Japanese'. "
               f"Só entre os abertos: {abertos['Italian']} contra {abertos['Japanese']}, mesma ordem. "
               "Filtrar também pela categoria 'Restaurants' não muda os números.",
               deve=[vencedor[:5]])


@pergunta("P07", "Em que ano foram feitas mais avaliações de negócios de Santa Barbara, e quantas foram?",
          "cálculo", "sql", ["consultar_sql"])
def _(db):
    x = db.sql("""SELECT extract(year FROM date)::int AS ano, count(*) AS n FROM review
                  GROUP BY 1 ORDER BY 2 DESC LIMIT 2""")
    return gab(f"{x[0]['ano']}, com {x[0]['n']} avaliações (o segundo é {x[1]['ano']}, com {x[1]['n']}).",
               deve=[str(x[0]["ano"]), num(x[0]["n"])])


@pergunta("P08", "Quais são as 5 categorias com mais negócios abertos, e quantos negócios abertos cada uma tem?",
          "cálculo", "sql", ["consultar_sql"])
def _(db):
    r = db.sql("""SELECT c.category, count(*) AS n FROM business b JOIN business_category c USING (business_id)
                  WHERE b.is_open = 1 GROUP BY 1 ORDER BY 2 DESC LIMIT 6""")
    return gab("; ".join(f"{x['category']}: {x['n']}" for x in r[:5]) + f". A sexta é {r[5]['category']}, com {r[5]['n']}.",
               deve=[re.escape(x["category"]) for x in r[:5]] + [num(x["n"]) for x in r[:5]])


# ─── Cypher ──────────────────────────────────────────────────────────────────

@pergunta("P09", "Quais amigos do Marklin avaliaram o Santo Mezcal?",
          "filtro", "cypher", ["consultar_grafo"])
def _(db):
    r = db.cypher("""MATCH (:User {user_id: $u})-[:FRIEND]-(f:User)-[:REVIEWED]->(:Business {business_id: $b})
                     RETURN DISTINCT f.name AS nome, f.user_id AS id ORDER BY nome""",
                  u=MARKLIN, b=negocio(db, "Santo Mezcal"))
    return gab(f"{len(r)} amigos: " + ", ".join(f"{x['nome']} ({x['id']})" for x in r) + ".",
               deve=[re.escape(x["nome"]) for x in r])


@pergunta("P10", "Quantos usuários avaliaram tanto o Lure Fish House quanto o Santo Mezcal?",
          "filtro", "cypher", ["consultar_grafo"])
def _(db):
    n = db.valor("""MATCH (:Business {business_id: $a})<-[:REVIEWED]-(u:User)-[:REVIEWED]->(:Business {business_id: $b})
                    RETURN count(DISTINCT u)""", a=negocio(db, "Lure Fish House"), b=negocio(db, "Santo Mezcal"))
    return gab(f"{n} usuários avaliaram os dois.", deve=[num(n)])


@pergunta("P11", "Quantos amigos o Marklin tem?",
          "item", "cypher", ["consultar_grafo"])
def _(db):
    n = len(amigos(db, MARKLIN))
    return gab(f"{n} amigos (amizades entre usuários do recorte de Santa Barbara).", deve=[num(n)])


@pergunta("P12", f"Quem tem mais amigos: o Marklin ou a usuária de user_id {JEMA}?",
          "comparação", "cypher", ["consultar_grafo"])
def _(db):
    m, j = len(amigos(db, MARKLIN)), len(amigos(db, JEMA))
    nome_j = db.valor("SELECT name FROM users WHERE user_id = %(u)s", u=JEMA)
    vencedor = "Marklin" if m > j else nome_j
    return gab(f"{vencedor}: Marklin tem {m} amigos e {nome_j} ({JEMA}) tem {j}.",
               deve=[re.escape(vencedor), num(m), num(j)])


@pergunta("P13", "Quantos usuários são amigos de amigos do Marklin sem serem amigos diretos dele?",
          "cálculo", "cypher", ["consultar_grafo"])
def _(db):
    n = db.valor("""MATCH (u:User {user_id: $u})-[:FRIEND]-(:User)-[:FRIEND]-(g:User)
                    WHERE g <> u AND NOT (u)-[:FRIEND]-(g)
                    RETURN count(DISTINCT g)""", u=MARKLIN)
    return gab(f"{n} usuários a exatamente dois passos de amizade (sem contar o próprio Marklin nem os amigos diretos).",
               deve=[num(n)])


@pergunta("P14", "Qual usuário tem mais amigos, e quantos são?",
          "cálculo", "cypher", ["consultar_grafo"])
def _(db):
    r = db.cypher("""MATCH (u:User)-[:FRIEND]-(f:User) WITH u, count(DISTINCT f) AS n
                     RETURN u.name AS nome, u.user_id AS id, n ORDER BY n DESC LIMIT 2""")
    return gab(f"{r[0]['nome']} ({r[0]['id']}), com {r[0]['n']} amigos. O segundo tem {r[1]['n']}.",
               deve=[re.escape(r[0]["nome"]), num(r[0]["n"])])


# ─── Híbridas: o grafo dá os ids, o SQL dá os números ────────────────────────

@pergunta("P15", "A nota média das avaliações feitas pelos amigos do Marklin é maior ou menor que a nota média das avaliações do próprio Marklin?",
          "comparação", "ambos", ["consultar_grafo", "consultar_sql"])
def _(db):
    ids = amigos(db, MARKLIN)
    f = db.sql("SELECT count(*) AS n, round(avg(stars), 2) AS media FROM review WHERE user_id = ANY(%(ids)s)", ids=ids)[0]
    m = db.sql("SELECT count(*) AS n, round(avg(stars), 2) AS media FROM review WHERE user_id = %(u)s", u=MARKLIN)[0]
    glob = db.valor("SELECT average_stars FROM users WHERE user_id = %(u)s", u=MARKLIN)
    rel = "menor" if f["media"] < m["media"] else "maior"
    return gab(f"{rel.capitalize()}: as {f['n']} avaliações dos {len(ids)} amigos em Santa Barbara têm média {f['media']}; "
               f"as {m['n']} do Marklin têm média {m['media']}. "
               f"(O average_stars do Marklin, {glob}, é do Yelp inteiro; comparar com ele mistura bases diferentes.)",
               deve=[rel, num(f["media"]), num(m["media"])])


@pergunta("P16", f"Qual é a nota média das avaliações feitas pelos amigos da usuária de user_id {ROSIE}?",
          "cálculo", "ambos", ["consultar_grafo", "consultar_sql"])
def _(db):
    ids = amigos(db, ROSIE)
    f = db.sql("SELECT count(*) AS n, round(avg(stars), 2) AS media FROM review WHERE user_id = ANY(%(ids)s)", ids=ids)[0]
    return gab(f"{f['media']}, sobre {f['n']} avaliações dos {len(ids)} amigos (média de todas as avaliações, "
               "e não a média das médias de cada amigo).", deve=[num(f["media"])])


@pergunta("P17", "Quantos negócios de comida mexicana foram avaliados por pelo menos um amigo do Marklin?",
          "cálculo", "ambos", ["consultar_grafo", "consultar_sql"])
def _(db):
    ids = amigos(db, MARKLIN)
    r = db.sql("""SELECT DISTINCT b.name FROM review r JOIN business_category c USING (business_id) JOIN business b USING (business_id)
                  WHERE r.user_id = ANY(%(ids)s) AND c.category = 'Mexican' ORDER BY 1""", ids=ids)
    n = db.valor("""SELECT count(DISTINCT r.business_id) FROM review r JOIN business_category c USING (business_id)
                    WHERE r.user_id = ANY(%(ids)s) AND c.category = 'Mexican'""", ids=ids)
    return gab(f"{n} negócios da categoria 'Mexican' (avaliados por ao menos um dos {len(ids)} amigos), entre eles "
               + ", ".join(x["name"] for x in r[:8]) + ("..." if len(r) > 8 else "."), deve=[num(n)])


# ─── Especiais ───────────────────────────────────────────────────────────────

@pergunta("P18", "Quantas avaliações o Marklin já escreveu?",
          "capciosa", "sql", ["consultar_sql"])   # buscar_entidade dá só o review_count do Yelp inteiro
def _(db):
    total = db.valor("SELECT review_count FROM users WHERE user_id = %(u)s", u=MARKLIN)
    sb = db.valor("SELECT count(*) FROM review WHERE user_id = %(u)s", u=MARKLIN)
    return gab(f"Depende da base, e a resposta certa diz qual está usando: {total} no Yelp inteiro (users.review_count) "
               f"e {sb} em Santa Barbara (linhas da tabela review). Dar só um dos números sem dizer de onde ele vem "
               "está incompleto; dar o número de Santa Barbara como se fosse o total está errado.",
               deve=[num(total), num(sb)])


@pergunta("P19", "Qual é a nota da Churrascaria Fogo Bravo em Santa Barbara?",
          "inexistente", "sql", ["buscar_entidade|consultar_sql"])
def _(db):
    n = db.valor("SELECT count(*) FROM business WHERE name ILIKE '%%churrascaria%%' OR name ILIKE '%%fogo bravo%%'")
    assert n == 0, "a Churrascaria Fogo Bravo não devia existir no recorte"
    return gab("Não existe negócio com esse nome nos dados de Santa Barbara. O certo é dizer isso, sem inventar nota "
               "nem trocar por outro negócio.",
               deve=[r"n[ãa]o (existe|foi encontrad|encontr|h[áa]|consta|est[áa])|nenhum|inexist"],
               proibido=[r"\b[1-5](?:[,.][05])?\s*(?:estrelas|de 5)"])


@pergunta("P20", "Qual é a previsão do tempo para Santa Barbara amanhã?",
          "fora do escopo", "nenhum", [])
def _(db):
    return gab("Fora do escopo: os dados são de negócios, avaliações e usuários do Yelp, sem clima. O certo é dizer que "
               "não pode responder, sem chamar ferramentas e sem inventar uma previsão.",
               deve=[r"n[ãa]o (posso|consigo|tenho|sei|é poss|dispon)|fora d|apenas|somente|s[óo] (posso|tenho|respond)"],
               proibido=[r"\d+\s*°|graus|ensolarado|chuva (forte|fraca)|nublado"])


assert len(PERGUNTAS) == 20 and len({p["id"] for p in PERGUNTAS}) == 20
