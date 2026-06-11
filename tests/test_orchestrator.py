"""Tests for agent_server/orchestrator/loop.py

Structure
---------
* No-DB section: pure-logic tests drive run_pipeline with FULLY FAKE in-memory
  stages AND a fake DB layer (monkeypatched).  These always run.

* Live-DB section: drive the full pipeline against the real apply_agent DB
  using the stub stages from runner.py.  Skipped when Postgres is unreachable.

Key assertions (all in both sections where feasible):
  - Stops at target=50 when 80 candidates exist (processes exactly 50).
  - Stops at 'exhausted' when fewer candidates than target.
  - Never indexes past the end of the survivors list.
  - verified_count is correct.
  - stop_reason is correct.
"""

from __future__ import annotations

import pytest

from agent_server.contracts.records import (
    CandidateCompany,
    FitVerdict,
    ResearchResult,
    VerifiedLead,
)
from agent_server.orchestrator.loop import Stages


# ---------------------------------------------------------------------------
# Helpers: build lightweight fake stages + a fake DB
# ---------------------------------------------------------------------------


def _make_candidates(n: int) -> list[CandidateCompany]:
    return [
        CandidateCompany(
            name=f"Co {i}",
            domain=f"co-{i}.test",
            source="open_web",
        )
        for i in range(1, n + 1)
    ]


def _fake_discover(n: int):
    """Return a discover callable that yields `n` fake candidates."""
    def discover(job_id: str, query_hint: str, target: int) -> list[CandidateCompany]:
        return _make_candidates(n)
    return discover


def _fake_dedup(job_id: str, candidates: list[CandidateCompany]) -> list[CandidateCompany]:
    return candidates  # pass-through


def _fake_fit_gate(
    job_id: str, candidate: CandidateCompany, fit_criteria: str
) -> FitVerdict:
    """Always-pass fit gate (no skipping) for the baseline pipeline tests."""
    return FitVerdict(passed=True, score=1.0, reason="test_pass")


def _fake_research(
    job_id: str, candidate: CandidateCompany, fit_criteria: str = ""
) -> ResearchResult:
    return ResearchResult(
        domain=candidate.domain,
        name=candidate.name,
    )


def _fake_verify(job_id: str, research: ResearchResult) -> VerifiedLead:
    return VerifiedLead(
        domain=research.domain,
        name=research.name,
        confidence=0.75,
    )


# delivered items accumulate here when using fake deliver
_deliveries: list[tuple[str, VerifiedLead, bool]] = []


def _fake_deliver(job_id: str, lead: VerifiedLead, dry_run: bool) -> None:
    _deliveries.append((job_id, lead, dry_run))


def _make_fake_stages(n_candidates: int) -> Stages:
    return Stages(
        discover=_fake_discover(n_candidates),
        dedup=_fake_dedup,
        fit_gate=_fake_fit_gate,
        research=_fake_research,
        verify=_fake_verify,
        deliver=_fake_deliver,
    )


# ---------------------------------------------------------------------------
# No-DB tests — always run; DB calls are fully monkeypatched
# ---------------------------------------------------------------------------


