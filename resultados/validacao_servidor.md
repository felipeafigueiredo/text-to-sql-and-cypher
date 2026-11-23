# Validação do servidor MCP (sem LLM)

Gerado por `servidor/validar.py` em 2026-11-23 08:13. Python 3.14.2, FastMCP 4.1.4, Windows.

**43 de 43 casos passaram.**

## O que o servidor oferece

| Tipo | Nome | Descrição |
|---|---|---|
| tool | `consultar_sql` | Executa uma consulta SQL de leitura no PostgreSQL: negócios, notas, categorias, avaliações e usuários. |
| tool | `consultar_grafo` | Executa uma consulta Cypher de leitura no Neo4j: amizades entre usuários (FRIEND) e quem avaliou |
| tool | `buscar_entidade` | Acha negócios, usuários ou categorias pelo nome (sem diferenciar maiúsculas) e devolve os ids. |
| tool | `detalhes_negocio` | Todos os dados de um negócio: endereço, nota, faixa de preço, se está aberto, check-ins, |
| tool | `comparar_negocios` | Compara de 2 a 5 negócios lado a lado e calcula o vencedor de cada critério: maior nota, mais |
| resource | `esquema://sql` | Tabelas, colunas e cuidados do banco PostgreSQL. |
| resource | `esquema://grafo` | Nós, relações e cuidados do banco Neo4j. |
| prompt | `analisar_negocio` | Pede uma análise de um negócio pelo nome: dados, categorias e comparação com concorrentes. |

## Casos

