"""Validação do servidor MCP sem LLM: um cliente MCP lista tudo e chama cada ferramenta, inclusive com erros.

Sobe o servidor como subprocesso (stdio), como um agente faria, e confere:
  - os requisitos mínimos: 4+ ferramentas (uma de cálculo), 1+ resource, 1+ prompt;
  - chamadas corretas devolvem os números conhecidos do recorte de Santa Barbara;
  - chamadas incorretas devolvem erro de execução (isError) com mensagem que diz o que corrigir;
  - a escrita é bloqueada nos dois bancos.

Grava o relatório em resultados/validacao_servidor.md e sai com código 1 se algum caso falhar.

Uso (com as bibliotecas do servidor no caminho, ex.: PYTHONPATH=servidor_libs):
    python servidor/validar.py
As variáveis YELP_* do ambiente são repassadas ao servidor.
"""
import asyncio
import datetime
import json
import os
import platform
import sys
import time
from pathlib import Path

import fastmcp
from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport

RAIZ = Path(__file__).resolve().parent.parent
SERVIDOR = RAIZ / "servidor" / "server.py"
RELATORIO = RAIZ / "resultados" / "validacao_servidor.md"
CONTAGENS = {"business": 3834, "business_category": 18194, "users": 130392, "review": 269703,
             "FRIEND": 377374, "REVIEWED": 262936}                 # o recorte de Santa Barbara (dados/recorte.py)


def ambiente_do_servidor():
    env = {k: v for k, v in os.environ.items() if k.startswith(("YELP_", "PYTHON", "PATH", "SYSTEMROOT", "HOME"))}
    env.update(FASTMCP_LOG_LEVEL="CRITICAL", FASTMCP_SHOW_SERVER_BANNER="false")   # os erros esperados não poluem a saída
    return env


def texto(res):
    return "".join(getattr(b, "text", "") for b in res.content)


