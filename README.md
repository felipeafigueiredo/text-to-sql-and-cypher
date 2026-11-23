# text-to-sql-and-cypher

Perguntas em linguagem natural sobre dados do Yelp de Santa Barbara (CA), respondidas por agentes de LLM que consultam um banco relacional (**PostgreSQL**, em SQL) e um banco de grafos (**Neo4j**, em Cypher) por meio de um **servidor MCP**. O projeto compara, com medições, um agente único e duas arquiteturas multiagente.

Trabalho 2 de DCA0305 (Projeto de sistemas baseados em aprendizado de máquina), UFRN.

> Em construção. Esta página será completada com as arquiteturas, os resultados, a análise e o link do vídeo.

## Dados

[Yelp Open Dataset](https://www.yelp.com/dataset). Os termos de uso do Yelp não permitem redistribuir os dados, então **este repositório não contém dados**: contém o script que gera o recorte a partir do download oficial.

| Banco | Conteúdo |
|---|---|
| PostgreSQL | `business` (3.834), `business_category` (18.194), `users` (130.392), `review` (269.703, sem o texto) |
| Neo4j | nós `User` e `Business`; relações `FRIEND` (377.374) e `REVIEWED` (262.936) |

Cada fato mora em um banco só; os dois compartilham as chaves `user_id` e `business_id`.

## Servidor MCP

`servidor/server.py` (FastMCP 4.1.4, transporte stdio):

| Tipo | Nome | O que faz |
|---|---|---|
| tool | `consultar_sql` | SELECT somente leitura no PostgreSQL |
| tool | `consultar_grafo` | Cypher somente leitura no Neo4j |
| tool | `buscar_entidade` | acha negócio, usuário ou categoria pelo nome; sinaliza homônimos e inexistentes |
| tool | `detalhes_negocio` | todos os dados de um negócio |
| tool | `comparar_negocios` | compara de 2 a 5 negócios e calcula o vencedor de cada critério |
| resource | `esquema://sql`, `esquema://grafo` | esquemas e cuidados de cada banco |
| prompt | `analisar_negocio` | pede a análise de um negócio pelo nome |

Chamadas incorretas devolvem erro de execução (`isError`) com uma dica do que corrigir. A escrita é bloqueada na ferramenta e no próprio banco. A validação sem LLM (`servidor/validar.py`) está em [resultados/validacao_servidor.md](resultados/validacao_servidor.md).

## Como executar

Tudo roda no **Google Colab** com GPU T4, sem Docker.

**1. Gerar o recorte (uma vez, na sua máquina).** Baixe o dataset em yelp.com/dataset e rode:

```bash
python dados/recorte.py "<pasta com os yelp_academic_dataset_*.json>" dados/recorte
```

Leva cerca de 1 minuto e grava 6 arquivos CSV (66 MB). Suba-os para uma pasta do seu Google Drive.

**2. Preparar o ambiente e validar o servidor.** Abra [notebooks/01_preparacao_e_servidor.ipynb](notebooks/01_preparacao_e_servidor.ipynb) no Colab (*Arquivo → Abrir notebook → GitHub*), escolha o runtime T4, ajuste `DRIVE_DIR` na primeira célula e rode tudo.

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `dados/` | recorte do dataset, esquema SQL e carga nos dois bancos |
| `servidor/` | servidor MCP e sua validação |
| `ambiente/` | instalação dos bancos, do servidor e do Ollama no Colab |
| `notebooks/` | notebooks para rodar no Colab |
| `resultados/` | evidências e resultados das execuções |
