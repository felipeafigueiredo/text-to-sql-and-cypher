"""A análise das 60 execuções: lê os arquivos do avaliador, do juiz e da revisão manual e monta as tabelas e os
gráficos do notebook 04. Só lê arquivos (não toca nos bancos nem nos modelos).

Arquivos (na pasta de resultados):
  execucoes.csv, execucoes.jsonl   avaliador (notebook 03)
  juiz.csv, juiz.jsonl             juiz (avaliacao/juiz.py)
  revisao_manual.csv               revisão humana dos vereditos (modelo gerado por modelo_revisao())
  ambiente.json                    hardware, modelo e versões (notebook 03)

Acerto: veredito "correta"; "parcial" conta como erro. O acerto que vale na tabela é o da revisão humana quando ela
existe para a execução, e o do juiz quando não existe (coluna acerto_final, com a fonte em fonte_acerto).
"""
import json
import os

import pandas as pd

SISTEMAS = ["agente único", "roteamento", "verificador"]
VEREDITOS = ["correta", "parcial", "incorreta", "falhou"]
FERRAMENTAS_DE_BANCO = ("consultar_sql", "consultar_grafo")       # as que recebem consulta escrita pelo modelo
CATEGORIAS = ["filtro", "item", "comparação", "cálculo", "capciosa", "inexistente", "fora do escopo"]
BANCOS = ["sql", "cypher", "ambos", "nenhum"]

# paleta de referência (skill de visualização): os 3 primeiros slots categóricos são validados todos contra todos
COR_SISTEMA = {"agente único": "#2a78d6", "roteamento": "#eb6834", "verificador": "#1baf7a"}
COR_VEREDITO = {"correta": "#0ca30c", "parcial": "#fab219", "incorreta": "#d03b3b", "falhou": "#c3c2b7"}
SIMBOLO = {"correta": "✓", "parcial": "~", "incorreta": "✗", "falhou": "?"}
SUPERFICIE, TEXTO, TEXTO_2, GRADE = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"


# ─── leitura ─────────────────────────────────────────────────────────────────

