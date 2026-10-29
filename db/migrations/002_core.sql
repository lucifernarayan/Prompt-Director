-- Core schema for Smart Prompt Director

-- projects(id, memory_version)
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    memory_version INT NOT NULL DEFAULT 1
);

-- project_members(user_id, project_id)
CREATE TABLE IF NOT EXISTS project_members (
    user_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, project_id)
);

-- memory_items(project_id, id, kind, body, license, tsv, embedding vector(64)) PK (project_id, id)
CREATE TABLE IF NOT EXISTS memory_items (
    project_id TEXT NOT NULL,
    id TEXT NOT NULL,
    kind TEXT NOT NULL,
    body TEXT NOT NULL,
    license TEXT NOT NULL,
    tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', COALESCE(body, ''))) STORED,
    embedding VECTOR(64),
    PRIMARY KEY (project_id, id)
);

CREATE INDEX IF NOT EXISTS idx_memory_items_tsv ON memory_items USING gin (tsv);

-- accounts(user_id, balance_micro bigint) with CHECK balance_micro >= 0
CREATE TABLE IF NOT EXISTS accounts (
    user_id TEXT PRIMARY KEY,
    balance_micro BIGINT NOT NULL DEFAULT 0 CHECK (balance_micro >= 0)
);

-- account_ledger(job_id, phase, amount_micro) unique (job_id, phase) with CHECK phase IN ('hold', 'settle', 'release')
CREATE TABLE IF NOT EXISTS account_ledger (
    job_id TEXT NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN ('hold', 'settle', 'release')),
    amount_micro BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT account_ledger_job_phase_key UNIQUE (job_id, phase)
);

-- jobs(id, user_id, project_id, idempotency_key, request_hash, status, cancel_requested, lease_owner, lease_expires_at, run_after, quote_micro, cost_cap_micro, hold_micro, charged_micro, flags, result, created_at) unique (user_id, idempotency_key)
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    run_after TIMESTAMPTZ,
    quote_micro BIGINT NOT NULL DEFAULT 0,
    cost_cap_micro BIGINT NOT NULL DEFAULT 0,
    hold_micro BIGINT NOT NULL DEFAULT 0,
    charged_micro BIGINT NOT NULL DEFAULT 0,
    flags JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT jobs_user_id_idempotency_key_key UNIQUE (user_id, idempotency_key)
);

-- checkpoints(job_id, seq, node_done, next_node, state jsonb)
CREATE TABLE IF NOT EXISTS checkpoints (
    job_id TEXT NOT NULL,
    seq INT NOT NULL,
    node_done TEXT NOT NULL,
    next_node TEXT,
    state JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (job_id, seq)
);

-- llm_calls(job_id, call_key, model, tokens_in, tokens_out, cost_micro, outcome, response) unique (job_id, call_key)
CREATE TABLE IF NOT EXISTS llm_calls (
    job_id TEXT NOT NULL,
    call_key TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INT NOT NULL DEFAULT 0,
    tokens_out INT NOT NULL DEFAULT 0,
    cost_micro BIGINT NOT NULL DEFAULT 0,
    outcome TEXT NOT NULL,
    response JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT llm_calls_job_id_call_key_key UNIQUE (job_id, call_key)
);

-- job_events(id bigserial, job_id, type, data jsonb)
CREATE TABLE IF NOT EXISTS job_events (
    id BIGSERIAL PRIMARY KEY,
    job_id TEXT NOT NULL,
    type TEXT NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- exact_cache(key pk, project_id, result)
CREATE TABLE IF NOT EXISTS exact_cache (
    key TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- semantic_cache(project_id, memory_version, settings_hash, embedding vector(64), result)
CREATE TABLE IF NOT EXISTS semantic_cache (
    id BIGSERIAL PRIMARY KEY,
    project_id TEXT NOT NULL,
    memory_version INT NOT NULL,
    settings_hash TEXT NOT NULL,
    embedding VECTOR(64) NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- inflight(key pk, job_id, owner_token, lease_expires_at)
CREATE TABLE IF NOT EXISTS inflight (
    key TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    owner_token TEXT NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL
);

-- feedback(job_id, decision)
CREATE TABLE IF NOT EXISTS feedback (
    job_id TEXT PRIMARY KEY,
    decision TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- chaos(name pk, value)
CREATE TABLE IF NOT EXISTS chaos (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
