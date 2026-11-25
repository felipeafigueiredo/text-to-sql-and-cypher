# Validação do servidor MCP (sem LLM)

Gerado por `servidor/validar.py` em 2026-11-24 20:50. Python 3.14.2, FastMCP 4.1.4, Windows. Dados: Inside Airbnb, São Paulo (coleta de setembro de 2026, avaliações desde 2025).

**52 de 52 casos passaram.**

## O que o servidor oferece

| Tipo | Nome | Descrição |
|---|---|---|
| tool | `consultar_sql` | Executa uma consulta SQL de leitura no PostgreSQL: anúncios, preços, notas, comodidades, anfitriões, bairros, |
| tool | `consultar_grafo` | Executa uma consulta Cypher de leitura no Neo4j: anfitriões e seus anúncios (HOSTS), o bairro de cada anúncio |
| tool | `buscar_entidade` | Acha anúncios, anfitriões, hóspedes, bairros ou comodidades pelo nome (sem diferenciar maiúsculas nem acentos) |
| tool | `detalhes_anuncio` | Todos os dados de um anúncio: bairro, tipo, capacidade, preço, notas, ocupação e receita estimadas, |
| tool | `comparar_anuncios` | Compara de 2 a 5 anúncios lado a lado e calcula o vencedor de cada critério: maior nota, mais avaliações, |
| resource | `esquema://sql` | Tabelas, colunas e cuidados do banco PostgreSQL. |
| resource | `esquema://grafo` | Nós, relações e cuidados do banco Neo4j. |
| prompt | `analisar_bairro` | Pede uma análise de um bairro de São Paulo: oferta, preços, notas e comparação com os bairros vizinhos. |

## Casos

