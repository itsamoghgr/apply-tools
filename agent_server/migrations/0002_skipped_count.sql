-- 0002_skipped_count.sql
-- Agent DB (apply_agent) — fit-gate support.
-- Adds jobs.skipped_count (companies dropped by the cheap fit gate before
-- deep research) and extends the seen_cache outcome CHECK to allow 'skipped'
-- so a low-fit company can be recorded in the seen-cache (never re-surfaced).
-- Idempotent; run via migrations/run.py.

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS skipped_count int DEFAULT 0;

-- seen_cache.outcome originally allowed ('verified','dropped'). The fit gate
-- records skipped companies with outcome='skipped'; widen the constraint.
ALTER TABLE seen_cache DROP CONSTRAINT IF EXISTS seen_cache_outcome_check;
ALTER TABLE seen_cache
    ADD CONSTRAINT seen_cache_outcome_check
    CHECK (outcome IN ('verified', 'dropped', 'skipped'));
