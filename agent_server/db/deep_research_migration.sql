-- deep_research_migration.sql
-- Additive deep-research columns on the platform "Lead" table (apply_tools DB).
-- ALL existing columns are preserved; this only adds new ones.
-- Apply once against the apply_tools database. Idempotent (ADD COLUMN IF NOT EXISTS).
--
-- Applied OUT-OF-BAND (psql), same pattern as platform_migration.sql and the
-- earlier agent attribute columns (employeeCount/revenue/location/industry/
-- lastRoundDate). NOT applied via prisma migrate; mirrored into the Prisma
-- schema so Prisma stays in sync.
--
--   psql "$DATABASE_URL" -f agent_server/db/deep_research_migration.sql
--
-- ---------------------------------------------------------------------------
-- Deep-research columns on "Lead"
--   brief         — qualitative 2-4 sentence company summary.
--   foundingYear  — year founded, e.g. "2021".
--   totalRaised   — cumulative funding raised, e.g. "$18M".
--   investorsJson — list of investor names (jsonb array).
--   competitorsJson — list of competitor names (jsonb array).
--   keyPeopleJson — list of notable people (jsonb array).
--   fitScore      — authoritative 0-1 fit score against the user's ICP.
--   fitReason     — short explanation for the fit score.
-- ---------------------------------------------------------------------------

ALTER TABLE "Lead"
    ADD COLUMN IF NOT EXISTS "brief"           text,
    ADD COLUMN IF NOT EXISTS "foundingYear"    text,
    ADD COLUMN IF NOT EXISTS "totalRaised"     text,
    ADD COLUMN IF NOT EXISTS "investorsJson"   jsonb,
    ADD COLUMN IF NOT EXISTS "competitorsJson" jsonb,
    ADD COLUMN IF NOT EXISTS "keyPeopleJson"   jsonb,
    ADD COLUMN IF NOT EXISTS "fitScore"        double precision,
    ADD COLUMN IF NOT EXISTS "fitReason"       text;
