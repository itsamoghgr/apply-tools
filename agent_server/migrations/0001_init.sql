-- 0001_init.sql
-- Agent DB (apply_agent) — initial schema.
-- All timestamps are timestamptz (UTC).  Run via migrations/run.py.

-- ---------------------------------------------------------------------------
-- jobs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jobs (
    id              text        PRIMARY KEY,
    status          text        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending','running','succeeded','failed','stopped')),
    target_count    int         NOT NULL,
    verified_count  int         NOT NULL DEFAULT 0,
    candidates_total     int,
    candidates_processed int    NOT NULL DEFAULT 0,
    stop_reason     text        CHECK (stop_reason IN ('target_reached','exhausted','error')),
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz
);

CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs (status);

-- ---------------------------------------------------------------------------
-- checkpoints
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS checkpoints (
    id       bigserial   PRIMARY KEY,
    job_id   text        NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    stage    text        NOT NULL
                         CHECK (stage IN ('discovery','dedup','research','verify','deliver','loop')),
    cursor   int,
    state    jsonb       NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS checkpoints_job_stage_idx ON checkpoints (job_id, stage, id DESC);

-- ---------------------------------------------------------------------------
-- seen_cache
-- Seeded from platform /exists endpoint + updated in-flight.
-- domain is the registrable root domain, already normalised by the caller.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS seen_cache (
    domain   text        PRIMARY KEY,
    outcome  text        NOT NULL CHECK (outcome IN ('verified','dropped')),
    reason   text,
    job_id   text,           -- informational; no FK so rows survive job deletion
    seen_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS seen_cache_outcome_idx ON seen_cache (outcome);

-- ---------------------------------------------------------------------------
-- outbox
-- payload = PlatformUpsertRequest (jsonb).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS outbox (
    id         bigserial   PRIMARY KEY,
    job_id     text        NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    domain     text        NOT NULL,
    payload    jsonb       NOT NULL,
    status     text        NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending','sent','failed')),
    attempts   int         NOT NULL DEFAULT 0,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at    timestamptz
);

CREATE INDEX IF NOT EXISTS outbox_status_idx ON outbox (status);
CREATE INDEX IF NOT EXISTS outbox_job_id_idx ON outbox (job_id);

-- ---------------------------------------------------------------------------
-- audit_traces
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_traces (
    id         bigserial   PRIMARY KEY,
    job_id     text        NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    domain     text,
    stage      text        NOT NULL,
    event      text        NOT NULL,
    data       jsonb       NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_traces_job_id_idx ON audit_traces (job_id);
CREATE INDEX IF NOT EXISTS audit_traces_domain_idx ON audit_traces (domain) WHERE domain IS NOT NULL;
