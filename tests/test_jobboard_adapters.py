"""Adapter parsing + ATS detection tests.

Runs entirely against recorded fixtures in tests/fixtures/jobboard/ — no network,
so these stay fast and deterministic even when a real board changes.
"""

from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path

import pytest

from agent_server.jobboard.adapters import detect, fetch_for
from agent_server.jobboard.adapters import amazon, ashby, greenhouse, lever, smartrecruiters
from agent_server.jobboard.adapters.base import AdapterError, RawPosting

FIXTURES = Path(__file__).parent / "fixtures" / "jobboard"


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def no_network(monkeypatch):
    """Fail loudly if a test accidentally makes a real request."""
    def _boom(*a, **k):
        raise AssertionError("unexpected network call in a fixture test")
    monkeypatch.setattr("agent_server.jobboard.adapters.base.httpx.Client", _boom)


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://boards.greenhouse.io/vercel", ("greenhouse", "vercel")),
        ("https://job-boards.greenhouse.io/vercel/jobs/6136160004", ("greenhouse", "vercel")),
        ("https://jobs.lever.co/palantir", ("lever", "palantir")),
        ("https://jobs.lever.co/palantir/abc-123", ("lever", "palantir")),
        ("https://jobs.ashbyhq.com/linear", ("ashby", "linear")),
        ("https://careers.smartrecruiters.com/Visa", ("smartrecruiters", "Visa")),
        ("boards.greenhouse.io/acme", ("greenhouse", "acme")),          # no scheme
        ("https://www.jobs.lever.co/acme", ("lever", "acme")),          # www prefix
        ("https://acme.com/careers", ("generic", None)),                # custom page
        ("https://acme.com/careers?gh_jid=1&src=boards.greenhouse.io/acme",
         ("greenhouse", "acme")),                                       # embedded board
        ("", ("generic", None)),
        ("not a url", ("generic", None)),
    ],
)
def test_detect(url, expected):
    assert detect(url) == expected


# ---------------------------------------------------------------------------
# Per-ATS parsing
# ---------------------------------------------------------------------------

def test_greenhouse_parses_fixture(monkeypatch, no_network):
    monkeypatch.setattr(greenhouse, "get_json", lambda *a, **k: load("greenhouse_vercel.json"))
    postings = greenhouse.fetch("vercel")

    assert len(postings) == 84
    assert all(isinstance(p, RawPosting) for p in postings)
    assert all(p.title and p.url for p in postings)
    assert all(p.source == "greenhouse" for p in postings)
    # first_published is present on every Vercel posting.
    assert all(p.posted_at is not None for p in postings)
    assert all(p.posted_at.tzinfo == timezone.utc for p in postings)
    assert all(p.external_id for p in postings)


def test_greenhouse_prefers_first_published_over_updated_at(monkeypatch, no_network):
    """updated_at moves on any edit; first_published is when the role went live."""
    monkeypatch.setattr(greenhouse, "get_json", lambda *a, **k: {"jobs": [{
        "id": 1, "title": "AI Engineer", "absolute_url": "https://x/1",
        "first_published": "2026-01-01T00:00:00-05:00",
        "updated_at": "2026-08-18T18:06:19-04:00",
    }]})
    assert greenhouse.fetch("x")[0].posted_at.year == 2026
    assert greenhouse.fetch("x")[0].posted_at.month == 1


def test_lever_parses_fixture(monkeypatch, no_network):
    monkeypatch.setattr(lever, "get_json", lambda *a, **k: load("lever_palantir.json"))
    postings = lever.fetch("palantir")

    assert len(postings) == 307
    assert all(p.title and p.url for p in postings)
    assert all(p.source == "lever" for p in postings)
    # createdAt is epoch milliseconds -> must land in a sane year range.
    dated = [p for p in postings if p.posted_at]
    assert dated, "expected at least some dated postings"
    assert all(2000 < p.posted_at.year < 2100 for p in dated)


def test_lever_unknown_slug_raises(monkeypatch, no_network):
    """Lever answers a bad slug with HTTP 200 + {"ok": false}, not a 404."""
    monkeypatch.setattr(lever, "get_json",
                        lambda *a, **k: {"ok": False, "error": "Document not found"})
    with pytest.raises(AdapterError, match="unknown slug"):
        lever.fetch("nope")


