"""Roda as 20 perguntas nos 3 sistemas pelo answer() e grava uma linha por execução.

Duas saídas, as duas gravadas a cada execução (se a sessão do Colab cair, nada do que já rodou se perde):
  - execucoes.csv: uma linha por execução, com as métricas e a checagem automática (é o que a análise lê);
  - execucoes.jsonl: o registro completo de cada execução (consultas com os resultados, chamadas ao modelo,
    vereditos do verificador, respostas anteriores), para o juiz, a revisão manual e a análise de erros.

Retomada: ao rodar de novo com os mesmos arquivos, as execuções que já estão no CSV são puladas.

Ordem: pergunta por pergunta, os 3 sistemas em seguida. Assim, se a GPU do Colab ficar mais lenta com o tempo, isso
pesa igual nos três sistemas, e uma sessão que cai no meio deixa as primeiras perguntas completas nos três.
"""
import csv
import datetime
import json
import os

from avaliacao.gabarito import checagem, trajetoria

# o campo banco da pergunta → a rota que o roteador devia escolher (sistema 2)
ROTA_ESPERADA = {"sql": "sql", "cypher": "cypher", "ambos": "ambos", "nenhum": "fora_do_escopo"}

COLUNAS = [
    "execucao", "sistema", "pergunta_id", "categoria", "banco", "pergunta", "resposta",
    "checagem_ok", "checagem_falhas", "trajetoria_ok", "ferramentas_esperadas", "ferramentas",
    "rota", "rota_esperada", "rota_certa",
    "segundos", "chamadas_modelo", "chamadas_ferramenta", "erros_ferramenta",
    "tokens_entrada", "tokens_saida", "tokens_total",
    "cpu_medio", "cpu_max", "ram_max_gb", "gpu_medio", "gpu_max", "gpu_mem_max_gb",
    "devolucoes", "estourou_limite", "erro", "inicio",
]

# nome do sistema → sigla usada no id da execução
SIGLA = {"agente único": "S1", "roteamento": "S2", "verificador": "S3"}


def execucao_id(sistema, pid):
    return f"{SIGLA.get(sistema, sistema)}-{pid}"


def linha(p, r, inicio):
    """A linha do CSV de uma execução: p é a pergunta com gabarito (calcular()), r é o retorno de answer()."""
    falhas = checagem(p, r)
    rota_esperada = ROTA_ESPERADA[p["banco"]] if r["rota"] is not None else None
    return {
        "execucao": execucao_id(r["sistema"], p["id"]), "sistema": r["sistema"], "pergunta_id": p["id"],
        "categoria": p["categoria"], "banco": p["banco"], "pergunta": p["pergunta"], "resposta": r["resposta"],
        "checagem_ok": not falhas, "checagem_falhas": "; ".join(falhas),
        "trajetoria_ok": not trajetoria(p, r), "ferramentas_esperadas": " + ".join(p["ferramentas"]),
        "ferramentas": " | ".join(r["ferramentas"]),
        "rota": r["rota"], "rota_esperada": rota_esperada,
        # P11 e P12 aceitam SQL ou grafo (T46): rota sql nelas não é erro
        "rota_certa": None if r["rota"] is None else (
            r["rota"] == rota_esperada or (p["id"] in ("P11", "P12") and r["rota"] == "sql")),
        "segundos": r["segundos"], "chamadas_modelo": r["chamadas_modelo"],
        "chamadas_ferramenta": r["chamadas_ferramenta"], "erros_ferramenta": r["erros_ferramenta"],
        "tokens_entrada": r["tokens_entrada"], "tokens_saida": r["tokens_saida"],
        "tokens_total": r["tokens_entrada"] + r["tokens_saida"],
        **{k: r[k] for k in ("cpu_medio", "cpu_max", "ram_max_gb", "gpu_medio", "gpu_max", "gpu_mem_max_gb")},
        "devolucoes": r["devolucoes"], "estourou_limite": r["estourou_limite"], "erro": r["erro"], "inicio": inicio,
    }


def registro(p, r, inicio):
    """O registro completo de uma execução para o JSONL (sem as mensagens do LangGraph, que não viram JSON)."""
    return {"execucao": execucao_id(r["sistema"], p["id"]), "pergunta_id": p["id"], "inicio": inicio,
            "gabarito": p["texto"], **{k: v for k, v in r.items() if k != "mensagens"}}


def ja_feitas(caminho_csv):
    if not os.path.exists(caminho_csv):
        return set()
    with open(caminho_csv, encoding="utf-8", newline="") as f:
        return {l["execucao"] for l in csv.DictReader(f)}


async def avaliar(sistemas, gabarito, pasta, ids=None, mostrar=None):
    """Roda cada pergunta em cada sistema e grava em pasta/execucoes.csv e pasta/execucoes.jsonl.

    sistemas: instâncias de Sistema; gabarito: a lista de calcular(); ids: só estas perguntas (None = todas);
    mostrar: função chamada com (p, r, linha) depois de cada execução, para o notebook imprimir o progresso.
    Devolve quantas execuções rodaram agora."""
    os.makedirs(pasta, exist_ok=True)
    caminho_csv, caminho_jsonl = os.path.join(pasta, "execucoes.csv"), os.path.join(pasta, "execucoes.jsonl")
    feitas, novo = ja_feitas(caminho_csv), not os.path.exists(caminho_csv)
    perguntas = [p for p in gabarito if ids is None or p["id"] in ids]
    rodadas = 0
    with open(caminho_csv, "a", encoding="utf-8", newline="") as fc, open(caminho_jsonl, "a", encoding="utf-8") as fj:
        escritor = csv.DictWriter(fc, fieldnames=COLUNAS)
        if novo:
            escritor.writeheader()
        for p in perguntas:
            for s in sistemas:
                if execucao_id(s.nome, p["id"]) in feitas:
                    continue
                inicio = datetime.datetime.now().isoformat(timespec="seconds")
                r = await s.answer(p["pergunta"])
                l = linha(p, r, inicio)
                fj.write(json.dumps(registro(p, r, inicio), ensure_ascii=False, default=str) + "\n")
                escritor.writerow(l)
                fc.flush(), fj.flush()
                os.fsync(fc.fileno()), os.fsync(fj.fileno())    # no Drive, garante que a linha foi gravada
                rodadas += 1
                if mostrar:
                    mostrar(p, r, l)
    return rodadas


def ler_registros(pasta):
    """Os registros completos (execucoes.jsonl), um por execução; se uma execução aparecer duas vezes, vale a última."""
    regs = {}
    with open(os.path.join(pasta, "execucoes.jsonl"), encoding="utf-8") as f:
        for l in f:
            if l.strip():
                r = json.loads(l)
                regs[r["execucao"]] = r
    return list(regs.values())