def _jsonl(caminho):
    if not os.path.exists(caminho):
        return []
    with open(caminho, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def carregar(pasta):
    """Os arquivos da pasta. juiz e revisao são None quando ainda não existem."""
    def csv(nome):
        c = os.path.join(pasta, nome)
        return pd.read_csv(c, keep_default_na=True) if os.path.exists(c) else None
    ex = csv("execucoes.csv")
    if ex is None:
        raise FileNotFoundError(f"Não achei execucoes.csv em {pasta}.")
    ex = ex.drop_duplicates("execucao", keep="last")
    juiz = csv("juiz.csv")
    if juiz is not None:
        juiz = juiz.drop_duplicates("execucao", keep="last")
    revisao = None
    c = os.path.join(pasta, "revisao_manual.csv")
    if os.path.exists(c):
        # o Excel em português salva com ';': sep=None descobre o separador
        revisao = pd.read_csv(c, sep=None, engine="python", dtype=str, encoding="utf-8-sig")
        revisao["veredito_humano"] = revisao["veredito_humano"].fillna("").str.strip().str.lower()
        revisao = revisao[revisao["veredito_humano"].isin(VEREDITOS[:3])]
        revisao = revisao if len(revisao) else None
    amb = os.path.join(pasta, "ambiente.json")
    return dict(ex=ex, juiz=juiz, revisao=revisao, registros=_jsonl(os.path.join(pasta, "execucoes.jsonl")),
                ambiente=json.load(open(amb, encoding="utf-8")) if os.path.exists(amb) else None)


def consultas_validas(registros):
    """Por execução: consultas escritas pelo modelo (SQL e Cypher), quantas rodaram sem erro e quantas tentativas
    até a primeira que rodou. É a 'valid query rate' do enunciado do problema."""
    linhas = []
    for r in registros:
        cs = [c for c in r.get("consultas", []) if c["ferramenta"] in FERRAMENTAS_DE_BANCO]
        primeira_ok = next((i + 1 for i, c in enumerate(cs) if not c["erro"]), None)
        linhas.append(dict(execucao=r["execucao"], consultas_banco=len(cs), consultas_banco_erro=sum(c["erro"] for c in cs),
                           tentativas_ate_valida=primeira_ok))
    return pd.DataFrame(linhas, columns=["execucao", "consultas_banco", "consultas_banco_erro", "tentativas_ate_valida"])


def por_execucao(dados):
    """Uma linha por execução com tudo junto: métricas, checagem, veredito do juiz, veredito humano e acerto final."""
    df = dados["ex"].copy()
    df["checagem_ok"] = df["checagem_ok"].astype(bool)
    df["trajetoria_ok"] = df["trajetoria_ok"].astype(bool)
    if dados["juiz"] is not None:
        final = dados["juiz"][dados["juiz"]["qual"] == "final"][["execucao", "veredito", "justificativa"]]
        df = df.merge(final.rename(columns={"veredito": "veredito_juiz", "justificativa": "justificativa_juiz"}),
                      on="execucao", how="left")
    else:
        df["veredito_juiz"], df["justificativa_juiz"] = None, None
    if dados["revisao"] is not None:
        df = df.merge(dados["revisao"][["execucao", "veredito_humano"]], on="execucao", how="left")
    else:
        df["veredito_humano"] = None
    df["acerto_juiz"] = df["veredito_juiz"].map(lambda v: None if pd.isna(v) or v == "falhou" else v == "correta")
    df["acerto_humano"] = df["veredito_humano"].map(lambda v: None if pd.isna(v) else v == "correta")
    df["acerto_final"] = df["acerto_humano"].where(df["acerto_humano"].notna(), df["acerto_juiz"])
    df["fonte_acerto"] = df["acerto_humano"].notna().map({True: "humano", False: "juiz"}).where(df["acerto_final"].notna())
    if dados["registros"]:
        df = df.merge(consultas_validas(dados["registros"]), on="execucao", how="left")
    df["sistema"] = pd.Categorical(df["sistema"], SISTEMAS, ordered=True)
    return df.sort_values(["pergunta_id", "sistema"]).reset_index(drop=True)


# ─── tabelas ─────────────────────────────────────────────────────────────────

def _taxa(s):
    s = s.dropna()
    return s.astype(float).mean() if len(s) else float("nan")


def tabela_comparativa(df):
    """A tabela principal: uma linha por sistema, com todas as métricas obrigatórias e as do tema."""
    g = df.groupby("sistema", observed=True)
    t = pd.DataFrame({
        "execuções": g.size(),
        "acerto (final)": g["acerto_final"].apply(_taxa),
        "acerto (juiz)": g["acerto_juiz"].apply(_taxa),
        "acerto (humano)": g["acerto_humano"].apply(_taxa),
        "checagem automática": g["checagem_ok"].mean(),
        "trajetória": g["trajetoria_ok"].mean(),
        "latência média (s)": g["segundos"].mean(),
        "latência mediana (s)": g["segundos"].median(),
        "latência p95 (s)": g["segundos"].quantile(0.95),
        "tokens entrada (média)": g["tokens_entrada"].mean(),
        "tokens saída (média)": g["tokens_saida"].mean(),
        "tokens total (soma)": g["tokens_total"].sum(),
        "chamadas ao modelo (média)": g["chamadas_modelo"].mean(),
        "chamadas a ferramentas (média)": g["chamadas_ferramenta"].mean(),
        "CPU média (%)": g["cpu_medio"].mean(),
        "RAM máx (GB)": g["ram_max_gb"].max(),
        "GPU média (%)": g["gpu_medio"].mean(),
        "VRAM máx (GB)": g["gpu_mem_max_gb"].max(),
    })
    if "consultas_banco" in df:
        t["consultas válidas"] = g.apply(lambda x: 1 - x["consultas_banco_erro"].sum() / max(x["consultas_banco"].sum(), 1),
                                         include_groups=False)
        t["tentativas até a 1ª válida (média)"] = g["tentativas_ate_valida"].mean()
    t["limite de passos estourado"] = g["estourou_limite"].apply(lambda s: s.astype(str).eq("True").sum())
    t["erros de execução"] = g["erro"].apply(lambda s: s.notna().sum())
    return t.T


TAXAS = ("acerto", "checagem", "trajetória", "consultas válidas")


def formatar(t):
    """A tabela comparativa para leitura: taxas em %, o resto arredondado."""
    def f(linha, v):
        if pd.isna(v):
            return "—"
        if linha.startswith(TAXAS):
            return f"{v:.0%}"
        return f"{v:,.0f}".replace(",", ".") if abs(v) >= 100 else f"{v:.2f}".rstrip("0").rstrip(".")
    return pd.DataFrame({c: [f(i, v) for i, v in t[c].items()] for c in t.columns}, index=t.index)


def pct(t, exceto=("perguntas", "execuções")):
    """Uma tabela (ou série) de taxas em %, sem depender do jinja2 (o .style do pandas exige)."""
    f = lambda v: "—" if pd.isna(v) else f"{v:.0%}"
    if isinstance(t, pd.Series):
        return t.map(f).to_frame(t.name)
    return pd.DataFrame({c: t[c] if c in exceto else t[c].map(f) for c in t.columns}, index=t.index)


def fatos(df, dados):
    """Os números que a conclusão precisa, em frases curtas (para conferir; a conclusão é escrita pelo grupo)."""
    t = tabela_comparativa(df)
    acerto = t.loc["acerto (final)"].dropna()
    linhas = []
    if len(acerto):
        melhor = acerto.idxmax()
        linhas.append("Acerto final: " + ", ".join(f"{s} {v:.0%}" for s, v in acerto.items())
                      + f". Melhor: {melhor}.")
        base = acerto.get("agente único")
        for s in ("roteamento", "verificador"):
            if s in acerto and base is not None:
                dt = t.at["tokens total (soma)", s] / t.at["tokens total (soma)", "agente único"] - 1
                dl = t.at["latência mediana (s)", s] / t.at["latência mediana (s)", "agente único"] - 1
                linhas.append(f"{s} × agente único: acerto {(acerto[s] - base) * 100:+.0f} pontos, tokens {dt:+.0%}, "
                              f"latência mediana {dl:+.0%}.")
    por_banco = acerto_por(df, "banco", BANCOS)
    for b, r in por_banco.iterrows():
        linhas.append(f"Perguntas {b} ({int(r['perguntas'])}): " + ", ".join(
            f"{s} {r[s]:.0%}" for s in SISTEMAS if s in r and pd.notna(r[s])) + ".")
    a, _ = roteamento(df)
    if a is not None:
        s2 = df[df["sistema"] == "roteamento"]["rota_certa"].astype(str).eq("True").mean()
        linhas.append(f"Roteamento: rota certa em {s2:.0%} das perguntas.")
    v = verificador(dados)
    if v is not None and len(v):
        c = v["efeito"].value_counts()
        linhas.append(f"Verificador: {len(v)} resposta(s) devolvida(s); " + ", ".join(f"{k}: {n}" for k, n in c.items()) + ".")
    pares, _ = concordancia(df)
    for par, r in pares.iterrows():
        if r["execuções"]:
            linhas.append(f"Concordância {par}: {r['concordância']:.0%} em {int(r['execuções'])} execuções (kappa {r['kappa']:.2f}).")
    return linhas


def acerto_por(df, coluna, ordem):
    """Acerto final por sistema em cada valor de uma coluna (categoria ou banco), com o número de perguntas."""
    t = df.pivot_table(index=coluna, columns="sistema", values="acerto_final", aggfunc=_taxa, observed=True)
    t["perguntas"] = df.groupby(coluna)["pergunta_id"].nunique()
    return t.reindex([o for o in ordem if o in t.index])


def mapa_perguntas(df, coluna="veredito_juiz"):
    """Pergunta × sistema com o veredito (do juiz, por padrão), mais a categoria e o banco da pergunta."""
    t = df.pivot_table(index="pergunta_id", columns="sistema", values=coluna, aggfunc="first", observed=True)
    info = df.drop_duplicates("pergunta_id").set_index("pergunta_id")[["categoria", "banco", "pergunta"]]
    return info.join(t)


def roteamento(df):
    """Sistema 2: acerto de rota por banco esperado e a matriz rota esperada × rota escolhida."""
    s2 = df[df["sistema"] == "roteamento"]
    if s2["rota"].isna().all():
        return None, None
    acerto = s2.groupby("banco")["rota_certa"].apply(lambda s: s.astype(str).eq("True").mean())
    matriz = pd.crosstab(s2["rota_esperada"], s2["rota"], margins=True, margins_name="total")
    return acerto, matriz


def verificador(dados):
    """Sistema 3: quantas respostas o verificador devolveu e como o veredito do juiz mudou (anterior → final)."""
    j = dados["juiz"]
    if j is None:
        return None
    ant = j[j["qual"] == "anterior"].assign(execucao=lambda d: d["execucao"].str.removesuffix("-anterior"))
    final = j[j["qual"] == "final"][["execucao", "veredito"]]
    m = ant[["execucao", "pergunta_id", "veredito"]].merge(final, on="execucao", suffixes=("_antes", "_depois"))
    m["mudou"] = m["veredito_antes"] != m["veredito_depois"]
    m["efeito"] = m.apply(lambda r: "melhorou" if r["veredito_depois"] == "correta" and r["veredito_antes"] != "correta"
                          else "piorou" if r["veredito_antes"] == "correta" and r["veredito_depois"] != "correta"
                          else "mudou sem mudar o acerto" if r["mudou"] else "não mudou", axis=1)
    return m


def kappa(a, b):
    """Kappa de Cohen entre duas listas de rótulos (concordância além do acaso)."""
    a, b = list(a), list(b)
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(x == y for x, y in zip(a, b)) / n
    rot = set(a) | set(b)
    pe = sum((a.count(r) / n) * (b.count(r) / n) for r in rot)
    return float("nan") if pe == 1 else (po - pe) / (1 - pe)


def concordancia(df):
    """Concordância, em acerto (certo/errado), entre humano, juiz e checagem automática, só nas execuções que têm os
    dois julgamentos comparados. Devolve a tabela de pares e as matrizes juiz × humano (com os 3 vereditos)."""
    fontes = {"humano": df["acerto_humano"], "juiz": df["acerto_juiz"], "checagem": df["checagem_ok"].astype(object)}
    linhas = []
    for x, y in [("juiz", "humano"), ("checagem", "humano"), ("checagem", "juiz")]:
        ok = fontes[x].notna() & fontes[y].notna()
        a, b = fontes[x][ok].astype(bool), fontes[y][ok].astype(bool)
        linhas.append({"par (A × B)": f"{x} × {y}", "execuções": int(ok.sum()),
                       "concordância": (a == b).mean() if ok.any() else float("nan"), "kappa": kappa(a, b),
                       "A certo, B errado": int((a & ~b).sum()), "A errado, B certo": int((~a & b).sum())})
    pares = pd.DataFrame(linhas).set_index("par (A × B)")
    ambos = df["veredito_juiz"].notna() & df["veredito_humano"].notna()
    matriz = pd.crosstab(df.loc[ambos, "veredito_juiz"], df.loc[ambos, "veredito_humano"],
                         rownames=["juiz"], colnames=["humano"]) if ambos.any() else None
    return pares, matriz


def discordancias(df):
    """As execuções em que humano e juiz discordam no acerto, para discutir na análise."""
    d = df[df["acerto_humano"].notna() & df["acerto_juiz"].notna() & (df["acerto_humano"] != df["acerto_juiz"])]
    return d[["execucao", "categoria", "veredito_juiz", "veredito_humano", "resposta", "justificativa_juiz"]]


def custo_do_juiz(dados):
    j = dados["juiz"]
    if j is None:
        return None
    return pd.Series({"julgamentos": len(j), "consultas do juiz": j["consultas_juiz"].sum(),
                      "julgamentos com consulta": (j["consultas_juiz"] > 0).sum(),
                      "tokens de entrada": j["tokens_entrada_juiz"].sum(), "tokens de saída": j["tokens_saida_juiz"].sum(),
                      "custo equivalente (US$)": j["custo_usd_juiz"].sum(), "tempo total (min)": j["segundos_juiz"].sum() / 60,
                      "modelo": ", ".join(sorted(j["modelo_juiz"].dropna().unique()))})


# ─── revisão manual ──────────────────────────────────────────────────────────

def modelo_revisao(pasta, dados):
    """Cria revisao_manual.csv para o grupo preencher (veredito_humano: correta, parcial ou incorreta; observacao
    livre), com pergunta, gabarito, resposta e o veredito do juiz ao lado. Não sobrescreve um arquivo existente."""
    caminho = os.path.join(pasta, "revisao_manual.csv")
    if os.path.exists(caminho):
        return caminho, False
    regs = {r["execucao"]: r for r in dados["registros"]}
    df = por_execucao(dados)
    m = pd.DataFrame({
        "execucao": df["execucao"], "pergunta_id": df["pergunta_id"], "sistema": df["sistema"].astype(str),
        "categoria": df["categoria"], "pergunta": df["pergunta"],
        "gabarito": df["execucao"].map(lambda e: regs.get(e, {}).get("gabarito", "")),
        "resposta": df["resposta"], "checagem_ok": df["checagem_ok"], "veredito_juiz": df["veredito_juiz"],
        "justificativa_juiz": df["justificativa_juiz"], "veredito_humano": "", "observacao": ""})
    m.to_csv(caminho, index=False, encoding="utf-8-sig")       # com BOM: o Excel abre os acentos certos
    return caminho, True


# ─── gráficos ────────────────────────────────────────────────────────────────

def _estilo():
    import matplotlib as mpl
    mpl.rcParams.update({
        "figure.facecolor": SUPERFICIE, "axes.facecolor": SUPERFICIE, "savefig.facecolor": SUPERFICIE,
        "axes.edgecolor": GRADE, "axes.labelcolor": TEXTO_2, "xtick.color": TEXTO_2, "ytick.color": TEXTO_2,
        "text.color": TEXTO, "axes.titlecolor": TEXTO, "axes.titleweight": "bold", "axes.titlesize": 12,
        "axes.titlelocation": "left", "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRADE, "grid.linewidth": 0.8, "axes.axisbelow": True,
        "font.size": 10, "legend.frameon": False, "figure.dpi": 110, "savefig.dpi": 160, "savefig.bbox": "tight"})


def _salvar(fig, pasta, nome):
    if pasta:
        os.makedirs(pasta, exist_ok=True)
        fig.savefig(os.path.join(pasta, nome))
    return fig


def grafico_acerto_custo(df, pasta=None):
    """Acerto × custo, em dois painéis com a mesma escala de acerto: tokens por pergunta e latência mediana.
    Dois painéis, e não dois eixos no mesmo gráfico: as duas medidas de custo têm escalas diferentes."""
    import matplotlib.pyplot as plt
    _estilo()
    g = df.groupby("sistema", observed=True).agg(acerto=("acerto_final", _taxa), tokens=("tokens_total", "mean"),
                                                 latencia=("segundos", "median"))
    fig, eixos = plt.subplots(1, 2, figsize=(10, 4), sharey=True)
    for ax, col, rotulo in [(eixos[0], "tokens", "tokens por pergunta (média, entrada + saída)"),
                            (eixos[1], "latencia", "latência por pergunta (mediana, s)")]:
        for s, r in g.iterrows():
            ax.scatter(r[col], r["acerto"] * 100, s=110, color=COR_SISTEMA[s], edgecolor=SUPERFICIE, linewidth=2,
                       zorder=3, label=s)
            ax.annotate(f"{s}\n{r['acerto']:.0%}", (r[col], r["acerto"] * 100), xytext=(9, 0),
                        textcoords="offset points", va="center", fontsize=9, color=TEXTO)
        ax.set_xlabel(rotulo)
        lo, hi = g[col].min(), g[col].max()
        folga = (hi - lo) * 0.35 or hi * 0.2 or 1
        ax.set_xlim(max(0, lo - folga * 0.5), hi + folga * 1.4)
    eixos[0].set_ylabel("acerto (%)")
    eixos[0].set_ylim(0, 100)
    eixos[0].set_title("Acerto × custo por sistema")
    eixos[0].legend(loc="lower left", fontsize=9)
    fig.tight_layout()
    return _salvar(fig, pasta, "acerto_x_custo.png")


def grafico_acerto_por(df, coluna, ordem, titulo, nome, pasta=None):
    """Barras agrupadas: acerto final de cada sistema em cada categoria (ou banco)."""
    import matplotlib.pyplot as plt
    _estilo()
    t = acerto_por(df, coluna, ordem)
    n = len(t)
    fig, ax = plt.subplots(figsize=(max(6, 1.3 * n + 2), 3.8))
    larg = 0.26
    for i, s in enumerate(SISTEMAS):
        if s not in t:
            continue
        xs = [k + (i - 1) * (larg + 0.02) for k in range(n)]
        ax.bar(xs, t[s] * 100, width=larg, color=COR_SISTEMA[s], label=s, edgecolor=SUPERFICIE, linewidth=1)
        for x, v in zip(xs, t[s]):          # barra de 0% não aparece: escreve o zero, para não parecer dado faltando
            if pd.notna(v) and v == 0:
                ax.text(x, 1.5, "0%", ha="center", va="bottom", fontsize=8, color=TEXTO_2)
    ax.set_xticks(range(n), [f"{c}\n({p} perg.)" for c, p in zip(t.index, t["perguntas"])])
    ax.set_ylim(0, 105)
    ax.set_ylabel("acerto (%)")
    ax.grid(axis="x", visible=False)
    ax.set_title(titulo)
    ax.legend(ncol=3, loc="upper left", bbox_to_anchor=(0, -0.18), fontsize=9)
    fig.tight_layout()
    return _salvar(fig, pasta, nome)


def grafico_mapa(df, coluna="veredito_juiz", pasta=None, nome="vereditos_por_pergunta.png"):
    """Pergunta × sistema: uma célula por execução, com a cor e o símbolo do veredito (a cor nunca sozinha)."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch, Rectangle
    _estilo()
    m = mapa_perguntas(df, coluna)
    fig, ax = plt.subplots(figsize=(7.2, 0.34 * len(m) + 1.6))
    for i, (pid, r) in enumerate(m.iterrows()):
        for j, s in enumerate(SISTEMAS):
            v = r.get(s)
            v = v if isinstance(v, str) else "falhou"
            ax.add_patch(Rectangle((j + 0.04, i + 0.06), 0.92, 0.88, color=COR_VEREDITO[v], linewidth=0))
            ax.text(j + 0.5, i + 0.5, SIMBOLO[v], ha="center", va="center", fontsize=10,
                    color=TEXTO if v in ("parcial", "falhou") else "#ffffff", fontweight="bold")
    ax.set_xlim(0, 3)
    ax.set_ylim(len(m), 0)
    ax.set_xticks([j + 0.5 for j in range(3)], SISTEMAS)
    ax.xaxis.tick_top()
    ax.set_yticks([i + 0.5 for i in range(len(m))], [f"{p} · {r['categoria']} · {r['banco']}" for p, r in m.iterrows()],
                  fontsize=9)
    ax.grid(False)
    for lado in ("left", "bottom", "top"):
        ax.spines[lado].set_visible(False)
    ax.tick_params(length=0)
    fonte = "juiz" if coluna == "veredito_juiz" else "revisão humana"
    ax.set_title(f"Veredito por pergunta ({fonte})", pad=28)
    ax.legend(handles=[Patch(color=COR_VEREDITO[v], label=f"{SIMBOLO[v]} {v}") for v in VEREDITOS],
              ncol=4, loc="upper left", bbox_to_anchor=(0, 0), fontsize=9)
    fig.tight_layout()
    return _salvar(fig, pasta, nome)


def grafico_latencia(df, pasta=None):
    """Distribuição da latência por sistema: cada execução é um ponto; a barra marca a mediana e o traço o p95."""
    import matplotlib.pyplot as plt
    import numpy as np
    _estilo()
    fig, ax = plt.subplots(figsize=(7, 3.4))
    rng = np.random.default_rng(0)
    for j, s in enumerate(SISTEMAS):
        v = df.loc[df["sistema"] == s, "segundos"].dropna()
        if v.empty:
            continue
        ax.scatter(v, j + rng.uniform(-0.18, 0.18, len(v)), s=22, color=COR_SISTEMA[s], alpha=0.75,
                   edgecolor=SUPERFICIE, linewidth=0.8, zorder=3)
        med, p95 = v.median(), v.quantile(0.95)
        ax.plot([med, med], [j - 0.3, j + 0.3], color=TEXTO, linewidth=2, zorder=4)
        ax.plot([p95, p95], [j - 0.2, j + 0.2], color=TEXTO_2, linewidth=1.2, linestyle=(0, (2, 2)), zorder=4)
        ax.annotate(f"mediana {med:.1f} s · p95 {p95:.1f} s", (v.max(), j), xytext=(8, 0), textcoords="offset points",
                    va="center", fontsize=8.5, color=TEXTO_2)
    ax.set_yticks(range(3), SISTEMAS)
    ax.set_ylim(2.6, -0.6)
    ax.set_xlabel("latência por pergunta (s)")
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, df["segundos"].max() * 1.45)
    ax.set_title("Latência por execução")
    fig.tight_layout()
    return _salvar(fig, pasta, "latencia.png")
