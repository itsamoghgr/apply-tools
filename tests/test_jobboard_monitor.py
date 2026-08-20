"""Monitor-cycle tests.

The platform client and agent-DB bookkeeping are faked, so these run with no
network and no database. What they actually pin down is the behaviour that
decides whether your inbox is right:

  - a seed cycle reports NOTHING,
  - an unchanged board on the next cycle reports NOTHING,
  - one added posting reports EXACTLY ONE,
  - one broken board never aborts the cycle.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_server.jobboard import monitor as mon
from agent_server.jobboard.adapters.base import AdapterError, RawPosting


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def posting(title="AI Engineer", *, ext=None, url=None, loc="NYC", posted=None):
    return RawPosting(
        external_id=ext,
        title=title,
        location=loc,
        url=url or f"https://x/{title.replace(' ', '-').lower()}",
        posted_at=posted,
        source="greenhouse",
    )


class FakePlatform:
    """In-memory stand-in for the platform API, keyed like the real UNIQUE index."""

    def __init__(self):
        self.rows: dict[tuple[str, str], dict] = {}
        self.patches: list[tuple[str, dict]] = []
        self.archived: list[tuple[str, list[str]]] = []
        self.fail_upsert = False

    def upsert_postings(self, company_id, postings):
        if self.fail_upsert:
            raise mon.pc.PlatformError("upsert boom")
        new_ids = []
        for p in postings:
            key = (company_id, p["dedup_key"])
            if key in self.rows:
                # Mirrors the real ON CONFLICT: refresh display fields, but
                # NEVER touch is_new.
                self.rows[key]["title"] = p["title"]
                continue
            self.rows[key] = dict(p, id=f"jp_{len(self.rows)}")
            new_ids.append(self.rows[key]["id"])
        return {"inserted": len(new_ids), "updated": len(postings) - len(new_ids),
                "new_ids": new_ids}

    def archive_postings(self, company_id, live_keys):
        self.archived.append((company_id, live_keys))
        return 0

    def patch_company(self, company_id, fields):
        self.patches.append((company_id, fields))


@pytest.fixture
def fake_env(monkeypatch):
    """Wire the monitor to fakes: no network, no DB, no sleeping."""
    plat = FakePlatform()
    monkeypatch.setattr(mon.pc, "upsert_postings", plat.upsert_postings)
    monkeypatch.setattr(mon.pc, "archive_postings", plat.archive_postings)
    monkeypatch.setattr(mon.pc, "patch_company", plat.patch_company)
    monkeypatch.setattr(mon.jb_db, "record_company", lambda *a, **k: None)
    monkeypatch.setattr(mon.jb_db, "start_run", lambda *a, **k: "run_1")
    monkeypatch.setattr(mon.jb_db, "finish_run", lambda *a, **k: None)
    monkeypatch.setattr(mon.time, "sleep", lambda s: None)
    return plat


def company(**kw):
    base = {"id": "c1", "name": "Acme", "careerUrl": "https://boards.greenhouse.io/acme",
            "ats": "greenhouse", "atsSlug": "acme", "seededAt": None, "roleFilter": None}
    base.update(kw)
    return base


def set_board(monkeypatch, postings):
    monkeypatch.setattr(mon, "fetch_for", lambda *a, **k: postings)


# ---------------------------------------------------------------------------
# The three cases that matter
# ---------------------------------------------------------------------------

def test_seed_cycle_reports_nothing(fake_env, monkeypatch):
    """Adding a company must not dump its back catalogue into the next alert."""
    set_board(monkeypatch, [posting("AI Engineer", ext="1"),
                            posting("Data Scientist", ext="2"),
                            posting("Product Manager", ext="3")])   # non-matching

    res = mon.monitor_company(company(), "run_1", now=NOW)

    assert res.ok
    assert res.seen == 3
    assert res.matched == 2            # PM filtered out
    assert res.new == 0                # NOTHING reported on a seed
    assert all(r["is_new"] is False for r in fake_env.rows.values())
    # seededAt is stamped so the next cycle behaves normally.
    assert any("seeded_at" in f for _, f in fake_env.patches)


def test_unchanged_board_yields_zero_new(fake_env, monkeypatch):
    board = [posting("AI Engineer", ext="1"), posting("Data Scientist", ext="2")]
    set_board(monkeypatch, board)
    mon.monitor_company(company(), "run_1", now=NOW)          # seed

    res = mon.monitor_company(company(seededAt=NOW.isoformat()), "run_1", now=NOW)

    assert res.matched == 2
    assert res.new == 0
    assert len(fake_env.rows) == 2      # no duplicates created


def test_one_added_posting_yields_exactly_one_new(fake_env, monkeypatch):
    set_board(monkeypatch, [posting("AI Engineer", ext="1")])
    mon.monitor_company(company(), "run_1", now=NOW)          # seed

    set_board(monkeypatch, [posting("AI Engineer", ext="1"),
                            posting("Founding Engineer", ext="2")])
    res = mon.monitor_company(company(seededAt=NOW.isoformat()), "run_1", now=NOW)

    assert res.new == 1
    assert len(fake_env.rows) == 2


def test_relisted_posting_is_not_new_again(fake_env, monkeypatch):
    """A board editing a title must not resurrect an already-reported role."""
    set_board(monkeypatch, [posting("AI Engineer", ext="1")])
    mon.monitor_company(company(), "run_1", now=NOW)

    set_board(monkeypatch, [posting("Senior AI Engineer", ext="1")])   # same id
    res = mon.monitor_company(company(seededAt=NOW.isoformat()), "run_1", now=NOW)

    assert res.new == 0
    assert len(fake_env.rows) == 1


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------

def test_adapter_failure_is_isolated(fake_env, monkeypatch):
    def boom(*a, **k):
        raise AdapterError("board is down")
    monkeypatch.setattr(mon, "fetch_for", boom)

    res = mon.monitor_company(company(), "run_1", now=NOW)

    assert res.ok is False
    assert "board is down" in res.error
    # The failure is recorded ON the company so the UI can show a red badge.
    _, fields = fake_env.patches[-1]
    assert fields["last_status"] == "error"
    assert "board is down" in fields["last_error"]


def test_unexpected_exception_is_isolated(fake_env, monkeypatch):
    """A bug in one adapter must not escape into the cycle."""
    def boom(*a, **k):
        raise ValueError("unexpected parser bug")
    monkeypatch.setattr(mon, "fetch_for", boom)

    res = mon.monitor_company(company(), "run_1", now=NOW)

    assert res.ok is False
    assert "ValueError" in res.error


def test_one_bad_company_does_not_abort_the_cycle(fake_env, monkeypatch):
    companies = [company(id="c1", name="Good"), company(id="c2", name="Bad"),
                 company(id="c3", name="AlsoGood")]
    monkeypatch.setattr(mon.pc, "list_companies", lambda **k: companies)

    def fetch(ats, slug, url):
        if url.endswith("bad"):
            raise AdapterError("dead board")
        return [posting("AI Engineer", ext="1")]
    monkeypatch.setattr(mon, "fetch_for", fetch)
    companies[1]["careerUrl"] = "https://boards.greenhouse.io/bad"

    summary = mon.run_monitor_cycle()

    assert summary.companies_total == 3
    assert summary.companies_ok == 2        # the other two still ran
    assert summary.companies_failed == 1


def test_success_clears_a_previous_error(fake_env, monkeypatch):
    """lastError must be reset once a board recovers — an explicit null."""
    set_board(monkeypatch, [posting("AI Engineer", ext="1")])
    mon.monitor_company(company(), "run_1", now=NOW)
    _, fields = fake_env.patches[-1]
    assert fields["last_status"] == "ok"
    assert fields["last_error"] is None


# ---------------------------------------------------------------------------
# Day filtering
# ---------------------------------------------------------------------------

def test_day_filter_keeps_today_and_all_undated(fake_env, monkeypatch):
    """Undated postings always survive: novelty is settled by dedup, and
    dropping them would hide roles on boards that publish no dates.

    CONFIG is pinned explicitly rather than inherited: JOBBOARD_TODAY_ONLY is a
    local .env choice, and a test that changes behaviour with the developer's
    environment is worse than no test.
    """
    import dataclasses
    monkeypatch.setattr(mon, "CONFIG",
                        dataclasses.replace(mon.CONFIG, jobboard_today_only=True))
    old = NOW - timedelta(days=9)
    set_board(monkeypatch, [
        posting("AI Engineer", ext="1", posted=NOW),        # today
        posting("Data Scientist", ext="2", posted=old),     # stale
        posting("Founding Engineer", ext="3", posted=None), # undated
    ])
    res = mon.monitor_company(company(seededAt=NOW.isoformat()), "run_1", now=NOW)

    titles = {r["title"] for r in fake_env.rows.values()}
    assert titles == {"AI Engineer", "Founding Engineer"}
    assert res.new == 2


def test_seed_cycle_is_never_day_filtered(fake_env, monkeypatch):
    """The backlog is exactly what a seed needs to record, stale dates and all."""
    import dataclasses
    monkeypatch.setattr(mon, "CONFIG",
                        dataclasses.replace(mon.CONFIG, jobboard_today_only=True))
    old = NOW - timedelta(days=200)
    set_board(monkeypatch, [posting("AI Engineer", ext="1", posted=old),
                            posting("Data Scientist", ext="2", posted=old)])

    res = mon.monitor_company(company(), "run_1", now=NOW)

    assert res.matched == 2         # both recorded despite being ancient
    assert res.new == 0


def test_day_filter_skipped_when_source_dates_nothing(fake_env, monkeypatch):
    import dataclasses
    monkeypatch.setattr(mon, "CONFIG",
                        dataclasses.replace(mon.CONFIG, jobboard_today_only=True))
    set_board(monkeypatch, [posting("AI Engineer", ext="1", posted=None),
                            posting("Data Scientist", ext="2", posted=None)])
    res = mon.monitor_company(company(seededAt=NOW.isoformat()), "run_1", now=NOW)
    assert res.new == 2


# ---------------------------------------------------------------------------
# Role filter + misc
# ---------------------------------------------------------------------------

def test_per_company_role_filter_narrows_the_board(fake_env, monkeypatch):
    set_board(monkeypatch, [posting("AI Engineer", ext="1"),
                            posting("Data Scientist", ext="2")])
    res = mon.monitor_company(
        company(seededAt=NOW.isoformat(), roleFilter=["AI Engineer"]), "run_1", now=NOW)
    assert res.matched == 1
    assert {r["matched_role"] for r in fake_env.rows.values()} == {"AI Engineer"}


def test_empty_role_filter_falls_back_to_defaults(fake_env, monkeypatch):
    set_board(monkeypatch, [posting("AI Engineer", ext="1")])
    res = mon.monitor_company(
        company(seededAt=NOW.isoformat(), roleFilter=[]), "run_1", now=NOW)
    assert res.matched == 1


def test_archive_receives_only_live_keys(fake_env, monkeypatch):
    set_board(monkeypatch, [posting("AI Engineer", ext="1")])
    mon.monitor_company(company(seededAt=NOW.isoformat()), "run_1", now=NOW)
    company_id, keys = fake_env.archived[-1]
    assert company_id == "c1"
    assert keys == ["greenhouse:1"]


def test_missing_ats_is_detected_and_persisted(fake_env, monkeypatch):
    set_board(monkeypatch, [posting("AI Engineer", ext="1")])
    mon.monitor_company(company(ats=None, atsSlug=None), "run_1", now=NOW)
    # Detection result is written back so the next cycle skips it.
    assert any(f.get("ats") == "greenhouse" for _, f in fake_env.patches)


def test_platform_upsert_failure_marks_company_failed(fake_env, monkeypatch):
    set_board(monkeypatch, [posting("AI Engineer", ext="1")])
    fake_env.fail_upsert = True
    res = mon.monitor_company(company(seededAt=NOW.isoformat()), "run_1", now=NOW)
    assert res.ok is False
    assert "upsert boom" in res.error


def test_cycle_with_no_worklist_succeeds_quietly(fake_env, monkeypatch):
    monkeypatch.setattr(mon.pc, "list_companies", lambda **k: [])
    summary = mon.run_monitor_cycle()
    assert summary.companies_total == 0
    assert summary.results == []


def test_unreachable_platform_fails_the_run_cleanly(fake_env, monkeypatch):
    def boom(**k):
        raise mon.pc.PlatformError("platform down")
    monkeypatch.setattr(mon.pc, "list_companies", boom)
    summary = mon.run_monitor_cycle()      # must not raise
    assert summary.companies_total == 0