| | Grupo | Caso | Chamada | Resultado |
|---|---|---|---|---|
| ✅ | requisitos | pelo menos 4 ferramentas | `tools/list` | 5 ferramentas |
| ✅ | requisitos | ferramenta de cálculo (comparar_anuncios) | `tools/list` | comparar_anuncios calcula o vencedor de cada critério |
| ✅ | requisitos | pelo menos 1 resource | `resources/list` | 2 resources |
| ✅ | requisitos | pelo menos 1 prompt | `prompts/list` | 1 prompts |
| ✅ | requisitos | toda ferramenta tem descrição e schema de entrada | `tools/list` | sem descrição: nenhuma |
| ✅ | resource | ler esquema://sql | `resources/read esquema://sql` | BANCO RELACIONAL (PostgreSQL) — ferramenta consultar_sql. Cidade única: São Paulo, coleta de setembro de 2026. |
| ✅ | resource | ler esquema://grafo | `resources/read esquema://grafo` | BANCO DE GRAFO (Neo4j) — ferramenta consultar_grafo. Cidade única: São Paulo. |
| ✅ | prompt | analisar_bairro preenche o nome | `prompts/get analisar_bairro(nome='Pinheiros')` | Analise o bairro 'Pinheiros' de São Paulo. Confirme o nome com buscar_entidade (os nomes estão sem acento), mostre quant |
| ✅ | consultar_sql | contar neighbourhood | `consultar_sql({"consulta": "SELECT count(*) FROM neighbourhood"})` | 96 (esperado 96) · 0.22 s |
| ✅ | consultar_sql | contar host | `consultar_sql({"consulta": "SELECT count(*) FROM host"})` | 15883 (esperado 15883) · 0.01 s |
| ✅ | consultar_sql | contar listing | `consultar_sql({"consulta": "SELECT count(*) FROM listing"})` | 44080 (esperado 44080) · 0.01 s |
| ✅ | consultar_sql | contar listing_amenity | `consultar_sql({"consulta": "SELECT count(*) FROM listing_amenity"})` | 1251474 (esperado 1251474) · 0.05 s |
| ✅ | consultar_sql | contar guest | `consultar_sql({"consulta": "SELECT count(*) FROM guest"})` | 651156 (esperado 651156) · 0.03 s |
| ✅ | consultar_sql | contar review | `consultar_sql({"consulta": "SELECT count(*) FROM review"})` | 933607 (esperado 933607) · 0.06 s |
| ✅ | consultar_sql | resultado grande vem truncado e com o total | `consultar_sql({"consulta": "SELECT name FROM listing ORDER BY name"})` | truncado=True, total=44080, devolvidas=50 · 0.45 s |
| ✅ | consultar_sql | bairro com acento casa com o nome sem acento (unaccent) | `consultar_sql({"consulta": "SELECT name FROM neighbourhood WHERE unaccent(name) ILIKE unaccent('Brás')"})` | [['Bras']] · 0.01 s |
| ✅ | consultar_grafo | contar HOSTS | `consultar_grafo({"consulta": "MATCH ()-[r:HOSTS]->() RETURN count(r) AS n"})` | 44080 (esperado 44080) · 0.11 s |
| ✅ | consultar_grafo | contar LOCATED_IN | `consultar_grafo({"consulta": "MATCH ()-[r:LOCATED_IN]->() RETURN count(r) AS n"})` | 44080 (esperado 44080) · 0.01 s |
| ✅ | consultar_grafo | contar REVIEWED | `consultar_grafo({"consulta": "MATCH ()-[r:REVIEWED]->() RETURN count(r) AS n"})` | 899238 (esperado 899238) · 0.01 s |
| ✅ | consultar_grafo | contar BORDERS | `consultar_grafo({"consulta": "MATCH ()-[r:BORDERS]->() RETURN count(r) AS n"})` | 235 (esperado 235) · 0.01 s |
| ✅ | consultar_grafo | lista grande vem truncada e com o total | `consultar_grafo({"consulta": "MATCH (n:Neighbourhood) RETURN n.name AS bairro"})` | truncado=True, total=96 · 0.02 s |
| ✅ | consultar_grafo | vizinhos de um bairro, sem direção | `consultar_grafo({"consulta": "MATCH (:Neighbourhood {name: 'Pinheiros'})-[:BORDERS]-(n) RETURN n.name AS vizinho ORDER B` | ['Alto De Pinheiros', 'Butanta', 'Itaim Bibi', 'Jardim Paulista', 'Morumbi', 'Perdizes'] · 0.01 s |
| ✅ | consultar_grafo | propriedade inexistente gera aviso de esquema (preço não está no grafo) | `consultar_grafo({"consulta": "MATCH (l:Listing)-[:LOCATED_IN]->(:Neighbourhood {name: 'Moema'}) RETURN avg(l.price) AS m` | ['Propriedade inexistente no grafo: price. Os nós só têm host_id, listing_id, guest_id e name. Preços, notas, datas, tipos, comodidades e contagens estão no SQL (consultar_sql).'] · 0.03 s |
| ✅ | consultar_grafo | id entre aspas gera aviso (no grafo os ids são inteiros) | `consultar_grafo({"consulta": "MATCH (l:Listing {listing_id: '13625646'}) RETURN l.name"})` | ['Id entre aspas: no grafo os ids são inteiros. Escreva {listing_id: 123}, sem aspas.'] · 0.02 s |
| ✅ | buscar_entidade | anúncio pelo nome | `buscar_entidade({"nome": "library in the sky", "tipo": "anuncio"})` | 1 encontrado(s): ['Noren - A library in the sky @ Copan'] · 0.31 s |
| ✅ | buscar_entidade | bairro escrito com acento | `buscar_entidade({"nome": "Brás", "tipo": "bairro"})` | [{'name': 'Bras', 'group_name': 'Mooca', 'anuncios': 911}, {'name': 'Brasilandia', 'group_name': 'Freguesia-Brasilandia', 'anuncios': 17}] · 0.01 s |
| ✅ | buscar_entidade | comodidade em inglês | `buscar_entidade({"nome": "Pool", "tipo": "comodidade"})` | [{'amenity': 'Pool', 'anuncios': 14579}, {'amenity': 'Pool table', 'anuncios': 2791}, {'amenity': 'Shared pool', 'anuncios': 1710}] · 1.23 s |
| ✅ | buscar_entidade | homônimos são sinalizados | `buscar_entidade({"nome": "Marcelo", "tipo": "anfitriao"})` | Há 10 anfitriãos chamados exatamente 'Marcelo' (talvez mais, além dos 10 devolvidos). Use o id para escolher; os outros campos ajudam a diferenciar. · 0.06 s |
| ✅ | buscar_entidade | item inexistente: vazio, sem erro | `buscar_entidade({"nome": "Pousada Potiguar Imaginária", "tipo": "anuncio"})` | Nenhum(a) anúncio com 'Pousada Potiguar Imaginária' no nome nos dados de São Paulo. · 0.64 s |
| ✅ | buscar_entidade | comodidade em português sugere o nome em inglês | `buscar_entidade({"nome": "piscina", "tipo": "comodidade"})` | Nenhum(a) comodidade com 'piscina' no nome nos dados de São Paulo. As comodidades estão em inglês (ex.: 'Pool', 'Air conditioning', 'Kitchen'). sugestões=None · 1.99 s |
| ✅ | detalhes_anuncio | detalhes de um anúncio | `detalhes_anuncio({"listing_id": 13625646})` | Noren - A library in the sky @ Copan, nota 4.85, R$ 584.7, 41 comodidades, 108 avaliações no banco · 0.01 s |
| ✅ | comparar_anuncios | cálculo do melhor em cada critério | `comparar_anuncios({"listing_ids": [13625646, 680472748773959587]})` | {"valor": 4.85, "listing_ids": [13625646]} {"valor": 413.0, "listing_ids": [680472748773959587]} · 0.01 s |
| ✅ | comparar_anuncios | id inexistente aparece em 'ausentes' | `comparar_anuncios({"listing_ids": [13625646, 680472748773959587, 1]})` | ausentes=[1] · 0.01 s |
| ✅ | erro | SQL: comando de escrita | `consultar_sql({"consulta": "DELETE FROM review"})` | isError=True: Só consultas de leitura são permitidas: o comando deve começar com SELECT ou WITH. O banco é somente leitura. · 0.0 s |
| ✅ | erro | SQL: escrita disfarçada de WITH é barrada pelo banco | `consultar_sql({"consulta": "WITH x AS (DELETE FROM review RETURNING *) SELECT count(*) FROM x"})` | isError=True: Erro do PostgreSQL: cannot execute SELECT in a read-only transaction Dica: O banco é somente leitura: só consultas SELECT são permitidas. · 0.01 s |
| ✅ | erro | SQL: tabela inexistente lista as tabelas certas | `consultar_sql({"consulta": "SELECT * FROM anuncios"})` | isError=True: Erro do PostgreSQL: relation "anuncios" does not exist Dica: Tabela inexistente. As tabelas são: neighbourhood(name, group_name); host(host_id, name, is_superhost, identity_verified, listings_count); listing(listing_id, host_id, neighbourhood, name, latitude, longitude, property_type,  |
| ✅ | erro | SQL: coluna inexistente lista as colunas | `consultar_sql({"consulta": "SELECT rating FROM listing"})` | isError=True: Erro do PostgreSQL: column "rating" does not exist Dica: Coluna inexistente. As colunas de cada tabela são: neighbourhood(name, group_name); host(host_id, name, is_superhost, identity_verified, listings_count); listing(listing_id, host_id, neighbourhood, name, latitude, longitude, prop |
| ✅ | erro | SQL: id com tipo errado | `consultar_sql({"consulta": "SELECT name FROM listing WHERE listing_id = 'Noren'"})` | isError=True: Erro do PostgreSQL: invalid input syntax for type bigint: "Noren" Dica: Valor com tipo errado: os ids são inteiros (sem aspas) e as datas vão no formato 'AAAA-MM-DD'. · 0.01 s |
| ✅ | erro | SQL: sintaxe | `consultar_sql({"consulta": "SELECT name FROM listing WHERE"})` | isError=True: Erro do PostgreSQL: syntax error at end of input Dica: Erro de sintaxe SQL. Confira a consulta e envie de novo. · 0.01 s |
| ✅ | erro | SQL: dois comandos | `consultar_sql({"consulta": "SELECT 1; SELECT 2"})` | isError=True: Envie um único comando SELECT, sem ';' separando comandos. · 0.0 s |
| ✅ | erro | SQL: consulta vazia | `consultar_sql({"consulta": "  "})` | isError=True: A consulta está vazia. Envie um comando SELECT. · 0.0 s |
| ✅ | erro | SQL: argumento com tipo errado | `consultar_sql({"consulta": 123})` | isError=True: 1 validation error for call[consultar_sql] consulta Input should be a valid string [type=string_type, input_value=123, input_type=int] For further information visit https://errors.pydantic.dev/2.14/v/string_type · 0.0 s |
| ✅ | erro | SQL: argumento faltando | `consultar_sql({})` | isError=True: 1 validation error for call[consultar_sql] consulta Missing required argument [type=missing_argument, input_value={}, input_type=dict] For further information visit https://errors.pydantic.dev/2.14/v/missing_argument · 0.0 s |
| ✅ | erro | SQL: consulta lenta é cancelada | `consultar_sql({"consulta": "SELECT count(*) FROM review a, review b"})` | isError=True: Erro do PostgreSQL: canceling statement due to statement timeout Dica: A consulta passou de 15 s e foi cancelada. Evite produtos cartesianos (junções sem condição), filtre antes de juntar e use LIMIT. · 15.02 s |
| ✅ | erro | Cypher: comando de escrita | `consultar_grafo({"consulta": "CREATE (n:Guest {name: 'x'})"})` | isError=True: O grafo é somente leitura: 'CREATE' não é permitido. Use MATCH ... RETURN. · 0.0 s |
| ✅ | erro | Cypher: sintaxe | `consultar_grafo({"consulta": "MATCH (g:Guest RETURN g"})` | isError=True: Erro do Neo4j (Neo.ClientError.Statement.SyntaxError): Invalid input 'RETURN': expected a parameter, '&', ')', ':', 'WHERE', '{' or '\|' (line 1, column 16 (offset: 15)) Dica: Erro de sintaxe Cypher. Confira parênteses, colchetes e o RETURN, e envie de novo. · 0.02 s |
| ✅ | erro | busca: tipo fora da lista | `buscar_entidade({"nome": "Copan", "tipo": "hotel"})` | isError=True: 1 validation error for call[buscar_entidade] tipo Input should be 'anuncio', 'anfitriao', 'hospede', 'bairro' or 'comodidade' [type=literal_error, input_value='hotel', input_type=str] For further information visit https://errors.pydantic.dev/2.14/v/literal_error · 0.01 s |
| ✅ | erro | busca: nome curto demais | `buscar_entidade({"nome": "a", "tipo": "anuncio"})` | isError=True: Informe pelo menos 2 caracteres do nome a procurar. · 0.0 s |
| ✅ | erro | detalhes: nome no lugar do id | `detalhes_anuncio({"listing_id": "Noren"})` | isError=True: listing_id inválido: 'Noren'. Um listing_id é um número inteiro, ex.: 1234567. Para achar o id a partir do nome, use buscar_entidade. · 0.0 s |
| ✅ | erro | detalhes: id inexistente | `detalhes_anuncio({"listing_id": 1})` | isError=True: Nenhum anúncio com listing_id 1. Confira o id com buscar_entidade. · 0.01 s |
| ✅ | erro | comparar: só um id | `comparar_anuncios({"listing_ids": [13625646]})` | isError=True: Envie de 2 a 5 listing_id diferentes; recebi 1. · 0.01 s |
| ✅ | erro | comparar: nenhum id existe | `comparar_anuncios({"listing_ids": [1, 2]})` | isError=True: Só 0 dos ids existe(m); não há o que comparar. Ids inexistentes: [1, 2]. Confira com buscar_entidade. · 0.01 s |
