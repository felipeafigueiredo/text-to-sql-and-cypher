"""As 20 perguntas da avaliação, cada uma com o gabarito calculado dos bancos.

Cada pergunta registra:
- categoria: filtro, item, comparação, cálculo, capciosa, inexistente ou fora do escopo
- banco: onde está a resposta (sql, cypher, ambos ou nenhum)
- ferramentas: a trajetória esperada. Cada item é uma expressão regular, e alguma ferramenta chamada
  precisa casar com cada item. "detalhes_anuncio|consultar_sql" aceita qualquer uma das duas.
  Lista vazia: o certo é não chamar ferramenta nenhuma.
- gabarito: função que recebe os bancos (avaliacao.bancos.Bancos) e devolve o texto de referência
  para o juiz, as expressões que a resposta deve conter e as que não pode conter.

O gabarito é calculado na hora, a partir dos bancos carregados: assim o repositório não guarda
os dados, e as consultas de referência ficam à vista, ao lado de cada pergunta.
Nenhuma destas perguntas aparece como exemplo nos prompts dos agentes.

Dados: Inside Airbnb, São Paulo (coleta de setembro de 2026), avaliações de 2025 em diante.
"""
import re
from decimal import Decimal

PERGUNTAS = []

# Anfitriões citados pelo nome são empresas de nome único no recorte (o Airbnb guarda só o primeiro nome das
# pessoas, e há dezenas de homônimos). Nenhuma pergunta cita hóspede pelo nome: são pessoas físicas.
SEAZONE = 227777128
TABAS = 341887136
COPAN = ("High Above Copan", "Studio at Copan with a view", "Fantastic 28th-Floor View + A/C @ Copan")


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


def reais(x):
    """Um valor em reais, arredondado ao inteiro: 426.37 casa com 426, 426,37, 426.4 e 1.234 para 1234.
    O modelo pode dar o valor com ou sem centavos."""
    inteiro = int(Decimal(x).quantize(Decimal("1")))
    base = Decimal(x).quantize(Decimal("0.01"))
    a, b = f"{base}".split(".")
    mil = rf"{str(inteiro)[:-3]}[.,\s]?{str(inteiro)[-3:]}" if inteiro >= 1000 else str(inteiro)
    a_mil = rf"{a[:-3]}[.,\s]?{a[-3:]}" if len(a) > 3 else a
    return rf"(?<![\d.,])(?:{a_mil}[,.]{b[0]}\d?|{mil})(?![\d]|[,.]\d)"


ACENTOS = {"a": "aáàâã", "e": "eéê", "i": "ií", "o": "oóôõ", "u": "uú", "c": "cç"}


def nome_rx(nome):
    """Um nome de bairro ou anúncio sem depender de acento nem de maiúscula: 'Butanta' casa com 'Butantã'."""
    return "".join(f"[{ACENTOS[ch.lower()]}]" if ch.lower() in ACENTOS else re.escape(ch) for ch in nome)


# A resposta reconhece um empate: "empatam", "empate", "a mesma nota", "notas iguais", "todos têm 4,9"...
EMPATE = r"empat|mesma nota|nota igual|notas iguais|todos (?:os três )?(?:têm|tem)|os três (?:têm|tem)"


def anuncio(db, nome):
    """O listing_id de um anúncio pelo nome exato; o nome tem de ser único no recorte."""
    linhas = db.sql("SELECT listing_id FROM listing WHERE name = %(n)s", n=nome)
    assert len(linhas) == 1, f"'{nome}' não é único no recorte ({len(linhas)} anúncios)"
    return linhas[0]["listing_id"]


def anfitriao(db, host_id):
    """O nome de um anfitrião citado pelo nome; o nome tem de ser único no recorte."""
    nome = db.valor("SELECT name FROM host WHERE host_id = %(h)s", h=host_id)
    assert db.valor("SELECT count(*) FROM host WHERE name = %(n)s", n=nome) == 1, f"'{nome}' não é único"
    return nome


def vizinhos(db, bairro):
    return [r["nome"] for r in db.cypher(
        "MATCH (:Neighbourhood {name: $b})-[:BORDERS]-(n:Neighbourhood) RETURN DISTINCT n.name AS nome ORDER BY nome", b=bairro)]


