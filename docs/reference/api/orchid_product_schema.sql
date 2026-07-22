CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS orchid_categories (
    id BIGSERIAL PRIMARY KEY,
    category_name VARCHAR(128) NOT NULL UNIQUE,
    category_description TEXT
);

CREATE TABLE IF NOT EXISTS orchid_varieties (
    id BIGSERIAL PRIMARY KEY,
    category_id BIGINT,
    category_name VARCHAR(128) NOT NULL,
    variety_name VARCHAR(256) NOT NULL,
    primary_alias VARCHAR(256),
    aliases_text TEXT,
    source_type VARCHAR(256),
    origin_area VARCHAR(256),
    history_background TEXT,
    summary TEXT,
    suitable_level VARCHAR(128),
    base_spec VARCHAR(256),
    base_price_text VARCHAR(256),
    raw_basic_info TEXT
);

CREATE INDEX IF NOT EXISTS idx_orchid_varieties_category ON orchid_varieties(category_name);
CREATE INDEX IF NOT EXISTS idx_orchid_varieties_name ON orchid_varieties(variety_name);

CREATE TABLE IF NOT EXISTS orchid_variety_traits (
    id BIGSERIAL PRIMARY KEY,
    variety_id BIGINT,
    variety_name VARCHAR(256) NOT NULL,
    category_name VARCHAR(128),
    trait_type VARCHAR(128) NOT NULL,
    trait_value TEXT NOT NULL,
    keywords TEXT
);

CREATE INDEX IF NOT EXISTS idx_orchid_traits_name ON orchid_variety_traits(variety_name);
CREATE INDEX IF NOT EXISTS idx_orchid_traits_type ON orchid_variety_traits(trait_type);

CREATE TABLE IF NOT EXISTS orchid_value_points (
    id BIGSERIAL PRIMARY KEY,
    variety_id BIGINT,
    variety_name VARCHAR(256) NOT NULL,
    category_name VARCHAR(128),
    value_type VARCHAR(128) NOT NULL,
    title VARCHAR(256),
    content TEXT NOT NULL,
    keywords TEXT
);

CREATE INDEX IF NOT EXISTS idx_orchid_value_points_name ON orchid_value_points(variety_name);
CREATE INDEX IF NOT EXISTS idx_orchid_value_points_type ON orchid_value_points(value_type);

CREATE TABLE IF NOT EXISTS orchid_skus (
    id BIGSERIAL PRIMARY KEY,
    category_name VARCHAR(128),
    variety_name VARCHAR(256) NOT NULL,
    seedling_count VARCHAR(128),
    package_spec VARCHAR(256),
    flower_bud_status VARCHAR(128),
    price DOUBLE PRECISION,
    price_text VARCHAR(256)
);

CREATE INDEX IF NOT EXISTS idx_orchid_skus_name ON orchid_skus(variety_name);

CREATE TABLE IF NOT EXISTS orchid_common_knowledge (
    id BIGSERIAL PRIMARY KEY,
    knowledge_category VARCHAR(256) NOT NULL,
    knowledge_type VARCHAR(128),
    applies_to_category VARCHAR(128),
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orchid_sales_copy (
    id BIGSERIAL PRIMARY KEY,
    writer_name VARCHAR(128),
    variety_name VARCHAR(256) NOT NULL,
    target_audience TEXT,
    product_background TEXT,
    leaf_posture TEXT,
    petal_type TEXT,
    flower_color TEXT,
    fragrance TEXT,
    flowering_period TEXT,
    care_difficulty TEXT,
    usage_scene TEXT,
    selling_points TEXT
);

CREATE INDEX IF NOT EXISTS idx_orchid_sales_copy_name ON orchid_sales_copy(variety_name);

CREATE TABLE IF NOT EXISTS orchid_hot_breakdowns (
    id BIGSERIAL PRIMARY KEY,
    variety_name VARCHAR(256) NOT NULL,
    category_name VARCHAR(128),
    status_history_supply_price_authenticity TEXT,
    aesthetic_traits TEXT,
    cultivation_care TEXT,
    consensus_reputation TEXT,
    raw_text TEXT
);

CREATE TABLE IF NOT EXISTS orchid_knowledge_chunks (
    id BIGSERIAL PRIMARY KEY,
    source_table VARCHAR(128) NOT NULL,
    source_id BIGINT,
    entity_type VARCHAR(128) NOT NULL,
    variety_name VARCHAR(256),
    category_name VARCHAR(128),
    chunk_type VARCHAR(128) NOT NULL,
    chunk_title VARCHAR(512) NOT NULL,
    content TEXT NOT NULL,
    embedding vector(1024)
);

CREATE INDEX IF NOT EXISTS idx_orchid_chunks_entity ON orchid_knowledge_chunks(entity_type);
CREATE INDEX IF NOT EXISTS idx_orchid_chunks_name ON orchid_knowledge_chunks(variety_name);
CREATE INDEX IF NOT EXISTS idx_orchid_chunks_type ON orchid_knowledge_chunks(chunk_type);
CREATE INDEX IF NOT EXISTS idx_orchid_chunks_embedding
    ON orchid_knowledge_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
