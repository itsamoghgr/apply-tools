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

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from agent_server.config import CONFIG, monitor_hours_expr, parse_days, parse_times
from agent_server.jobboard import platform_client as pc
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


@dataclass(frozen=True)
class Schedule:
    """The effective schedule — UI settings layered over the env defaults."""
    timezone: str
    monitor_interval_h: int
    monitor_start: str
    monitor_end: str
    monitor_days: str | None
    alert_at: list[tuple[int, int]]
    alert_days: str | None
    alert_interval_h: int
    source: str  # "settings" | "env"

    @property
    def monitor_hours(self) -> str:
        return monitor_hours_expr(
            self.monitor_interval_h, self.monitor_start, self.monitor_end
        )

    @property
    def tzinfo(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except Exception:  # noqa: BLE001
            return ZoneInfo("UTC")


def resolve_schedule() -> Schedule:
    """Read the user's schedule from the platform, falling back to env.

    Every field falls back INDEPENDENTLY, so a settings row that only sets the
    alert times still inherits the env scrape window. A read failure yields the
    env schedule rather than no schedule at all.
    """
    try:
        raw = pc.schedule_settings()
    except Exception:  # noqa: BLE001 — the platform being down must not stop boot
        raw = {}

    def pick(key: str, default):
        value = raw.get(key)
        return default if value in (None, "") else value

    try:
        interval = int(pick("monitorIntervalH", CONFIG.jobboard_monitor_interval_h))
    except (TypeError, ValueError):
        interval = CONFIG.jobboard_monitor_interval_h
    try:
        alert_interval = int(pick("alertIntervalH", CONFIG.jobboard_alert_interval_h))
    except (TypeError, ValueError):
        alert_interval = CONFIG.jobboard_alert_interval_h

    alert_at_raw = str(pick("alertAt", CONFIG.jobboard_alert_at))
    return Schedule(
        timezone=str(pick("timezone", CONFIG.jobboard_timezone)),
        monitor_interval_h=max(1, interval),
        monitor_start=str(pick("monitorStart", CONFIG.jobboard_monitor_active_start)),
        monitor_end=str(pick("monitorEnd", CONFIG.jobboard_monitor_active_end)),
        monitor_days=parse_days(str(pick("monitorDays", CONFIG.jobboard_monitor_days))),
        alert_at=parse_times(alert_at_raw),
        alert_days=parse_days(str(pick("alertDays", CONFIG.jobboard_alert_days))),
        alert_interval_h=max(1, alert_interval),
        source="settings" if raw else "env",
    )


def start_scheduler() -> BackgroundScheduler | None:
    """Start the Job Board schedules. Idempotent; returns None when disabled."""
    global _scheduler

    if not CONFIG.jobboard_enabled:
        logger.info("jobboard.scheduler_disabled")
        return None
    if _scheduler is not None:
        return _scheduler

    schedule = resolve_schedule()
    tz = schedule.tzinfo
    scheduler = BackgroundScheduler(timezone=tz)
    now = datetime.now(timezone.utc)

    # SCRAPE — every Nth hour, but only within the active window/days.
    monitor_kwargs: dict = {
        "hour": schedule.monitor_hours,
        "minute": 0,
        "timezone": tz,
    }
    if schedule.monitor_days:
        monitor_kwargs["day_of_week"] = schedule.monitor_days
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
    times = schedule.alert_at
    if times:
        for index, (hour, minute) in enumerate(times):
            alert_kwargs: dict = {"hour": hour, "minute": minute, "timezone": tz}
            if schedule.alert_days:
                alert_kwargs["day_of_week"] = schedule.alert_days
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
                hours=schedule.alert_interval_h,
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
        source=schedule.source,
        monitor_hours=schedule.monitor_hours,
        monitor_days=schedule.monitor_days or "*",
        alert_at=(",".join(f"{h:02d}:{m:02d}" for h, m in times)
                  if times else f"every {schedule.alert_interval_h}h"),
        alert_days=schedule.alert_days or "*",
    )
    return scheduler


def reschedule() -> list[dict]:
    """Re-read the schedule and re-register the jobs, without a restart.

    Called after the UI saves new settings. Implemented as stop-and-start rather
    than mutating triggers in place: the alert is one job PER TIME, so the job
    SET itself changes when the times change, and rebuilding is both simpler and
    less error-prone than diffing. Nothing is lost by doing so — the watermark
    lives in Postgres, so a job that has not run yet simply runs at its next
    slot, and postings found meanwhile roll into the next alert.
    """
    if _scheduler is None:
        # Not running (disabled, or never started) — nothing to reschedule.
        return []
    shutdown_scheduler()
    start_scheduler()
    return job_status()


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
