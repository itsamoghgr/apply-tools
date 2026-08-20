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


def _config_with(**kw):
    return dataclasses.replace(sch.CONFIG, **kw)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def test_scheduler_registers_both_jobs(monkeypatch):
    monkeypatch.setattr(sch, "CONFIG", _config_with(jobboard_enabled=True))
    scheduler = sch.start_scheduler()
    assert scheduler is not None
    ids = {job.id for job in scheduler.get_jobs()}
    assert ids == {sch.MONITOR_JOB_ID, sch.DIGEST_JOB_ID}


def test_intervals_come_from_config(monkeypatch):
    monkeypatch.setattr(sch, "CONFIG", _config_with(
        jobboard_enabled=True, jobboard_monitor_interval_h=3,
        jobboard_digest_interval_h=6))
    scheduler = sch.start_scheduler()
    by_id = {job.id: job for job in scheduler.get_jobs()}
    assert by_id[sch.MONITOR_JOB_ID].trigger.interval.total_seconds() == 3 * 3600
    assert by_id[sch.DIGEST_JOB_ID].trigger.interval.total_seconds() == 6 * 3600


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


def test_digest_first_run_is_offset_after_monitor(monkeypatch):
    """A cold-start digest must not race the very first monitor cycle."""
    monkeypatch.setattr(sch, "CONFIG", _config_with(jobboard_enabled=True))
    scheduler = sch.start_scheduler()
    by_id = {job.id: job for job in scheduler.get_jobs()}
    assert by_id[sch.DIGEST_JOB_ID].next_run_time > by_id[sch.MONITOR_JOB_ID].next_run_time


def test_disabled_config_starts_nothing(monkeypatch):
    monkeypatch.setattr(sch, "CONFIG", _config_with(jobboard_enabled=False))
    assert sch.start_scheduler() is None
    assert sch.job_status() == []


def test_start_is_idempotent(monkeypatch):
    monkeypatch.setattr(sch, "CONFIG", _config_with(jobboard_enabled=True))
    first = sch.start_scheduler()
    second = sch.start_scheduler()
    assert first is second
    assert len(first.get_jobs()) == 2


def test_shutdown_is_safe_when_never_started():
    sch.shutdown_scheduler()      # must not raise


def test_job_status_reports_next_run(monkeypatch):
    monkeypatch.setattr(sch, "CONFIG", _config_with(jobboard_enabled=True))
    sch.start_scheduler()
    status = sch.job_status()
    assert len(status) == 2
    assert all(entry["next_run_at"] for entry in status)


def test_scheduled_job_swallows_exceptions(monkeypatch):
    """A crash inside a scheduled run must never propagate into the scheduler."""
    import agent_server.jobboard.monitor as mon
    monkeypatch.setattr(mon, "run_monitor_cycle",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    sch._run_monitor()            # must not raise

    import agent_server.jobboard.digest as dg
    monkeypatch.setattr(dg, "send_digest",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    sch._run_digest()             # must not raise


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


def test_digest_trigger_passes_force(client, monkeypatch):
    seen = []
    import agent_server.api.jobboard as api_jb
    monkeypatch.setattr(api_jb, "_run_digest_task", lambda force: seen.append(force))
    assert client.post("/api/v1/jobboard/digest/send", json={"force": True}).status_code == 202
    assert seen == [True]
    client.post("/api/v1/jobboard/digest/send", json={})
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
    assert set(body) >= {"enabled", "monitor_interval_h", "digest_interval_h",
                         "jobs", "last_monitor", "last_digest"}
    assert body["monitor_interval_h"] == 3
    assert body["digest_interval_h"] == 6
