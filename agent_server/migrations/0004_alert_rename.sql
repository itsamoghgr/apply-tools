-- digest -> alert rename for the agent DB.
--
-- RENAME ONLY: no row is inserted, updated or deleted. The kind CHECK is the
-- one thing that cannot be a pure rename — existing rows hold kind='digest',
-- so the constraint is widened to accept both, the existing rows are relabelled
-- (a relabel, not a delete), and then it is narrowed to 'alert'.
--
-- Every statement is guarded, so re-running this file is a no-op.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name='jobboard_runs' AND column_name='digest_run_id') THEN
    ALTER TABLE jobboard_runs RENAME COLUMN digest_run_id TO alert_run_id;
  END IF;
END $$;

ALTER TABLE jobboard_runs DROP CONSTRAINT IF EXISTS jobboard_runs_kind_check;
UPDATE jobboard_runs SET kind = 'alert' WHERE kind = 'digest';
ALTER TABLE jobboard_runs
    ADD CONSTRAINT jobboard_runs_kind_check CHECK (kind IN ('monitor','alert'));
