"""Tests for the deterministic dedup stage (CONTRACTS §0 dedup rules).

All collaborators (seen-cache DB, platform exists endpoint) are monkeypatched —
no live DB or network needed.
"""

from __future__ import annotations

import pytest

from agent_server.contracts.records import CandidateCompany
from agent_server.stages import dedup as dedup_mod
from agent_server.stages.dedup import dedup


def _cand(domain, **kw):
    return CandidateCompany(name=kw.pop("name", domain), domain=domain, source="open_web", **kw)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Default: nothing in seen-cache, platform knows nothing, bulk_add is a no-op."""
    monkeypatch.setattr(dedup_mod.agent_db, "seen_has", lambda d: False)
    monkeypatch.setattr(dedup_mod.agent_db, "seen_bulk_add", lambda rows: None)
    monkeypatch.setattr(dedup_mod._pc, "leads_exists", lambda domains: [])


def test_empty_input_returns_empty():
    assert dedup("job1", []) == []


def test_self_dedup_by_normalized_domain():
    cands = [_cand("acme.com"), _cand("www.acme.com"), _cand("blog.acme.com")]
    out = dedup("job1", cands)
    assert [c.domain for c in out] == ["acme.com"]


def test_drops_non_domain_candidates():
    cands = [_cand("acme.com"), _cand("linkedin.com"), _cand("not a domain")]
    out = dedup("job1", cands)
    assert [c.domain for c in out] == ["acme.com"]


def test_self_dedup_merges_structured_funding(monkeypatch):
    first = _cand("acme.com")  # no funding
    later = _cand("www.acme.com", funding_stage="Seed", funding_amount="$2M")
    out = dedup("job1", [first, later])
    assert len(out) == 1
    assert out[0].funding_stage == "Seed"
    assert out[0].funding_amount == "$2M"


def test_seen_cache_filters_known_domains(monkeypatch):
    monkeypatch.setattr(dedup_mod.agent_db, "seen_has", lambda d: d == "old.com")
    out = dedup("job1", [_cand("old.com"), _cand("new.com")])
    assert [c.domain for c in out] == ["new.com"]


def test_platform_exists_filters_and_seeds(monkeypatch):
    seeded = {}
    monkeypatch.setattr(dedup_mod._pc, "leads_exists", lambda domains: ["known.com"])
    monkeypatch.setattr(
        dedup_mod.agent_db, "seen_bulk_add", lambda rows: seeded.update({r["domain"]: r for r in rows})
    )
    out = dedup("job1", [_cand("known.com"), _cand("fresh.com")])
    assert [c.domain for c in out] == ["fresh.com"]
    # known domain was seeded into the seen-cache
    assert "known.com" in seeded
    assert seeded["known.com"]["outcome"] == "verified"


def test_platform_unreachable_keeps_batch(monkeypatch):
    # leads_exists returns [] on unreachable (its own contract) -> nothing dropped
    monkeypatch.setattr(dedup_mod._pc, "leads_exists", lambda domains: [])
    out = dedup("job1", [_cand("a.com"), _cand("b.com")])
    assert {c.domain for c in out} == {"a.com", "b.com"}


def test_seen_check_error_is_conservative(monkeypatch):
    def boom(d):
        raise RuntimeError("db down")

    monkeypatch.setattr(dedup_mod.agent_db, "seen_has", boom)
    out = dedup("job1", [_cand("a.com")])
    # conservative: include rather than silently lose
    assert [c.domain for c in out] == ["a.com"]
