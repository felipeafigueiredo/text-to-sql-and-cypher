# text-to-sql-and-cypher

Perguntas em linguagem natural sobre os anúncios do Airbnb em São Paulo, respondidas por agentes de LLM que consultam um banco relacional (**PostgreSQL**, em SQL) e um banco de grafos (**Neo4j**, em Cypher) por meio de um **servidor MCP**. O projeto compara, com medições, um agente único e duas arquiteturas multiagente.

Trabalho 2 de DCA0305 (Projeto de sistemas baseados em aprendizado de máquina), UFRN.

> Em construção. Esta página será completada com as arquiteturas, os resultados, a análise e o link do vídeo.

## Dados

[Inside Airbnb](https://insideairbnb.com/get-the-data/), São Paulo, coleta de setembro de 2026: anúncios (`listings.csv.gz`, versão detalhada), avaliações (`reviews.csv.gz`) e os polígonos dos distritos (`neighbourhoods.geojson`). O recorte usa todos os anúncios da cidade e as avaliações de 2025 em diante, sem o texto.

| Banco | Conteúdo |
|---|---|
| PostgreSQL | `neighbourhood` (96), `host` (15.883), `listing` (44.080), `listing_amenity` (1.251.474), `guest` (651.156), `review` (933.607) |
| Neo4j | nós `Host`, `Listing`, `Guest` e `Neighbourhood`; relações `HOSTS` (44.080), `LOCATED_IN` (44.080), `REVIEWED` (899.238) e `BORDERS` (235) |

Preços, notas, comodidades e datas ficam só no PostgreSQL. A vizinhança entre bairros (`BORDERS`, calculada dos polígonos) fica só no Neo4j. Os dois bancos compartilham as chaves `listing_id`, `host_id`, `guest_id` e o nome do bairro.

O repositório não contém os dados: contém o script que gera o recorte a partir dos arquivos baixados.

## Servidor MCP

`servidor/server.py` (FastMCP 4.1.4, transporte stdio):

| Tipo | Nome | O que faz |
|---|---|---|
| tool | `consultar_sql` | SELECT somente leitura no PostgreSQL |
| tool | `consultar_grafo` | Cypher somente leitura no Neo4j |
| tool | `buscar_entidade` | acha anúncio, anfitrião, hóspede, bairro ou comodidade pelo nome (sem diferenciar acentos); sinaliza homônimos e inexistentes; para anfitriões, mostra os anúncios em São Paulo e no Airbnb inteiro |
| tool | `detalhes_anuncio` | todos os dados de um anúncio |
| tool | `comparar_anuncios` | compara de 2 a 5 anúncios e calcula o vencedor de cada critério |
| resource | `esquema://sql`, `esquema://grafo` | esquemas e cuidados de cada banco |
| prompt | `analisar_bairro` | pede a análise de um bairro e a comparação com os vizinhos |

Chamadas incorretas devolvem erro de execução (`isError`) com uma dica do que corrigir; reenviar sem mudança a consulta que acabou de falhar é recusado, com o pedido de reescrevê-la. A escrita é bloqueada na ferramenta e no próprio banco. A validação sem LLM (`servidor/validar.py`) está em [resultados/validacao_servidor.md](resultados/validacao_servidor.md).

## Como executar

Tudo roda no **Google Colab**, sem Docker. Só os notebooks que usam o modelo precisam de GPU T4.

**1. Gerar o recorte (uma vez, na sua máquina).** Em insideairbnb.com/get-the-data, na seção de São Paulo, baixe `listings.csv.gz`, `reviews.csv.gz` e `neighbourhoods.geojson` para uma mesma pasta e rode:

```bash
pip install -r dados/requirements.txt
python dados/recorte.py "<pasta com os 3 arquivos>" dados/recorte
```

Leva cerca de 30 segundos e grava 8 arquivos CSV (148 MB). Suba-os para uma pasta do seu Google Drive.

**2. Preparar o ambiente e validar o servidor.** Abra [notebooks/01_preparacao_e_servidor.ipynb](notebooks/01_preparacao_e_servidor.ipynb) no Colab (*Arquivo → Abrir notebook → GitHub*), mantenha o runtime padrão (só CPU), ajuste `DRIVE_DIR` na primeira célula e rode tudo.

**3. Os agentes.** Abra [notebooks/02_agentes.ipynb](notebooks/02_agentes.ipynb) com o runtime **T4 GPU**, ajuste `DRIVE_DIR` e rode tudo. A primeira célula prepara a sessão sozinha (`ambiente/preparar.py`, cerca de 10 minutos) e as seguintes mostram os três sistemas (agente único, roteamento e verificador) respondendo as mesmas perguntas, com o trace de cada consulta e a checagem contra o gabarito.

**4. As 60 execuções.** Abra [notebooks/03_avaliacao.ipynb](notebooks/03_avaliacao.ipynb) com o runtime **T4 GPU**, ajuste `DRIVE_DIR` e `RESULTADOS_DIR` e rode tudo. O notebook faz as 60 execuções (20 perguntas × 3 sistemas), gravando no Drive a cada linha; se a sessão cair, rodar de novo continua de onde parou.

**5. O juiz.** Roda fora do Colab, numa máquina com os bancos carregados e o [Claude Code](https://docs.claude.com/en/docs/claude-code) instalado e logado numa conta Claude. Baixe `execucoes.csv` e `execucoes.jsonl` do Drive para `resultados/`, aponte `AIRBNB_PG_DSN` e `AIRBNB_NEO4J_URI` para os bancos e rode:

```bash
python avaliacao/juiz.py resultados --python-servidor <python com as libs de servidor/requirements.txt>
```

O juiz só usa a biblioteca padrão do Python; quem precisa das bibliotecas do servidor é o servidor MCP, que o Claude Code sobe com o `--python-servidor`. Se a cota da conta acabar, ele para; rodar de novo continua de onde parou.

**6. A análise.** Com os arquivos em `resultados/`, abra [notebooks/04_analise.ipynb](notebooks/04_analise.ipynb) (runtime padrão, só CPU, ou localmente) e rode tudo. Ele monta a tabela comparativa, os gráficos (gravados em `resultados/graficos/`), os erros por pergunta, o roteamento, o verificador e a concordância entre juiz, revisão humana e checagem automática. A revisão humana é feita no `resultados/revisao_manual.csv`, que o próprio notebook cria: preencha a coluna `veredito_humano` e rode de novo.

## Avaliação

`avaliacao/perguntas.py` tem as 20 perguntas, cada uma com categoria, banco, ferramenta esperada e a consulta de referência que calcula o gabarito direto nos bancos. Para ver as perguntas com o gabarito:

```bash
python avaliacao/gabarito.py
```

`avaliacao/avaliador.py` roda as perguntas nos sistemas pelo `answer()` e grava `execucoes.csv` (uma linha por execução, com as métricas e a checagem automática) e `execucoes.jsonl` (o registro completo de cada execução). `avaliacao/juiz.py` julga cada resposta com o Claude (modelo de fronteira), chamado pelo Claude Code em modo não interativo. Ele recebe a pergunta, o gabarito e a resposta, e tem acesso somente leitura ao servidor MCP: só as 5 ferramentas dele, com as ferramentas do próprio Claude Code (terminal, arquivos, web) desligadas. Grava `juiz.csv` e `juiz.jsonl` com o veredito (correta, parcial ou incorreta), a justificativa e as consultas que fez.

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `dados/` | recorte do dataset, esquema SQL e carga nos dois bancos |
| `servidor/` | servidor MCP e sua validação |
| `agentes/` | código comum (sessão MCP, modelo, medição, `answer()`) e um arquivo por sistema |
| `avaliacao/` | as 20 perguntas, o gabarito, a checagem automática, o avaliador, o juiz e a análise |
| `ambiente/` | instalação dos bancos, do servidor e do Ollama no Colab, e a preparação de uma sessão |
| `notebooks/` | notebooks para rodar no Colab |
| `resultados/` | evidências e resultados das execuções |
