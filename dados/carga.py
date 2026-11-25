"""Carrega o recorte (CSV gerados por recorte.py) no PostgreSQL e no Neo4j.

Recria tudo do zero a cada execução, então pode ser rodado de novo sem problema.
No PostgreSQL, cria também o usuário `leitor`, que só lê e tem teto de tempo por consulta;
é com ele que o servidor MCP se conecta.

Uso:
    python dados/carga.py <pasta_dos_csv> [--pg-host localhost] [--pg-porta 5432]
        [--pg-admin postgres] [--pg-senha-admin postgres] [--neo4j-uri bolt://localhost:7687]
        [--neo4j-senha airbnb-sao-paulo]
"""
import argparse
import csv
import os
import time

import psycopg
from neo4j import GraphDatabase

AQUI = os.path.dirname(os.path.abspath(__file__))
TABELAS = ["neighbourhood", "host", "listing", "listing_amenity", "guest", "review"]   # ordem das chaves estrangeiras
LOTE = 10_000


def carregar_postgres(pasta, host, porta, admin, senha_admin, senha_leitor="leitor"):
    conninfo = dict(host=host, port=porta, user=admin, password=senha_admin)
    with psycopg.connect(**conninfo, dbname="postgres", autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS airbnb WITH (FORCE)")
        c.execute("CREATE DATABASE airbnb")
        c.execute("DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'leitor') "
                  "THEN CREATE ROLE leitor LOGIN; END IF; END $$")
        c.execute(f"ALTER ROLE leitor PASSWORD '{senha_leitor}'")
        c.execute("ALTER ROLE leitor SET default_transaction_read_only = on")
        c.execute("ALTER ROLE leitor SET statement_timeout = '15s'")

    with psycopg.connect(**conninfo, dbname="airbnb", autocommit=True) as c:
        c.execute(open(os.path.join(AQUI, "esquema.sql"), encoding="utf-8").read())
        for tabela in TABELAS:
            t = time.time()
            with open(os.path.join(pasta, f"{tabela}.csv"), "rb") as f, \
                    c.cursor().copy(f"COPY {tabela} FROM STDIN WITH (FORMAT csv, HEADER true)") as copia:
                while bloco := f.read(1 << 20):
                    copia.write(bloco)
            n = c.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0]
            print(f"  PostgreSQL · {tabela:<16} {n:>9} linhas  {time.time() - t:5.1f} s")
        c.execute("""
            CREATE EXTENSION IF NOT EXISTS pg_trgm;
            CREATE EXTENSION IF NOT EXISTS unaccent;
            CREATE INDEX ON listing (host_id);
            CREATE INDEX ON listing (neighbourhood);
            CREATE INDEX ON listing_amenity (listing_id);
            CREATE INDEX ON listing_amenity (amenity);
            CREATE INDEX ON review (listing_id);
            CREATE INDEX ON review (guest_id);
            CREATE INDEX ON review (date);
            CREATE INDEX ON listing USING gin (name gin_trgm_ops);
            CREATE INDEX ON host USING gin (name gin_trgm_ops);
            CREATE INDEX ON guest USING gin (name gin_trgm_ops);
            GRANT CONNECT ON DATABASE airbnb TO leitor;
            GRANT USAGE ON SCHEMA public TO leitor;
            GRANT SELECT ON ALL TABLES IN SCHEMA public TO leitor;
            ANALYZE;
        """)


def ler_csv(caminho, inteiros=()):
    """Linhas do CSV como dicionários; as colunas em `inteiros` viram int (os ids são inteiros no grafo também)."""
    with open(caminho, encoding="utf-8", newline="") as f:
        linhas = list(csv.DictReader(f))
    for linha in linhas:
        for c in inteiros:
            linha[c] = int(linha[c])
    return linhas


def em_lotes(sessao, cypher, linhas):
    for i in range(0, len(linhas), LOTE):
        sessao.run(cypher, linhas=linhas[i:i + LOTE]).consume()