async def validar():
    casos = []                                   # (grupo, descrição, chamada, passou, detalhe)

    def registrar(grupo, descricao, chamada, passou, detalhe):
        casos.append((grupo, descricao, chamada, passou, detalhe))
        print(f"  {'✅' if passou else '❌'} [{grupo}] {descricao}\n      {detalhe[:220]}")

    transporte = PythonStdioTransport(SERVIDOR, env=ambiente_do_servidor(), python_cmd=sys.executable)
    async with Client(transporte, timeout=60) as cliente:
        # ── 1 · o que o servidor oferece ──
        ferramentas = await cliente.list_tools()
        resources = await cliente.list_resources()
        prompts = await cliente.list_prompts()
        print("🔧 ferramentas:", [t.name for t in ferramentas])
        print("📄 resources:  ", [str(r.uri) for r in resources])
        print("💬 prompts:    ", [p.name for p in prompts])
        nomes = {t.name for t in ferramentas}
        registrar("requisitos", "pelo menos 4 ferramentas", "tools/list", len(ferramentas) >= 4, f"{len(ferramentas)} ferramentas")
        registrar("requisitos", "ferramenta de cálculo (comparar_negocios)", "tools/list", "comparar_negocios" in nomes,
                  "comparar_negocios calcula o vencedor de cada critério")
        registrar("requisitos", "pelo menos 1 resource", "resources/list", len(resources) >= 1, f"{len(resources)} resources")
        registrar("requisitos", "pelo menos 1 prompt", "prompts/list", len(prompts) >= 1, f"{len(prompts)} prompts")
        sem_descricao = [t.name for t in ferramentas if not (t.description or "").strip()]
        registrar("requisitos", "toda ferramenta tem descrição e schema de entrada", "tools/list",
                  not sem_descricao and all(t.inputSchema.get("properties") for t in ferramentas),
                  f"sem descrição: {sem_descricao or 'nenhuma'}")

        for r in resources:
            conteudo = (await cliente.read_resource(str(r.uri)))[0].text
            registrar("resource", f"ler {r.uri}", f"resources/read {r.uri}", len(conteudo) > 200,
                      conteudo.splitlines()[0])
        p = await cliente.get_prompt("analisar_negocio", {"nome": "Brophy Bros"})
        registrar("prompt", "analisar_negocio preenche o nome", "prompts/get analisar_negocio(nome='Brophy Bros')",
                  "Brophy Bros" in p.messages[0].content.text, p.messages[0].content.text[:120])

        async def chamar(nome, args):
            t = time.time()
            res = await cliente.call_tool(nome, args, raise_on_error=False)
            return res.is_error, texto(res), round(time.time() - t, 2)

        def json_ou_nada(t):
            try:
                return json.loads(t)
            except json.JSONDecodeError:
                return {}

        async def ok(grupo, descricao, nome, args, conferir):
            erro, t, s = await chamar(nome, args)
            d = json_ou_nada(t)
            try:
                passou, detalhe = (False, f"veio erro: {t}") if erro else conferir(d)
            except Exception as e:                   # a conferência quebrou: o formato não é o esperado
                passou, detalhe = False, f"formato inesperado ({e}): {t[:200]}"
            registrar(grupo, descricao, f"{nome}({json.dumps(args, ensure_ascii=False)})", passou, f"{detalhe} · {s} s")
            return d

        async def falha(grupo, descricao, nome, args, deve_conter):
            erro, t, s = await chamar(nome, args)
            passou = erro and all(x.lower() in t.lower() for x in deve_conter)
            registrar(grupo, descricao, f"{nome}({json.dumps(args, ensure_ascii=False)})", passou,
                      f"isError={erro}: {' '.join(t.split())[:300]} · {s} s")

        # ── 2 · chamadas corretas ──
        print("\n── chamadas corretas ──")
        for tabela in ["business", "business_category", "users", "review"]:
            await ok("consultar_sql", f"contar {tabela}", "consultar_sql", {"consulta": f"SELECT count(*) FROM {tabela}"},
                     lambda d, t=tabela: (d["linhas"][0][0] == CONTAGENS[t], f"{d['linhas'][0][0]} (esperado {CONTAGENS[t]})"))
        await ok("consultar_sql", "resultado grande vem truncado e com o total", "consultar_sql",
                 {"consulta": "SELECT name FROM business ORDER BY name"},
                 lambda d: (d["truncado"] and d["total_linhas"] == 3834 and d["linhas_devolvidas"] == 50 and "aviso" in d,
                            f"truncado={d['truncado']}, total={d['total_linhas']}, devolvidas={d['linhas_devolvidas']}"))
        for rel in ["FRIEND", "REVIEWED"]:
            await ok("consultar_grafo", f"contar {rel}", "consultar_grafo",
                     {"consulta": f"MATCH ()-[r:{rel}]->() RETURN count(r) AS n"},
                     lambda d, r=rel: (d["linhas"][0]["n"] == CONTAGENS[r], f"{d['linhas'][0]['n']} (esperado {CONTAGENS[r]})"))
        await ok("consultar_grafo", "lista grande vem truncada e com o total", "consultar_grafo",
                 {"consulta": "MATCH (b:Business) RETURN b.name AS nome"},
                 lambda d: (d["truncado"] and d["total_linhas"] == 3834, f"truncado={d['truncado']}, total={d['total_linhas']}"))
        await ok("consultar_grafo", "propriedade inexistente gera aviso de esquema (nota não está no grafo)", "consultar_grafo",
                 {"consulta": "MATCH (u:User)-[r:REVIEWED]->(b:Business) RETURN avg(r.stars) AS media"},
                 lambda d: (any("SQL" in a for a in d.get("avisos_de_esquema", [])), str(d.get("avisos_de_esquema"))))

        d = await ok("buscar_entidade", "negócio pelo nome", "buscar_entidade", {"nome": "Brophy Bros", "tipo": "negocio"},
                     lambda d: (d["encontrados"] >= 1, f"{d['encontrados']} encontrado(s): {[x['name'] for x in d['resultados']]}"))
        brophy = d["resultados"][0]["business_id"] if d.get("resultados") else "x" * 22
        await ok("buscar_entidade", "categoria em inglês", "buscar_entidade", {"nome": "Mexican", "tipo": "categoria"},
                 lambda d: (d["resultados"][0]["category"] == "Mexican" and d["resultados"][0]["negocios"] == 118,
                            str(d["resultados"][:3])))
        await ok("buscar_entidade", "homônimos são sinalizados", "buscar_entidade", {"nome": "Starbucks", "tipo": "negocio"},
                 lambda d: ("homonimos" in d, d.get("homonimos", "sem aviso de homônimos")))
        await ok("buscar_entidade", "item inexistente: vazio, sem erro", "buscar_entidade",
                 {"nome": "Churrascaria Potiguar Imaginária", "tipo": "negocio"},
                 lambda d: (d["encontrados"] == 0 and "mensagem" in d, d.get("mensagem", "")))
        await ok("buscar_entidade", "categoria em português sugere o nome em inglês", "buscar_entidade",
                 {"nome": "mexicano", "tipo": "categoria"},
                 lambda d: (d["encontrados"] == 0 and "inglês" in d.get("mensagem", ""),
                            f"{d.get('mensagem')} sugestões={d.get('nomes_parecidos')}"))
        await ok("detalhes_negocio", "detalhes de um negócio", "detalhes_negocio", {"business_id": brophy},
                 lambda d: (d["business_id"] == brophy and d["categorias"] and d["avaliacoes_no_banco"]["quantidade"] > 0,
                            f"{d['name']}, {d['stars']} estrelas, {len(d['categorias'])} categorias, "
                            f"{d['avaliacoes_no_banco']['quantidade']} avaliações no banco"))
        dois = json_ou_nada((await chamar("consultar_sql", {"consulta":
              "SELECT business_id FROM business ORDER BY review_count DESC LIMIT 2"}))[1])["linhas"]
        ids = [brophy] + [x[0] for x in dois if x[0] != brophy][:1]
        await ok("comparar_negocios", "cálculo do melhor em cada critério", "comparar_negocios", {"business_ids": ids},
                 lambda d: (len(d["negocios"]) == 2 and all(k in d["melhor"] for k in
                            ["maior_nota", "mais_avaliacoes", "mais_checkins", "mais_barato", "maior_media_no_banco"]),
                            json.dumps(d["melhor"]["maior_nota"]) + " " + json.dumps(d["melhor"]["mais_avaliacoes"])))
        await ok("comparar_negocios", "id inexistente aparece em 'ausentes'", "comparar_negocios",
                 {"business_ids": ids + ["AAAAAAAAAAAAAAAAAAAAAA"]},
                 lambda d: (d["ausentes"] == ["AAAAAAAAAAAAAAAAAAAAAA"], f"ausentes={d['ausentes']}"))

        # ── 3 · chamadas incorretas: erro claro, o loop continua ──
        print("\n── chamadas incorretas ──")
        await falha("erro", "SQL: comando de escrita", "consultar_sql", {"consulta": "DELETE FROM review"}, ["SELECT"])
        await falha("erro", "SQL: escrita disfarçada de WITH é barrada pelo banco", "consultar_sql",
                    {"consulta": "WITH x AS (DELETE FROM review RETURNING *) SELECT count(*) FROM x"}, ["somente leitura"])
        await falha("erro", "SQL: tabela inexistente lista as tabelas certas", "consultar_sql",
                    {"consulta": "SELECT * FROM usuarios"}, ["users"])
        await falha("erro", "SQL: coluna inexistente lista as colunas", "consultar_sql",
                    {"consulta": "SELECT rating FROM business"}, ["coluna inexistente", "stars"])
        await falha("erro", "SQL: sintaxe", "consultar_sql", {"consulta": "SELECT name FROM business WHERE"}, ["sintaxe"])
        await falha("erro", "SQL: dois comandos", "consultar_sql", {"consulta": "SELECT 1; SELECT 2"}, ["único"])
        await falha("erro", "SQL: consulta vazia", "consultar_sql", {"consulta": "  "}, ["vazia"])
        await falha("erro", "SQL: argumento com tipo errado", "consultar_sql", {"consulta": 123}, ["consulta"])
        await falha("erro", "SQL: argumento faltando", "consultar_sql", {}, ["consulta"])
        await falha("erro", "SQL: consulta lenta é cancelada", "consultar_sql",
                    {"consulta": "SELECT count(*) FROM review a, review b"}, ["cancelada"])
        await falha("erro", "Cypher: comando de escrita", "consultar_grafo", {"consulta": "CREATE (n:User {name: 'x'})"},
                    ["somente leitura"])
        await falha("erro", "Cypher: sintaxe", "consultar_grafo", {"consulta": "MATCH (u:User RETURN u"}, ["sintaxe"])
        await falha("erro", "busca: tipo fora da lista", "buscar_entidade", {"nome": "Pizza", "tipo": "restaurante"},
                    ["tipo"])
        await falha("erro", "busca: nome curto demais", "buscar_entidade", {"nome": "a", "tipo": "negocio"}, ["2 caracteres"])
        await falha("erro", "detalhes: id mal formado", "detalhes_negocio", {"business_id": "Brophy Bros"},
                    ["22 caracteres", "buscar_entidade"])
        await falha("erro", "detalhes: id inexistente", "detalhes_negocio", {"business_id": "AAAAAAAAAAAAAAAAAAAAAA"},
                    ["nenhum negócio"])
        await falha("erro", "comparar: só um id", "comparar_negocios", {"business_ids": [brophy]}, ["de 2 a 5"])
        await falha("erro", "comparar: nenhum id existe", "comparar_negocios",
                    {"business_ids": ["AAAAAAAAAAAAAAAAAAAAAA", "BBBBBBBBBBBBBBBBBBBBBB"]}, ["inexistentes"])

    return casos, ferramentas, resources, prompts


