"""Recorte do Inside Airbnb de São Paulo, pronto para o PostgreSQL e o Neo4j.

Lê os três arquivos baixados de insideairbnb.com (página "Get the data", São Paulo):

    listings.csv.gz          anúncios detalhados (90 colunas)
    reviews.csv.gz           avaliações (com o texto, que não é guardado)
    neighbourhoods.geojson   polígonos dos 96 distritos

e grava CSV limpos:

    neighbourhood.csv     -> PostgreSQL e Neo4j (nós Neighbourhood)
    host.csv              -> PostgreSQL e Neo4j (nós Host)
    listing.csv           -> PostgreSQL e Neo4j (nós Listing, relações HOSTS e LOCATED_IN)
    listing_amenity.csv   -> PostgreSQL
    guest.csv             -> PostgreSQL e Neo4j (nós Guest)
    review.csv            -> PostgreSQL (sem o texto)
    reviewed.csv          -> Neo4j (uma linha por par hóspede-anúncio)
    borders.csv           -> Neo4j (pares de bairros vizinhos, calculados dos polígonos)

Uso:
    python dados/recorte.py <pasta_com_os_3_arquivos> <pasta_de_saida> [--desde 2025-01-01]
Precisa de pandas e shapely (dados/requirements.txt).
"""
import argparse
import itertools
import json
import os
import re
import time
import unicodedata

import pandas as pd
from shapely.geometry import shape

PRECO_MAXIMO = 10_000          # R$ por noite; acima disso é erro de coleta (há um studio a R$ 500.210)
COMODIDADE_MINIMA = 0.01       # só comodidades presentes em pelo menos 1% dos anúncios
FRONTEIRA_MINIMA = 1e-4        # graus (~10 m) de divisa em comum para dois bairros serem vizinhos; ponto não conta
NOTAS = ["rating", "accuracy", "cleanliness", "checkin", "communication", "location", "value"]


def limpo(texto):
    """Tira barra invertida, quebras de linha, espaços repetidos e caracteres invisíveis de formatação
    (ex.: as marcas de direção U+2068/U+2069 que vêm em alguns nomes), que atrapalham a busca por nome."""
    if not isinstance(texto, str):
        return texto
    texto = "".join(ch for ch in texto if unicodedata.category(ch) != "Cf")
    return re.sub(r"\s+", " ", texto.replace("\\", "/")).strip()


def booleano(serie):
    return serie.map({"t": True, "f": False})


def banheiros(texto):
    """'1.5 shared baths' -> 1.5; 'Half-bath' -> 0.5; nulo quando não dá para ler."""
    if not isinstance(texto, str):
        return None
    if "half" in texto.lower():
        return 0.5
    m = re.match(r"\s*(\d+(?:\.\d+)?)", texto)
    return float(m.group(1)) if m else None


def gravar(pasta, nome, df):
    caminho = os.path.join(pasta, nome)
    df.to_csv(caminho, index=False)
    print(f"  {nome:<22} {len(df):>9} linhas  {os.path.getsize(caminho) / 1e6:6.1f} MB")