def test_ashby_parses_fixture(monkeypatch, no_network):
    monkeypatch.setattr(ashby, "get_json", lambda *a, **k: load("ashby_linear.json"))
    postings = ashby.fetch("linear")

    assert len(postings) == 32
    assert all(p.title and p.url for p in postings)
    assert all(p.source == "ashby" for p in postings)
    assert all(p.posted_at is not None for p in postings)


def test_ashby_skips_unlisted(monkeypatch, no_network):
    monkeypatch.setattr(ashby, "get_json", lambda *a, **k: {"jobs": [
        {"id": "a", "title": "Listed", "jobUrl": "https://x/a", "isListed": True},
        {"id": "b", "title": "Hidden", "jobUrl": "https://x/b", "isListed": False},
        {"id": "c", "title": "NoFlag", "jobUrl": "https://x/c"},
    ]})
    titles = [p.title for p in ashby.fetch("x")]
    assert titles == ["Listed", "NoFlag"]


def test_smartrecruiters_parses_fixture(monkeypatch, no_network):
    monkeypatch.setattr(smartrecruiters, "get_json",
                        lambda *a, **k: load("smartrecruiters_visa.json"))
    postings = smartrecruiters.fetch("Visa")

    assert len(postings) == 2
    assert all(p.source == "smartrecruiters" for p in postings)
    # URL is composed from the documented public pattern.
    assert all(p.url.startswith("https://jobs.smartrecruiters.com/Visa/") for p in postings)
    assert all(p.posted_at is not None for p in postings)
    assert postings[0].location  # {city, region} flattened


def test_smartrecruiters_paginates(monkeypatch, no_network):
    """The one ATS that genuinely pages: walk until totalFound is covered."""
    pages = {
        0: {"totalFound": 150, "content": [
            {"id": f"a{i}", "name": f"Role {i}", "releasedDate": "2026-08-01T00:00:00Z"}
            for i in range(100)]},
        100: {"totalFound": 150, "content": [
            {"id": f"b{i}", "name": f"Role {100+i}", "releasedDate": "2026-08-01T00:00:00Z"}
            for i in range(50)]},
    }
    calls = []
    def fake(url, *, params=None):
        calls.append(params["offset"])
        return pages[params["offset"]]
    monkeypatch.setattr(smartrecruiters, "get_json", fake)

    postings = smartrecruiters.fetch("Big")
    assert len(postings) == 150
    assert calls == [0, 100]          # stopped after the short page
    assert len({p.external_id for p in postings}) == 150   # no cross-page dupes


def test_smartrecruiters_dedupes_across_pages(monkeypatch, no_network):
    """A board shifting under us can repeat a row on the next page."""
    pages = {
        0: {"totalFound": 200, "content": [
            {"id": f"x{i}", "name": f"R{i}"} for i in range(100)]},
        100: {"totalFound": 200, "content": [
            {"id": "x99", "name": "R99"}] + [
            {"id": f"y{i}", "name": f"S{i}"} for i in range(99)]},
    }
    monkeypatch.setattr(smartrecruiters, "get_json",
                        lambda url, *, params=None: pages[params["offset"]])
    postings = smartrecruiters.fetch("Shifty")
    assert len(postings) == 199          # the repeat was dropped
    assert len({p.external_id for p in postings}) == 199


# ---------------------------------------------------------------------------
# Malformed input — every adapter must raise AdapterError, never leak a
# TypeError/KeyError into the monitor loop.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module,payload", [
    (greenhouse, {"not_jobs": []}),
    (greenhouse, []),
    (ashby, "nonsense"),
    (smartrecruiters, {"no_content": 1}),
])
def test_malformed_payload_raises_adapter_error(monkeypatch, no_network, module, payload):
    monkeypatch.setattr(module, "get_json", lambda *a, **k: payload)
    with pytest.raises(AdapterError):
        module.fetch("x")


def test_rows_missing_required_fields_are_skipped(monkeypatch, no_network):
    monkeypatch.setattr(greenhouse, "get_json", lambda *a, **k: {"jobs": [
        {"id": 1, "title": "Good", "absolute_url": "https://x/1"},
        {"id": 2, "title": "", "absolute_url": "https://x/2"},      # no title
        {"id": 3, "absolute_url": "https://x/3"},                    # no title key
        {"id": 4, "title": "No URL"},                                # no url
        "not a dict",
    ]})
    assert [p.title for p in greenhouse.fetch("x")] == ["Good"]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def test_fetch_for_dispatches_by_ats(monkeypatch, no_network):
    monkeypatch.setattr(greenhouse, "get_json", lambda *a, **k: {"jobs": [
        {"id": 1, "title": "AI Engineer", "absolute_url": "https://x/1"}]})
    out = fetch_for("greenhouse", "acme", "https://boards.greenhouse.io/acme")
    assert [p.title for p in out] == ["AI Engineer"]