def carregar_neo4j(pasta, uri, senha):
    passos = [
        ("Neighbourhood", "neighbourhood.csv", (),
         "UNWIND $linhas AS r CREATE (:Neighbourhood {name: r.name})"),
        ("Host", "host.csv", ("host_id",),
         "UNWIND $linhas AS r CREATE (:Host {host_id: r.host_id, name: r.name})"),
        ("Listing", "listing.csv", ("listing_id", "host_id"),
         "UNWIND $linhas AS r MATCH (h:Host {host_id: r.host_id}), (n:Neighbourhood {name: r.neighbourhood}) "
         "CREATE (h)-[:HOSTS]->(:Listing {listing_id: r.listing_id, name: r.name})-[:LOCATED_IN]->(n)"),
        ("Guest", "guest.csv", ("guest_id",),
         "UNWIND $linhas AS r CREATE (:Guest {guest_id: r.guest_id, name: r.name})"),
        ("REVIEWED", "reviewed.csv", ("guest_id", "listing_id"),
         "UNWIND $linhas AS r MATCH (g:Guest {guest_id: r.guest_id}), (l:Listing {listing_id: r.listing_id}) "
         "CREATE (g)-[:REVIEWED]->(l)"),
        ("BORDERS", "borders.csv", (),
         "UNWIND $linhas AS r MATCH (a:Neighbourhood {name: r.neighbourhood_a}), (b:Neighbourhood {name: r.neighbourhood_b}) "
         "CREATE (a)-[:BORDERS]->(b)"),
    ]
    with GraphDatabase.driver(uri, auth=("neo4j", senha)) as driver, driver.session() as s:
        s.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS").consume()
        for comando in ["CREATE CONSTRAINT host_id IF NOT EXISTS FOR (h:Host) REQUIRE h.host_id IS UNIQUE",
                        "CREATE CONSTRAINT listing_id IF NOT EXISTS FOR (l:Listing) REQUIRE l.listing_id IS UNIQUE",
                        "CREATE CONSTRAINT guest_id IF NOT EXISTS FOR (g:Guest) REQUIRE g.guest_id IS UNIQUE",
                        "CREATE CONSTRAINT neighbourhood_name IF NOT EXISTS FOR (n:Neighbourhood) REQUIRE n.name IS UNIQUE",
                        "CREATE INDEX host_name IF NOT EXISTS FOR (h:Host) ON (h.name)",
                        "CREATE INDEX listing_name IF NOT EXISTS FOR (l:Listing) ON (l.name)",
                        "CREATE INDEX guest_name IF NOT EXISTS FOR (g:Guest) ON (g.name)"]:
            s.run(comando).consume()
        s.run("CALL db.awaitIndexes(300)").consume()
        for nome, arquivo, inteiros, cypher in passos:
            t = time.time()
            em_lotes(s, cypher, ler_csv(os.path.join(pasta, arquivo), inteiros))
            print(f"  Neo4j · {nome:<13} {time.time() - t:5.1f} s")
        contagens = {
            **{r: f"MATCH (n:{r}) RETURN count(n)" for r in ("Neighbourhood", "Host", "Listing", "Guest")},
            **{r: f"MATCH ()-[r:{r}]->() RETURN count(r)" for r in ("HOSTS", "LOCATED_IN", "REVIEWED", "BORDERS")}}
        for nome, consulta in contagens.items():
            print(f"  Neo4j · {nome:<13} {s.run(consulta).single()[0]:>9}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pasta")
    ap.add_argument("--pg-host", default="localhost")
    ap.add_argument("--pg-porta", type=int, default=5432)
    ap.add_argument("--pg-admin", default="postgres")
    ap.add_argument("--pg-senha-admin", default="postgres")
    ap.add_argument("--neo4j-uri", default="bolt://localhost:7687")
    ap.add_argument("--neo4j-senha", default="airbnb-sao-paulo")
    a = ap.parse_args()
    t = time.time()
    carregar_postgres(a.pasta, a.pg_host, a.pg_porta, a.pg_admin, a.pg_senha_admin)
    carregar_neo4j(a.pasta, a.neo4j_uri, a.neo4j_senha)
    print(f"carga completa em {time.time() - t:.0f} s")


if __name__ == "__main__":
    main()
