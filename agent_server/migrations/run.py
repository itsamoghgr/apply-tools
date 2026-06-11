"""Migration runner for the agent DB (apply_agent).

Tracks applied migrations in a `_migrations` table, then applies any .sql
files in this directory that haven't been run yet, in lexicographic order.

Usage:
    python -m agent_server.migrations.run
  or call run_migrations() from within Python.

The database is identified by AGENT_DATABASE_URL (falls through to the default
in CONFIG).  The runner is intentionally dead-simple: each .sql file is
executed as a single transaction; if it errors the transaction rolls back and
the runner aborts so nothing is partially applied.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# We accept the URL from the environment directly so this script can also be
# run standalone (before the full package is configured).
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent


def _normalise_url(url: str) -> str:
    """Pin to the psycopg v3 driver, matching backend/db.py.

    Also strips Prisma-style query params that the psycopg driver doesn't
    understand (e.g. ?schema=public, connection_limit).
    """
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    import re
    url = re.sub(r"\?.*$", "", url)
    return url


def _agent_database_url() -> str:
    url = os.environ.get("AGENT_DATABASE_URL")
    if url:
        return _normalise_url(url)
    # Fall through to CONFIG default so the runner works without an explicit env var.
    try:
        from agent_server.config import CONFIG  # noqa: PLC0415
        return _normalise_url(CONFIG.agent_database_url)
    except Exception:
        return "postgresql+psycopg://apply:apply@localhost:5432/apply_agent"


def run_migrations(database_url: str | None = None) -> None:
    """Apply all unapplied .sql migrations in order."""
    url = database_url or _agent_database_url()
    engine = create_engine(url, future=True)

    sql_files = sorted(p for p in _HERE.glob("*.sql"))
    if not sql_files:
        print("migrations/run.py: no .sql files found — nothing to do.")
        return

    with engine.begin() as conn:
        # Ensure the bookkeeping table exists.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS _migrations (
                name       text        PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
        """))

        applied = {
            row.name
            for row in conn.execute(text("SELECT name FROM _migrations")).fetchall()
        }

    for sql_file in sql_files:
        name = sql_file.name
        if name in applied:
            print(f"  skip  {name}  (already applied)")
            continue

        sql = sql_file.read_text(encoding="utf-8")
        print(f"  apply {name} …", end=" ", flush=True)
        with engine.begin() as conn:
            conn.execute(text(sql))
            conn.execute(
                text("INSERT INTO _migrations (name) VALUES (:name)"),
                {"name": name},
            )
        print("done")

    engine.dispose()
    print("migrations/run.py: all migrations applied.")


if __name__ == "__main__":
    run_migrations()
    sys.exit(0)
