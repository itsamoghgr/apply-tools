"""Typed helpers for the agent DB (apply_agent).

Connection style mirrors backend/db.py exactly:
  - URL normalisation to postgresql+psycopg://
  - Single pooled Engine via get_engine()
  - get_conn() context manager (auto-commit on success, rollback on exception)
  - _row_to_dict() coerces datetimes to ISO-8601 strings

All timestamps are stored as timestamptz (UTC).  Callers pass plain Python
dicts for jsonb columns; this module serialises/deserialises transparently.
"""

from __future__ import annotations

import json
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, Row

from agent_server.config import CONFIG
from agent_server.log import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Engine (singleton, created lazily)
# ---------------------------------------------------------------------------

_engine_singleton: Engine | None = None

_DATETIME_COLUMNS = frozenset({
    "created_at",
    "updated_at",
    "finished_at",
    "seen_at",
    "sent_at",
})


def _normalise_url(url: str) -> str:
    """Pin to the psycopg v3 driver, matching backend/db.py.

    Also strips Prisma-style query params (e.g. ?schema=public,
    connection_limit) that the psycopg driver doesn't understand.
    """
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    # Strip query params regardless of the final scheme.
    url = re.sub(r"\?.*$", "", url)
    return url


def get_engine() -> Engine:
    global _engine_singleton
    if _engine_singleton is None:
        db_url = _normalise_url(CONFIG.agent_database_url)
        _engine_singleton = create_engine(
            db_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            future=True,
        )
    return _engine_singleton


@contextmanager
def get_conn() -> Iterator[Connection]:
    """Yield a transactional SQLAlchemy connection.

    Commits on success, rolls back on exception — callers never call
    .commit() themselves, mirroring backend/db.py.
    """
    with get_engine().begin() as conn:
        yield conn


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: Row) -> dict[str, Any]:
    """Convert a SQLAlchemy Row to a plain dict.

    - datetime columns are ISO-8601 strings (UTC-aware).
    - jsonb columns come back as dicts/lists already (psycopg v3 decodes them).
      If for some reason they arrive as strings, we parse them.
    """
    d = dict(row._mapping)
    for key, value in d.items():
        if isinstance(value, datetime) and key in _DATETIME_COLUMNS:
            d[key] = value.isoformat()
        elif isinstance(value, str) and key in ("state", "payload", "data"):
            try:
                d[key] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def _rows_to_dicts(rows) -> list[dict[str, Any]]:
    return [_row_to_dict(r) for r in rows]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------

def create_job(target_count: int) -> str:
    """Insert a new job row with status 'pending'. Returns the job_id."""
    job_id = secrets.token_urlsafe(12)
    with get_conn() as conn:
        conn.execute(
            text("""
                INSERT INTO jobs (id, status, target_count, created_at, updated_at)
                VALUES (:id, 'pending', :target_count, :now, :now)
            """),
            {"id": job_id, "target_count": target_count, "now": _utcnow()},
        )
    logger.info("job_created", job_id=job_id, target_count=target_count)
    return job_id


def get_job(job_id: str) -> dict | None:
    """Return the job row as a dict, or None if not found."""
    with get_conn() as conn:
        row = conn.execute(
            text("SELECT * FROM jobs WHERE id = :id"),
            {"id": job_id},
        ).fetchone()
    return _row_to_dict(row) if row else None


