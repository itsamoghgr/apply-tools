"""One-shot migration: snap JobApplication.appliedDate / decisionDate back to
UTC midnight of their intended calendar date.

WHY
----
The SQLite -> Postgres move (see migrate_sqlite_to_postgres.py) left the date
columns time-shifted: every value sits at 19:00 or 20:00 UTC instead of the
00:00 UTC midnight the app stores for a `<input type="date">` value (see
server._coerce_date). Because the dashboard ("Applied today", "This week")
compares against UTC-midnight-of-the-local-day boundaries, those shifted rows
fall on the *wrong* side of the boundary — e.g. an application made "today"
shows up as 0 applied today.

THE FIX
-------
Round each non-midnight timestamp to the NEAREST UTC midnight. The shift is a
fixed 19:00/20:00 (EST/EDT), both > 12:00, so every affected row rounds UP to
the following midnight — which is exactly the intended calendar date. Verified
against the data before writing this script: 2026-06-01T20:00Z -> 2026-06-02
(today), with zero rows whose time-of-day is < 12:00 (none round the wrong way).

SAFETY
------
- Idempotent: rows already at 00:00:00 UTC are skipped, so re-running is a no-op.
  New rows created through the UI are stored correctly (00:00) and are untouched.
- Reversible: the original values are copied into backup columns
  `appliedDate_premigration` / `decisionDate_premigration` before any update.
  To roll back: UPDATE ... SET "appliedDate" = "appliedDate_premigration" ...

Run from `backend/` with DATABASE_URL pointing at the Postgres instance:
    python migrate_normalize_applied_dates.py            # apply
    python migrate_normalize_applied_dates.py --dry-run  # report only, no writes
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from db import get_conn

# Round-to-nearest-UTC-midnight, done in SQL so it runs server-side in one pass.
# date_trunc('day', ts + 12h) rounds to the nearest day: adding 12h pushes any
# time >= 12:00 into the next day before truncating, and leaves < 12:00 on the
# same day. Our data is all 19:00/20:00, so everything rounds up correctly.
_ROUND_EXPR = "date_trunc('day', {col} + interval '12 hours')"


def _counts(conn) -> tuple[int, int]:
    """Return (appliedDate, decisionDate) rows NOT already at 00:00:00 UTC."""
    applied = conn.execute(
        text(
            'SELECT COUNT(*) FROM "JobApplication" '
            "WHERE \"appliedDate\" <> date_trunc('day', \"appliedDate\")"
        )
    ).scalar_one()
    decision = conn.execute(
        text(
            'SELECT COUNT(*) FROM "JobApplication" '
            'WHERE "decisionDate" IS NOT NULL '
            "AND \"decisionDate\" <> date_trunc('day', \"decisionDate\")"
        )
    ).scalar_one()
    return applied, decision


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    with get_conn() as conn:
        before_applied, before_decision = _counts(conn)
        total = conn.execute(text('SELECT COUNT(*) FROM "JobApplication"')).scalar_one()

        print(f"Total JobApplication rows: {total}")
        print(f"appliedDate rows off-midnight: {before_applied}")
        print(f"decisionDate rows off-midnight: {before_decision}")

        if before_applied == 0 and before_decision == 0:
            print("Nothing to do — all dates already at UTC midnight.")
            return

        if dry_run:
            # Show a few example mappings without writing.
            rows = conn.execute(
                text(
                    'SELECT "appliedDate" AS before, '
                    f"{_ROUND_EXPR.format(col='\"appliedDate\"')} AS after "
                    'FROM "JobApplication" '
                    "WHERE \"appliedDate\" <> date_trunc('day', \"appliedDate\") "
                    'ORDER BY "appliedDate" DESC LIMIT 5'
                )
            ).all()
            print("\nDRY RUN — example appliedDate mappings (no writes):")
            for r in rows:
                print(f"  {r.before.isoformat()}  ->  {r.after.isoformat()}")
            print("\nRe-run without --dry-run to apply.")
            return

        # 1) Back up the original columns (idempotent: add if missing, fill once).
        print("\nBacking up original values to *_premigration columns…")
        conn.execute(
            text(
                'ALTER TABLE "JobApplication" '
                'ADD COLUMN IF NOT EXISTS "appliedDate_premigration" timestamp(3), '
                'ADD COLUMN IF NOT EXISTS "decisionDate_premigration" timestamp(3)'
            )
        )
        # Only fill backup where it's still NULL, so re-runs don't overwrite the
        # backup with already-normalized values.
        conn.execute(
            text(
                'UPDATE "JobApplication" '
                'SET "appliedDate_premigration" = "appliedDate" '
                'WHERE "appliedDate_premigration" IS NULL'
            )
        )
        conn.execute(
            text(
                'UPDATE "JobApplication" '
                'SET "decisionDate_premigration" = "decisionDate" '
                'WHERE "decisionDate_premigration" IS NULL AND "decisionDate" IS NOT NULL'
            )
        )

        # 2) Normalize to nearest UTC midnight (only the off-midnight rows).
        print("Normalizing appliedDate / decisionDate to UTC midnight…")
        applied_updated = conn.execute(
            text(
                'UPDATE "JobApplication" '
                f"SET \"appliedDate\" = {_ROUND_EXPR.format(col='\"appliedDate\"')} "
                "WHERE \"appliedDate\" <> date_trunc('day', \"appliedDate\")"
            )
        ).rowcount
        decision_updated = conn.execute(
            text(
                'UPDATE "JobApplication" '
                f"SET \"decisionDate\" = {_ROUND_EXPR.format(col='\"decisionDate\"')} "
                'WHERE "decisionDate" IS NOT NULL '
                "AND \"decisionDate\" <> date_trunc('day', \"decisionDate\")"
            )
        ).rowcount

        after_applied, after_decision = _counts(conn)

    print(f"\nUpdated appliedDate rows: {applied_updated}")
    print(f"Updated decisionDate rows: {decision_updated}")
    print(f"Remaining off-midnight appliedDate: {after_applied} (expect 0)")
    print(f"Remaining off-midnight decisionDate: {after_decision} (expect 0)")
    print("\nDone. Backup columns: appliedDate_premigration, decisionDate_premigration.")


if __name__ == "__main__":
    main()
