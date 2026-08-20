"""Job Board HTTP routes — manual triggers and run history.

Mounted on the existing :8002 app and reachable from the browser through the
frontend's /api/agent/[...path] proxy.

These are TRIGGERS and OBSERVABILITY only. Watchlist and posting CRUD is done by
the Next.js app directly through Prisma (as /applications and /resumes already
do); routing product data through the agent service would buy nothing.

Both triggers run as FastAPI BackgroundTasks so the POST returns immediately —
a monitor cycle over a real watchlist takes far longer than an HTTP timeout.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field

from agent_server.config import CONFIG
from agent_server.jobboard import db as jb_db
from agent_server.jobboard import scheduler as jb_scheduler
from agent_server.jobboard.adapters import detect, fetch_for
from agent_server.jobboard.adapters.base import AdapterError
from agent_server.jobboard.matcher import role_match
from agent_server.log import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/jobboard", tags=["jobboard"])


class AlertSendRequest(BaseModel):
    force: bool = Field(
        default=False,
        description="Send even when nothing is new (a heartbeat).",
    )


class DetectRequest(BaseModel):
    career_url: str = Field(..., min_length=1, max_length=2000)


def _run_monitor_task() -> None:
    from agent_server.jobboard.monitor import run_monitor_cycle

    try:
        run_monitor_cycle(trigger="manual")
    except Exception as exc:  # noqa: BLE001
        logger.error("jobboard.manual_monitor_crashed", error=str(exc), exc_info=True)


def _run_alert_task(force: bool) -> None:
    from agent_server.jobboard.alert import send_alert

    try:
        send_alert(force=force, trigger="manual")
    except Exception as exc:  # noqa: BLE001
        logger.error("jobboard.manual_alert_crashed", error=str(exc), exc_info=True)


@router.post("/monitor/run", status_code=202, summary="Trigger a monitor cycle now")
def trigger_monitor(background: BackgroundTasks) -> dict[str, Any]:
    background.add_task(_run_monitor_task)
    return {"ok": True, "started": True}


@router.post("/alert/send", status_code=202, summary="Send an alert now")
def trigger_alert(req: AlertSendRequest, background: BackgroundTasks) -> dict[str, Any]:
    background.add_task(_run_alert_task, req.force)
    return {"ok": True, "started": True, "force": req.force}


@router.post("/detect", summary="Resolve a career URL to an ATS + preview it")
def detect_career_url(req: DetectRequest) -> dict[str, Any]:
    """Validate a career page BEFORE it is saved to the watchlist.

    Returns the detected ATS plus a live count of what would be picked up, so
    the add-company form can show "found 84 jobs, 2 matching" instead of the
    user discovering three hours later that the URL was wrong.

    Never raises on a bad URL: an unreachable board comes back ok=false with the
    reason, which is exactly what the form needs to display.
    """
    ats, slug = detect(req.career_url)
    result: dict[str, Any] = {"ats": ats, "slug": slug, "ok": False,
                              "total": 0, "matched": 0, "sample": [], "error": None}
    try:
        postings = fetch_for(ats, slug, req.career_url)
    except AdapterError as exc:
        result["error"] = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{exc.__class__.__name__}: {exc}"
        return result

    matched = [(p, role_match(p.title)) for p in postings]
    hits = [(p, r) for p, r in matched if r]
    result.update(
        ok=True,
        total=len(postings),
        matched=len(hits),
        sample=[
            {"title": p.title, "role": r, "location": p.location, "url": p.url}
            for p, r in hits[:5]
        ],
    )
    return result


@router.get("/runs", summary="Recent monitor / alert runs")
def list_runs(kind: str | None = None, limit: int = 20) -> dict[str, Any]:
    runs = jb_db.recent_runs(kind=kind, limit=min(limit, 100))
    return {"runs": runs}


@router.get("/runs/{run_id}/companies", summary="Per-company outcome for one run")
def run_companies(run_id: str) -> dict[str, Any]:
    return {"companies": jb_db.run_companies(run_id)}


@router.get("/status", summary="Scheduler + last-run status")
def status() -> dict[str, Any]:
    """Powers the UI header strip: what ran, and what runs next."""
    monitor_runs = jb_db.recent_runs(kind="monitor", limit=1)
    alert_runs = jb_db.recent_runs(kind="alert", limit=1)
    return {
        "enabled": CONFIG.jobboard_enabled,
        "timezone": CONFIG.jobboard_timezone,
        "monitor_interval_h": CONFIG.jobboard_monitor_interval_h,
        "monitor_active_start": CONFIG.jobboard_monitor_active_start,
        "monitor_active_end": CONFIG.jobboard_monitor_active_end,
        "monitor_days": CONFIG.monitor_day_of_week or "*",
        "alert_interval_h": CONFIG.jobboard_alert_interval_h,
        "alert_at": CONFIG.jobboard_alert_at,
        "alert_days": CONFIG.alert_day_of_week or "*",
        "jobs": jb_scheduler.job_status(),
        "last_monitor": monitor_runs[0] if monitor_runs else None,
        "last_alert": alert_runs[0] if alert_runs else None,
    }