def update_job(job_id: str, **fields) -> None:
    """Patch a job row.  Accepted field names:
      status, verified_count, candidates_total, candidates_processed,
      stop_reason, error, finished_at.
    updated_at is always bumped.
    """
    allowed = {
        "status", "verified_count", "candidates_total",
        "candidates_processed", "stop_reason", "error", "finished_at",
        "skipped_count",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = _utcnow()
    updates["_id"] = job_id
    set_sql = ", ".join(f"{k} = :{k}" for k in updates if k != "_id")
    with get_conn() as conn:
        conn.execute(
            text(f"UPDATE jobs SET {set_sql} WHERE id = :_id"),
            updates,
        )
    logger.debug("job_updated", job_id=job_id, fields=list(fields.keys()))


# ---------------------------------------------------------------------------
# checkpoints
# ---------------------------------------------------------------------------

def add_checkpoint(
    job_id: str,
    stage: str,
    cursor: int | None,
    state: dict,
) -> None:
    """Append a checkpoint row for the given job + stage."""
    with get_conn() as conn:
        conn.execute(
            text("""
                INSERT INTO checkpoints (job_id, stage, cursor, state, created_at)
                VALUES (:job_id, :stage, :cursor, :state, :now)
            """),
            {
                "job_id": job_id,
                "stage": stage,
                "cursor": cursor,
                "state": json.dumps(state),
                "now": _utcnow(),
            },
        )


def latest_checkpoint(job_id: str, stage: str | None = None) -> dict | None:
    """Return the most recent checkpoint for job_id (optionally filtered by stage)."""
    if stage is not None:
        sql = """
            SELECT * FROM checkpoints
            WHERE job_id = :job_id AND stage = :stage
            ORDER BY id DESC LIMIT 1
        """
        params: dict = {"job_id": job_id, "stage": stage}
    else:
        sql = """
            SELECT * FROM checkpoints
            WHERE job_id = :job_id
            ORDER BY id DESC LIMIT 1
        """
        params = {"job_id": job_id}

    with get_conn() as conn:
        row = conn.execute(text(sql), params).fetchone()
    return _row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# seen_cache
# ---------------------------------------------------------------------------

def seen_has(domain: str) -> bool:
    """Return True if the domain is already in the seen cache."""
    with get_conn() as conn:
        row = conn.execute(
            text("SELECT 1 FROM seen_cache WHERE domain = :domain"),
            {"domain": domain},
        ).fetchone()
    return row is not None


def seen_add(
    domain: str,
    outcome: str,
    *,
    reason: str | None = None,
    job_id: str | None = None,
) -> None:
    """Upsert a single domain into seen_cache.  ON CONFLICT updates outcome/reason."""
    with get_conn() as conn:
        conn.execute(
            text("""
                INSERT INTO seen_cache (domain, outcome, reason, job_id, seen_at)
                VALUES (:domain, :outcome, :reason, :job_id, :now)
                ON CONFLICT (domain) DO UPDATE
                    SET outcome = EXCLUDED.outcome,
                        reason  = EXCLUDED.reason,
                        job_id  = EXCLUDED.job_id,
                        seen_at = EXCLUDED.seen_at
            """),
            {
                "domain": domain,
                "outcome": outcome,
                "reason": reason,
                "job_id": job_id,
                "now": _utcnow(),
            },
        )


def seen_bulk_add(rows: list[dict]) -> None:
    """Upsert many domains at once.

    Each dict must have keys: domain, outcome; optionally reason, job_id.
    Uses ON CONFLICT(domain) DO NOTHING — bulk ingests from the platform
    exists-endpoint don't need to overwrite local state.
    """
    if not rows:
        return
    now = _utcnow()
    with get_conn() as conn:
        conn.execute(
            text("""
                INSERT INTO seen_cache (domain, outcome, reason, job_id, seen_at)
                VALUES (:domain, :outcome, :reason, :job_id, :now)
                ON CONFLICT (domain) DO NOTHING
            """),
            [
                {
                    "domain": r["domain"],
                    "outcome": r["outcome"],
                    "reason": r.get("reason"),
                    "job_id": r.get("job_id"),
                    "now": now,
                }
                for r in rows
            ],
        )


def seen_filter_unknown(domains: list[str]) -> list[str]:
    """Return only the domains NOT already in seen_cache.

    Preserves the relative order of the input list.
    """
    if not domains:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            text("SELECT domain FROM seen_cache WHERE domain = ANY(:domains)"),
            {"domains": domains},
        ).fetchall()
    known = {r.domain for r in rows}
    return [d for d in domains if d not in known]