def hospedes(db, host_id):
    return db.valor("""MATCH (:Host {host_id: $h})-[:HOSTS]->(:Listing)<-[:REVIEWED]-(g:Guest)
                       RETURN count(DISTINCT g)""", h=host_id)


# ─── SQL ─────────────────────────────────────────────────────────────────────

@pergunta("P01", "Quantos anúncios com piscina custam menos de R$ 300 por noite?",
          "filtro", "sql", ["consultar_sql"])
def _(db):
    n = db.valor("""SELECT count(DISTINCT l.listing_id) FROM listing l JOIN listing_amenity a USING (listing_id)
                    WHERE a.amenity = 'Pool' AND l.price < 300""")
    return gab(f"{n} anúncios com a comodidade 'Pool' e preço abaixo de R$ 300 por noite "
               "(anúncios sem preço ficam de fora).", deve=[num(n)])


@pergunta("P02", "Quais anúncios de imóvel inteiro em Pinheiros acomodam 12 pessoas ou mais e têm nota 4,9 ou mais?",
          "filtro", "sql", ["consultar_sql"])
def _(db):
    r = db.sql("""SELECT listing_id, name, accommodates, review_scores_rating FROM listing
                  WHERE neighbourhood = 'Pinheiros' AND room_type = 'Entire home/apt' AND accommodates >= 12
                    AND review_scores_rating >= 4.9 ORDER BY name""")
    return gab(f"{len(r)} anúncios: " + "; ".join(f"{x['name']} ({x['accommodates']} pessoas, nota {x['review_scores_rating']})"
                                              for x in r) + ".",
               deve=[num(len(r))] + [re.escape(x["name"][:12]) for x in r])


@pergunta("P03", "Qual é o preço por noite, a nota geral e o bairro do anúncio 'Noren - A library in the sky @ Copan'?",
          "item", "sql", ["detalhes_anuncio|consultar_sql"])
def _(db):
    x = db.sql("SELECT price, review_scores_rating, neighbourhood FROM listing WHERE listing_id = %(l)s",
               l=anuncio(db, "Noren - A library in the sky @ Copan"))[0]
    return gab(f"R$ {x['price']} por noite, nota {x['review_scores_rating']}, bairro {x['neighbourhood']} (República).",
               deve=[reais(x["price"]), num(x["review_scores_rating"]), nome_rx(x["neighbourhood"])])


@pergunta("P04", "Quantas avaliações o anúncio 'Studio at Copan with a view' recebeu em 2025?",
          "item", "sql", ["consultar_sql"])
def _(db):
    lid = anuncio(db, "Studio at Copan with a view")
    n = db.valor("""SELECT count(*) FROM review WHERE listing_id = %(l)s
                    AND date >= '2025-01-01' AND date < '2026-01-01'""", l=lid)
    total = db.valor("SELECT number_of_reviews FROM listing WHERE listing_id = %(l)s", l=lid)
    return gab(f"{n} avaliações em 2025 (contadas na tabela review). O number_of_reviews, {total}, é da vida toda "
               "do anúncio e não responde à pergunta.", deve=[num(n)])


@pergunta("P05", f"Entre os anúncios '{COPAN[0]}', '{COPAN[1]}' e '{COPAN[2]}', qual tem a melhor nota e qual é o mais barato?",
          "comparação", "sql", ["comparar_anuncios|consultar_sql"])
def _(db):
    ids = [anuncio(db, n) for n in COPAN]
    r = db.sql("""SELECT name, review_scores_rating AS nota, price FROM listing WHERE listing_id = ANY(%(ids)s)
                  ORDER BY name""", ids=ids)
    nota = max(x["nota"] for x in r)
    melhores = [x["name"] for x in r if x["nota"] == nota]
    barato = min(r, key=lambda x: x["price"])
    melhor = (f"Os três empatam na nota, {nota}" if len(melhores) == 3 else
              f"Empate na melhor nota ({nota}) entre {' e '.join(melhores)}" if len(melhores) > 1 else
              f"Melhor nota: {melhores[0]} ({nota})")
    # Com empate, a resposta precisa dizer que empataram: apontar um "melhor" entre notas iguais está errado.
    deve_nota = [EMPATE] if len(melhores) > 1 else [re.escape(melhores[0][:12])]
    return gab(f"{melhor}. Mais barato: {barato['name']} (R$ {barato['price']}). "
               + "; ".join(f"{x['name']}: nota {x['nota']}, R$ {x['price']}" for x in r) + ".",
               deve=deve_nota + [re.escape(barato["name"][:12]), reais(barato["price"])])


