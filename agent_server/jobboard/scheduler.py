"""APScheduler wiring for the Job Board's two recurring jobs.

    scrape  -> run_monitor_cycle()   every N hours, inside an active window
    alert   -> send_alert()          at explicit times of day

Both are CRON triggers evaluated in CONFIG.jobboard_timezone, so "alert me at
09:00" means 09:00 where the user is and keeps meaning that across a DST shift —
an interval trigger cannot express either idea. When JOBBOARD_ALERT_AT is blank
the alert falls back to a plain interval, for anyone who prefers the old shape.

Runs in-process with the :8002 uvicorn app, so ./start.sh remains the only thing
to run. State lives in Postgres (the AlertRun watermark), not in the scheduler,
so a restart loses nothing and an in-memory jobstore is sufficient.

Job options, and why each is there:

  max_instances=1     A slow cycle can never overlap itself. Without this, a
                      monitor run that outlives its interval would start a
                      second copy and both would scrape the same boards.
  coalesce=True       After a laptop sleep, APScheduler would otherwise fire
                      once per missed interval. Coalescing collapses the backlog
                      into a single catch-up run.
  misfire_grace_time  A job whose trigger time passed while the process was busy
                      (or asleep) still runs if it is within the grace window,
                      instead of being silently dropped.

Narrowing a schedule never loses postings: the alert window is a watermark over
the last SENT run, so anything found outside alert hours is simply reported by
the next alert rather than dropped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agent_server.config import CONFIG
from agent_server.log import get_logger

logger = get_logger(__name__)

_MISFIRE_GRACE_S = 1800  # 30 min

_scheduler: BackgroundScheduler | None = None

MONITOR_JOB_ID = "jobboard_monitor"
MONITOR_BOOT_JOB_ID = "jobboard_monitor_boot"
ALERT_JOB_ID = "jobboard_alert"


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


def _run_alert() -> None:
    from agent_server.jobboard.alert import send_alert

    try:
        result = send_alert(trigger="schedule")
        logger.info(
            "jobboard.scheduled_alert_done",
            status=result.status,
            count=result.new_count,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("jobboard.scheduled_alert_crashed", error=str(exc), exc_info=True)


def start_scheduler() -> BackgroundScheduler | None:
    """Start the Job Board schedules. Idempotent; returns None when disabled."""
    global _scheduler

    if not CONFIG.jobboard_enabled:
        logger.info("jobboard.scheduler_disabled")
        return None
    if _scheduler is not None:
        return _scheduler

    tz = CONFIG.tzinfo
    scheduler = BackgroundScheduler(timezone=tz)
    now = datetime.now(timezone.utc)

    # SCRAPE — every Nth hour, but only within the active window/days.
    monitor_kwargs: dict = {
        "hour": CONFIG.monitor_hour_expr,
        "minute": 0,
        "timezone": tz,
    }
    if CONFIG.monitor_day_of_week:
        monitor_kwargs["day_of_week"] = CONFIG.monitor_day_of_week
    scheduler.add_job(
        _run_monitor,
        CronTrigger(**monitor_kwargs),
        id=MONITOR_JOB_ID,
        name="Job Board monitor",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=_MISFIRE_GRACE_S,
    )
    # A cron trigger's first fire could be hours away, so a fresh boot still gets
    # one prompt cycle rather than waiting for the next slot.
    scheduler.add_job(
        _run_monitor,
        "date",
        run_date=now + timedelta(minutes=1),
        id=MONITOR_BOOT_JOB_ID,
        name="Job Board monitor (startup)",
        max_instances=1,
        misfire_grace_time=_MISFIRE_GRACE_S,
    )

    # ALERT — explicit times of day when given, else the legacy interval.
    # One job PER TIME, not one cron with hour and minute lists: APScheduler
    # crosses those fields, so hour="9,18" minute="0,30" would fire four times a
    # day (09:00, 09:30, 18:00, 18:30) instead of the two the user asked for.
    times = CONFIG.alert_times
    if times:
        for index, (hour, minute) in enumerate(times):
            alert_kwargs: dict = {"hour": hour, "minute": minute, "timezone": tz}
            if CONFIG.alert_day_of_week:
                alert_kwargs["day_of_week"] = CONFIG.alert_day_of_week
            scheduler.add_job(
                _run_alert,
                CronTrigger(**alert_kwargs),
                id=ALERT_JOB_ID if index == 0 else f"{ALERT_JOB_ID}_{hour:02d}{minute:02d}",
                name=f"Job Board alert ({hour:02d}:{minute:02d})",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=_MISFIRE_GRACE_S,
            )
    else:
        scheduler.add_job(
            _run_alert,
            IntervalTrigger(
                hours=max(1, CONFIG.jobboard_alert_interval_h),
                start_date=now + timedelta(minutes=30),
                timezone=tz,
            ),
            id=ALERT_JOB_ID,
            name="Job Board alert",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=_MISFIRE_GRACE_S,
        )

    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "jobboard.scheduler_started",
        timezone=str(tz),
        monitor_hours=CONFIG.monitor_hour_expr,
        monitor_days=CONFIG.monitor_day_of_week or "*",
        alert_at=CONFIG.jobboard_alert_at if times else f"every {CONFIG.jobboard_alert_interval_h}h",
        alert_days=CONFIG.alert_day_of_week or "*",
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