def gravar(casos, ferramentas, resources, prompts):
    passaram = sum(c[3] for c in casos)
    linhas = [
        "# Validação do servidor MCP (sem LLM)", "",
        f"Gerado por `servidor/validar.py` em {datetime.datetime.now():%Y-%m-%d %H:%M}. "
        f"Python {platform.python_version()}, FastMCP {fastmcp.__version__}, {platform.system()}.", "",
        f"**{passaram} de {len(casos)} casos passaram.**", "",
        "## O que o servidor oferece", "",
        "| Tipo | Nome | Descrição |", "|---|---|---|",
        *[f"| tool | `{t.name}` | {(t.description or '').splitlines()[0]} |" for t in ferramentas],
        *[f"| resource | `{r.uri}` | {r.description or ''} |" for r in resources],
        *[f"| prompt | `{p.name}` | {p.description or ''} |" for p in prompts],
        "", "## Casos", "", "| | Grupo | Caso | Chamada | Resultado |", "|---|---|---|---|---|",
    ]
    limpa = lambda s: s.replace("|", "\\|").replace("\n", " ")
    for grupo, descricao, chamada, passou, detalhe in casos:
        linhas.append(f"| {'✅' if passou else '❌'} | {grupo} | {descricao} | `{limpa(chamada)[:120]}` | {limpa(detalhe)[:300]} |")
    RELATORIO.parent.mkdir(exist_ok=True)
    RELATORIO.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print(f"\n{passaram}/{len(casos)} casos passaram · relatório em {RELATORIO.relative_to(RAIZ)}")
    return passaram == len(casos)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if gravar(*asyncio.run(validar())) else 1)
