"""Tests for the agentic contact-finder (agent_server/agents/contact.py).

Fake LLM + fake verifier + fake search — no network, no real provider calls.
"""

from __future__ import annotations

from agent_server.agents.contact import ContactResult, find_contact, _patterns
from agent_server.agents.deps import AgentDeps
from agent_server.web import SearchResult
from agent_server.web.verifier import EmailVerdict


def _deps(llm):
    return AgentDeps(
        search=lambda q, max_results=10: [],
        fetch_page=lambda u, render_js=False: None,
        llm=llm,
        audit=lambda *a, **k: None,
        normalize_domain=lambda d: d,
    )


class _Verifier:
    """Fake WaterfallVerifier returning a scripted verdict."""

    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = 0

    def find_and_verify(self, domain, name):
        self.calls += 1
        return self.verdict


# ── pattern helper ────────────────────────────────────────────────────────────

def test_patterns_generates_common_shapes():
    pats = _patterns("Jane Doe", "acme.com")
    assert "jane@acme.com" in pats
    assert "jane.doe@acme.com" in pats
    assert "jdoe@acme.com" in pats
    assert all(p.endswith("@acme.com") for p in pats)


def test_patterns_handles_single_name():
    pats = _patterns("Cher", "acme.com")
    assert "cher@acme.com" in pats


# ── agent flow ──────────────────────────────────────────────────────────────

class _LLM:
    """Scripted LLM: a list of responses, popped per call."""

    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, system, messages, *, tools=None):
        return self.responses.pop(0) if self.responses else {"text": "{}", "tool_calls": []}


def test_agent_uses_verify_tool_then_returns_email():
    # Turn 1: call provider_lookup.  Turn 2: final JSON with the found email.
    llm = _LLM([
        {"text": "", "tool_calls": [
            {"id": "t1", "name": "provider_lookup", "input": {"domain": "acme.com", "full_name": "Jane Doe"}}]},
        {"text": '{"email": "jane@acme.com", "score": 0.95, "method": "apollo", "rationale": "Apollo verified"}',
         "tool_calls": []},
    ])
    verifier = _Verifier(EmailVerdict(email="jane@acme.com", score=0.95, method="apollo", detail={}))
    res = find_contact("acme.com", "Jane Doe", _deps(llm), verifier=verifier)
    assert isinstance(res, ContactResult)
    assert res.email == "jane@acme.com"
    assert res.score == 0.95
    assert res.method == "apollo"
    assert verifier.calls == 1


def test_agent_falls_back_to_best_candidate_when_final_json_empty():
    # Model calls verify (which finds an email) but then returns empty JSON.
    llm = _LLM([
        {"text": "", "tool_calls": [
            {"id": "t1", "name": "provider_lookup", "input": {"domain": "acme.com", "full_name": "Jane Doe"}}]},
        {"text": "{}", "tool_calls": []},
    ])
    verifier = _Verifier(EmailVerdict(email="jane@acme.com", score=0.8, method="hunter", detail={}))
    res = find_contact("acme.com", "Jane Doe", _deps(llm), verifier=verifier)
    assert res.email == "jane@acme.com"  # recovered from the verified candidate
    assert res.method == "hunter"


def test_agent_never_raises_on_llm_error():
    class _Boom:
        def complete(self, *a, **k):
            raise RuntimeError("llm down")

    res = find_contact("acme.com", "Jane Doe", _deps(_Boom()),
                       verifier=_Verifier(EmailVerdict(None, 0.0, "none", {})))
    assert isinstance(res, ContactResult)
    assert res.email is None  # nothing found, but no exception


def test_agent_collects_emails_from_search_snippets():
    # Model searches; a snippet contains an email; model returns it.
    deps = _deps(_LLM([
        {"text": "", "tool_calls": [
            {"id": "t1", "name": "web_search", "input": {"query": "Jane Doe acme email"}}]},
        {"text": '{"email": "jane@acme.com", "score": 0.4, "method": "web_snippet", "rationale": "found in snippet"}',
         "tool_calls": []},
    ]))
    deps.search = lambda q, max_results=10: [
        SearchResult(title="Contact", url="https://acme.com/about", snippet="Reach Jane at jane@acme.com")
    ]
    res = find_contact("acme.com", "Jane Doe", deps,
                       verifier=_Verifier(EmailVerdict(None, 0.0, "none", {})))
    assert res.email == "jane@acme.com"
