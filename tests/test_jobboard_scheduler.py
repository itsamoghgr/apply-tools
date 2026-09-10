"""Scheduler configuration and trigger-endpoint tests.

The scheduler's job OPTIONS are the substance here: max_instances / coalesce /
misfire_grace_time are what stop a slow cycle overlapping itself and what turn a
laptop sleep into one catch-up run instead of a burst.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from agent_server.jobboard import scheduler as sch


@pytest.fixture(autouse=True)
def clean_scheduler():
    """Never leave a live scheduler behind for the next test."""
    sch.shutdown_scheduler()
    yield
    sch.shutdown_scheduler()


@pytest.fixture(autouse=True)
def no_stored_schedule(monkeypatch):
    """Default every test to the ENV schedule.

    resolve_schedule() layers the user's saved settings over the env defaults,
    which without this would make these tests read the developer's real
    database — so whether they passed depended on what was saved in the UI.
    Tests that care about stored settings patch this themselves.
    """
    monkeypatch.setattr(sch.pc, "schedule_settings", lambda: {})


def _config_with(**kw):
    return dataclasses.replace(sch.CONFIG, **kw)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def test_scheduler_registers_monitor_and_alert_jobs(monkeypatch):
    monkeypatch.setattr(sch, "CONFIG", _config_with(
        jobboard_enabled=True, jobboard_alert_at="09:00"))
    scheduler = sch.start_scheduler()
    assert scheduler is not None
    ids = {job.id for job in scheduler.get_jobs()}
    # The boot job is what gives a fresh start one prompt cycle instead of
    # waiting for the next cron slot, which could be hours away.
    assert ids == {sch.MONITOR_JOB_ID, sch.MONITOR_BOOT_JOB_ID, sch.ALERT_JOB_ID}


def test_scrape_window_and_interval_come_from_config(monkeypatch):
    """The active window and the interval BOTH shape the scrape hours."""
    monkeypatch.setattr(sch, "CONFIG", _config_with(
        jobboard_enabled=True, jobboard_monitor_interval_h=3,
        jobboard_monitor_active_start="07:00",
        jobboard_monitor_active_end="23:00"))
    scheduler = sch.start_scheduler()
    by_id = {job.id: job for job in scheduler.get_jobs()}
    fields = {f.name: str(f) for f in by_id[sch.MONITOR_JOB_ID].trigger.fields}
    assert fields["hour"] == "7,10,13,16,19,22"
    assert fields["minute"] == "0"


def test_scrape_never_runs_outside_the_active_window(monkeypatch):
    """The whole point of the window: no 04:00 scrape."""
    monkeypatch.setattr(sch, "CONFIG", _config_with(
        jobboard_enabled=True, jobboard_monitor_interval_h=1,
        jobboard_monitor_active_start="09:00",
        jobboard_monitor_active_end="17:00"))
    scheduler = sch.start_scheduler()
    by_id = {job.id: job for job in scheduler.get_jobs()}
    hours = str(next(f for f in by_id[sch.MONITOR_JOB_ID].trigger.fields
                     if f.name == "hour"))
    assert set(hours.split(",")) == {str(h) for h in range(9, 18)}


def test_equal_window_bounds_mean_around_the_clock(monkeypatch):
    monkeypatch.setattr(sch, "CONFIG", _config_with(
        jobboard_enabled=True, jobboard_monitor_interval_h=4,
        jobboard_monitor_active_start="00:00",
        jobboard_monitor_active_end="00:00"))
    scheduler = sch.start_scheduler()
    by_id = {job.id: job for job in scheduler.get_jobs()}
    hours = str(next(f for f in by_id[sch.MONITOR_JOB_ID].trigger.fields
                     if f.name == "hour"))
    assert hours == "*/4"


def test_alert_fires_once_per_configured_time(monkeypatch):
    """Regression: hour="9,18" + minute="0,30" on ONE cron would cross-product
    into four daily fires (09:00, 09:30, 18:00, 18:30). One job per time."""
    monkeypatch.setattr(sch, "CONFIG", _config_with(
        jobboard_enabled=True, jobboard_alert_at="09:00,18:30"))
    scheduler = sch.start_scheduler()
    alert_jobs = [j for j in scheduler.get_jobs() if j.id.startswith(sch.ALERT_JOB_ID)]
    assert len(alert_jobs) == 2
    fired = set()
    for job in alert_jobs:
        fields = {f.name: str(f) for f in job.trigger.fields}
        fired.add((fields["hour"], fields["minute"]))
    assert fired == {("9", "0"), ("18", "30")}


def test_blank_alert_at_falls_back_to_the_interval(monkeypatch):
    monkeypatch.setattr(sch, "CONFIG", _config_with(
        jobboard_enabled=True, jobboard_alert_at="",
        jobboard_alert_interval_h=6))
    scheduler = sch.start_scheduler()
    by_id = {job.id: job for job in scheduler.get_jobs()}
    assert by_id[sch.ALERT_JOB_ID].trigger.interval.total_seconds() == 6 * 3600


def test_schedule_honours_configured_timezone(monkeypatch):
    """09:00 must mean 09:00 where the user is, not 09:00 UTC."""
    monkeypatch.setattr(sch, "CONFIG", _config_with(
        jobboard_enabled=True, jobboard_timezone="America/New_York",
        jobboard_alert_at="09:00"))
    scheduler = sch.start_scheduler()
    by_id = {job.id: job for job in scheduler.get_jobs()}
    assert str(by_id[sch.ALERT_JOB_ID].trigger.timezone) == "America/New_York"
    assert by_id[sch.ALERT_JOB_ID].next_run_time.hour == 9


def test_day_filter_restricts_the_week(monkeypatch):
    monkeypatch.setattr(sch, "CONFIG", _config_with(
        jobboard_enabled=True, jobboard_monitor_days="mon-fri",
        jobboard_alert_at="09:00", jobboard_alert_days="mon,wed"))
    scheduler = sch.start_scheduler()
    by_id = {job.id: job for job in scheduler.get_jobs()}
    monitor_days = str(next(f for f in by_id[sch.MONITOR_JOB_ID].trigger.fields
                            if f.name == "day_of_week"))
    alert_days = str(next(f for f in by_id[sch.ALERT_JOB_ID].trigger.fields
                          if f.name == "day_of_week"))
    assert monitor_days == "mon-fri"
    assert alert_days == "mon,wed"


def test_bad_schedule_values_widen_rather_than_mute(monkeypatch):
    """A typo must never silently stop alerts: it falls back to every day."""
    monkeypatch.setattr(sch, "CONFIG", _config_with(
        jobboard_enabled=True, jobboard_alert_at="notatime",
        jobboard_alert_days="funday", jobboard_alert_interval_h=6))
    scheduler = sch.start_scheduler()
    by_id = {job.id: job for job in scheduler.get_jobs()}
    # Unparseable times -> interval fallback, not "no alert job at all".
    assert by_id[sch.ALERT_JOB_ID].trigger.interval.total_seconds() == 6 * 3600


def test_jobs_cannot_overlap_themselves(monkeypatch):
    """A monitor cycle outliving its interval must not start a second copy."""
    monkeypatch.setattr(sch, "CONFIG", _config_with(jobboard_enabled=True))
    scheduler = sch.start_scheduler()
    for job in scheduler.get_jobs():
        assert job.max_instances == 1, job.id


def test_jobs_coalesce_after_sleep(monkeypatch):
    """After a laptop sleep: one catch-up run, not one per missed interval."""
    monkeypatch.setattr(sch, "CONFIG", _config_with(jobboard_enabled=True))
    scheduler = sch.start_scheduler()
    for job in scheduler.get_jobs():
        assert job.coalesce is True, job.id
        assert job.misfire_grace_time == sch._MISFIRE_GRACE_S, job.id


def test_alert_first_run_is_offset_after_monitor(monkeypatch):
    """A cold-start alert must not race the very first monitor cycle."""
    monkeypatch.setattr(sch, "CONFIG", _config_with(jobboard_enabled=True))
    scheduler = sch.start_scheduler()
    by_id = {job.id: job for job in scheduler.get_jobs()}
    assert by_id[sch.ALERT_JOB_ID].next_run_time > by_id[sch.MONITOR_JOB_ID].next_run_time


def test_saved_settings_override_env(monkeypatch):
    """What the user picks in the UI beats the env defaults."""
    monkeypatch.setattr(sch, "CONFIG", _config_with(
        jobboard_enabled=True, jobboard_timezone="UTC",
        jobboard_alert_at="09:00", jobboard_monitor_interval_h=3))
    monkeypatch.setattr(sch.pc, "schedule_settings", lambda: {
        "timezone": "Europe/London", "monitorIntervalH": 2,
        "monitorStart": "09:00", "monitorEnd": "17:00",
        "monitorDays": "mon-fri", "alertAt": "08:00,20:00",
        "alertDays": "mon-fri",
    })
    schedule = sch.resolve_schedule()
    assert schedule.source == "settings"
    assert schedule.timezone == "Europe/London"
    # 17 included: the window end is inclusive, so a 17:00 scan still runs.
    assert schedule.monitor_hours == "9,11,13,15,17"
    assert schedule.alert_at == [(8, 0), (20, 0)]
    assert schedule.alert_days == "mon-fri"


def test_each_field_falls_back_independently(monkeypatch):
    """A partial save keeps the env value for everything it didn't set."""
    monkeypatch.setattr(sch, "CONFIG", _config_with(
        jobboard_enabled=True, jobboard_timezone="America/New_York",
        jobboard_monitor_interval_h=3, jobboard_monitor_active_start="07:00",
        jobboard_monitor_active_end="23:00"))
    monkeypatch.setattr(sch.pc, "schedule_settings",
                        lambda: {"alertAt": "12:00"})
    schedule = sch.resolve_schedule()
    assert schedule.alert_at == [(12, 0)]          # from settings
    assert schedule.timezone == "America/New_York"  # inherited from env
    assert schedule.monitor_hours == "7,10,13,16,19,22"


