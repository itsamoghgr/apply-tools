"""APScheduler wiring for the Job Board's two recurring jobs.

    every 3h -> run_monitor_cycle()
    every 6h -> send_digest()

Runs in-process with the :8002 uvicorn app, so ./start.sh remains the only thing
to run. State lives in Postgres (the DigestRun watermark), not in the scheduler,
so a restart loses nothing and an in-memory jobstore is sufficient.

Job options, and why each is there:

  max_instances=1     A slow cycle can never overlap itself. Without this, a
                      monitor run that outlives its 3h interval would start a
                      second copy and both would scrape the same boards.
  coalesce=True       After a laptop sleep, APScheduler would otherwise fire
                      once per missed interval. Coalescing collapses the backlog
                      into a single catch-up run.
  misfire_grace_time  A job whose trigger time passed while the process was busy
                      (or asleep) still runs if it is within the grace window,
                      instead of being silently dropped.

The digest is offset from the monitor on a cold start so it does not race the
very first monitor cycle; the watermark makes that harmless either way, but an
empty first digest is noise.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from agent_server.config import CONFIG
from agent_server.log import get_logger

logger = get_logger(__name__)

_MISFIRE_GRACE_S = 1800  # 30 min

_scheduler: BackgroundScheduler | None = None

MONITOR_JOB_ID = "jobboard_monitor"
DIGEST_JOB_ID = "jobboard_digest"


def _run_monitor() -> None:
    """Scheduler entry point. Swallows everything: a raised exception inside a
    scheduled job would be logged by APScheduler but must never kill the app."""
    from agent_server.jobboard.monitor import run_monitor_cycle

    try:
        summary = run_monitor_cycle(trigger="schedule")
        logger.info(
            "jobboard.scheduled_monitor_done",
            companies=summary.companies_total,
            failed=summary.companies_failed,
            new=summary.postings_new,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("jobboard.scheduled_monitor_crashed", error=str(exc), exc_info=True)


def _run_digest() -> None:
    from agent_server.jobboard.digest import send_digest

    try:
        result = send_digest(trigger="schedule")
        logger.info(
            "jobboard.scheduled_digest_done",
            status=result.status,
            count=result.new_count,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("jobboard.scheduled_digest_crashed", error=str(exc), exc_info=True)


def start_scheduler() -> BackgroundScheduler | None:
    """Start the Job Board schedules. Idempotent; returns None when disabled."""
    global _scheduler

    if not CONFIG.jobboard_enabled:
        logger.info("jobboard.scheduler_disabled")
        return None
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(timezone="UTC")
    now = datetime.now(timezone.utc)

    scheduler.add_job(
        _run_monitor,
        "interval",
        hours=CONFIG.jobboard_monitor_interval_h,
        id=MONITOR_JOB_ID,
        name="Job Board monitor",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=_MISFIRE_GRACE_S,
        # First run shortly after boot rather than a full interval later, so a
        # fresh start picks up new postings promptly.
        next_run_time=now + timedelta(minutes=1),
    )
    scheduler.add_job(
        _run_digest,
        "interval",
        hours=CONFIG.jobboard_digest_interval_h,
        id=DIGEST_JOB_ID,
        name="Job Board digest",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=_MISFIRE_GRACE_S,
        # Offset so the first digest lands after the first monitor cycle has
        # had time to finish.
        next_run_time=now + timedelta(minutes=30),
    )

    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "jobboard.scheduler_started",
        monitor_interval_h=CONFIG.jobboard_monitor_interval_h,
        digest_interval_h=CONFIG.jobboard_digest_interval_h,
    )
    return scheduler


def shutdown_scheduler() -> None:
    """Stop the scheduler without waiting for a running job to finish."""
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
        logger.info("jobboard.scheduler_stopped")
    except Exception as exc:  # noqa: BLE001
        logger.warning("jobboard.scheduler_stop_failed", error=str(exc))
    finally:
        _scheduler = None


def job_status() -> list[dict]:
    """Next-run times for the UI's status strip."""
    if _scheduler is None:
        return []
    return [
        {
            "id": job.id,
            "name": job.name,
            "next_run_at": job.next_run_time.isoformat() if job.next_run_time else None,
        }
        for job in _scheduler.get_jobs()
    ]
