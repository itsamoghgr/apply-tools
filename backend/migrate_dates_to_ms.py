"""One-shot migration: convert JobApplication.appliedDate / decisionDate from
text-format ('YYYY-MM-DD HH:MM:SS') to INTEGER epoch milliseconds (UTC).

OBSOLETE (SQLite era): only relevant to the old SQLite epoch-ms storage. The
app now runs on Postgres with native timestamp columns; see
migrate_sqlite_to_postgres.py for the one-time data move. Kept for history.

Why: Prisma's SQLite adapter stores DateTime as INTEGER ms and binds query
inputs the same way. Mixing text + integer storage breaks Prisma range
filters (text >= integer is always true under SQLite type-affinity rules),
making "Applied today" / "This week" return totals.

Run from `backend/`:
    python migrate_dates_to_ms.py
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "apply-tools.db"


def parse_text_date(value: str) -> int:
    """Parse a stored text date and return epoch ms (UTC)."""
    s = value.strip()
    # Tolerate either 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DDTHH:MM:SS[.fff][Z]'.
    s = s.replace("T", " ").replace("Z", "")
    # Drop fractional seconds if present.
    if "." in s:
        s = s.split(".", 1)[0]
    if len(s) == 10:
        s = s + " 00:00:00"
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def main() -> int:
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}", file=sys.stderr)
        return 1

    backup = DB_PATH.with_suffix(".db.bak")
    print(f"Backing up {DB_PATH} -> {backup}")
    shutil.copy2(DB_PATH, backup)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        for col in ("appliedDate", "decisionDate"):
            rows = conn.execute(
                f'SELECT id, "{col}" AS v FROM JobApplication '
                f'WHERE "{col}" IS NOT NULL AND typeof("{col}") = ?',
                ("text",),
            ).fetchall()
            print(f"  {col}: {len(rows)} text rows to migrate")
            for r in rows:
                try:
                    ms = parse_text_date(r["v"])
                except ValueError as exc:
                    print(f"    skipping id={r['id']} ({col}={r['v']!r}): {exc}")
                    continue
                conn.execute(
                    f'UPDATE JobApplication SET "{col}" = ? WHERE id = ?',
                    (ms, r["id"]),
                )
        conn.commit()
    finally:
        conn.close()

    # Verify.
    conn = sqlite3.connect(DB_PATH)
    try:
        for col in ("appliedDate", "decisionDate"):
            counts = dict(
                conn.execute(
                    f'SELECT typeof("{col}"), COUNT(*) FROM JobApplication '
                    f'WHERE "{col}" IS NOT NULL GROUP BY typeof("{col}")'
                ).fetchall()
            )
            print(f"  post-migration {col} types:", counts)
    finally:
        conn.close()

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