def test_unreadable_settings_fall_back_to_env(monkeypatch):
    """A platform outage must leave a working schedule, not none at all."""
    def boom():
        raise RuntimeError("platform down")

    monkeypatch.setattr(sch, "CONFIG", _config_with(
        jobboard_enabled=True, jobboard_alert_at="09:00"))
    monkeypatch.setattr(sch.pc, "schedule_settings", boom)
    schedule = sch.resolve_schedule()
    assert schedule.source == "env"
    assert schedule.alert_at == [(9, 0)]


def test_reschedule_applies_new_settings_without_restart(monkeypatch):
    """Saving in the UI takes effect immediately."""
    monkeypatch.setattr(sch, "CONFIG", _config_with(
        jobboard_enabled=True, jobboard_alert_at="09:00"))
    sch.start_scheduler()
    before = {j["id"] for j in sch.job_status()}
    assert "jobboard_alert_1700" not in before

    monkeypatch.setattr(sch.pc, "schedule_settings",
                        lambda: {"alertAt": "09:00,17:00"})
    after = {j["id"] for j in sch.reschedule()}
    assert "jobboard_alert_1700" in after


def test_reschedule_is_a_noop_when_not_running(monkeypatch):
    monkeypatch.setattr(sch, "CONFIG", _config_with(jobboard_enabled=False))
    assert sch.reschedule() == []


