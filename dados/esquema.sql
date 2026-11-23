-- Esquema relacional do recorte do Yelp (Santa Barbara, CA).
-- Atributos, números e categorias ficam aqui; amizades e "quem avaliou o quê" ficam no Neo4j.

CREATE TABLE business (
    business_id   text PRIMARY KEY,
    name          text NOT NULL,
    address       text,
    postal_code   text,
    latitude      double precision,
    longitude     double precision,
    stars         numeric(2,1),      -- nota média do negócio no Yelp inteiro, de 1.0 a 5.0
    review_count  integer,           -- número de avaliações no Yelp inteiro
    is_open       integer,           -- 1 aberto, 0 fechado
    price_range   integer,           -- 1 a 4; nulo em cerca de metade dos negócios
    checkin_count integer            -- número de check-ins registrados
);

CREATE TABLE business_category (
    business_id text NOT NULL REFERENCES business,
    category    text NOT NULL        -- em inglês, ex.: 'Mexican', 'Restaurants'
);

CREATE TABLE users (                 -- "user" é palavra reservada no PostgreSQL
    user_id       text PRIMARY KEY,
    name          text,              -- só o primeiro nome; há muitos homônimos
    review_count  integer,           -- avaliações no Yelp inteiro, não só em Santa Barbara
    yelping_since timestamp,
    useful        integer,
    funny         integer,
    cool          integer,
    fans          integer,
    average_stars numeric(3,2)       -- média no Yelp inteiro, não só em Santa Barbara
);

CREATE TABLE review (
    review_id   text PRIMARY KEY,
    user_id     text NOT NULL REFERENCES users,
    business_id text NOT NULL REFERENCES business,
    stars       integer,             -- nota da avaliação, de 1 a 5
    date        timestamp,
    useful      integer,
    funny       integer,
    cool        integer
);