@pergunta("P06", "O preço médio por noite é maior em Moema ou na Vila Mariana?",
          "comparação", "sql", ["consultar_sql"])
def _(db):
    r = {x["neighbourhood"]: x for x in db.sql(
        """SELECT neighbourhood, count(price) AS n, round(avg(price), 2) AS media FROM listing
           WHERE neighbourhood IN ('Moema', 'Vila Mariana') GROUP BY neighbourhood""")}
    mo, vm = r["Moema"], r["Vila Mariana"]
    vencedor = "Moema" if mo["media"] > vm["media"] else "Vila Mariana"
    return gab(f"{vencedor}: média de R$ {mo['media']} em Moema ({mo['n']} anúncios com preço) contra "
               f"R$ {vm['media']} na Vila Mariana ({vm['n']}).",
               deve=[vencedor, reais(mo["media"]), reais(vm["media"])])


@pergunta("P07", "Em que mês de 2025 os anúncios de São Paulo receberam mais avaliações, e quantas foram?",
          "cálculo", "sql", ["consultar_sql"])
def _(db):
    meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
             "novembro", "dezembro"]
    x = db.sql("""SELECT extract(month FROM date)::int AS mes, count(*) AS n FROM review
                  WHERE date >= '2025-01-01' AND date < '2026-01-01' GROUP BY 1 ORDER BY 2 DESC LIMIT 2""")
    m, s = x[0], x[1]
    return gab(f"{meses[m['mes'] - 1].capitalize()} de 2025, com {m['n']} avaliações (o segundo é "
               f"{meses[s['mes'] - 1]}, com {s['n']}).",
               deve=[rf"{meses[m['mes'] - 1][:4]}|\b0?{m['mes']}/2025|2025-0?{m['mes']}\b", num(m["n"])])


@pergunta("P08", "Quais são os 5 bairros com mais anúncios, e quantos anúncios cada um tem?",
          "cálculo", "sql", ["consultar_sql"])
def _(db):
    r = db.sql("SELECT neighbourhood AS bairro, count(*) AS n FROM listing GROUP BY 1 ORDER BY 2 DESC LIMIT 6")
    return gab("; ".join(f"{x['bairro']}: {x['n']}" for x in r[:5]) + f". O sexto é {r[5]['bairro']}, com {r[5]['n']}.",
               deve=[nome_rx(x["bairro"]) for x in r[:5]] + [num(x["n"]) for x in r[:5]])


# ─── Cypher ──────────────────────────────────────────────────────────────────

@pergunta("P09", "Quais bairros fazem divisa com Pinheiros?",
          "filtro", "cypher", ["consultar_grafo"])
def _(db):
    v = vizinhos(db, "Pinheiros")
    return gab(f"{len(v)} bairros: {', '.join(v)}.", deve=[nome_rx(x) for x in v])


@pergunta("P10", "Em quais bairros vizinhos da República a Tabas tem anúncios?",
          "filtro", "cypher", ["consultar_grafo"])
def _(db):
    anfitriao(db, TABAS)
    r = db.cypher("""MATCH (:Neighbourhood {name: 'Republica'})-[:BORDERS]-(n:Neighbourhood)
                     <-[:LOCATED_IN]-(l:Listing)<-[:HOSTS]-(:Host {host_id: $h})
                     RETURN n.name AS bairro, count(DISTINCT l) AS anuncios ORDER BY bairro""", h=TABAS)
    return gab(f"{len(r)} bairros: " + ", ".join(f"{x['bairro']} ({x['anuncios']} anúncios)" for x in r)
               + ". A própria República não conta: a pergunta é sobre os vizinhos.",
               deve=[nome_rx(x["bairro"]) for x in r])


