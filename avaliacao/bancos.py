"""Acesso direto aos dois bancos, só para calcular os gabaritos (fora do servidor MCP e sem LLM)."""
import os

import psycopg
from neo4j import GraphDatabase, READ_ACCESS
from psycopg.rows import dict_row


class Bancos:
    """Consultas de leitura nos dois bancos, com o mesmo usuário somente leitura do servidor."""

    def __init__(self, pg_dsn=None, neo4j_uri=None, neo4j_usuario=None, neo4j_senha=None):
        self.pg = psycopg.connect(
            pg_dsn or os.environ.get("YELP_PG_DSN", "host=localhost port=5432 dbname=yelp user=leitor password=leitor"),
            autocommit=True, row_factory=dict_row)
        self.neo4j = GraphDatabase.driver(
            neo4j_uri or os.environ.get("YELP_NEO4J_URI", "bolt://localhost:7687"),
            auth=(neo4j_usuario or os.environ.get("YELP_NEO4J_USUARIO", "neo4j"),
                  neo4j_senha or os.environ.get("YELP_NEO4J_SENHA", "yelp-santa-barbara")))

    def sql(self, consulta, **parametros):
        """Linhas como dicionários. Parâmetros no formato %(nome)s."""
        return self.pg.execute(consulta, parametros).fetchall()

    def cypher(self, consulta, **parametros):
        """Linhas como dicionários. Parâmetros no formato $nome."""
        with self.neo4j.session(default_access_mode=READ_ACCESS) as s:
            return [r.data() for r in s.run(consulta, parametros)]

    def valor(self, consulta, **parametros):
        """O primeiro valor da primeira linha (SQL ou Cypher, conforme a consulta)."""
        linhas = (self.cypher if consulta.lstrip().upper().startswith(("MATCH", "UNWIND", "CALL")) else self.sql)(
            consulta, **parametros)
        return next(iter(linhas[0].values())) if linhas else None

    def fechar(self):
        self.pg.close()
        self.neo4j.close()
