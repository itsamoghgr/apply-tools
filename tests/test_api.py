"""Tests for agent_server/api/app.py

Structure
---------
* No-DB section: monkeypatches both agent_db helpers AND the runner so these
  tests always run without Postgres.

* Live-DB section: uses the real DB + stub stages; BackgroundTasks runs the
  full pipeline synchronously-ish.  Skipped when Postgres is unreachable.

Assertions:
  - POST /api/v1/hunt returns 202 + {job_id, status:"pending"} quickly.
  - GET /api/v1/hunt/{job_id} returns the documented shape.
  - GET /api/v1/hunt/<unknown-id> returns 404.
  - GET + HEAD /health return 200 + {"status":"ok"}.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# No-DB tests — always run; DB + runner fully monkeypatched
# ---------------------------------------------------------------------------


class TestApiNoDb:
    """Tests that never touch Postgres or the pipeline."""

    @pytest.fixture()
    def client(self, monkeypatch):
        """Build a TestClient with DB helpers and runner replaced by fakes."""
        import agent_server.api.app as app_mod

        # ---- fake DB helpers ----
        _jobs: dict = {}

        def fake_create_job(target_count: int) -> str:
            job_id = f"fake-job-{len(_jobs) + 1}"
            _jobs[job_id] = {
                "id": job_id,
                "status": "pending",
                "target_count": target_count,
                "verified_count": 0,
                "candidates_total": None,
                "candidates_processed": None,
                "stop_reason": None,
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
                "finished_at": None,
            }
            return job_id

        def fake_get_job(job_id: str):
            return _jobs.get(job_id)

        monkeypatch.setattr(app_mod, "create_job", fake_create_job)
        monkeypatch.setattr(app_mod, "get_job", fake_get_job)

        # ---- fake runner: no-op (don't run the pipeline) ----
        monkeypatch.setattr(app_mod, "launch_pipeline", lambda *a, **kw: None)

        from agent_server.api.app import app
        return TestClient(app, raise_server_exceptions=True)

    # ----------------------------------------------------------------
    # POST /api/v1/hunt
    # ----------------------------------------------------------------

    def test_post_hunt_returns_202(self, client):
        resp = client.post("/api/v1/hunt", json={})
        assert resp.status_code == 202

    def test_post_hunt_returns_job_id_and_pending_status(self, client):
        resp = client.post("/api/v1/hunt", json={})
        body = resp.json()
        assert "job_id" in body
        assert body["status"] == "pending"

    def test_post_hunt_with_target_count(self, client):
        resp = client.post("/api/v1/hunt", json={"target_count": 10})
        assert resp.status_code == 202
        body = resp.json()
        assert body["job_id"].startswith("fake-job-")

    def test_post_hunt_with_dry_run(self, client):
        resp = client.post("/api/v1/hunt", json={"dry_run": True, "query_hint": "SaaS"})
        assert resp.status_code == 202

    def test_post_hunt_invalid_target_count_rejected(self, client):
        """target_count must be >= 1."""
        resp = client.post("/api/v1/hunt", json={"target_count": 0})
        assert resp.status_code == 422

    # ----------------------------------------------------------------
    # GET /api/v1/hunt/{job_id}
    # ----------------------------------------------------------------

    def test_get_hunt_returns_job_shape(self, client):
        post_resp = client.post("/api/v1/hunt", json={"target_count": 25})
        job_id = post_resp.json()["job_id"]

        get_resp = client.get(f"/api/v1/hunt/{job_id}")
        assert get_resp.status_code == 200

        body = get_resp.json()
        # All documented fields must be present.
        expected_keys = {
            "job_id", "status", "verified_count", "target_count",
            "candidates_total", "candidates_processed", "stop_reason",
            "created_at", "updated_at", "finished_at",
        }
        assert expected_keys.issubset(body.keys())

    def test_get_hunt_status_is_pending_initially(self, client):
        post_resp = client.post("/api/v1/hunt", json={})
        job_id = post_resp.json()["job_id"]

        get_resp = client.get(f"/api/v1/hunt/{job_id}")
        assert get_resp.json()["status"] == "pending"

    def test_get_hunt_unknown_id_returns_404(self, client):
        resp = client.get("/api/v1/hunt/does-not-exist-xyzzy")
        assert resp.status_code == 404

    # ----------------------------------------------------------------
    # Health
    # ----------------------------------------------------------------

    def test_get_health_returns_200_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_head_health_returns_200(self, client):
        resp = client.head("/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Live-DB tests — skipped when Postgres is unreachable
# ---------------------------------------------------------------------------


class TestApiLiveDb:
    """Full-stack tests against the real apply_agent DB + stub pipeline."""

    @pytest.fixture()
    def client(self, live_db, monkeypatch):
        """Real DB; but monkeypatch launch_pipeline to run synchronously
        (same process, no threads) using stub stages so the whole pipeline
        completes before the test's GET assertion runs.
        """
        import time as _time
        import agent_server.api.app as app_mod
        from agent_server.orchestrator.loop import run_pipeline
        from agent_server.orchestrator.runner import build_stub_stages

        def sync_pipeline(job_id, *, query_hint, target, dry_run):
            """Run the pipeline synchronously (no threading) in the same call."""
            with monkeypatch.context() as mp:
                mp.setattr(_time, "sleep", lambda _: None)
            # Note: monkeypatch context is exited, but we can just patch here:
            import unittest.mock as mock
            with mock.patch.object(_time, "sleep", lambda _: None):
                run_pipeline(
                    job_id,
                    query_hint=query_hint,
                    target=target,
                    dry_run=dry_run,
                    stages=build_stub_stages(),
                )

        monkeypatch.setattr(app_mod, "launch_pipeline", sync_pipeline)

        from agent_server.api.app import app
        return TestClient(app, raise_server_exceptions=True)

    def test_post_returns_202_with_job_id(self, client, live_db):
        resp = client.post("/api/v1/hunt", json={"target_count": 50, "dry_run": True})
        assert resp.status_code == 202
        body = resp.json()
        assert "job_id" in body
        assert body["status"] == "pending"

    def test_pipeline_completes_with_target_50(self, client, live_db):
        """POST /hunt with target=50; with synchronous stub pipeline, the job
        should be succeeded with verified_count==50 by the time GET runs."""
        post_resp = client.post("/api/v1/hunt", json={"target_count": 50, "dry_run": True})
        assert post_resp.status_code == 202
        job_id = post_resp.json()["job_id"]

        # BackgroundTasks in TestClient run synchronously during post(), so the
        # job is already done by the time we call GET.
        get_resp = client.get(f"/api/v1/hunt/{job_id}")
        assert get_resp.status_code == 200

        body = get_resp.json()
        assert body["status"] == "succeeded"
        assert body["verified_count"] == 50
        assert body["stop_reason"] == "target_reached"

    def test_get_unknown_id_404(self, client, live_db):
        resp = client.get("/api/v1/hunt/totally-unknown-xyzzy-live")
        assert resp.status_code == 404

    def test_health_always_ok(self, client, live_db):
        assert client.get("/health").status_code == 200
