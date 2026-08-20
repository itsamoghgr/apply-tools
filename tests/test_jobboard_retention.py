"""Scrape-level retention.

One global window, applied once per monitor cycle — not per company, because it
describes how much history the USER wants to see rather than anything about a
particular board.

Postings are ARCHIVED, never deleted, so narrowing the window is reversible.
"""

from __future__ import annotations

import pytest

from agent_server.jobboard import monitor as mon
from agent_server.jobboard import platform_client as pc


def test_default_when_setting_is_missing(monkeypatch):
    """A settings outage must not silently widen or narrow the window."""
    monkeypatch.setattr(pc, "_request", lambda *a, **k: {"value": None})
    assert pc.retention_weeks() == pc.DEFAULT_RETENTION_WEEKS


def test_default_when_platform_unreachable(monkeypatch):
    def boom(*a, **k):
        raise pc.PlatformError("down")
    monkeypatch.setattr(pc, "_request", boom)
    assert pc.retention_weeks() == pc.DEFAULT_RETENTION_WEEKS


@pytest.mark.parametrize("stored,expected", [
    ("1", 1), ("2", 2), ("4", 4), ("52", 52),
])
def test_reads_a_stored_window(monkeypatch, stored, expected):
    monkeypatch.setattr(pc, "_request", lambda *a, **k: {"value": stored})
    assert pc.retention_weeks() == expected


@pytest.mark.parametrize("bad", ["0", "-3", "999", "abc", ""])
def test_out_of_range_falls_back_to_default(monkeypatch, bad):
    monkeypatch.setattr(pc, "_request", lambda *a, **k: {"value": bad})
    assert pc.retention_weeks() == pc.DEFAULT_RETENTION_WEEKS


def test_sweep_runs_once_per_cycle_not_per_company(monkeypatch):
    """The whole point of moving this off the company row: one pass, whatever
    the size of the watchlist."""
    calls = []
    companies = [
        {"id": "c1", "name": "A", "careerUrl": "u1", "ats": "greenhouse",
         "atsSlug": "a", "seededAt": "x", "roleFilter": None, "countryFilter": None},
        {"id": "c2", "name": "B", "careerUrl": "u2", "ats": "greenhouse",
         "atsSlug": "b", "seededAt": "x", "roleFilter": None, "countryFilter": None},
    ]
    monkeypatch.setattr(mon.pc, "list_companies", lambda **k: companies)
    monkeypatch.setattr(mon, "monitor_company",
                        lambda c, r, now=None, max_years=None, default_countries=None: mon.CompanyResult(c["id"], c["name"], True))
    monkeypatch.setattr(mon.pc, "retention_weeks", lambda: 4)
    monkeypatch.setattr(mon.pc, "max_years", lambda: 0)
    monkeypatch.setattr(mon.pc, "global_countries", lambda: [])
    monkeypatch.setattr(mon.pc, "archive_stale_postings",
                        lambda w: calls.append(w) or 7)
    monkeypatch.setattr(mon.jb_db, "start_run", lambda *a, **k: "r1")
    monkeypatch.setattr(mon.jb_db, "finish_run", lambda *a, **k: None)
    monkeypatch.setattr(mon.time, "sleep", lambda s: None)

    mon.run_monitor_cycle()
    assert calls == [4]


def test_sweep_failure_does_not_fail_the_cycle(monkeypatch):
    """Scraping succeeded; a housekeeping error must not undo that."""
    monkeypatch.setattr(mon.pc, "list_companies", lambda **k: [
        {"id": "c1", "name": "A", "careerUrl": "u", "ats": "greenhouse",
         "atsSlug": "a", "seededAt": "x", "roleFilter": None, "countryFilter": None}])
    monkeypatch.setattr(mon, "monitor_company",
                        lambda c, r, now=None, max_years=None, default_countries=None: mon.CompanyResult("c1", "A", True))
    monkeypatch.setattr(mon.pc, "retention_weeks", lambda: 4)
    monkeypatch.setattr(mon.pc, "max_years", lambda: 0)
    monkeypatch.setattr(mon.pc, "global_countries", lambda: [])
    def boom(w):
        raise mon.pc.PlatformError("sweep failed")
    monkeypatch.setattr(mon.pc, "archive_stale_postings", boom)
    monkeypatch.setattr(mon.jb_db, "start_run", lambda *a, **k: "r1")
    monkeypatch.setattr(mon.jb_db, "finish_run", lambda *a, **k: None)
    monkeypatch.setattr(mon.time, "sleep", lambda s: None)

    summary = mon.run_monitor_cycle()      # must not raise
    assert summary.companies_ok == 1


