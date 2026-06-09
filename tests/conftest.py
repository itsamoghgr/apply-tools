"""Shared pytest fixtures for the agent server test suite.

The `live_db` fixture:
  - Tries to connect to the agent DB using AGENT_DATABASE_URL / CONFIG default.
  - If the DB is unreachable it calls pytest.skip() so the entire test is
    skipped cleanly (no error, just "s" in the output).
  - When the DB IS reachable it runs all pending migrations so the schema is
    always current before tests run.

Tests that need a live DB should request the `live_db` fixture.  Tests that
don't touch the DB need not request it.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def live_db():
    """Session-scoped fixture.  Skips the test session for this fixture if
    the agent DB is unreachable; otherwise runs migrations and returns the
    SQLAlchemy engine so tests can borrow connections if needed.
    """
    # Import lazily so that a missing AGENT_DATABASE_URL doesn't blow up the
    # entire collection phase.
    try:
        from agent_server.db.agent_db import get_engine, _normalise_url
        from agent_server.config import CONFIG
        from agent_server.migrations.run import run_migrations
        from sqlalchemy import text
    except ImportError as exc:
        pytest.skip(f"agent_server package not importable: {exc}")

    engine = get_engine()

    # Probe the connection — skip if Postgres is down.
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Agent DB unreachable ({exc}); skipping live-DB tests.")

    # Apply any unapplied migrations so the schema is always current.
    run_migrations(_normalise_url(CONFIG.agent_database_url))

    yield engine

    engine.dispose()


# Operational tables wiped before each live-DB test so the suite is isolated
# from rows left behind by prior runs (orchestrator/e2e tests write many outbox
# rows). FK CASCADE from jobs clears the children; seen_cache is independent.
_OPERATIONAL_TABLES = "outbox, audit_traces, checkpoints, seen_cache, jobs"


@pytest.fixture(autouse=True)
def _clean_agent_db(request):
    """Truncate operational tables before any test that uses `live_db`.

    Autouse but a no-op for tests that don't request `live_db` (most pure-logic
    tests), so non-DB tests stay fast and DB-free.
    """
    if "live_db" not in request.fixturenames:
        yield
        return
    engine = request.getfixturevalue("live_db")  # may pytest.skip()
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {_OPERATIONAL_TABLES} RESTART IDENTITY CASCADE"))
    yield