def test_fetch_for_unknown_ats_raises(no_network):
    with pytest.raises(AdapterError, match="unknown ats"):
        fetch_for("workday", "acme", "https://x")


def test_fetch_for_ats_without_slug_falls_back_to_generic(monkeypatch, no_network):
    """A half-filled row still gets scraped rather than failing outright."""
    called = {}
    def fake_generic(url, **k):
        called["url"] = url
        return []
    monkeypatch.setattr("agent_server.jobboard.adapters.generic.fetch", fake_generic)
    fetch_for("greenhouse", None, "https://acme.com/careers")
    assert called["url"] == "https://acme.com/careers"


# ---------------------------------------------------------------------------
# Amazon (amazon.jobs) — not an ATS, but a public JSON search API, so it gets a
# real adapter rather than the LLM fallback.
# ---------------------------------------------------------------------------

def _amazon_job(**kw):
    base = {"id_icims": "123", "title": "Data Scientist II", "job_path": "/en/jobs/123/ds",
            "location": "US, WA, Seattle", "city": "Seattle", "posted_date": "August 19, 2026"}
    base.update(kw)
    return base


def test_amazon_detect_needs_no_slug():
    """amazon.jobs is one global board, not a per-company one."""
    assert detect("https://www.amazon.jobs") == ("amazon", None)
    assert detect("https://www.amazon.jobs/en/search?base_query=x") == ("amazon", None)


def test_amazon_parses_and_builds_absolute_urls(monkeypatch, no_network):
    monkeypatch.setattr(amazon, "get_json",
                        lambda *a, **k: {"hits": 1, "jobs": [_amazon_job()]})
    postings = amazon.fetch(queries=("data scientist",))
    assert len(postings) == 1
    p = postings[0]
    assert p.title == "Data Scientist II"
    assert p.url == "https://www.amazon.jobs/en/jobs/123/ds"
    assert p.external_id == "123"
    assert p.posted_at.year == 2026 and p.posted_at.month == 8


def test_amazon_parses_human_dates():
    """Amazon returns "August 19, 2026", not ISO — fromisoformat is no use."""
    assert amazon._parse_posted("August 19, 2026").day == 19
    assert amazon._parse_posted("Aug 19, 2026").day == 19
    assert amazon._parse_posted("garbage") is None
    assert amazon._parse_posted(None) is None


def test_amazon_dedupes_across_queries(monkeypatch, no_network):
    """One role legitimately matches several search terms."""
    monkeypatch.setattr(amazon, "get_json",
                        lambda *a, **k: {"hits": 1, "jobs": [_amazon_job(id_icims="same")]})
    postings = amazon.fetch(queries=("data scientist", "data analyst", "ml engineer"))
    assert len(postings) == 1


def test_amazon_skips_rows_missing_required_fields(monkeypatch, no_network):
    monkeypatch.setattr(amazon, "get_json", lambda *a, **k: {"jobs": [
        _amazon_job(),
        _amazon_job(id_icims="2", title=""),          # no title
        _amazon_job(id_icims="3", job_path=None),     # no path -> no URL
    ]})
    assert len(amazon.fetch(queries=("x",))) == 1


def test_amazon_partial_failure_still_returns_results(monkeypatch, no_network):
    """One failing query must not lose the others' results."""
    calls = {"n": 0}
    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise AdapterError("boom")
        return {"jobs": [_amazon_job()]}
    monkeypatch.setattr(amazon, "get_json", flaky)
    assert len(amazon.fetch(queries=("a", "b"))) == 1


def test_amazon_all_queries_failing_raises(monkeypatch, no_network):
    def boom(*a, **k):
        raise AdapterError("down")
    monkeypatch.setattr(amazon, "get_json", boom)
    with pytest.raises(AdapterError, match="all .* queries failed"):
        amazon.fetch(queries=("a", "b"))


def test_fetch_for_amazon_ignores_slug(monkeypatch, no_network):
    monkeypatch.setattr(amazon, "get_json", lambda *a, **k: {"jobs": [_amazon_job()]})
    assert len(fetch_for("amazon", None, "https://www.amazon.jobs")) == 1
