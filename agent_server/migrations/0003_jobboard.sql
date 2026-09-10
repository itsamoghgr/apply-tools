-- 0003_jobboard.sql
-- Job Board operational bookkeeping (agent DB, apply_agent).
--
-- Per CONTRACTS.md §0 the agent DB is operational ONLY: it never holds a second
-- permanent copy of clean data. The watchlist and the postings themselves live
-- in the platform DB (apply_tools); these tables record only how each monitor /
-- digest run went, so "which board broke, and why" is answerable without
-- grepping logs.
--
-- Conventions follow 0001_init.sql: text/bigserial PKs, timestamptz (UTC),
-- CHECK-constrained status columns.

-- ---------------------------------------------------------------------------
-- jobboard_runs — one row per monitor cycle or digest attempt.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jobboard_runs (
    id                text        PRIMARY KEY,
    kind              text        NOT NULL CHECK (kind IN ('monitor','digest')),
    status            text        NOT NULL DEFAULT 'running'
                                  CHECK (status IN ('running','succeeded','failed')),
    trigger           text        NOT NULL DEFAULT 'schedule'
                                  CHECK (trigger IN ('schedule','manual')),
    companies_total   int         NOT NULL DEFAULT 0,
    companies_ok      int         NOT NULL DEFAULT 0,
    companies_failed  int         NOT NULL DEFAULT 0,
    postings_seen     int         NOT NULL DEFAULT 0,   -- returned by the source
    postings_matched  int         NOT NULL DEFAULT 0,   -- passed the role filter
    postings_new      int         NOT NULL DEFAULT 0,   -- genuinely inserted
    -- Platform DigestRun.id for kind='digest'. No FK: different database.
    digest_run_id     text,
    error             text,
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz
);

CREATE INDEX IF NOT EXISTS jobboard_runs_kind_idx
    ON jobboard_runs (kind, started_at DESC);

-- ---------------------------------------------------------------------------
-- jobboard_run_companies — per-company outcome within one monitor run.
--
-- `used_llm` and `pages_fetched` are >0/true only on the generic (non-ATS)
-- fallback path, so these columns show at a glance which companies are costing
-- LLM calls — i.e. which ones should be repointed at a real ATS board URL.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jobboard_run_companies (
    id                 bigserial   PRIMARY KEY,
    run_id             text        NOT NULL REFERENCES jobboard_runs (id) ON DELETE CASCADE,
    -- Platform WatchedCompany.id. No FK: different database.
    watched_company_id text        NOT NULL,
    company_name       text,
    ats                text,
    ok                 boolean     NOT NULL,
    postings_seen      int         NOT NULL DEFAULT 0,
    postings_matched   int         NOT NULL DEFAULT 0,
    postings_new       int         NOT NULL DEFAULT 0,
    pages_fetched      int         NOT NULL DEFAULT 1,
    used_llm           boolean     NOT NULL DEFAULT false,
    error              text,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS jobboard_run_companies_run_idx
    ON jobboard_run_companies (run_id);
