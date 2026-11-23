"""Carrega o recorte (CSV gerados por recorte.py) no PostgreSQL e no Neo4j.

Recria tudo do zero a cada execução, então pode ser rodado de novo sem problema.
No PostgreSQL, cria também o usuário `leitor`, que só lê e tem teto de tempo por consulta;
é com ele que o servidor MCP se conecta.

Uso:
    python dados/carga.py <pasta_dos_csv> [--pg-host localhost] [--pg-porta 5432]
        [--pg-admin postgres] [--pg-senha-admin postgres] [--neo4j-uri bolt://localhost:7687]
        [--neo4j-senha yelp-santa-barbara]
"""
import argparse
import csv
import os
import time

import psycopg
from neo4j import GraphDatabase

AQUI = os.path.dirname(os.path.abspath(__file__))
TABELAS = ["business", "business_category", "users", "review"]          # ordem das chaves estrangeiras
LOTE = 10_000


def carregar_postgres(pasta, host, porta, admin, senha_admin, senha_leitor="leitor"):
    conninfo = dict(host=host, port=porta, user=admin, password=senha_admin)
    with psycopg.connect(**conninfo, dbname="postgres", autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS yelp WITH (FORCE)")
        c.execute("CREATE DATABASE yelp")
        c.execute("DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'leitor') "
                  "THEN CREATE ROLE leitor LOGIN; END IF; END $$")
        c.execute(f"ALTER ROLE leitor PASSWORD '{senha_leitor}'")
        c.execute("ALTER ROLE leitor SET default_transaction_read_only = on")
        c.execute("ALTER ROLE leitor SET statement_timeout = '15s'")

    with psycopg.connect(**conninfo, dbname="yelp", autocommit=True) as c:
        c.execute(open(os.path.join(AQUI, "esquema.sql"), encoding="utf-8").read())
        for tabela in TABELAS:
            t = time.time()
            with open(os.path.join(pasta, f"{tabela}.csv"), "rb") as f, \
                    c.cursor().copy(f"COPY {tabela} FROM STDIN WITH (FORMAT csv, HEADER true)") as copia:
                while bloco := f.read(1 << 20):
                    copia.write(bloco)
            n = c.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0]
            print(f"  PostgreSQL · {tabela:<18} {n:>8} linhas  {time.time() - t:5.1f} s")
        c.execute("""
            CREATE EXTENSION IF NOT EXISTS pg_trgm;
            CREATE INDEX ON review (business_id);
            CREATE INDEX ON review (user_id);
            CREATE INDEX ON business_category (business_id);
            CREATE INDEX ON business_category (category);
            CREATE INDEX ON business USING gin (name gin_trgm_ops);
            CREATE INDEX ON users USING gin (name gin_trgm_ops);
            GRANT CONNECT ON DATABASE yelp TO leitor;
            GRANT USAGE ON SCHEMA public TO leitor;
            GRANT SELECT ON ALL TABLES IN SCHEMA public TO leitor;
            ANALYZE;
        """)


def ler_csv(caminho):
    with open(caminho, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def em_lotes(sessao, cypher, linhas):
    for i in range(0, len(linhas), LOTE):
        sessao.run(cypher, linhas=linhas[i:i + LOTE]).consume()


def carregar_neo4j(pasta, uri, senha):
    passos = [
        ("User", "users.csv", "UNWIND $linhas AS r CREATE (:User {user_id: r.user_id, name: r.name})"),
        ("Business", "business.csv", "UNWIND $linhas AS r CREATE (:Business {business_id: r.business_id, name: r.name})"),
        ("FRIEND", "friends.csv", "UNWIND $linhas AS r MATCH (a:User {user_id: r.user_a}), (b:User {user_id: r.user_b}) "
                                  "CREATE (a)-[:FRIEND]->(b)"),
        ("REVIEWED", "reviewed.csv", "UNWIND $linhas AS r MATCH (u:User {user_id: r.user_id}), "
                                     "(b:Business {business_id: r.business_id}) CREATE (u)-[:REVIEWED]->(b)"),
    ]
    with GraphDatabase.driver(uri, auth=("neo4j", senha)) as driver, driver.session() as s:
        s.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS").consume()
        for comando in ["CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.user_id IS UNIQUE",
                        "CREATE CONSTRAINT business_id IF NOT EXISTS FOR (b:Business) REQUIRE b.business_id IS UNIQUE",
                        "CREATE INDEX user_name IF NOT EXISTS FOR (u:User) ON (u.name)",
                        "CREATE INDEX business_name IF NOT EXISTS FOR (b:Business) ON (b.name)"]:
            s.run(comando).consume()
        s.run("CALL db.awaitIndexes(300)").consume()
        for nome, arquivo, cypher in passos:
            t = time.time()
            em_lotes(s, cypher, ler_csv(os.path.join(pasta, arquivo)))
            print(f"  Neo4j · {nome:<10} {time.time() - t:5.1f} s")
        contagens = {
            "User": "MATCH (n:User) RETURN count(n)", "Business": "MATCH (n:Business) RETURN count(n)",
            "FRIEND": "MATCH ()-[r:FRIEND]->() RETURN count(r)", "REVIEWED": "MATCH ()-[r:REVIEWED]->() RETURN count(r)"}
        for nome, consulta in contagens.items():
            print(f"  Neo4j · {nome:<10} {s.run(consulta).single()[0]:>8}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pasta")
    ap.add_argument("--pg-host", default="localhost")
    ap.add_argument("--pg-porta", type=int, default=5432)
    ap.add_argument("--pg-admin", default="postgres")
    ap.add_argument("--pg-senha-admin", default="postgres")
    ap.add_argument("--neo4j-uri", default="bolt://localhost:7687")
    ap.add_argument("--neo4j-senha", default="yelp-santa-barbara")
    a = ap.parse_args()
    t = time.time()
    carregar_postgres(a.pasta, a.pg_host, a.pg_porta, a.pg_admin, a.pg_senha_admin)
    carregar_neo4j(a.pasta, a.neo4j_uri, a.neo4j_senha)
    print(f"carga completa em {time.time() - t:.0f} s")


if __name__ == "__main__":
    main()
