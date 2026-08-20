-- jobboard_migration.sql — Job Board (career-page monitor) tables.
--
-- Applied OUT-OF-BAND with psql, NOT via `prisma migrate`: this database has
-- drifted from the Prisma migration history, so `migrate dev` / `db push` are
-- unsafe here. Mirror any change into frontend/prisma/schema.prisma by hand and
-- re-run `npx prisma generate`.
--
-- ADDITIVE ONLY. No DROP, no DELETE, no TRUNCATE. Safe to re-run (idempotent).
--
--   psql "$DATABASE_URL" -f frontend/prisma/sql/jobboard_migration.sql
--
-- Conventions match the surrounding Prisma-owned tables: text primary keys
-- (secrets.token_urlsafe(12) from the backend), timestamp(3) without time zone
-- holding UTC, quoted camelCase column names.

-- ---------------------------------------------------------------------------
-- WatchedCompany — the user-managed watchlist. One row per career page.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "WatchedCompany" (
    "id"            text        PRIMARY KEY,
    "name"          text        NOT NULL,
    "careerUrl"     text        NOT NULL,
    "domain"        text,
    "logoUrl"       text,
    -- greenhouse | lever | ashby | smartrecruiters | generic. Detected from
    -- careerUrl at add time and stored, so it is visible/correctable in the UI
    -- rather than re-guessed every cycle.
    "ats"           text,
    "atsSlug"       text,
    -- NULL = inherit the global matcher set (Setting 'jobboard.roleMatchers').
    "roleFilter"    jsonb,
    -- Scrape config. These DISCARD postings at scrape time rather than filtering
    -- at display time, so they are deliberately coarse and user-controlled.
    -- maxAgeDays: skip postings older than N days (NULL = any age).
    -- countryFilter: ISO-3166 alpha-2 codes to keep (NULL/[] = all). A posting
    -- whose country cannot be parsed is ALWAYS kept — an unrecognised location
    -- must never be the reason a real job is dropped.
    "maxAgeDays"    integer,
    "countryFilter" jsonb,
    "active"        boolean     NOT NULL DEFAULT true,
    -- Set once the first-cycle backlog seed completes. Until then the whole
    -- board is written with isNew=false so adding a company never floods a digest.
    "seededAt"      timestamp(3),
    "lastCheckedAt" timestamp(3),
    "lastStatus"    text,
    "lastError"     text,
    "createdAt"     timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"     timestamp(3) NOT NULL
);

-- Makes add-company an idempotent upsert instead of a duplicate-row error.
CREATE UNIQUE INDEX IF NOT EXISTS "WatchedCompany_careerUrl_key"
    ON "WatchedCompany" ("careerUrl");
CREATE INDEX IF NOT EXISTS "WatchedCompany_active_idx"
    ON "WatchedCompany" ("active");

-- ---------------------------------------------------------------------------
-- JobPosting — a role found on a watched board that matched the role filter.
-- Non-matching postings are counted for observability but never stored.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "JobPosting" (
    "id"               text        PRIMARY KEY,
    "watchedCompanyId" text        NOT NULL
        REFERENCES "WatchedCompany"("id") ON DELETE CASCADE,
    -- ATS external id when the source provides one, else a sha1 of the
    -- normalized title + location + url path. See jobboard/matcher.py.
    "dedupKey"         text        NOT NULL,
    "externalId"       text,
    "title"            text        NOT NULL,
    "matchedRole"      text        NOT NULL,
    "location"         text,
    "url"              text        NOT NULL,
    -- Source-provided posting date (UTC). NULL when the board doesn't say —
    -- novelty is decided by dedupKey, never by this column.
    "postedAt"         timestamp(3),
    -- Minimum years of experience parsed from the job description, when the
    -- posting states one. NULL means "not stated" — never a guess.
    "minYears"         integer,
    -- ISO-3166 alpha-2 parsed from `location`; NULL when it cannot be resolved.
    "country"          text,
    "firstSeenAt"      timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- false for rows written by a company's seed cycle (pre-existing backlog).
    "isNew"            boolean     NOT NULL DEFAULT true,
    -- Stamped when a digest reports this posting, so it is never reported twice.
    "digestRunId"      text,
    -- Set when the posting disappears from the board. Rows are NEVER deleted.
    "archivedAt"       timestamp(3),
    "jobApplicationId" text
        REFERENCES "JobApplication"("id") ON DELETE SET NULL,
    "createdAt"        timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"        timestamp(3) NOT NULL
);

-- THE central invariant. Every posting write is an ON CONFLICT DO UPDATE against
-- this index, which is what makes a monitor cycle idempotent: re-running it, or
-- crashing halfway through, can never duplicate a posting.
CREATE UNIQUE INDEX IF NOT EXISTS "JobPosting_company_dedup_key"
    ON "JobPosting" ("watchedCompanyId", "dedupKey");

-- Digest query: unreported new postings since the watermark.
CREATE INDEX IF NOT EXISTS "JobPosting_undigested_idx"
    ON "JobPosting" ("firstSeenAt")
    WHERE "digestRunId" IS NULL AND "isNew";
-- Experience filter: live postings within a seniority band.
CREATE INDEX IF NOT EXISTS "JobPosting_minYears_idx"
    ON "JobPosting" ("minYears") WHERE "archivedAt" IS NULL;
CREATE INDEX IF NOT EXISTS "JobPosting_country_idx"
    ON "JobPosting" ("country") WHERE "archivedAt" IS NULL;
CREATE INDEX IF NOT EXISTS "JobPosting_digestRunId_idx"
    ON "JobPosting" ("digestRunId");
-- Feed query: live (non-archived) postings for one company, newest first.
CREATE INDEX IF NOT EXISTS "JobPosting_company_live_idx"
    ON "JobPosting" ("watchedCompanyId", "firstSeenAt" DESC)
    WHERE "archivedAt" IS NULL;

-- ---------------------------------------------------------------------------
-- DigestRun — one row per digest attempt. finishedAt of the latest status='sent'
-- row is the watermark that bounds the next digest window, so a failed or
-- delayed run carries its postings forward instead of dropping them.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS "DigestRun" (
    "id"           text        PRIMARY KEY,
    "startedAt"    timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "finishedAt"   timestamp(3),
    "windowStart"  timestamp(3),
    "windowEnd"    timestamp(3) NOT NULL,
    "newCount"     integer     NOT NULL DEFAULT 0,
    "companyCount" integer     NOT NULL DEFAULT 0,
    -- pending | sent | skipped | failed
    "status"       text        NOT NULL DEFAULT 'pending',
    "error"        text
);

CREATE INDEX IF NOT EXISTS "DigestRun_startedAt_idx"
    ON "DigestRun" ("startedAt" DESC);
-- Watermark lookup: the most recent digest that actually mailed.
CREATE INDEX IF NOT EXISTS "DigestRun_sent_idx"
    ON "DigestRun" ("finishedAt" DESC) WHERE "status" = 'sent';
