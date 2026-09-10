-- digest -> alert rename. RENAME ONLY: no row is inserted, updated or deleted.
-- Every statement is guarded so re-running is a no-op.
BEGIN;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema='public' AND table_name='DigestRun') THEN
    ALTER TABLE "DigestRun" RENAME TO "AlertRun";
  END IF;

  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema='public' AND table_name='JobPosting'
               AND column_name='digestRunId') THEN
    ALTER TABLE "JobPosting" RENAME COLUMN "digestRunId" TO "alertRunId";
  END IF;
END $$;

ALTER INDEX IF EXISTS "DigestRun_pkey"             RENAME TO "AlertRun_pkey";
ALTER INDEX IF EXISTS "DigestRun_startedAt_idx"    RENAME TO "AlertRun_startedAt_idx";
ALTER INDEX IF EXISTS "DigestRun_sent_idx"         RENAME TO "AlertRun_sent_idx";
ALTER INDEX IF EXISTS "JobPosting_digestRunId_idx" RENAME TO "JobPosting_alertRunId_idx";
ALTER INDEX IF EXISTS "JobPosting_undigested_idx"  RENAME TO "JobPosting_unalerted_idx";

COMMIT;