def test_disabled_config_starts_nothing(monkeypatch):
    monkeypatch.setattr(sch, "CONFIG", _config_with(jobboard_enabled=False))
    assert sch.start_scheduler() is None
    assert sch.job_status() == []


def test_start_is_idempotent(monkeypatch):
    monkeypatch.setattr(sch, "CONFIG", _config_with(jobboard_enabled=True))
    first = sch.start_scheduler()
    second = sch.start_scheduler()
    assert first is second
    assert len(first.get_jobs()) == len(second.get_jobs())


def test_shutdown_is_safe_when_never_started():
    sch.shutdown_scheduler()      # must not raise


def test_job_status_reports_next_run(monkeypatch):
    monkeypatch.setattr(sch, "CONFIG", _config_with(jobboard_enabled=True))
    sch.start_scheduler()
    status = sch.job_status()
    assert len(status) >= 2
    assert all(entry["next_run_at"] for entry in status)


def test_scheduled_job_swallows_exceptions(monkeypatch):
    """A crash inside a scheduled run must never propagate into the scheduler."""
    import agent_server.jobboard.monitor as mon
    monkeypatch.setattr(mon, "run_monitor_cycle",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    sch._run_monitor()            # must not raise

    import agent_server.jobboard.alert as dg
    monkeypatch.setattr(dg, "send_alert",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    sch._run_alert()             # must not raise


# ---------------------------------------------------------------------------
# Trigger endpoints
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    """TestClient WITHOUT the lifespan, so no real scheduler starts."""
    from agent_server.api.app import app
    return TestClient(app)


def test_monitor_trigger_returns_202_immediately(client, monkeypatch):
    calls = []
    import agent_server.api.jobboard as api_jb
    monkeypatch.setattr(api_jb, "_run_monitor_task", lambda: calls.append(1))
    resp = client.post("/api/v1/jobboard/monitor/run")
    assert resp.status_code == 202
    assert resp.json()["started"] is True
    assert calls == [1]           # ran as a background task


def test_alert_trigger_passes_force(client, monkeypatch):
    seen = []
    import agent_server.api.jobboard as api_jb
    monkeypatch.setattr(api_jb, "_run_alert_task", lambda force: seen.append(force))
    assert client.post("/api/v1/jobboard/alert/send", json={"force": True}).status_code == 202
    assert seen == [True]
    client.post("/api/v1/jobboard/alert/send", json={})
    assert seen == [True, False]      # defaults to False


def test_detect_endpoint_previews_a_board(client, monkeypatch):
    """The add-company form validates a URL before saving it."""
    from agent_server.jobboard.adapters.base import RawPosting
    import agent_server.api.jobboard as api_jb
    monkeypatch.setattr(api_jb, "fetch_for", lambda *a: [
        RawPosting("1", "AI Engineer", "NYC", "https://x/1", None, "greenhouse"),
        RawPosting("2", "Product Manager", "NYC", "https://x/2", None, "greenhouse"),
    ])
    resp = client.post("/api/v1/jobboard/detect",
                       json={"career_url": "https://boards.greenhouse.io/acme"})
    body = resp.json()
    assert body["ats"] == "greenhouse"
    assert body["slug"] == "acme"
    assert body["ok"] is True
    assert body["total"] == 2
    assert body["matched"] == 1               # the PM is filtered out
    assert body["sample"][0]["role"] == "AI Engineer"


def test_detect_reports_a_bad_board_without_raising(client, monkeypatch):
    """An unreachable URL must come back as ok=false with a reason, not a 500 —
    that message is what the form shows the user."""
    from agent_server.jobboard.adapters.base import AdapterError
    import agent_server.api.jobboard as api_jb
    monkeypatch.setattr(api_jb, "fetch_for",
                        lambda *a: (_ for _ in ()).throw(AdapterError("HTTP 404")))
    resp = client.post("/api/v1/jobboard/detect",
                       json={"career_url": "https://boards.greenhouse.io/nope"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert "404" in resp.json()["error"]


def test_detect_recognises_a_custom_page(client, monkeypatch):
    import agent_server.api.jobboard as api_jb
    monkeypatch.setattr(api_jb, "fetch_for", lambda *a: [])
    body = client.post("/api/v1/jobboard/detect",
                       json={"career_url": "https://acme.com/careers"}).json()
    assert body["ats"] == "generic"
    assert body["slug"] is None


def test_status_endpoint_shape(client, monkeypatch):
    import agent_server.jobboard.db as jb_db
    monkeypatch.setattr(jb_db, "recent_runs", lambda **k: [])
    body = client.get("/api/v1/jobboard/status").json()
    assert set(body) >= {"enabled", "monitor_interval_h", "alert_interval_h",
                         "jobs", "last_monitor", "last_alert"}
    assert body["monitor_interval_h"] == 3
    assert body["alert_interval_h"] == 6
