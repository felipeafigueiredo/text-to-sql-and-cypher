-- Esquema relacional do recorte do Inside Airbnb (São Paulo, coleta de setembro de 2026).
-- Atributos, números e comodidades ficam aqui; vizinhança entre bairros e "quem se hospedou onde" ficam também no Neo4j.

CREATE TABLE neighbourhood (
    name       text PRIMARY KEY,      -- distrito, sem acento como no dado original: 'Republica', 'Bras', 'Butanta'
    group_name text NOT NULL          -- agrupamento de distritos (subprefeitura): 'Pinheiros', 'Se', 'Vila Mariana'
);

CREATE TABLE host (
    host_id           bigint PRIMARY KEY,
    name              text,           -- só o primeiro nome (ou o nome da empresa); há muitos homônimos
    is_superhost      boolean,
    identity_verified boolean,
    listings_count    integer         -- anúncios do anfitrião no Airbnb INTEIRO (todas as cidades), segundo o Airbnb
);

CREATE TABLE listing (
    listing_id                  bigint PRIMARY KEY,
    host_id                     bigint NOT NULL REFERENCES host,
    neighbourhood               text NOT NULL REFERENCES neighbourhood,
    name                        text,
    latitude                    double precision,
    longitude                   double precision,
    property_type               text,           -- em inglês: 'Entire rental unit', 'Private room in home'...
    room_type                   text,           -- 'Entire home/apt', 'Private room', 'Shared room', 'Hotel room'
    accommodates                integer,        -- capacidade máxima de hóspedes
    bedrooms                    integer,
    beds                        integer,
    bathrooms                   numeric(3,1),
    price                       numeric(10,2),  -- R$ por noite; nulo quando não informado ou acima de R$ 10.000 (erro de coleta)
    minimum_nights              integer,
    availability_365            integer,        -- noites disponíveis nos próximos 365 dias
    number_of_reviews           integer,        -- avaliações na vida toda do anúncio (desde 2011), não só as da tabela review
    first_review                date,
    last_review                 date,
    review_scores_rating        numeric(3,2),   -- nota geral de 0 a 5; nula quando o anúncio não tem avaliação
    review_scores_accuracy      numeric(3,2),
    review_scores_cleanliness   numeric(3,2),
    review_scores_checkin       numeric(3,2),
    review_scores_communication numeric(3,2),
    review_scores_location      numeric(3,2),
    review_scores_value         numeric(3,2),
    estimated_occupancy_l365d   integer,        -- noites ocupadas estimadas nos últimos 365 dias
    estimated_revenue_l365d     numeric(12,2)   -- receita estimada em R$ nos últimos 365 dias
);

CREATE TABLE listing_amenity (
    listing_id bigint NOT NULL REFERENCES listing,
    amenity    text NOT NULL          -- em inglês: 'Wifi', 'Kitchen', 'Pool', 'Air conditioning'...
);

CREATE TABLE guest (
    guest_id bigint PRIMARY KEY,
    name     text                     -- só o primeiro nome; há muitos homônimos
);

CREATE TABLE review (
    review_id  bigint PRIMARY KEY,
    listing_id bigint NOT NULL REFERENCES listing,
    guest_id   bigint NOT NULL REFERENCES guest,
    date       date NOT NULL          -- só de 2025-01-01 em diante; a avaliação do Airbnb não tem nota própria
);
