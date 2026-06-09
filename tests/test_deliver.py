"""Tests for outbox delivery (CONTRACTS §6 delivery / outbox pattern).

agent_db and the platform client are monkeypatched with an in-memory fake
outbox — no live DB or network needed.
"""

from __future__ import annotations

import pytest

from agent_server.contracts.records import VerifiedLead
from agent_server.stages import deliver as deliver_mod
from agent_server.stages.deliver import deliver, retry_pending
from agent_server.stages.platform_client import PlatformUnreachable


class FakeOutbox:
    """In-memory stand-in for the agent-DB outbox + seen-cache."""

    def __init__(self):
        self.rows = {}
        self.seen = {}
        self.audit = []
        self._next = 1

    # --- outbox helpers (match agent_db signatures) ---
    def outbox_add(self, job_id, domain, payload):
        rid = self._next
        self._next += 1
        self.rows[rid] = {
            "id": rid, "job_id": job_id, "domain": domain,
            "payload": payload, "status": "pending", "attempts": 0,
        }
        return rid

    def outbox_mark_sent(self, rid):
        self.rows[rid]["status"] = "sent"

    def outbox_mark_failed(self, rid, error):
        self.rows[rid]["status"] = "failed"
        self.rows[rid]["attempts"] += 1
        self.rows[rid]["last_error"] = error

    def outbox_retryable(self, limit=100):
        return [r for r in self.rows.values() if r["status"] in ("pending", "failed")][:limit]

    def seen_add(self, domain, outcome, **kw):
        self.seen[domain] = outcome

    def audit_add(self, *a, **kw):
        self.audit.append((a, kw))


@pytest.fixture
def fake(monkeypatch):
    fo = FakeOutbox()
    monkeypatch.setattr(deliver_mod.agent_db, "outbox_add", fo.outbox_add)
    monkeypatch.setattr(deliver_mod.agent_db, "outbox_mark_sent", fo.outbox_mark_sent)
    monkeypatch.setattr(deliver_mod.agent_db, "outbox_mark_failed", fo.outbox_mark_failed)
    monkeypatch.setattr(deliver_mod.agent_db, "outbox_retryable", fo.outbox_retryable)
    monkeypatch.setattr(deliver_mod.agent_db, "seen_add", fo.seen_add)
    monkeypatch.setattr(deliver_mod.agent_db, "audit_add", fo.audit_add)
    return fo


def _lead(domain="acme.com"):
    return VerifiedLead(domain=domain, name="Acme", confidence=0.8, founder_email="j@acme.com")


def test_outbox_write_happens(fake, monkeypatch):
    monkeypatch.setattr(deliver_mod, "leads_upsert", lambda p: {"lead_id": "x", "created": True})
    deliver("job1", _lead())
    assert len(fake.rows) == 1


def test_dry_run_skips_platform(fake, monkeypatch):
    called = {"n": 0}

    def _upsert(p):
        called["n"] += 1
        return {}

    monkeypatch.setattr(deliver_mod, "leads_upsert", _upsert)
    deliver("job1", _lead(), dry_run=True)
    assert called["n"] == 0
    assert list(fake.rows.values())[0]["status"] == "pending"  # written, not sent


def test_success_marks_sent_and_records_seen(fake, monkeypatch):
    monkeypatch.setattr(deliver_mod, "leads_upsert", lambda p: {"lead_id": "x", "created": True})
    deliver("job1", _lead())
    row = list(fake.rows.values())[0]
    assert row["status"] == "sent"
    assert fake.seen["acme.com"] == "verified"


def test_platform_unreachable_marks_failed_not_lost(fake, monkeypatch):
    def _boom(p):
        raise PlatformUnreachable("down")

    monkeypatch.setattr(deliver_mod, "leads_upsert", _boom)
    deliver("job1", _lead())
    row = list(fake.rows.values())[0]
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    # the lead is NOT lost — still in the outbox, retryable
    assert fake.outbox_retryable() == [row]


def test_retry_pending_redelivers_failed_rows(fake, monkeypatch):
    # First attempt fails...
    monkeypatch.setattr(deliver_mod, "leads_upsert", lambda p: (_ for _ in ()).throw(PlatformUnreachable("down")))
    deliver("job1", _lead())
    assert list(fake.rows.values())[0]["status"] == "failed"

    # ...platform comes back, retry succeeds.
    monkeypatch.setattr(deliver_mod, "leads_upsert", lambda p: {"lead_id": "x", "created": False})
    delivered = retry_pending()
    assert delivered == 1
    assert list(fake.rows.values())[0]["status"] == "sent"
    assert fake.seen["acme.com"] == "verified"


def test_deliver_never_raises_on_failure(fake, monkeypatch):
    monkeypatch.setattr(deliver_mod, "leads_upsert", lambda p: (_ for _ in ()).throw(PlatformUnreachable("down")))
    # Should not raise — a delivery failure must not abort the orchestrator loop.
    deliver("job1", _lead())