def vizinhos(caminho_geojson):
    """Pares de bairros que dividem um trecho de divisa (não só um ponto)."""
    with open(caminho_geojson, encoding="utf-8") as f:
        feicoes = json.load(f)["features"]
    poligonos = {x["properties"]["neighbourhood"]: shape(x["geometry"]).buffer(0) for x in feicoes}
    pares = []
    for (a, pa), (b, pb) in itertools.combinations(sorted(poligonos.items()), 2):
        if not pa.envelope.intersects(pb.envelope):
            continue
        divisa = pa.boundary.intersection(pb.buffer(1e-5))
        if divisa.length > FRONTEIRA_MINIMA:
            pares.append((a, b))
    return pares


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("entrada")
    ap.add_argument("saida")
    ap.add_argument("--desde", default="2025-01-01", help="avaliações a partir desta data")
    a = ap.parse_args()
    os.makedirs(a.saida, exist_ok=True)
    t0 = time.time()

    # 1 · anúncios
    L = pd.read_csv(os.path.join(a.entrada, "listings.csv.gz"), low_memory=False)
    coleta = f"{L.last_scraped.min()} a {L.last_scraped.max()}"
    print(f"[{time.time() - t0:4.0f}s] {len(L)} anúncios (coleta de {coleta})")
    preco = pd.to_numeric(L.price.astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce")
    caros = int((preco > PRECO_MAXIMO).sum())
    preco = preco.where(preco <= PRECO_MAXIMO)
    listing = pd.DataFrame({
        "listing_id": L.id,
        "host_id": L.host_id,
        "neighbourhood": L.neighbourhood_cleansed,
        "name": L.name.map(limpo),
        "latitude": L.latitude.round(6),
        "longitude": L.longitude.round(6),
        "property_type": L.property_type,
        "room_type": L.room_type,
        "accommodates": L.accommodates,
        "bedrooms": L.bedrooms.astype("Int64"),
        "beds": L.beds.astype("Int64"),
        "bathrooms": L.bathrooms_text.map(banheiros),
        "price": preco.round(2),
        "minimum_nights": L.minimum_nights,
        "availability_365": L.availability_365,
        "number_of_reviews": L.number_of_reviews,
        "first_review": L.first_review,
        "last_review": L.last_review,
        **{f"review_scores_{n}": L[f"review_scores_{n}"] for n in NOTAS},
        "estimated_occupancy_l365d": L.estimated_occupancy_l365d,
        "estimated_revenue_l365d": L.estimated_revenue_l365d.where(preco.notna()).round(2),
    })
    inteiras = ["accommodates", "bedrooms", "beds", "minimum_nights", "availability_365", "number_of_reviews",
                "estimated_occupancy_l365d"]
    listing[inteiras] = listing[inteiras].apply(lambda s: pd.to_numeric(s, errors="coerce").round().astype("Int64"))
    print(f"  {caros} preços acima de R$ {PRECO_MAXIMO} viraram nulo (e a receita estimada deles também)")

    # 2 · anfitriões e bairros
    H = L.drop_duplicates("host_id")
    host = pd.DataFrame({
        "host_id": H.host_id,
        "name": H.host_name.map(limpo),
        "is_superhost": booleano(H.host_is_superhost),
        "identity_verified": booleano(H.host_identity_verified),
        "listings_count": pd.to_numeric(H.host_listings_count, errors="coerce").astype("Int64"),
    })
    neighbourhood = (L[["neighbourhood_cleansed", "neighbourhood_group_cleansed"]].drop_duplicates()
                     .rename(columns={"neighbourhood_cleansed": "name", "neighbourhood_group_cleansed": "group_name"})
                     .sort_values("name"))
    assert neighbourhood.name.is_unique, "um bairro aparece em dois grupos"

    # 3 · comodidades frequentes
    am = L[["id", "amenities"]].dropna()
    am = pd.DataFrame([(i, limpo(x)) for i, lista in zip(am.id, am.amenities) for x in set(json.loads(lista))],
                      columns=["listing_id", "amenity"]).drop_duplicates()
    freq = am.amenity.value_counts()
    manter = set(freq[freq >= COMODIDADE_MINIMA * len(L)].index)
    listing_amenity = am[am.amenity.isin(manter)].sort_values(["listing_id", "amenity"])
    print(f"  comodidades: {len(manter)} de {len(freq)} distintas (as presentes em >= {COMODIDADE_MINIMA:.0%} dos anúncios)")

    # 4 · avaliações a partir da data de corte, sem o texto
    R = pd.read_csv(os.path.join(a.entrada, "reviews.csv.gz"),
                    usecols=["listing_id", "id", "date", "reviewer_id", "reviewer_name"], low_memory=False)
    total = len(R)
    R = R[(R.date >= a.desde) & R.listing_id.isin(listing.listing_id)]
    # o Airbnb repete a mesma avaliação (mesmo id) nos anúncios "irmãos" de um anfitrião: fica uma, no anúncio de menor id,
    # senão a estadia conta várias vezes e infla os "hóspedes em comum" do grafo
    antes = len(R)
    R = R.sort_values(["id", "listing_id"]).drop_duplicates("id").sort_values(["date", "id"])
    print(f"[{time.time() - t0:4.0f}s] {len(R)} de {total} avaliações desde {a.desde} "
          f"({antes - len(R)} cópias da mesma avaliação em anúncios irmãos descartadas)")
    review = R.rename(columns={"id": "review_id", "reviewer_id": "guest_id"})[["review_id", "listing_id", "guest_id", "date"]]
    # o nome do hóspede é o da avaliação mais recente (o Airbnb guarda só o primeiro nome)
    guest = (R.drop_duplicates("reviewer_id", keep="last")
             .rename(columns={"reviewer_id": "guest_id", "reviewer_name": "name"})[["guest_id", "name"]]
             .assign(name=lambda d: d.name.map(limpo)).sort_values("guest_id"))
    reviewed = review[["guest_id", "listing_id"]].drop_duplicates().sort_values(["guest_id", "listing_id"])

    # 5 · vizinhança entre bairros
    borders = pd.DataFrame(vizinhos(os.path.join(a.entrada, "neighbourhoods.geojson")), columns=["neighbourhood_a", "neighbourhood_b"])
    sem_vizinho = set(neighbourhood.name) - set(borders.neighbourhood_a) - set(borders.neighbourhood_b)
    print(f"[{time.time() - t0:4.0f}s] {len(borders)} pares de bairros vizinhos; sem vizinho: {sorted(sem_vizinho) or 'nenhum'}")

    # 6 · integridade: toda chave estrangeira fecha
    assert listing.listing_id.is_unique and host.host_id.is_unique and guest.guest_id.is_unique and review.review_id.is_unique
    assert listing.host_id.isin(host.host_id).all() and listing.neighbourhood.isin(neighbourhood.name).all()
    assert review.guest_id.isin(guest.guest_id).all() and set(borders.stack()) <= set(neighbourhood.name)

    print("arquivos gravados:")
    gravar(a.saida, "neighbourhood.csv", neighbourhood)
    gravar(a.saida, "host.csv", host)
    gravar(a.saida, "listing.csv", listing)
    gravar(a.saida, "listing_amenity.csv", listing_amenity)
    gravar(a.saida, "guest.csv", guest)
    gravar(a.saida, "review.csv", review)
    gravar(a.saida, "reviewed.csv", reviewed)
    gravar(a.saida, "borders.csv", borders)
    print(f"pronto em {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