| | Grupo | Caso | Chamada | Resultado |
|---|---|---|---|---|
| ✅ | requisitos | pelo menos 4 ferramentas | `tools/list` | 5 ferramentas |
| ✅ | requisitos | ferramenta de cálculo (comparar_negocios) | `tools/list` | comparar_negocios calcula o vencedor de cada critério |
| ✅ | requisitos | pelo menos 1 resource | `resources/list` | 2 resources |
| ✅ | requisitos | pelo menos 1 prompt | `prompts/list` | 1 prompts |
| ✅ | requisitos | toda ferramenta tem descrição e schema de entrada | `tools/list` | sem descrição: nenhuma |
| ✅ | resource | ler esquema://sql | `resources/read esquema://sql` | BANCO RELACIONAL (PostgreSQL) — ferramenta consultar_sql. Cidade única: Santa Barbara, CA. |
| ✅ | resource | ler esquema://grafo | `resources/read esquema://grafo` | BANCO DE GRAFO (Neo4j) — ferramenta consultar_grafo. Cidade única: Santa Barbara, CA. |
| ✅ | prompt | analisar_negocio preenche o nome | `prompts/get analisar_negocio(nome='Brophy Bros')` | Analise o negócio 'Brophy Bros' em Santa Barbara. Encontre-o pelo nome (se houver mais de um, diga quais), mostre nota,  |
| ✅ | consultar_sql | contar business | `consultar_sql({"consulta": "SELECT count(*) FROM business"})` | 3834 (esperado 3834) · 0.21 s |
| ✅ | consultar_sql | contar business_category | `consultar_sql({"consulta": "SELECT count(*) FROM business_category"})` | 18194 (esperado 18194) · 0.01 s |
| ✅ | consultar_sql | contar users | `consultar_sql({"consulta": "SELECT count(*) FROM users"})` | 130392 (esperado 130392) · 0.01 s |
| ✅ | consultar_sql | contar review | `consultar_sql({"consulta": "SELECT count(*) FROM review"})` | 269703 (esperado 269703) · 0.02 s |
| ✅ | consultar_sql | resultado grande vem truncado e com o total | `consultar_sql({"consulta": "SELECT name FROM business ORDER BY name"})` | truncado=True, total=3834, devolvidas=50 · 0.02 s |
| ✅ | consultar_grafo | contar FRIEND | `consultar_grafo({"consulta": "MATCH ()-[r:FRIEND]->() RETURN count(r) AS n"})` | 377374 (esperado 377374) · 0.09 s |
| ✅ | consultar_grafo | contar REVIEWED | `consultar_grafo({"consulta": "MATCH ()-[r:REVIEWED]->() RETURN count(r) AS n"})` | 262936 (esperado 262936) · 0.01 s |
| ✅ | consultar_grafo | lista grande vem truncada e com o total | `consultar_grafo({"consulta": "MATCH (b:Business) RETURN b.name AS nome"})` | truncado=True, total=3834 · 0.27 s |
| ✅ | consultar_grafo | propriedade inexistente gera aviso de esquema (nota não está no grafo) | `consultar_grafo({"consulta": "MATCH (u:User)-[r:REVIEWED]->(b:Business) RETURN avg(r.stars) AS media"})` | ['Propriedade inexistente no grafo: stars. Os nós só têm user_id, business_id e name. Notas, datas, categorias e contagens estão no SQL (consultar_sql).'] · 0.3 s |
| ✅ | buscar_entidade | negócio pelo nome | `buscar_entidade({"nome": "Brophy Bros", "tipo": "negocio"})` | 1 encontrado(s): ['Brophy Bros - Santa Barbara'] · 0.02 s |
| ✅ | buscar_entidade | categoria em inglês | `buscar_entidade({"nome": "Mexican", "tipo": "categoria"})` | [{'category': 'Mexican', 'negocios': 118}, {'category': 'New Mexican Cuisine', 'negocios': 3}] · 0.06 s |
| ✅ | buscar_entidade | homônimos são sinalizados | `buscar_entidade({"nome": "Starbucks", "tipo": "negocio"})` | Há 10 negócios chamados exatamente 'Starbucks'. Use o id para escolher; endereço e números ajudam a diferenciar. · 0.01 s |
| ✅ | buscar_entidade | item inexistente: vazio, sem erro | `buscar_entidade({"nome": "Churrascaria Potiguar Imaginária", "tipo": "negocio"})` | Nenhum(a) negócio com 'Churrascaria Potiguar Imaginária' no nome em Santa Barbara. · 0.06 s |
| ✅ | buscar_entidade | categoria em português sugere o nome em inglês | `buscar_entidade({"nome": "mexicano", "tipo": "categoria"})` | Nenhum(a) categoria com 'mexicano' no nome em Santa Barbara. As categorias estão em inglês (ex.: 'Mexican', 'Coffee & Tea'). sugestões=['Mexican', 'New Mexican Cuisine'] · 0.13 s |
| ✅ | detalhes_negocio | detalhes de um negócio | `detalhes_negocio({"business_id": "U3grYFIeu6RgAAQgdriHww"})` | Brophy Bros - Santa Barbara, 4.0 estrelas, 6 categorias, 3003 avaliações no banco · 0.02 s |
| ✅ | comparar_negocios | cálculo do melhor em cada critério | `comparar_negocios({"business_ids": ["U3grYFIeu6RgAAQgdriHww", "yPSejq3_erxo9zdVYTBnZA"]})` | {"valor": 4.5, "business_ids": ["yPSejq3_erxo9zdVYTBnZA"]} {"valor": 3834, "business_ids": ["yPSejq3_erxo9zdVYTBnZA"]} · 0.02 s |
| ✅ | comparar_negocios | id inexistente aparece em 'ausentes' | `comparar_negocios({"business_ids": ["U3grYFIeu6RgAAQgdriHww", "yPSejq3_erxo9zdVYTBnZA", "AAAAAAAAAAAAAAAAAAAAAA"]})` | ausentes=['AAAAAAAAAAAAAAAAAAAAAA'] · 0.01 s |
| ✅ | erro | SQL: comando de escrita | `consultar_sql({"consulta": "DELETE FROM review"})` | isError=True: Só consultas de leitura são permitidas: o comando deve começar com SELECT ou WITH. O banco é somente leitura. · 0.01 s |
| ✅ | erro | SQL: escrita disfarçada de WITH é barrada pelo banco | `consultar_sql({"consulta": "WITH x AS (DELETE FROM review RETURNING *) SELECT count(*) FROM x"})` | isError=True: Erro do PostgreSQL: cannot execute SELECT in a read-only transaction Dica: O banco é somente leitura: só consultas SELECT são permitidas. · 0.01 s |
| ✅ | erro | SQL: tabela inexistente lista as tabelas certas | `consultar_sql({"consulta": "SELECT * FROM usuarios"})` | isError=True: Erro do PostgreSQL: relation "usuarios" does not exist Dica: Tabela inexistente. As tabelas são: business(business_id, name, address, postal_code, latitude, longitude, stars, review_count, is_open, price_range, checkin_count); business_category(business_id, category); review(review_id, |
| ✅ | erro | SQL: coluna inexistente lista as colunas | `consultar_sql({"consulta": "SELECT rating FROM business"})` | isError=True: Erro do PostgreSQL: column "rating" does not exist Dica: Coluna inexistente. As colunas de cada tabela são: business(business_id, name, address, postal_code, latitude, longitude, stars, review_count, is_open, price_range, checkin_count); business_category(business_id, category); review |
| ✅ | erro | SQL: sintaxe | `consultar_sql({"consulta": "SELECT name FROM business WHERE"})` | isError=True: Erro do PostgreSQL: syntax error at end of input Dica: Erro de sintaxe SQL. Confira a consulta e envie de novo. · 0.01 s |
| ✅ | erro | SQL: dois comandos | `consultar_sql({"consulta": "SELECT 1; SELECT 2"})` | isError=True: Envie um único comando SELECT, sem ';' separando comandos. · 0.0 s |
| ✅ | erro | SQL: consulta vazia | `consultar_sql({"consulta": "  "})` | isError=True: A consulta está vazia. Envie um comando SELECT. · 0.0 s |
| ✅ | erro | SQL: argumento com tipo errado | `consultar_sql({"consulta": 123})` | isError=True: 1 validation error for call[consultar_sql] consulta Input should be a valid string [type=string_type, input_value=123, input_type=int] For further information visit https://errors.pydantic.dev/2.14/v/string_type · 0.0 s |
| ✅ | erro | SQL: argumento faltando | `consultar_sql({})` | isError=True: 1 validation error for call[consultar_sql] consulta Missing required argument [type=missing_argument, input_value={}, input_type=dict] For further information visit https://errors.pydantic.dev/2.14/v/missing_argument · 0.0 s |
| ✅ | erro | SQL: consulta lenta é cancelada | `consultar_sql({"consulta": "SELECT count(*) FROM review a, review b"})` | isError=True: Erro do PostgreSQL: canceling statement due to statement timeout Dica: A consulta passou de 15 s e foi cancelada. Evite produtos cartesianos (junções sem condição), filtre antes de juntar e use LIMIT. · 15.02 s |
| ✅ | erro | Cypher: comando de escrita | `consultar_grafo({"consulta": "CREATE (n:User {name: 'x'})"})` | isError=True: O grafo é somente leitura: 'CREATE' não é permitido. Use MATCH ... RETURN. · 0.01 s |
| ✅ | erro | Cypher: sintaxe | `consultar_grafo({"consulta": "MATCH (u:User RETURN u"})` | isError=True: Erro do Neo4j (Neo.ClientError.Statement.SyntaxError): Invalid input 'RETURN': expected a parameter, '&', ')', ':', 'WHERE', '{' or '\|' (line 1, column 15 (offset: 14)) Dica: Erro de sintaxe Cypher. Confira parênteses, colchetes e o RETURN, e envie de novo. · 0.02 s |
| ✅ | erro | busca: tipo fora da lista | `buscar_entidade({"nome": "Pizza", "tipo": "restaurante"})` | isError=True: 1 validation error for call[buscar_entidade] tipo Input should be 'negocio', 'usuario' or 'categoria' [type=literal_error, input_value='restaurante', input_type=str] For further information visit https://errors.pydantic.dev/2.14/v/literal_error · 0.0 s |
| ✅ | erro | busca: nome curto demais | `buscar_entidade({"nome": "a", "tipo": "negocio"})` | isError=True: Informe pelo menos 2 caracteres do nome a procurar. · 0.0 s |
| ✅ | erro | detalhes: id mal formado | `detalhes_negocio({"business_id": "Brophy Bros"})` | isError=True: business_id inválido: 'Brophy Bros'. Um business_id tem 22 caracteres (letras, números, '-' e '_'). Para achar o id a partir do nome, use buscar_entidade com tipo 'negocio'. · 0.01 s |
| ✅ | erro | detalhes: id inexistente | `detalhes_negocio({"business_id": "AAAAAAAAAAAAAAAAAAAAAA"})` | isError=True: Nenhum negócio com business_id 'AAAAAAAAAAAAAAAAAAAAAA'. Confira o id com buscar_entidade. · 0.01 s |
| ✅ | erro | comparar: só um id | `comparar_negocios({"business_ids": ["U3grYFIeu6RgAAQgdriHww"]})` | isError=True: Envie de 2 a 5 business_id diferentes; recebi 1. · 0.01 s |
| ✅ | erro | comparar: nenhum id existe | `comparar_negocios({"business_ids": ["AAAAAAAAAAAAAAAAAAAAAA", "BBBBBBBBBBBBBBBBBBBBBB"]})` | isError=True: Só 0 dos ids existe(m); não há o que comparar. Ids inexistentes: ['AAAAAAAAAAAAAAAAAAAAAA', 'BBBBBBBBBBBBBBBBBBBBBB']. Confira com buscar_entidade. · 0.02 s |
