"""Offline tests for agent_server.stages.fit_gate.run_fit_gate.

All deps (llm, search, audit) are fakes — zero network/LLM calls.

Key guarantees proved here:
  - PASS-THROUGH: empty/whitespace fit_criteria → passed=True, reason="no_criteria",
    and NO LLM call is made (degrades to today's behaviour, no skipping).
  - SCORING: with criteria, the gate scores via ONE LLM call; passed reflects
    score >= CONFIG.fit_threshold.
  - FAIL-OPEN: any error inside the gate → passed=True, reason="gate_error"
    (a gate bug must never silently drop every company).
"""

from __future__ import annotations

import json

from agent_server.agents.deps import AgentDeps
from agent_server.config import CONFIG
from agent_server.contracts.records import CandidateCompany, FitVerdict
from agent_server.stages.fit_gate import run_fit_gate


class _FakeLLM:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    def complete(self, system, messages, *, tools=None):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        if self.responses:
            return self.responses.pop(0)
        return {"text": "{}", "tool_calls": []}


class _RaisingLLM:
    def complete(self, system, messages, *, tools=None):
        raise RuntimeError("LLM down")


def _audit():
    events: list[dict] = []

    def a(stage, event, data):
        events.append({"stage": stage, "event": event, "data": data})

    a.events = events  # type: ignore[attr-defined]
    return a


def _deps(llm, search=None, audit=None):
    return AgentDeps(
        search=search or (lambda q, *, max_results=10: []),
        fetch_page=lambda url, *, render_js=False: None,
        llm=llm,
        audit=audit or _audit(),
        normalize_domain=lambda x: x,
    )


def _candidate(description="Builds developer tools"):
    return CandidateCompany(
        name="Acme", domain="acme.com", source="open_web", description=description
    )


class TestPassThrough:
    def test_empty_criteria_passes_without_llm(self):
        llm = _FakeLLM()
        v = run_fit_gate("j", _candidate(), "", deps=_deps(llm))
        assert v.passed is True
        assert v.reason == "no_criteria"
        assert v.score == 1.0
        assert llm.calls == []  # no LLM call at all

    def test_whitespace_criteria_passes_without_llm(self):
        llm = _FakeLLM()
        v = run_fit_gate("j", _candidate(), "   \n  ", deps=_deps(llm))
        assert v.passed is True
        assert llm.calls == []


class TestScoring:
    def test_high_score_passes(self):
        llm = _FakeLLM([{"text": json.dumps({"score": 0.9, "reason": "great fit"}),
                         "tool_calls": []}])
        v = run_fit_gate("j", _candidate(), "dev tools startups", deps=_deps(llm))
        assert isinstance(v, FitVerdict)
        assert v.passed is True
        assert v.score == 0.9
        assert len(llm.calls) == 1

    def test_low_score_fails(self):
        llm = _FakeLLM([{"text": json.dumps({"score": 0.1, "reason": "off-target"}),
                         "tool_calls": []}])
        v = run_fit_gate("j", _candidate(), "biotech only", deps=_deps(llm))
        assert v.passed is False
        assert v.score == 0.1

    def test_threshold_boundary_passes(self):
        llm = _FakeLLM([{"text": json.dumps({"score": CONFIG.fit_threshold,
                                             "reason": "edge"}), "tool_calls": []}])
        v = run_fit_gate("j", _candidate(), "x", deps=_deps(llm))
        assert v.passed is True  # score >= threshold

    def test_score_clamped(self):
        llm = _FakeLLM([{"text": json.dumps({"score": 5.0, "reason": "huge"}),
                         "tool_calls": []}])
        v = run_fit_gate("j", _candidate(), "x", deps=_deps(llm))
        assert v.score == 1.0


class TestFailOpen:
    def test_llm_raises_fails_open(self):
        v = run_fit_gate("j", _candidate(), "some ICP", deps=_deps(_RaisingLLM()))
        assert v.passed is True
        assert v.reason == "gate_error"

    def test_malformed_json_scores_zero_and_skips(self):
        # Non-JSON output is parsed as {} → score 0.0 → below threshold → skip.
        llm = _FakeLLM([{"text": "not json", "tool_calls": []}])
        v = run_fit_gate("j", _candidate(), "x", deps=_deps(llm))
        assert v.passed is False
        assert v.score == 0.0