class TestRunPipelineNoDb:
    """Drive run_pipeline with patched DB functions so no Postgres is needed."""

    @pytest.fixture(autouse=True)
    def _patch_db(self, monkeypatch):
        """Replace all DB helpers with no-ops / collectors."""
        import agent_server.orchestrator.loop as loop_mod

        # Job state accumulator keyed by field name.
        self.job_state: dict = {"status": "pending", "verified_count": 0}

        def fake_update_job(job_id: str, **fields):
            self.job_state.update(fields)

        def fake_add_checkpoint(*args, **kwargs):
            pass

        def fake_audit_add(*args, **kwargs):
            pass

        # Collector for fit-gate skips written to the seen-cache.
        self.seen_skips: list[dict] = []

        def fake_seen_add(domain, outcome, *, reason=None, job_id=None):
            self.seen_skips.append(
                {"domain": domain, "outcome": outcome, "reason": reason}
            )

        monkeypatch.setattr(loop_mod, "update_job", fake_update_job)
        monkeypatch.setattr(loop_mod, "add_checkpoint", fake_add_checkpoint)
        monkeypatch.setattr(loop_mod, "audit_add", fake_audit_add)
        monkeypatch.setattr(loop_mod, "seen_add", fake_seen_add)

        # Also suppress the sleep so tests run instantly.
        import time
        monkeypatch.setattr(time, "sleep", lambda _: None)

    @pytest.fixture(autouse=True)
    def _clear_deliveries(self):
        _deliveries.clear()
        yield
        _deliveries.clear()

    # ----------------------------------------------------------------
    # Core logic: stops at target
    # ----------------------------------------------------------------

    def test_stops_at_target_when_surplus_candidates(self):
        """With 80 candidates and target=50, should stop at exactly 50."""
        from agent_server.orchestrator.loop import run_pipeline
        stages = _make_fake_stages(80)

        run_pipeline("job-1", query_hint="test", target=50, dry_run=False, stages=stages)

        assert self.job_state["verified_count"] == 50
        assert self.job_state["stop_reason"] == "target_reached"
        assert self.job_state["status"] == "succeeded"

    def test_stops_at_target_exactly(self):
        """If candidates == target, should reach target_reached (not exhausted)."""
        from agent_server.orchestrator.loop import run_pipeline
        stages = _make_fake_stages(50)

        run_pipeline("job-2", query_hint="test", target=50, dry_run=False, stages=stages)

        assert self.job_state["verified_count"] == 50
        assert self.job_state["stop_reason"] == "target_reached"

    # ----------------------------------------------------------------
    # Exhaustion path
    # ----------------------------------------------------------------

    def test_exhausted_when_fewer_candidates_than_target(self):
        """With only 10 candidates and target=50, should exhaust the list."""
        from agent_server.orchestrator.loop import run_pipeline
        stages = _make_fake_stages(10)

        run_pipeline("job-3", query_hint="test", target=50, dry_run=False, stages=stages)

        assert self.job_state["verified_count"] == 10
        assert self.job_state["stop_reason"] == "exhausted"
        assert self.job_state["status"] == "succeeded"

    def test_exhausted_empty_candidates(self):
        """With zero candidates, should exhaust immediately with verified_count=0."""
        from agent_server.orchestrator.loop import run_pipeline
        stages = _make_fake_stages(0)

        run_pipeline("job-4", query_hint="test", target=50, dry_run=False, stages=stages)

        assert self.job_state["verified_count"] == 0
        assert self.job_state["stop_reason"] == "exhausted"

    # ----------------------------------------------------------------
    # Never indexes past end
    # ----------------------------------------------------------------

    def test_never_indexes_past_end(self):
        """Processed count must never exceed the actual number of candidates."""
        from agent_server.orchestrator.loop import run_pipeline

        n = 15
        stages = _make_fake_stages(n)

        run_pipeline("job-5", query_hint="test", target=100, dry_run=False, stages=stages)

        # candidates_processed should be <= n
        assert self.job_state.get("candidates_processed", 0) <= n
        assert self.job_state["verified_count"] == n

    def test_deliver_called_correct_times_for_target(self):
        """deliver() must be called exactly `target` times when hitting the target."""
        from agent_server.orchestrator.loop import run_pipeline
        stages = _make_fake_stages(80)

        run_pipeline("job-6", query_hint="test", target=50, dry_run=False, stages=stages)

        assert len(_deliveries) == 50

    def test_deliver_called_correct_times_for_exhaustion(self):
        """deliver() must be called exactly n times when exhausted."""
        from agent_server.orchestrator.loop import run_pipeline
        stages = _make_fake_stages(7)

        run_pipeline("job-7", query_hint="test", target=50, dry_run=False, stages=stages)

        assert len(_deliveries) == 7

    # ----------------------------------------------------------------
    # Small target
    # ----------------------------------------------------------------

    def test_target_1(self):
        """target=1 with many candidates should stop after first verified lead."""
        from agent_server.orchestrator.loop import run_pipeline
        stages = _make_fake_stages(80)

        run_pipeline("job-8", query_hint="test", target=1, dry_run=False, stages=stages)

        assert self.job_state["verified_count"] == 1
        assert self.job_state["stop_reason"] == "target_reached"
        assert len(_deliveries) == 1

    # ----------------------------------------------------------------
    # Per-candidate exception does not abort the whole run
    # ----------------------------------------------------------------

    def test_candidate_failure_does_not_abort_pipeline(self):
        """A research failure for one candidate should be swallowed; others proceed."""
        from agent_server.orchestrator.loop import run_pipeline

        fail_at = 5  # the 5th candidate (0-indexed cursor=4) will raise

        call_count = {"n": 0}

        def flaky_research(
            job_id: str, candidate: CandidateCompany, fit_criteria: str = ""
        ) -> ResearchResult:
            call_count["n"] += 1
            if call_count["n"] == fail_at:
                raise RuntimeError("simulated research failure")
            return ResearchResult(domain=candidate.domain, name=candidate.name)

        stages = Stages(
            discover=_fake_discover(10),
            dedup=_fake_dedup,
            fit_gate=_fake_fit_gate,
            research=flaky_research,
            verify=_fake_verify,
            deliver=_fake_deliver,
        )

        run_pipeline("job-9", query_hint="test", target=50, dry_run=False, stages=stages)

        # 10 candidates, 1 failed -> 9 delivered.
        assert self.job_state["verified_count"] == 9
        assert self.job_state["stop_reason"] == "exhausted"
        assert len(_deliveries) == 9

    # ----------------------------------------------------------------
    # Fit gate skip path
    # ----------------------------------------------------------------

    def test_fit_gate_skip_records_seen_and_skips_research(self):
        """A failing fit gate skips the company: it is recorded in the seen-cache
        with outcome='skipped', never researched/verified/delivered, and counted
        in skipped_count."""
        from agent_server.contracts.records import ResearchResult
        from agent_server.orchestrator.loop import run_pipeline

        researched: list[str] = []

        def gate(job_id, candidate, fit_criteria):
            # Skip every even-indexed domain (co-2, co-4 of 5 candidates).
            n = int(candidate.domain.split("-")[1].split(".")[0])
            passed = n % 2 == 1
            return FitVerdict(passed=passed, score=0.1 if not passed else 0.9,
                              reason="test")

        def tracking_research(job_id, candidate, fit_criteria=""):
            researched.append(candidate.domain)
            return ResearchResult(domain=candidate.domain, name=candidate.name)

        stages = Stages(
            discover=_fake_discover(5),
            dedup=_fake_dedup,
            fit_gate=gate,
            research=tracking_research,
            verify=_fake_verify,
            deliver=_fake_deliver,
        )

        run_pipeline("job-fit", query_hint="x", target=50, dry_run=False,
                     fit_criteria="ICP", stages=stages)

        # co-2 and co-4 were skipped -> 2 skips recorded in the seen-cache.
        assert self.job_state["skipped_count"] == 2
        assert all(s["outcome"] == "skipped" for s in self.seen_skips)
        assert {s["domain"] for s in self.seen_skips} == {"co-2.test", "co-4.test"}
        # Skipped domains are NEVER researched; the 3 passing ones are.
        assert "co-2.test" not in researched
        assert "co-4.test" not in researched
        assert self.job_state["verified_count"] == 3
        assert len(_deliveries) == 3

    # ----------------------------------------------------------------
    # dry_run propagation
    # ----------------------------------------------------------------

    def test_dry_run_flag_propagated_to_deliver(self):
        """deliver() must receive dry_run=True when requested."""
        from agent_server.orchestrator.loop import run_pipeline

        dry_delivers: list[bool] = []

        def capturing_deliver(job_id: str, lead: VerifiedLead, dry_run: bool) -> None:
            dry_delivers.append(dry_run)

        stages = Stages(
            discover=_fake_discover(5),
            dedup=_fake_dedup,
            fit_gate=_fake_fit_gate,
            research=_fake_research,
            verify=_fake_verify,
            deliver=capturing_deliver,
        )

        run_pipeline("job-10", query_hint="test", target=50, dry_run=True, stages=stages)

        assert all(dry_delivers), "All deliver calls should have dry_run=True"
        assert len(dry_delivers) == 5


