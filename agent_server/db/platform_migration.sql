-- platform_migration.sql
-- Additive restructure of the platform "Lead" table (apply_tools DB).
-- ALL existing columns are preserved; this only adds new ones.
-- Apply once against the apply_tools database.  Idempotent (IF NOT EXISTS / DO NOTHING).
--
-- Owner: lead engineer applies this to backend (backend/db.py + backend/server.py).
-- Authored by: schema & infra agent per CONTRACTS §7.

-- ---------------------------------------------------------------------------
-- 1. New columns on the "Lead" table
--    domain          — normalised registrable root domain; UNIQUE across leads.
--    companyName     — canonical company name from the agent pipeline.
--    fundingStage    — e.g. "seed", "series_a".
--    fundingAmount   — raw string e.g. "$2M" (agents extract, platform stores).
--    founderName     — primary founder name.
--    confidence      — verification confidence score 0–1.
--    source          — discovery source label (open_web|yc_oss|product_hunt|rss).
--    sourcesJson     — full sources array from ResearchResult / VerifiedLead.
-- ---------------------------------------------------------------------------

ALTER TABLE "Lead"
    ADD COLUMN IF NOT EXISTS domain          text,
    ADD COLUMN IF NOT EXISTS "companyName"   text,
    ADD COLUMN IF NOT EXISTS "fundingStage"  text,
    ADD COLUMN IF NOT EXISTS "fundingAmount" text,
    ADD COLUMN IF NOT EXISTS "founderName"   text,
    ADD COLUMN IF NOT EXISTS confidence      double precision,
    ADD COLUMN IF NOT EXISTS source          text,
    ADD COLUMN IF NOT EXISTS "sourcesJson"   jsonb;

-- ---------------------------------------------------------------------------
-- 2. UNIQUE index on domain
--    Use CREATE UNIQUE INDEX … IF NOT EXISTS so the statement is idempotent.
--    Index is partial (WHERE domain IS NOT NULL) so existing rows with NULL
--    domain don't conflict with each other; only newly ingested agent-sourced
--    leads, which always carry a domain, are deduplicated by this index.
-- ---------------------------------------------------------------------------

CREATE UNIQUE INDEX IF NOT EXISTS lead_domain_unique_idx
    ON "Lead" (domain)
    WHERE domain IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Notes for the lead engineer
-- ---------------------------------------------------------------------------
-- Upsert mapping (PlatformUpsertRequest → Lead columns):
--   domain              → domain
--   company_name        → companyName
--   funding_stage       → fundingStage
--   funding_amount      → fundingAmount
--   founder_name        → founderName  (and also name when name is blank)
--   founder_email       → email
--   founder_linkedin_url→ linkedinUrl
--   confidence          → confidence
--   source              → source
--   sources[]           → sourcesJson (serialised as JSONB array)
--
-- The ON CONFLICT target for the upsert endpoint must be (domain) — use
-- the partial index above (WHERE domain IS NOT NULL).  Prisma users: add
-- @@unique([domain]) scoped to non-null at the Prisma schema level; for now
-- the raw SQL index suffices for the FastAPI server.py implementation.
