"""Alert send — report every new role found since the last successful alert.

Runs every 6 hours (APScheduler) or on demand via the trigger endpoint.

The window is a WATERMARK, not a fixed 6-hour slice: it starts at the finishedAt
of the last alert that actually reached the inbox. In the healthy case that is
exactly two monitor cycles, but if a cycle is slow, a send fails, or the machine
sleeps, the unreported postings are simply carried into the next alert instead
of falling into a gap.

Ordering is deliberate:

    open run -> collect -> render -> SEND -> close(sent, posting_ids)

Postings are stamped with the alert id only AFTER the mail is accepted, and the
stamp happens in the same transaction as the run close (see db.close_alert_run).
A send that fails leaves them unstamped, so they roll into the next successful
alert rather than vanishing into a transient SMTP error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from agent_server.config import CONFIG
from agent_server.jobboard import db as jb_db
from agent_server.jobboard import mailer
from agent_server.jobboard import platform_client as pc
from agent_server.jobboard import templates
from agent_server.log import get_logger

logger = get_logger(__name__)


@dataclass
class AlertResult:
    run_id: str | None
    status: str            # sent | skipped | failed
    new_count: int = 0
    company_count: int = 0
    error: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def send_alert(*, force: bool = False, trigger: str = "schedule") -> AlertResult:
    """Send one alert. Never raises — a failure is recorded, not propagated.

    `force` mails even when nothing is new (the heartbeat), matching
    JOBBOARD_ALERT_HEARTBEAT.
    """
    bookkeeping_id = jb_db.start_run("alert", trigger=trigger)
    log = logger.bind(run_id=bookkeeping_id, trigger=trigger)
    log.info("jobboard.alert_start")

    # 1. Watermark + eligible postings. Both are platform reads; if either fails
    #    there is nothing to report on, so bail before opening a AlertRun.
    try:
        window_start = pc.alert_watermark()
        postings = pc.unalerted_postings(since=window_start)
    except pc.PlatformError as exc:
        log.error("jobboard.alert_read_failed", error=str(exc))
        jb_db.finish_run(bookkeeping_id, status="failed", error=str(exc)[:1000])
        return AlertResult(run_id=None, status="failed", error=str(exc))

    window_end = _utcnow()
    count = len(postings)
    companies = len(templates.group_by_company(postings))

    # 2. Nothing new: record a skipped run and send no mail. The watermark is
    #    NOT advanced (only a 'sent' run moves it), so a later alert still
    #    covers this window.
    if count == 0 and not (force or CONFIG.jobboard_alert_heartbeat):
        log.info("jobboard.alert_skipped_empty")
        try:
            run_id = pc.create_alert_run(window_start, window_end)
            pc.close_alert_run(run_id, status="skipped")
        except pc.PlatformError as exc:
            log.warning("jobboard.alert_skip_record_failed", error=str(exc))
            run_id = None
        jb_db.finish_run(bookkeeping_id, status="succeeded", postings_new=0,
                         alert_run_id=run_id)
        return AlertResult(run_id=run_id, status="skipped")

    # 3. Open the AlertRun before sending, so a crash mid-send still leaves a
    #    record that an attempt happened.
    try:
        run_id = pc.create_alert_run(window_start, window_end)
    except pc.PlatformError as exc:
        log.error("jobboard.alert_run_create_failed", error=str(exc))
        jb_db.finish_run(bookkeeping_id, status="failed", error=str(exc)[:1000])
        return AlertResult(run_id=None, status="failed", error=str(exc))

    # 4. Render + send.
    subject = templates.subject_line(postings)
    html_body = templates.render_alert_html(postings)
    text_body = templates.render_alert_text(postings)

    try:
        mailer.send_mail(subject, html_body, text_body)
    except (mailer.MailNotConfigured, mailer.MailSendError) as exc:
        error = str(exc)
        log.error("jobboard.alert_send_failed", error=error)
        # posting_ids deliberately omitted: the postings stay unstamped and are
        # picked up by the next successful alert.
        try:
            pc.close_alert_run(run_id, status="failed", new_count=count,
                                company_count=companies, error=error[:2000])
        except pc.PlatformError as close_exc:
            log.warning("jobboard.alert_close_failed", error=str(close_exc))
        jb_db.finish_run(bookkeeping_id, status="failed", postings_new=count,
                         alert_run_id=run_id, error=error[:1000])
        return AlertResult(run_id=run_id, status="failed", new_count=count,
                            company_count=companies, error=error)

    # 5. Mail accepted — stamp the postings and close the run atomically.
    posting_ids = [p["id"] for p in postings]
    try:
        pc.close_alert_run(run_id, status="sent", new_count=count,
                            company_count=companies, posting_ids=posting_ids)
    except pc.PlatformError as exc:
        # The mail HAS been delivered but the stamp failed, so these postings
        # will appear once more in the next alert. A duplicate is strictly
        # better than a silent drop, and the watermark stays put either way.
        log.error("jobboard.alert_stamp_failed", error=str(exc), count=count)
        jb_db.finish_run(bookkeeping_id, status="failed", postings_new=count,
                         alert_run_id=run_id, error=f"sent but not stamped: {exc}"[:1000])
        return AlertResult(run_id=run_id, status="failed", new_count=count,
                            company_count=companies, error=str(exc))

    jb_db.finish_run(bookkeeping_id, status="succeeded", postings_new=count,
                     alert_run_id=run_id)
    log.info("jobboard.alert_sent", count=count, companies=companies)
    return AlertResult(run_id=run_id, status="sent", new_count=count,
                        company_count=companies)
