"""Per-company scrape config: roles, countries, max age.

These filters DISCARD postings during the scrape rather than hiding them at
display time, so the behaviour under uncertainty is the whole point: every
filter must fail OPEN.

Age is deliberately NOT here: retention is a scrape-level setting applied once
per cycle (see test_jobboard_retention.py), not a per-company filter.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_server.jobboard import monitor as mon
from agent_server.jobboard.adapters.base import RawPosting

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def posting(title="Data Scientist", location="US, WA, Seattle", posted=NOW):
    return RawPosting(
        external_id="1", title=title, location=location,
        url="https://x/1", posted_at=posted, source="greenhouse",
    )


# ---------------------------------------------------------------------------
# Country filter
# ---------------------------------------------------------------------------

def test_country_filter_keeps_matching():
    assert mon._country_allowed(posting(location="US, WA, Seattle"), ["US"]) is True


def test_country_filter_drops_non_matching():
    assert mon._country_allowed(posting(location="IN, KA, Bengaluru"), ["US"]) is False


def test_empty_country_filter_keeps_everything():
    assert mon._country_allowed(posting(location="IN, KA, Bengaluru"), []) is True
    assert mon._country_allowed(posting(location="IN, KA, Bengaluru"), None) is True


def test_unparseable_country_is_KEPT():
    """The load-bearing guarantee. ~1% of locations ("Remote", "n/a") can't be
    resolved; discarding them would permanently lose real jobs, and a scrape
    filter has no undo short of a full re-seed."""
    assert mon._country_allowed(posting(location="Remote"), ["US"]) is True
    assert mon._country_allowed(posting(location=None), ["US"]) is True
    assert mon._country_allowed(posting(location="n/a"), ["US"]) is True


def test_multiple_allowed_countries():
    assert mon._country_allowed(posting(location="CA, BC, Vancouver"), ["US", "CA"]) is True
    assert mon._country_allowed(posting(location="JP, 13, Tokyo"), ["US", "CA"]) is False


# ---------------------------------------------------------------------------
# Role filter
# ---------------------------------------------------------------------------

def test_role_filter_narrows_to_selected_roles():
    matchers = mon._matchers_for({"roleFilter": ["AI Engineer"]})
    assert list(matchers) == ["AI Engineer"]


def test_empty_role_filter_means_all_roles_not_none():
    """An empty list must read as "no restriction". If it meant "match
    nothing", a company would silently stop producing results and look
    identical to a broken scraper."""
    assert len(mon._matchers_for({"roleFilter": []})) == 5
    assert len(mon._matchers_for({"roleFilter": None})) == 5
    assert len(mon._matchers_for({})) == 5


def test_unknown_role_name_falls_back_to_all():
    assert len(mon._matchers_for({"roleFilter": ["Nonexistent Role"]})) == 5


# ---------------------------------------------------------------------------
# Global country default
#
# Scrape-wide, but a DEFAULT rather than an override: a company with its own
# list keeps it, so one board can be pinned while the rest stay worldwide.
# ---------------------------------------------------------------------------

def _company(country_filter=None):
    return {"id": "c1", "name": "A", "careerUrl": "u", "ats": "greenhouse",
            "atsSlug": "a", "seededAt": "x", "roleFilter": None,
            "countryFilter": country_filter}


def test_global_countries_apply_when_company_has_none(monkeypatch):
    seen = {}
    monkeypatch.setattr(mon, "fetch_for", lambda *a: [])
    monkeypatch.setattr(mon.pc, "upsert_postings",
                        lambda cid, p: seen.update(payload=p) or {"new_ids": []})
    monkeypatch.setattr(mon.pc, "archive_postings", lambda *a: 0)
    monkeypatch.setattr(mon.pc, "patch_company", lambda *a, **k: None)
    monkeypatch.setattr(mon.jb_db, "record_company", lambda *a, **k: None)
    # The default reaches _country_allowed for a company with no list of its own.
    assert mon._country_allowed(
        RawPosting("1", "Data Scientist", "IN, KA, Bengaluru", "u", None, "greenhouse"),
        ["US"],
    ) is False


def test_company_filter_overrides_the_global_default():
    """Pinning one board must not be undone by the global list."""
    india = RawPosting("1", "Data Scientist", "IN, KA, Bengaluru", "u", None, "greenhouse")
    # Company explicitly allows IN; the global US default must not apply.
    assert mon._country_allowed(india, ["IN"]) is True


def test_empty_global_list_means_worldwide():
    india = RawPosting("1", "Data Scientist", "IN, KA, Bengaluru", "u", None, "greenhouse")
    assert mon._country_allowed(india, []) is True
    assert mon._country_allowed(india, None) is True


def test_unparseable_country_survives_the_global_filter():
    """Same fail-open rule as the per-company filter."""
    remote = RawPosting("1", "Data Scientist", "Remote", "u", None, "greenhouse")
    assert mon._country_allowed(remote, ["US"]) is True


def test_global_countries_default_to_empty_on_failure(monkeypatch):
    from agent_server.jobboard import platform_client as pc
    monkeypatch.setattr(pc, "_request",
                        lambda *a, **k: (_ for _ in ()).throw(pc.PlatformError("down")))
    assert pc.global_countries() == []


@pytest.mark.parametrize("stored,expected", [
    ("US,CA", ["US", "CA"]),
    ("us, gb ", ["US", "GB"]),
    ("", []),
    ("USA,XX,DE", ["DE"]),        # 3-letter codes are dropped
])
def test_global_countries_parsing(monkeypatch, stored, expected):
    from agent_server.jobboard import platform_client as pc
    monkeypatch.setattr(pc, "_request", lambda *a, **k: {"value": stored})
    assert pc.global_countries() == expected
