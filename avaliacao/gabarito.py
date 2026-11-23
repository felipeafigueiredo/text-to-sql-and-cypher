"""Calcula o gabarito das 20 perguntas a partir dos bancos e faz a checagem automática de uma resposta.

Uso na linha de comando (imprime as perguntas com o gabarito):
    python avaliacao/gabarito.py
Usa as mesmas variáveis de ambiente do servidor: YELP_PG_DSN, YELP_NEO4J_URI, YELP_NEO4J_SENHA.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from avaliacao.bancos import Bancos            # noqa: E402
from avaliacao.perguntas import PERGUNTAS      # noqa: E402


def calcular(db=None):
    """As 20 perguntas com o gabarito preenchido (texto, deve, proibido)."""
    proprio = db is None
    db = db or Bancos()
    try:
        return [{**{k: v for k, v in p.items() if k != "gabarito"}, **p["gabarito"](db)} for p in PERGUNTAS]
    finally:
        if proprio:
            db.fechar()


def checagem(p, r):
    """Checagem automática de uma execução contra o gabarito. Devolve a lista de falhas (vazia = passou).

    p: uma pergunta de calcular(); r: o resultado de answer() (usa 'resposta' e 'ferramentas')."""
    resposta, falhas = (r["resposta"] or "").lower(), []
    falhas += [f"faltou /{d}/" for d in p["deve"] if not re.search(d, resposta, re.I)]
    falhas += [f"não devia ter /{x}/" for x in p["proibido"] if re.search(x, resposta, re.I)]
    return falhas + trajetoria(p, r)


def trajetoria(p, r):
    """A trajetória esperada foi seguida? Cada item de p['ferramentas'] precisa casar com alguma ferramenta chamada;
    lista vazia quer dizer que nenhuma ferramenta devia ser chamada."""
    chamadas = r["ferramentas"]
    if not p["ferramentas"]:
        return [f"chamou ferramentas sem precisar: {chamadas}"] if chamadas else []
    return [f"não chamou {f}" for f in p["ferramentas"] if not any(re.fullmatch(f, c) for c in chamadas)]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for p in calcular():
        print(f"{p['id']} [{p['categoria']} · {p['banco']} · {' + '.join(p['ferramentas']) or 'sem ferramentas'}]")
        print(f"   {p['pergunta']}")
        print(f"   → {p['texto']}")
        print(f"   deve: {p['deve']}" + (f" · proibido: {p['proibido']}" if p["proibido"] else "") + "\n")
