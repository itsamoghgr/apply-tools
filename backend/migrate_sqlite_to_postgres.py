"""One-time data migration: copy every row from the old SQLite database
(data/apply-tools.db) into the new Postgres database.

The schema in Postgres is created by Prisma (`cd frontend && npx prisma migrate
deploy`). This script only moves data — it issues no DDL.

Two storage quirks of the old SQLite DB are normalised here:

  * DateTime columns are *mixed*: Prisma's SQLite adapter wrote epoch-ms
    integers, but some legacy rows hold text like '2026-04-28 23:51:00'.
    `_to_dt` accepts either and yields a timezone-aware UTC datetime, which
    psycopg binds straight into Postgres timestamp columns.
  * Booleans are stored as 0/1 integers; `_to_bool` converts them.

Usage (from backend/, with the venv active):

    DATABASE_URL=postgresql://apply:apply@localhost:5432/apply_tools \\
        python migrate_sqlite_to_postgres.py [--wipe] [--dry-run]

  --wipe     TRUNCATE the Postgres tables first (so the load is repeatable).
             Without it, the script refuses to run if any target table is
             non-empty, to avoid duplicate-key errors / double imports.
  --dry-run  Read and convert everything, report counts, but roll back.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from db import get_engine  # reuse the configured Postgres engine

REPO_ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATH = REPO_ROOT / "data" / "apply-tools.db"

# Tables in FK-dependency order: parents before children. The same order is
# reversed for TRUNCATE so cascades don't fight foreign keys.
#
# Each entry: (table, columns, datetime_cols, bool_cols). `columns` mirrors the
# SQLite column list 1:1 — every column is carried over.
TABLES: list[tuple[str, list[str], set[str], set[str]]] = [
    (
        "Resume",
        ["id", "label", "content", "isActive", "createdAt", "updatedAt"],
        {"createdAt", "updatedAt"},
        {"isActive"},
    ),
    (
        "Lead",
        [
            "id", "name", "email", "linkedinUrl", "linkedinProfile",
            "currentCompany", "role", "replied", "repliedAt", "notes",
            "createdAt", "updatedAt",
        ],
        {"repliedAt", "createdAt", "updatedAt"},
        {"replied"},
    ),
    (
        "Application",
        [
            "id", "mode", "company", "jobDescription", "resumeId", "output",
            "scoreData", "pdfPath", "createdAt",
        ],
        {"createdAt"},
        set(),
    ),
    (
        "JobApplication",
        [
            "id", "companyName", "jobRole", "location", "interviewStatus",
            "status", "appliedDate", "resumeId", "companyCareerPage",
            "decisionDate", "decisionTime", "notes", "hrName", "hrLinkedin",
            "hrEmail", "referral", "referralLinkedin", "jobDescription",
            "createdAt", "updatedAt", "jobUrl",
        ],
        {"appliedDate", "decisionDate", "createdAt", "updatedAt"},
        set(),
    ),
    (
        "ReachOut",
        [
            "id", "recipientName", "recipientEmail", "linkedinProfile",
            "contextNote", "resumeId", "leadId", "subject", "body", "htmlBody",
            "status", "sentAt", "errorMessage", "createdAt", "updatedAt",
            "jobApplicationId", "channel",
        ],
        {"sentAt", "createdAt", "updatedAt"},
        set(),
    ),
    (
        "JobApplicationLead",
        ["jobApplicationId", "leadId", "role", "createdAt"],
        {"createdAt"},
        set(),
    ),
    (
        "Setting",
        ["key", "value", "updatedAt"],
        {"updatedAt"},
        set(),
    ),
]


def _to_dt(value: Any) -> datetime | None:
    """Normalise a SQLite DateTime cell to a tz-aware UTC datetime.

    Handles the three shapes seen in the legacy DB: epoch-ms int, numeric
    string, and 'YYYY-MM-DD HH:MM:SS' / ISO text.
    """
    if value is None or value == "":
        return None
    # Epoch milliseconds (int, or an all-digit string).
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    s = str(value).strip()
    if s.lstrip("-").isdigit():
        return datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc)
    # Text timestamp. Tolerate a space or 'T' separator and a trailing 'Z'.
    s = s.replace("T", " ").replace("Z", "")
    if "." in s:
        s = s.split(".", 1)[0]
    if len(s) == 10:  # date only
        s += " 00:00:00"
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _convert_row(
    row: sqlite3.Row,
    columns: list[str],
    dt_cols: set[str],
    bool_cols: set[str],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in columns:
        v = row[col]
        if col in dt_cols:
            v = _to_dt(v)
        elif col in bool_cols:
            v = _to_bool(v)
        out[col] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wipe", action="store_true", help="TRUNCATE targets first")
    ap.add_argument("--dry-run", action="store_true", help="convert but roll back")
    args = ap.parse_args()

    if not SQLITE_PATH.exists():
        print(f"SQLite DB not found at {SQLITE_PATH}", file=sys.stderr)
        return 1

    src = sqlite3.connect(str(SQLITE_PATH))
    src.row_factory = sqlite3.Row

    total = 0
    # One transaction for the whole load: all-or-nothing.
    with get_engine().begin() as conn:
        if args.wipe:
            # CASCADE + reversed order clears children before parents safely.
            names = ", ".join(f'"{t}"' for t, *_ in TABLES)
            conn.execute(text(f"TRUNCATE {names} CASCADE"))
            print(f"Wiped: {names}")
        else:
            for table, *_ in TABLES:
                n = conn.execute(
                    text(f'SELECT COUNT(*) FROM "{table}"')
                ).scalar_one()
                if n:
                    print(
                        f"Refusing to run: \"{table}\" already has {n} rows. "
                        "Re-run with --wipe to replace, or empty it first.",
                        file=sys.stderr,
                    )
                    return 1

        for table, columns, dt_cols, bool_cols in TABLES:
            rows = src.execute(f'SELECT * FROM "{table}"').fetchall()
            if not rows:
                print(f"  {table}: 0 rows")
                continue
            col_sql = ", ".join(f'"{c}"' for c in columns)
            bind_sql = ", ".join(f":{c}" for c in columns)
            payload = [
                _convert_row(r, columns, dt_cols, bool_cols) for r in rows
            ]
            conn.execute(
                text(f'INSERT INTO "{table}" ({col_sql}) VALUES ({bind_sql})'),
                payload,
            )
            total += len(payload)
            print(f"  {table}: {len(payload)} rows")

        if args.dry_run:
            print("Dry run — rolling back.")
            raise _Rollback()

    src.close()
    print(f"Done. Migrated {total} rows.")
    return 0


class _Rollback(Exception):
    """Sentinel to abort the _engine.begin() transaction on --dry-run."""


if __name__ == "__main__":
    try:
        sys.exit(main())
    except _Rollback:
        # Expected on --dry-run: the transaction rolled back cleanly.
        sys.exit(0)
