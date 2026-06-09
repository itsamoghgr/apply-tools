"""Tests for agent_server/db/agent_db.py.

Structure
---------
* "No-DB" tests at the top — these never touch Postgres and always run.
  They exercise URL normalisation, module imports, payload serialisation,
  and the _row_to_dict() helper.

* "Live-DB" tests below — each requests the `live_db` fixture from conftest.py.
  If Postgres is unreachable the fixture calls pytest.skip() and the test is
  reported as "s" (skipped), not "F" (failed).

Run from the repo root:
    agent_server/venv/bin/python -m pytest tests/test_agent_db.py -v
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# No-DB tests — always run, no Postgres needed
# ---------------------------------------------------------------------------


class TestUrlNormalisation:
    """_normalise_url must pin to postgresql+psycopg:// regardless of the
    input scheme so SQLAlchemy uses the psycopg v3 driver."""

    def _normalise(self, url: str) -> str:
        from agent_server.db.agent_db import _normalise_url
        return _normalise_url(url)

    def test_bare_postgres_scheme(self):
        result = self._normalise("postgres://user:pw@host/db")
        assert result.startswith("postgresql+psycopg://")
        assert "user:pw@host/db" in result

    def test_postgresql_scheme(self):
        result = self._normalise("postgresql://user:pw@host:5432/db")
        assert result.startswith("postgresql+psycopg://")
        assert "5432" in result

    def test_already_pinned(self):
        url = "postgresql+psycopg://user:pw@host/db"
        assert self._normalise(url) == url

    def test_strips_query_params(self):
        url = "postgresql://user:pw@host/db?schema=public&connection_limit=5"
        result = self._normalise(url)
        assert "?" not in result
        assert "schema" not in result

    def test_idempotent(self):
        url = "postgresql://apply:apply@localhost:5432/apply_agent"
        once = self._normalise(url)
        twice = self._normalise(once)
        assert once == twice


class TestRowToDict:
    """_row_to_dict must coerce datetime columns and leave jsonb columns as
    dicts (or parse them from strings if they arrive as raw JSON text)."""

    def _make_row(self, mapping: dict):
        """Return a lightweight mock that mimics a SQLAlchemy Row._mapping."""
        row = MagicMock()
        row._mapping = mapping
        return row

    def test_datetime_coerced_to_iso(self):
        from agent_server.db.agent_db import _row_to_dict
        ts = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        d = _row_to_dict(self._make_row({"id": "x", "created_at": ts}))
        assert isinstance(d["created_at"], str)
        assert "2024-06-01" in d["created_at"]

    def test_non_datetime_columns_left_alone(self):
        from agent_server.db.agent_db import _row_to_dict
        d = _row_to_dict(self._make_row({"id": "x", "status": "pending"}))
        assert d["status"] == "pending"

    def test_jsonb_string_parsed(self):
        """If psycopg returns a jsonb column as a raw string, we parse it."""
        from agent_server.db.agent_db import _row_to_dict
        payload_str = json.dumps({"domain": "acme.io", "confidence": 0.9})
        d = _row_to_dict(self._make_row({"payload": payload_str}))
        assert isinstance(d["payload"], dict)
        assert d["payload"]["domain"] == "acme.io"

    def test_jsonb_already_dict(self):
        from agent_server.db.agent_db import _row_to_dict
        payload = {"domain": "acme.io"}
        d = _row_to_dict(self._make_row({"payload": payload}))
        assert d["payload"] is payload  # no copy needed


class TestModuleImport:
    """The module must be importable and expose all required public names."""

    REQUIRED_NAMES = [
        "create_job",
        "get_job",
        "update_job",
        "add_checkpoint",
        "latest_checkpoint",
        "seen_has",
        "seen_add",
        "seen_bulk_add",
        "seen_filter_unknown",
        "outbox_add",
        "outbox_pending",
        "outbox_mark_sent",
        "outbox_mark_failed",
        "audit_add",
        "get_engine",
        "get_conn",
    ]

    def test_all_names_present(self):
        import agent_server.db.agent_db as mod
        missing = [n for n in self.REQUIRED_NAMES if not hasattr(mod, n)]
        assert missing == [], f"Missing names in agent_db: {missing}"


class TestPayloadSerialisation:
    """Verify that dicts passed as payload survive a json.dumps/loads round-trip
    (the pattern used before writing to Postgres)."""

    def test_nested_dict_round_trips(self):
        payload = {
            "domain": "startup.io",
            "company_name": "Startup Inc",
            "confidence": 0.85,
            "sources": ["https://techcrunch.com/a", "https://startup.io"],
            "verification_detail": {"smtp": True, "hunter_score": 72},
        }
        raw = json.dumps(payload)
        restored = json.loads(raw)
        assert restored == payload

    def test_state_dict_round_trips(self):
        state = {"cursor": 42, "batch": ["a.io", "b.io"], "meta": {"retries": 1}}
        assert json.loads(json.dumps(state)) == state


# ---------------------------------------------------------------------------
# Live-DB tests — skipped automatically when Postgres is unreachable
# ---------------------------------------------------------------------------


class TestJobsCRUD:
    """CRUD round-trip for the jobs table."""

    def test_create_and_get_job(self, live_db):
        from agent_server.db.agent_db import create_job, get_job

        job_id = create_job(target_count=10)
        assert isinstance(job_id, str) and len(job_id) > 0

        job = get_job(job_id)
        assert job is not None
        assert job["id"] == job_id
        assert job["status"] == "pending"
        assert job["target_count"] == 10
        assert job["verified_count"] == 0

    def test_update_job_status(self, live_db):
        from agent_server.db.agent_db import create_job, update_job, get_job

        job_id = create_job(target_count=5)
        update_job(job_id, status="running", candidates_total=20)

        job = get_job(job_id)
        assert job["status"] == "running"
        assert job["candidates_total"] == 20

    def test_update_job_finished(self, live_db):
        from agent_server.db.agent_db import create_job, update_job, get_job

        now = datetime.now(timezone.utc)
        job_id = create_job(target_count=3)
        update_job(
            job_id,
            status="succeeded",
            verified_count=3,
            stop_reason="target_reached",
            finished_at=now,
        )
        job = get_job(job_id)
        assert job["status"] == "succeeded"
        assert job["stop_reason"] == "target_reached"
        assert job["finished_at"] is not None

    def test_get_unknown_job_returns_none(self, live_db):
        from agent_server.db.agent_db import get_job
        assert get_job("no-such-job-xyzzy") is None

    def test_update_job_no_allowed_fields_is_noop(self, live_db):
        from agent_server.db.agent_db import create_job, update_job, get_job
        job_id = create_job(target_count=1)
        # Passing a key that isn't in the allowed set should be silently ignored.
        update_job(job_id, totally_unknown_field="boom")
        job = get_job(job_id)
        assert job["status"] == "pending"  # unchanged


class TestCheckpoints:
    def test_add_and_retrieve_checkpoint(self, live_db):
        from agent_server.db.agent_db import create_job, add_checkpoint, latest_checkpoint

        job_id = create_job(target_count=1)
        state = {"page": 3, "urls": ["https://a.io"]}
        add_checkpoint(job_id, "discovery", cursor=3, state=state)

        cp = latest_checkpoint(job_id, "discovery")
        assert cp is not None
        assert cp["job_id"] == job_id
        assert cp["stage"] == "discovery"
        assert cp["cursor"] == 3
        assert cp["state"] == state

    def test_latest_checkpoint_no_stage_filter(self, live_db):
        from agent_server.db.agent_db import create_job, add_checkpoint, latest_checkpoint

        job_id = create_job(target_count=1)
        add_checkpoint(job_id, "discovery", cursor=1, state={"x": 1})
        add_checkpoint(job_id, "loop", cursor=2, state={"x": 2})

        cp = latest_checkpoint(job_id)
        # Should return the most recent one (loop, cursor=2).
        assert cp["stage"] == "loop"
        assert cp["cursor"] == 2

    def test_no_checkpoint_returns_none(self, live_db):
        from agent_server.db.agent_db import create_job, latest_checkpoint
        job_id = create_job(target_count=1)
        assert latest_checkpoint(job_id) is None

    def test_checkpoint_null_cursor(self, live_db):
        from agent_server.db.agent_db import create_job, add_checkpoint, latest_checkpoint
        job_id = create_job(target_count=1)
        add_checkpoint(job_id, "dedup", cursor=None, state={})
        cp = latest_checkpoint(job_id, "dedup")
        assert cp["cursor"] is None


class TestSeenCache:
    def test_seen_has_false_for_unknown(self, live_db):
        from agent_server.db.agent_db import seen_has
        assert seen_has("definitely-not-in-cache-xyzzy-999.io") is False

    def test_seen_add_and_has(self, live_db):
        from agent_server.db.agent_db import seen_add, seen_has
        domain = "test-seen-domain.io"
        seen_add(domain, "verified", reason="found via hunter")
        assert seen_has(domain) is True

    def test_seen_add_upserts(self, live_db):
        from agent_server.db.agent_db import seen_add, seen_has
        domain = "upsert-seen-test.io"
        seen_add(domain, "dropped", reason="first")
        seen_add(domain, "verified", reason="updated")
        # Should not raise; domain is still present.
        assert seen_has(domain) is True

    def test_seen_bulk_add(self, live_db):
        from agent_server.db.agent_db import seen_bulk_add, seen_has
        rows = [
            {"domain": "bulk1.io", "outcome": "verified"},
            {"domain": "bulk2.io", "outcome": "dropped", "reason": "no email"},
        ]
        seen_bulk_add(rows)
        assert seen_has("bulk1.io") is True
        assert seen_has("bulk2.io") is True

    def test_seen_bulk_add_empty_list(self, live_db):
        from agent_server.db.agent_db import seen_bulk_add
        # Should not raise.
        seen_bulk_add([])

    def test_seen_filter_unknown(self, live_db):
        from agent_server.db.agent_db import seen_add, seen_filter_unknown
        known = "known-filter-test.io"
        unknown = "unknown-filter-test-xyzzy.io"
        seen_add(known, "verified")

        result = seen_filter_unknown([known, unknown])
        assert unknown in result
        assert known not in result

    def test_seen_filter_unknown_empty_input(self, live_db):
        from agent_server.db.agent_db import seen_filter_unknown
        assert seen_filter_unknown([]) == []


class TestOutbox:
    def test_outbox_add_and_pending(self, live_db):
        from agent_server.db.agent_db import create_job, outbox_add, outbox_pending

        job_id = create_job(target_count=1)
        payload = {"domain": "outbox-test.io", "company_name": "Test Co", "confidence": 0.8}
        outbox_id = outbox_add(job_id, "outbox-test.io", payload)
        assert isinstance(outbox_id, int) and outbox_id > 0

        pending = outbox_pending(limit=200)
        ids = [r["id"] for r in pending]
        assert outbox_id in ids

    def test_outbox_payload_round_trips(self, live_db):
        from agent_server.db.agent_db import create_job, outbox_add, outbox_pending

        job_id = create_job(target_count=1)
        payload = {
            "domain": "payload-rt.io",
            "company_name": "RT Corp",
            "confidence": 0.91,
            "sources": ["https://a.io/press"],
        }
        outbox_id = outbox_add(job_id, "payload-rt.io", payload)

        pending = outbox_pending(limit=500)
        row = next((r for r in pending if r["id"] == outbox_id), None)
        assert row is not None
        assert isinstance(row["payload"], dict)
        assert row["payload"]["domain"] == "payload-rt.io"
        assert row["payload"]["sources"] == ["https://a.io/press"]

    def test_outbox_mark_sent(self, live_db):
        from agent_server.db.agent_db import (
            create_job, outbox_add, outbox_mark_sent, outbox_pending
        )
        from sqlalchemy import text

        job_id = create_job(target_count=1)
        outbox_id = outbox_add(job_id, "mark-sent.io", {"domain": "mark-sent.io"})
        outbox_mark_sent(outbox_id)

        # Should no longer appear in pending list.
        pending = outbox_pending(limit=500)
        assert outbox_id not in [r["id"] for r in pending]

        # Verify status in DB.
        with live_db.connect() as conn:
            row = conn.execute(
                text("SELECT status, sent_at, attempts FROM outbox WHERE id = :id"),
                {"id": outbox_id},
            ).fetchone()
        assert row.status == "sent"
        assert row.sent_at is not None
        assert row.attempts == 1

    def test_outbox_mark_failed(self, live_db):
        from agent_server.db.agent_db import (
            create_job, outbox_add, outbox_mark_failed
        )
        from sqlalchemy import text

        job_id = create_job(target_count=1)
        outbox_id = outbox_add(job_id, "mark-failed.io", {"domain": "mark-failed.io"})
        outbox_mark_failed(outbox_id, "connection timeout")

        with live_db.connect() as conn:
            row = conn.execute(
                text("SELECT status, last_error, attempts FROM outbox WHERE id = :id"),
                {"id": outbox_id},
            ).fetchone()
        assert row.status == "failed"
        assert "timeout" in row.last_error
        assert row.attempts == 1


class TestAuditTraces:
    def test_audit_add(self, live_db):
        from agent_server.db.agent_db import create_job, audit_add
        from sqlalchemy import text

        job_id = create_job(target_count=1)
        audit_add(
            job_id,
            stage="discovery",
            event="batch_fetched",
            data={"count": 12, "source": "yc_oss"},
            domain="somecompany.io",
        )

        with live_db.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT * FROM audit_traces "
                    "WHERE job_id = :job_id AND event = 'batch_fetched'"
                ),
                {"job_id": job_id},
            ).fetchone()

        assert row is not None
        assert row.stage == "discovery"
        assert row.domain == "somecompany.io"
        # data comes back as dict from psycopg jsonb decode.
        data = row.data if isinstance(row.data, dict) else json.loads(row.data)
        assert data["count"] == 12

    def test_audit_add_no_domain(self, live_db):
        from agent_server.db.agent_db import create_job, audit_add
        from sqlalchemy import text

        job_id = create_job(target_count=1)
        audit_add(job_id, "loop", "iteration_start", {"iter": 1})

        with live_db.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT domain FROM audit_traces "
                    "WHERE job_id = :job_id AND event = 'iteration_start'"
                ),
                {"job_id": job_id},
            ).fetchone()
        assert row.domain is None