# ---------------------------------------------------------------------------
# outbox
# ---------------------------------------------------------------------------

def outbox_add(job_id: str, domain: str, payload: dict) -> int:
    """Insert a pending outbox row. Returns the new row id."""
    with get_conn() as conn:
        row = conn.execute(
            text("""
                INSERT INTO outbox (job_id, domain, payload, status, attempts, created_at)
                VALUES (:job_id, :domain, :payload, 'pending', 0, :now)
                RETURNING id
            """),
            {
                "job_id": job_id,
                "domain": domain,
                "payload": json.dumps(payload),
                "now": _utcnow(),
            },
        ).fetchone()
    return row.id


def outbox_pending(limit: int = 100) -> list[dict]:
    """Return up to `limit` pending outbox rows, oldest first."""
    with get_conn() as conn:
        rows = conn.execute(
            text("""
                SELECT * FROM outbox
                WHERE status = 'pending'
                ORDER BY id ASC
                LIMIT :limit
            """),
            {"limit": limit},
        ).fetchall()
    return _rows_to_dicts(rows)


def outbox_retryable(limit: int = 100) -> list[dict]:
    """Return up to `limit` rows that still need delivery — both 'pending' (never
    attempted) and 'failed' (attempted but the platform was unreachable). Oldest
    first. Used by deliver.retry_pending() so a platform outage loses nothing.
    """
    with get_conn() as conn:
        rows = conn.execute(
            text("""
                SELECT * FROM outbox
                WHERE status IN ('pending', 'failed')
                ORDER BY id ASC
                LIMIT :limit
            """),
            {"limit": limit},
        ).fetchall()
    return _rows_to_dicts(rows)


def outbox_mark_sent(outbox_id: int) -> None:
    """Mark an outbox row as sent."""
    with get_conn() as conn:
        conn.execute(
            text("""
                UPDATE outbox
                SET status = 'sent', sent_at = :now, attempts = attempts + 1
                WHERE id = :id
            """),
            {"id": outbox_id, "now": _utcnow()},
        )


def outbox_mark_failed(outbox_id: int, error: str) -> None:
    """Increment attempts and record the error; status stays 'failed'."""
    with get_conn() as conn:
        conn.execute(
            text("""
                UPDATE outbox
                SET status = 'failed',
                    attempts = attempts + 1,
                    last_error = :error
                WHERE id = :id
            """),
            {"id": outbox_id, "error": error},
        )


# ---------------------------------------------------------------------------
# audit_traces
# ---------------------------------------------------------------------------

def audit_add(
    job_id: str,
    stage: str,
    event: str,
    data: dict,
    *,
    domain: str | None = None,
) -> None:
    """Append an audit trace row."""
    with get_conn() as conn:
        conn.execute(
            text("""
                INSERT INTO audit_traces (job_id, domain, stage, event, data, created_at)
                VALUES (:job_id, :domain, :stage, :event, :data, :now)
            """),
            {
                "job_id": job_id,
                "domain": domain,
                "stage": stage,
                "event": event,
                "data": json.dumps(data),
                "now": _utcnow(),
            },
        )


def audit_since(job_id: str, after_id: int = 0, limit: int = 200) -> list[dict]:
    """Return audit_traces for a job with id > after_id, oldest first.

    Backs the SSE live-activity stream: the client polls with the last id it
    saw, so events are delivered in order, exactly once.
    """
    with get_conn() as conn:
        rows = conn.execute(
            text("""
                SELECT id, stage, event, domain, data, created_at
                FROM audit_traces
                WHERE job_id = :job_id AND id > :after_id
                ORDER BY id ASC
                LIMIT :limit
            """),
            {"job_id": job_id, "after_id": after_id, "limit": limit},
        ).fetchall()
    return _rows_to_dicts(rows)