# ---------------------------------------------------------------------------
# Scrape-wide experience ceiling
#
# Like retention, this is global rather than per-company, and it ARCHIVES
# rather than deletes — so lowering it is reversible.
# ---------------------------------------------------------------------------

from agent_server.jobboard.adapters.base import RawPosting


def _posting(min_years):
    return RawPosting(
        external_id="1", title="Data Scientist", location="US, WA, Seattle",
        url="https://x/1", posted_at=None, source="greenhouse",
        min_years=min_years,
    )


@pytest.mark.parametrize("years,ceiling,kept", [
    (2, 4, True),    # under
    (4, 4, True),    # exactly at the ceiling
    (5, 4, False),   # over
    (9, 4, False),
])
def test_experience_ceiling_filters_by_stated_years(years, ceiling, kept):
    assert mon._experience_allowed(_posting(years), ceiling) is kept


def test_unstated_experience_is_KEPT():
    """~20% of postings publish no requirement. "Unstated" is not evidence of
    seniority — dropping them would lose junior roles at companies that simply
    don't list requirements."""
    assert mon._experience_allowed(_posting(None), 4) is True
    assert mon._experience_allowed(_posting(None), 2) is True


def test_zero_ceiling_means_no_limit_not_zero_years():
    """0 is the "unset" value. Reading it as a literal maximum of zero would
    silently reject every dated posting."""
    assert mon._experience_allowed(_posting(15), 0) is True
    assert mon._experience_allowed(_posting(None), 0) is True


def test_ceiling_read_once_per_cycle(monkeypatch):
    """One settings read per cycle, not one per company."""
    reads = []
    companies = [
        {"id": f"c{i}", "name": f"C{i}", "careerUrl": "u", "ats": "greenhouse",
         "atsSlug": "a", "seededAt": "x", "roleFilter": None, "countryFilter": None}
        for i in range(4)
    ]
    monkeypatch.setattr(mon.pc, "list_companies", lambda **k: companies)
    monkeypatch.setattr(mon.pc, "max_years", lambda: reads.append(1) or 4)
    monkeypatch.setattr(mon.pc, "global_countries", lambda: [])
    monkeypatch.setattr(mon, "monitor_company",
                        lambda c, r, now=None, max_years=None, default_countries=None: mon.CompanyResult(c["id"], c["name"], True))
    monkeypatch.setattr(mon.pc, "retention_weeks", lambda: 4)
    monkeypatch.setattr(mon.pc, "archive_stale_postings", lambda w: 0)
    monkeypatch.setattr(mon.pc, "apply_experience_threshold", lambda m: {})
    monkeypatch.setattr(mon.jb_db, "start_run", lambda *a, **k: "r1")
    monkeypatch.setattr(mon.jb_db, "finish_run", lambda *a, **k: None)
    monkeypatch.setattr(mon.time, "sleep", lambda s: None)

    mon.run_monitor_cycle()
    assert len(reads) == 1


def test_unreadable_ceiling_means_no_limit(monkeypatch):
    """A settings outage must not start silently discarding roles."""
    monkeypatch.setattr(pc, "_request",
                        lambda *a, **k: (_ for _ in ()).throw(pc.PlatformError("down")))
    assert pc.max_years() == pc.NO_EXPERIENCE_LIMIT


@pytest.mark.parametrize("bad", ["-1", "99", "abc", ""])
def test_out_of_range_ceiling_means_no_limit(monkeypatch, bad):
    monkeypatch.setattr(pc, "_request", lambda *a, **k: {"value": bad})
    assert pc.max_years() == pc.NO_EXPERIENCE_LIMIT