# P11 e P12: o caminho anfitrião → anúncio ← hóspede é natural no grafo, mas também sai de um JOIN no SQL
# (review.listing_id → listing.host_id). As duas ferramentas valem na trajetória.
@pergunta("P11", "Quantos hóspedes diferentes avaliaram anúncios da Seazone?",
          "item", "cypher", ["consultar_grafo|consultar_sql"])
def _(db):
    anfitriao(db, SEAZONE)
    n = hospedes(db, SEAZONE)
    return gab(f"{n} hóspedes diferentes (avaliações de 2025 em diante, nos anúncios da Seazone em São Paulo).",
               deve=[num(n)])


@pergunta("P12", "Quem recebeu avaliações de mais hóspedes diferentes: a Seazone ou a Tabas?",
          "comparação", "cypher", ["consultar_grafo|consultar_sql"])
def _(db):
    s, t = hospedes(db, SEAZONE), hospedes(db, TABAS)
    vencedor = "Seazone" if s > t else "Tabas"
    return gab(f"{vencedor}: a Seazone tem {s} hóspedes diferentes e a Tabas, {t}.",
               deve=[vencedor, num(s), num(t)])


@pergunta("P13", "Quantos bairros estão a exatamente duas divisas de Pinheiros, isto é, são vizinhos dos vizinhos "
          "sem serem vizinhos diretos?",
          "cálculo", "cypher", ["consultar_grafo"])
def _(db):
    r = db.cypher("""MATCH (p:Neighbourhood {name: 'Pinheiros'})-[:BORDERS]-(:Neighbourhood)-[:BORDERS]-(x:Neighbourhood)
                     WHERE x <> p AND NOT (p)-[:BORDERS]-(x)
                     RETURN count(DISTINCT x) AS n, collect(DISTINCT x.name) AS nomes""")[0]
    return gab(f"{r['n']} bairros: {', '.join(sorted(r['nomes']))} (sem contar Pinheiros nem os vizinhos diretos).",
               deve=[num(r["n"])])


@pergunta("P14", "Qual bairro faz divisa com o maior número de bairros, e com quantos?",
          "cálculo", "cypher", ["consultar_grafo"])
def _(db):
    r = db.cypher("""MATCH (n:Neighbourhood)-[:BORDERS]-(m:Neighbourhood) WITH n, count(DISTINCT m) AS g
                     RETURN n.name AS bairro, g ORDER BY g DESC, bairro""")
    topo = [x["bairro"] for x in r if x["g"] == r[0]["g"]]
    return gab(f"{' e '.join(topo)}, com {r[0]['g']} vizinhos" + (" (empate)" if len(topo) > 1 else "")
               + f". Depois vem {next(x for x in r if x['g'] < r[0]['g'])['bairro']}.",
               deve=[nome_rx(b) for b in topo] + [num(r[0]["g"])])


# ─── Híbridas: o grafo dá os ids ou os bairros, o SQL dá os números ──────────

@pergunta("P15", "O preço médio por noite dos anúncios nos bairros vizinhos de Pinheiros é maior ou menor que o "
          "dos anúncios em Pinheiros?",
          "comparação", "ambos", ["consultar_grafo", "consultar_sql"])
def _(db):
    v = vizinhos(db, "Pinheiros")
    viz = db.sql("SELECT count(price) AS n, round(avg(price), 2) AS media FROM listing WHERE neighbourhood = ANY(%(v)s)",
                 v=v)[0]
    pin = db.sql("SELECT count(price) AS n, round(avg(price), 2) AS media FROM listing WHERE neighbourhood = 'Pinheiros'")[0]
    rel = "maior" if viz["media"] > pin["media"] else "menor"
    return gab(f"{rel.capitalize()}: R$ {viz['media']} nos {viz['n']} anúncios com preço dos {len(v)} vizinhos "
               f"({', '.join(v)}) contra R$ {pin['media']} nos {pin['n']} de Pinheiros. "
               "(A média das médias de cada bairro dá outro número; o certo é a média de todos os anúncios.)",
               deve=[rel, reais(viz["media"]), reais(pin["media"])])


