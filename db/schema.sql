-- Haqdar AI data model (frozen Day 1 contract). Idempotent: safe to run more than once.
-- Embedding dims (768) match gemini-embedding-001 at outputDimensionality=768; change both here and EMBED_DIM if you swap models.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schemes (
    id                 serial PRIMARY KEY,
    slug               text UNIQUE NOT NULL,
    name               text NOT NULL,
    name_hi            text,
    ministry           text,
    state              text NOT NULL DEFAULT 'ALL',      -- 'ALL' = central scheme
    category           text,
    benefits           text,
    benefits_hi        text,
    documents_required text[] NOT NULL DEFAULT '{}',
    criteria           jsonb NOT NULL DEFAULT '{}'::jsonb,
    verification       text NOT NULL DEFAULT 'auto'  CHECK (verification IN ('verified', 'auto', 'stale')),
    status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    source_url         text,
    pdf_url            text,
    content_hash       text,
    last_checked       timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scheme_versions (
    id           serial PRIMARY KEY,
    scheme_id    int NOT NULL REFERENCES schemes(id) ON DELETE CASCADE,
    content_hash text,
    raw_text     text,
    criteria     jsonb,
    captured_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    id         serial PRIMARY KEY,
    scheme_id  int NOT NULL REFERENCES schemes(id) ON DELETE CASCADE,
    lang       text NOT NULL DEFAULT 'en',               -- en | hi
    section    text NOT NULL,                            -- details | benefits | eligibility | ...
    page       int,
    text       text NOT NULL,
    embedding  vector(768),
    -- 'simple' config: no English stemming, so Devanagari tokens are kept as-is.
    tsv        tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(text, ''))) STORED
);

CREATE TABLE IF NOT EXISTS sessions (
    id         uuid PRIMARY KEY,
    profile    jsonb NOT NULL DEFAULT '{}'::jsonb,
    history    jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS change_log (
    id       serial PRIMARY KEY,
    run_at   timestamptz NOT NULL DEFAULT now(),
    added    int NOT NULL DEFAULT 0,
    updated  int NOT NULL DEFAULT 0,
    inactive int NOT NULL DEFAULT 0,
    details  jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS eval_questions (
    id              serial PRIMARY KEY,
    question        text NOT NULL,
    lang            text NOT NULL,
    profile         jsonb,
    gold_scheme_ids int[] NOT NULL DEFAULT '{}',
    gold_answer     text
);

-- Indexes: HNSW on embedding, GIN on tsv and criteria, B-tree on state and status.
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_gin        ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_scheme_idx     ON chunks (scheme_id);
CREATE INDEX IF NOT EXISTS schemes_criteria_gin  ON schemes USING gin (criteria);
CREATE INDEX IF NOT EXISTS schemes_state_idx     ON schemes (state);
CREATE INDEX IF NOT EXISTS schemes_status_idx    ON schemes (status);
CREATE INDEX IF NOT EXISTS schemes_category_idx  ON schemes (category);
