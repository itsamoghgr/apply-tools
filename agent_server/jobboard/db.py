"""Agent-DB bookkeeping for Job Board runs (apply_agent).

Operational only, per CONTRACTS.md §0: the watchlist and the postings themselves
live in the platform DB. These tables answer "how did the last cycle go, and
which board broke" without grepping logs — and `used_llm` / `pages_fetched`
show which companies are costing LLM calls, i.e. which should be repointed at a
real ATS board URL.

Reuses the shared engine/connection helpers from db/agent_db.py.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from agent_server.db.agent_db import _rows_to_dicts, get_conn
from agent_server.log import get_logger

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def start_run(kind: str, *, trigger: str = "schedule") -> str:
    """Open a jobboard_runs row (status 'running'). Returns its id."""
    run_id = secrets.token_urlsafe(12)
    with get_conn() as conn:
        conn.execute(
            text(
                "INSERT INTO jobboard_runs (id, kind, status, trigger) "
                "VALUES (:id, :kind, 'running', :trigger)"
            ),
            {"id": run_id, "kind": kind, "trigger": trigger},
        )
    return run_id


def finish_run(run_id: str, *, status: str, **counts: Any) -> None:
    """Close a run row with its final counts.

    Accepted count fields: companies_total, companies_ok, companies_failed,
    postings_seen, postings_matched, postings_new, alert_run_id, error.
    """
    allowed = {
        "companies_total",
        "companies_ok",
        "companies_failed",
        "postings_seen",
        "postings_matched",
        "postings_new",
        "alert_run_id",
        "error",
    }
    fields = {k: v for k, v in counts.items() if k in allowed}
    assignments = ", ".join(f"{k} = :{k}" for k in fields)
    set_sql = "status = :status, finished_at = :finished_at"
    if assignments:
        set_sql += ", " + assignments
    with get_conn() as conn:
        conn.execute(
            text(f"UPDATE jobboard_runs SET {set_sql} WHERE id = :id"),
            {"id": run_id, "status": status, "finished_at": _utcnow(), **fields},
        )


def record_company(
    run_id: str,
    *,
    watched_company_id: str,
    company_name: str | None,
    ats: str | None,
    ok: bool,
    postings_seen: int = 0,
    postings_matched: int = 0,
    postings_new: int = 0,
    pages_fetched: int = 1,
    used_llm: bool = False,
    error: str | None = None,
) -> None:
    """Record one company's outcome within a monitor run.

    Never raises: a bookkeeping failure must not abort the cycle that produced
    the data. It is logged and swallowed.
    """
    try:
        with get_conn() as conn:
            conn.execute(
                text(
                    "INSERT INTO jobboard_run_companies ("
                    "  run_id, watched_company_id, company_name, ats, ok,"
                    "  postings_seen, postings_matched, postings_new,"
                    "  pages_fetched, used_llm, error"
                    ") VALUES ("
                    "  :run_id, :cid, :name, :ats, :ok,"
                    "  :seen, :matched, :new, :pages, :used_llm, :error)"
                ),
                {
                    "run_id": run_id,
                    "cid": watched_company_id,
                    "name": company_name,
                    "ats": ats,
                    "ok": ok,
                    "seen": postings_seen,
                    "matched": postings_matched,
                    "new": postings_new,
                    "pages": pages_fetched,
                    "used_llm": used_llm,
                    "error": error,
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("jobboard.record_company_failed", run_id=run_id, error=str(exc))


def recent_runs(kind: str | None = None, limit: int = 20) -> list[dict]:
    """Run history for the UI's status strip."""
    sql = "SELECT * FROM jobboard_runs"
    params: dict[str, Any] = {"limit": limit}
    if kind:
        sql += " WHERE kind = :kind"
        params["kind"] = kind
    sql += " ORDER BY started_at DESC LIMIT :limit"
    with get_conn() as conn:
        return _rows_to_dicts(conn.execute(text(sql), params).fetchall())


def run_companies(run_id: str) -> list[dict]:
    """Per-company breakdown for one run."""
    with get_conn() as conn:
        return _rows_to_dicts(
            conn.execute(
                text(
                    "SELECT * FROM jobboard_run_companies WHERE run_id = :id "
                    "ORDER BY id ASC"
                ),
                {"id": run_id},
            ).fetchall()
        )