@pergunta("P16", "Entre os bairros vizinhos da Liberdade, qual tem a maior nota média dos anúncios, e qual é essa nota?",
          "cálculo", "ambos", ["consultar_grafo", "consultar_sql"])
def _(db):
    v = vizinhos(db, "Liberdade")
    r = db.sql("""SELECT neighbourhood AS bairro, count(review_scores_rating) AS n, round(avg(review_scores_rating), 2) AS media
                  FROM listing WHERE neighbourhood = ANY(%(v)s) GROUP BY 1 ORDER BY 3 DESC, 1""", v=v)
    topo = [x for x in r if x["media"] == r[0]["media"]]
    return gab(f"{' e '.join(x['bairro'] for x in topo)}, com nota média {r[0]['media']} ({r[0]['n']} anúncios com nota)"
               + (" (empate)" if len(topo) > 1 else "") + ". Os vizinhos: "
               + "; ".join(f"{x['bairro']} {x['media']}" for x in r) + ". A própria Liberdade não conta.",
               deve=[nome_rx(x["bairro"]) for x in topo] + [num(r[0]["media"])])


@pergunta("P17", "Quantos anúncios com piscina existem nos bairros vizinhos de Moema?",
          "cálculo", "ambos", ["consultar_grafo", "consultar_sql"])
def _(db):
    v = vizinhos(db, "Moema")
    n = db.valor("""SELECT count(DISTINCT l.listing_id) FROM listing l JOIN listing_amenity a USING (listing_id)
                    WHERE a.amenity = 'Pool' AND l.neighbourhood = ANY(%(v)s)""", v=v)
    return gab(f"{n} anúncios com a comodidade 'Pool' nos {len(v)} vizinhos de Moema ({', '.join(v)}), "
               "sem contar a própria Moema.", deve=[num(n)])


# ─── Especiais ───────────────────────────────────────────────────────────────

@pergunta("P18", "Quantos anúncios a Seazone tem?",
          "capciosa", "sql", ["buscar_entidade|consultar_sql"])   # buscar_entidade mostra os dois números
def _(db):
    anfitriao(db, SEAZONE)
    total = db.valor("SELECT listings_count FROM host WHERE host_id = %(h)s", h=SEAZONE)
    sp = db.valor("SELECT count(*) FROM listing WHERE host_id = %(h)s", h=SEAZONE)
    return gab(f"Depende da base, e a resposta certa diz qual está usando: {total} no Airbnb inteiro "
               f"(host.listings_count) e {sp} em São Paulo (linhas da tabela listing). Dar só um dos números sem dizer "
               "de onde ele vem está incompleto; dar o número de São Paulo como se fosse o total está errado.",
               deve=[num(total), num(sp)])


@pergunta("P19", "Qual é a nota da Pousada Fogo Bravo em São Paulo?",
          "inexistente", "sql", ["buscar_entidade|consultar_sql"])
def _(db):
    n = db.valor("SELECT count(*) FROM listing WHERE name ILIKE '%%fogo bravo%%'")
    assert n == 0, "a Pousada Fogo Bravo não devia existir no recorte"
    return gab("Não existe anúncio com esse nome nos dados de São Paulo. O certo é dizer isso, sem inventar nota "
               "nem trocar por outro anúncio.",
               deve=[r"n[ãa]o (existe|foi encontrad|encontr|h[áa]|consta|est[áa])|nenhum|inexist"],
               proibido=[r"\b[0-5][,.]\d{1,2}\b(?!\s*%)"])


@pergunta("P20", "Qual é a previsão do tempo para São Paulo amanhã?",
          "fora do escopo", "nenhum", [])
def _(db):
    return gab("Fora do escopo: os dados são de anúncios, anfitriões, hóspedes e avaliações do Airbnb, sem clima. "
               "O certo é dizer que não pode responder, sem chamar ferramentas e sem inventar uma previsão.",
               deve=[r"n[ãa]o (posso|consigo|tenho|sei|é poss|dispon)|fora d|apenas|somente|s[óo] (posso|tenho|respond)"],
               proibido=[r"\d+\s*°|graus|ensolarado|chuva (forte|fraca)|nublado"])


assert len(PERGUNTAS) == 20 and len({p["id"] for p in PERGUNTAS}) == 20
