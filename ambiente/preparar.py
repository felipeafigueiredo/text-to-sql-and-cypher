"""Prepara uma sessão do Colab do zero para rodar os agentes: dados, bancos, servidor MCP, Ollama e bibliotecas.

Os notebooks 02 e 03 rodam cada um na sua máquina do Colab e chamam isto em vez de copiar células:

    from ambiente.preparar import preparar
    TEMPOS = preparar("/content/drive/MyDrive/yelp_recorte")

Cada etapa confere o que já existe e só faz o que falta: rodar de novo na mesma sessão leva segundos.
Só usa a biblioteca padrão, porque roda antes de as bibliotecas dos agentes estarem instaladas.
"""
import os
import shutil
import subprocess
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARQUIVOS = ["business.csv", "business_category.csv", "users.csv", "review.csv", "friends.csv", "reviewed.csv"]
DADOS = "/content/recorte"
AMBIENTE = {
    "YELP_PG_DSN": "host=localhost port=5432 dbname=yelp user=leitor password=leitor",
    "YELP_NEO4J_URI": "bolt://localhost:7687",
    "YELP_NEO4J_USUARIO": "neo4j",
    "YELP_NEO4J_SENHA": "yelp-santa-barbara",
    "YELP_SERVIDOR_LIBS": os.path.join(RAIZ, "servidor_libs"),
}


def etapa(nome, comando, tempos):
    """Roda um comando de shell mostrando a saída linha a linha e guarda quanto tempo levou.
    (O Colab não mostra o que um subprocesso escreve direto no terminal, por isso o print.)"""
    t = time.time()
    p = subprocess.Popen(comando, shell=True, cwd=RAIZ, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for linha in p.stdout:
        print(linha, end="")
    codigo = p.wait()
    tempos[nome] = round(time.time() - t, 1)
    print(f"⏱ {nome}: {tempos[nome]} s")
    if codigo != 0:
        raise RuntimeError(f"'{nome}' falhou (código {codigo}). Veja a saída acima.")


def bancos_carregados():
    """Os dois bancos já têm o recorte? (evita recarregar quando a célula roda de novo)"""
    try:
        import psycopg
        from neo4j import GraphDatabase
        with psycopg.connect(AMBIENTE["YELP_PG_DSN"], connect_timeout=3) as c:
            if c.execute("SELECT count(*) FROM business").fetchone()[0] != 3834:
                return False
        with GraphDatabase.driver(AMBIENTE["YELP_NEO4J_URI"], auth=("neo4j", AMBIENTE["YELP_NEO4J_SENHA"])) as d:
            return d.execute_query("MATCH ()-[r:FRIEND]->() RETURN count(r) AS n").records[0]["n"] == 377374
    except Exception:
        return False


def preparar(drive_dir, modelo="qwen3:8b", com_modelo=True):
    """Deixa a sessão pronta e devolve os tempos de cada etapa, em segundos."""
    tempos = {}
    os.chdir(RAIZ)
    if RAIZ not in sys.path:
        sys.path.insert(0, RAIZ)

    # 1 · o recorte, do Drive para o disco local
    if not all(os.path.exists(os.path.join(DADOS, a)) for a in ARQUIVOS):
        from google.colab import drive
        drive.mount("/content/drive")
        faltando = [a for a in ARQUIVOS if not os.path.exists(os.path.join(drive_dir, a))]
        if faltando:
            raise RuntimeError(f"Não achei {faltando} em {drive_dir}. Ajuste DRIVE_DIR ou suba os CSV.")
        t = time.time()
        os.makedirs(DADOS, exist_ok=True)
        for a in ARQUIVOS:
            shutil.copy(os.path.join(drive_dir, a), os.path.join(DADOS, a))
        tempos["copiar o recorte"] = round(time.time() - t, 1)
    print(f"✅ recorte em {DADOS}")

    # 2 · bibliotecas dos agentes e da carga (LangGraph, adaptador MCP, Ollama, psycopg, neo4j)
    etapa("bibliotecas dos agentes",
          f"pip -q install --disable-pip-version-check -r {os.path.join(RAIZ, 'agentes', 'requirements.txt')}", tempos)

    # 3 · PostgreSQL e Neo4j instalados, de pé e com o recorte carregado
    etapa("instalar e subir os bancos", f"bash ambiente/instalar_colab.sh bancos '{os.path.join(drive_dir, 'cache')}'", tempos)
    if bancos_carregados():
        print("✅ os dois bancos já têm o recorte")
    else:
        etapa("carregar os bancos", f"{sys.executable} dados/carga.py {DADOS}", tempos)

    # 4 · bibliotecas do servidor MCP, separadas (mcp 2.x no servidor, mcp 1.x nos agentes)
    etapa("instalar o servidor", "bash ambiente/instalar_colab.sh servidor", tempos)

    # 5 · Ollama e o modelo
    if com_modelo:
        etapa("Ollama e o modelo", f"bash ambiente/instalar_colab.sh ollama {modelo}", tempos)

    os.environ.update(AMBIENTE)
    os.environ["YELP_MODELO"] = modelo
    print(f"\n🚀 sessão pronta em {round(sum(tempos.values()))} s")
    return tempos