# ---------------------------------------------------------------------------
# Live-DB tests — skipped when Postgres is unreachable
# ---------------------------------------------------------------------------


class TestRunPipelineLiveDb:
    """End-to-end: run_pipeline with stub stages against the real apply_agent DB."""

    def test_stop_at_target_50(self, live_db):
        """Stub discover returns 80 candidates; pipeline should stop at 50."""
        from agent_server.db.agent_db import create_job, get_job
        from agent_server.orchestrator.loop import run_pipeline
        from agent_server.orchestrator.runner import build_stub_stages
        import time as _time
        from unittest.mock import patch

        job_id = create_job(target_count=50)

        # Suppress sleep so test runs in reasonable time.
        with patch.object(_time, "sleep", lambda _: None):
            stages = build_stub_stages()
            run_pipeline(
                job_id,
                query_hint="live-db test",
                target=50,
                dry_run=True,
                stages=stages,
            )

        job = get_job(job_id)
        assert job is not None
        assert job["status"] == "succeeded"
        assert job["verified_count"] == 50
        assert job["stop_reason"] == "target_reached"

    def test_exhausted_small_target(self, live_db):
        """target=200 > 80 stubs -> stop_reason must be 'exhausted'."""
        from agent_server.db.agent_db import create_job, get_job
        from agent_server.orchestrator.loop import run_pipeline
        from agent_server.orchestrator.runner import build_stub_stages
        import time as _time
        from unittest.mock import patch

        job_id = create_job(target_count=200)

        with patch.object(_time, "sleep", lambda _: None):
            stages = build_stub_stages()
            run_pipeline(
                job_id,
                query_hint="exhaustion test",
                target=200,
                dry_run=True,
                stages=stages,
            )

        job = get_job(job_id)
        assert job is not None
        assert job["status"] == "succeeded"
        assert job["stop_reason"] == "exhausted"
        assert job["verified_count"] == 80  # all 80 stubs processed

    def test_candidates_processed_never_exceeds_total(self, live_db):
        """candidates_processed must never exceed candidates_total."""
        from agent_server.db.agent_db import create_job, get_job
        from agent_server.orchestrator.loop import run_pipeline
        from agent_server.orchestrator.runner import build_stub_stages
        import time as _time
        from unittest.mock import patch

        job_id = create_job(target_count=50)

        with patch.object(_time, "sleep", lambda _: None):
            stages = build_stub_stages()
            run_pipeline(
                job_id,
                query_hint="bounds test",
                target=50,
                dry_run=True,
                stages=stages,
            )

        job = get_job(job_id)
        assert job["candidates_processed"] <= job["candidates_total"]
