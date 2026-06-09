"""Unit tests for agent_server/agents/discovery.py (run_discovery).

All external I/O is mocked:
- LLM via a fake LLMClient injected into AgentDeps
- Web tools via in-memory fakes
- Three source connectors are monkeypatched to return controlled fixtures
- No real Anthropic API, no real network
"""

from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_server.agents.deps import AgentDeps
from agent_server.agents.discovery import run_discovery, MAX_TOOL_CALLS
from agent_server.contracts.records import CandidateCompany
from agent_server.web import FetchedPage, SearchResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize(raw: str) -> str | None:
    """Minimal normalizer for tests."""
    blocked = {
        "linkedin.com", "twitter.com", "x.com", "facebook.com",
        "medium.com", "github.com", "youtube.com", "crunchbase.com",
    }
    raw = raw.strip()
    raw = re.sub(r"^https?://", "", raw)
    raw = re.sub(r"^www\.", "", raw)
    raw = raw.split("/")[0].split("?")[0].split("#")[0].lower()
    if not raw or "." not in raw:
        return None
    if raw in blocked:
        return None
    return raw


def _make_candidate(name: str, domain: str, source: str = "open_web") -> CandidateCompany:
    return CandidateCompany(name=name, domain=domain, source=source)


# ---------------------------------------------------------------------------
# Fake LLM that emits a scripted response
# ---------------------------------------------------------------------------


class _ScriptedLLM:
    """Fake LLMClient that immediately returns a final JSON list (no tool calls)."""

    def __init__(self, companies: list[dict[str, Any]]):
        self._json = json.dumps(companies)
        self.call_count = 0

    def complete(
        self, system: str, messages: list[dict], *, tools: list[dict] | None = None
    ) -> dict:
        self.call_count += 1
        return {"text": self._json, "tool_calls": []}


class _ToolLoopLLM:
    """LLM that first emits a tool call, then emits a final JSON list."""

    def __init__(
        self,
        companies: list[dict[str, Any]],
        tool_name: str = "web_search",
        tool_input: dict | None = None,
    ):
        self._companies = companies
        self._tool_name = tool_name
        self._tool_input = tool_input or {"query": "startups 2024"}
        self._turn = 0

    def complete(
        self, system: str, messages: list[dict], *, tools: list[dict] | None = None
    ) -> dict:
        self._turn += 1
        if self._turn == 1:
            # First turn: emit a tool call
            return {
                "text": "Let me search.",
                "tool_calls": [
                    {
                        "name": self._tool_name,
                        "input": self._tool_input,
                        "id": "toolu_001",
                    }
                ],
            }
        # Second turn: final answer
        return {"text": json.dumps(self._companies), "tool_calls": []}


class _FailingLLM:
    """LLM that always raises."""

    def complete(
        self, system: str, messages: list[dict], *, tools: list[dict] | None = None
    ) -> dict:
        raise RuntimeError("LLM is down")


def _make_deps(llm=None, search_fn=None, fetch_fn=None) -> tuple[AgentDeps, list]:
    """Build AgentDeps with fakes; returns (deps, audit_log)."""
    audit_log: list[tuple[str, str, dict]] = []

    def _audit(stage: str, event: str, data: dict) -> None:
        audit_log.append((stage, event, data))

    fake_search = search_fn or (lambda query, max_results=10: [])
    fake_fetch = fetch_fn or (
        lambda url, render_js=False: FetchedPage(
            url=url,
            final_url=url,
            title="fake page",
            text="no content",
            ok=True,
            status=200,
        )
    )

    deps = AgentDeps(
        search=fake_search,
        fetch_page=fake_fetch,
        llm=llm or _ScriptedLLM([]),
        audit=_audit,
        normalize_domain=_normalize,
    )
    return deps, audit_log


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunDiscoveryBasic:
    def test_returns_list_of_candidates(self, monkeypatch):
        """run_discovery returns a list of CandidateCompany."""
        llm = _ScriptedLLM(
            [{"name": "Acme", "domain_or_url": "acme.com"}]
        )
        deps, _ = _make_deps(llm=llm)

        monkeypatch.setattr(
            "agent_server.agents.discovery.fetch_yc_candidates", lambda d, **kw: []
        )
        monkeypatch.setattr(
            "agent_server.agents.discovery.fetch_producthunt_candidates",
            lambda d, **kw: [],
        )
        monkeypatch.setattr(
            "agent_server.agents.discovery.fetch_rss_candidates", lambda d, **kw: []
        )

        result = run_discovery("job1", query_hint=None, target=5, deps=deps)

        assert isinstance(result, list)
        assert all(isinstance(c, CandidateCompany) for c in result)

    def test_domains_are_normalized(self, monkeypatch):
        """Domains in the returned candidates are normalized (no scheme/www)."""
        llm = _ScriptedLLM(
            [
                {"name": "Acme", "domain_or_url": "https://www.acme.com/about"},
                {"name": "Beta", "domain_or_url": "beta.io"},
            ]
        )
        deps, _ = _make_deps(llm=llm)

        for src in ("fetch_yc_candidates", "fetch_producthunt_candidates", "fetch_rss_candidates"):
            monkeypatch.setattr(
                f"agent_server.agents.discovery.{src}", lambda d, **kw: []
            )

        result = run_discovery("job2", query_hint=None, target=5, deps=deps)

        domains = {c.domain for c in result}
        assert "acme.com" in domains
        assert "beta.io" in domains
        # Scheme and www are stripped
        assert "https://www.acme.com/about" not in domains

    def test_merges_structured_floor(self, monkeypatch):
        """Candidates from all three structured sources are merged into the result."""
        llm = _ScriptedLLM([])  # Open web finds nothing
        deps, _ = _make_deps(llm=llm)

        monkeypatch.setattr(
            "agent_server.agents.discovery.fetch_yc_candidates",
            lambda d, **kw: [_make_candidate("YCCo", "ycco.com", "yc_oss")],
        )
        monkeypatch.setattr(
            "agent_server.agents.discovery.fetch_producthunt_candidates",
            lambda d, **kw: [_make_candidate("PHCo", "phco.com", "product_hunt")],
        )
        monkeypatch.setattr(
            "agent_server.agents.discovery.fetch_rss_candidates",
            lambda d, **kw: [_make_candidate("RSSCo", "rssco.com", "rss")],
        )

        result = run_discovery("job3", query_hint=None, target=5, deps=deps)

        domains = {c.domain for c in result}
        assert "ycco.com" in domains
        assert "phco.com" in domains
        assert "rssco.com" in domains

    def test_deduplicates_exact_domain_dupes(self, monkeypatch):
        """If the same domain appears in multiple sources, it appears once."""
        llm = _ScriptedLLM(
            [{"name": "Acme-Web", "domain_or_url": "acme.com"}]
        )
        deps, _ = _make_deps(llm=llm)

        monkeypatch.setattr(
            "agent_server.agents.discovery.fetch_yc_candidates",
            lambda d, **kw: [_make_candidate("Acme-YC", "acme.com", "yc_oss")],
        )
        monkeypatch.setattr(
            "agent_server.agents.discovery.fetch_producthunt_candidates",
            lambda d, **kw: [],
        )
        monkeypatch.setattr(
            "agent_server.agents.discovery.fetch_rss_candidates",
            lambda d, **kw: [],
        )

        result = run_discovery("job4", query_hint=None, target=5, deps=deps)

        acme_hits = [c for c in result if c.domain == "acme.com"]
        assert len(acme_hits) == 1

    def test_open_web_source_label(self, monkeypatch):
        """Companies found via open web have source="open_web"."""
        llm = _ScriptedLLM(
            [{"name": "WebCo", "domain_or_url": "webco.com"}]
        )
        deps, _ = _make_deps(llm=llm)

        for src in ("fetch_yc_candidates", "fetch_producthunt_candidates", "fetch_rss_candidates"):
            monkeypatch.setattr(
                f"agent_server.agents.discovery.{src}", lambda d, **kw: []
            )

        result = run_discovery("job5", query_hint=None, target=5, deps=deps)

        webco = next((c for c in result if c.domain == "webco.com"), None)
        assert webco is not None
        assert webco.source == "open_web"


class TestRunDiscoveryLLMFailure:
    def test_llm_failure_still_returns_floor(self, monkeypatch):
        """When the LLM raises, run_discovery returns the structured-floor candidates."""
        deps, _ = _make_deps(llm=_FailingLLM())

        monkeypatch.setattr(
            "agent_server.agents.discovery.fetch_yc_candidates",
            lambda d, **kw: [_make_candidate("SafeYC", "safeyc.com", "yc_oss")],
        )
        monkeypatch.setattr(
            "agent_server.agents.discovery.fetch_producthunt_candidates",
            lambda d, **kw: [],
        )
        monkeypatch.setattr(
            "agent_server.agents.discovery.fetch_rss_candidates",
            lambda d, **kw: [],
        )

        # Must NOT raise
        result = run_discovery("job-fail", query_hint=None, target=5, deps=deps)

        assert any(c.domain == "safeyc.com" for c in result)

    def test_llm_failure_does_not_raise(self, monkeypatch):
        """run_discovery never raises even when the LLM completely fails."""
        deps, _ = _make_deps(llm=_FailingLLM())

        for src in ("fetch_yc_candidates", "fetch_producthunt_candidates", "fetch_rss_candidates"):
            monkeypatch.setattr(
                f"agent_server.agents.discovery.{src}", lambda d, **kw: []
            )

        try:
            result = run_discovery("job-fail2", query_hint=None, target=5, deps=deps)
        except Exception as exc:
            pytest.fail(f"run_discovery raised unexpectedly: {exc}")

        assert isinstance(result, list)


class TestRunDiscoveryToolLoop:
    def test_tool_loop_executes_search(self, monkeypatch):
        """When the LLM emits a web_search tool call, search is actually called."""
        search_calls: list[str] = []

        def _fake_search(query: str, max_results: int = 10) -> list[SearchResult]:
            search_calls.append(query)
            return [
                SearchResult(
                    title="Startup News",
                    url="https://techblog.com/article",
                    snippet="Great startups.",
                )
            ]

        companies = [{"name": "FoundCo", "domain_or_url": "foundco.com"}]
        llm = _ToolLoopLLM(companies, tool_name="web_search")
        deps, _ = _make_deps(llm=llm, search_fn=_fake_search)

        for src in ("fetch_yc_candidates", "fetch_producthunt_candidates", "fetch_rss_candidates"):
            monkeypatch.setattr(
                f"agent_server.agents.discovery.{src}", lambda d, **kw: []
            )

        result = run_discovery("job-tool", query_hint="SaaS", target=5, deps=deps)

        assert len(search_calls) >= 1
        assert any(c.domain == "foundco.com" for c in result)

    def test_tool_loop_executes_fetch_page(self, monkeypatch):
        """When the LLM emits a fetch_page tool call, fetch_page is called."""
        fetch_calls: list[str] = []

        def _fake_fetch(url: str, render_js: bool = False) -> FetchedPage:
            fetch_calls.append(url)
            return FetchedPage(
                url=url,
                final_url=url,
                title="Page",
                text="startup content",
                ok=True,
                status=200,
            )

        companies = [{"name": "FetchedCo", "domain_or_url": "fetchedco.com"}]
        llm = _ToolLoopLLM(
            companies,
            tool_name="fetch_page",
            tool_input={"url": "https://news.example.com/article"},
        )
        deps, _ = _make_deps(llm=llm, fetch_fn=_fake_fetch)

        for src in ("fetch_yc_candidates", "fetch_producthunt_candidates", "fetch_rss_candidates"):
            monkeypatch.setattr(
                f"agent_server.agents.discovery.{src}", lambda d, **kw: []
            )

        result = run_discovery("job-fetch", query_hint=None, target=5, deps=deps)

        assert "https://news.example.com/article" in fetch_calls
        assert any(c.domain == "fetchedco.com" for c in result)

    def test_never_fetches_linkedin(self, monkeypatch):
        """fetch_page is NEVER called with a LinkedIn URL."""
        fetch_calls: list[str] = []

        def _fake_fetch(url: str, render_js: bool = False) -> FetchedPage:
            fetch_calls.append(url)
            return FetchedPage(
                url=url, final_url=url, title=None, text="", ok=False, status=403
            )

        # LLM tries to fetch a LinkedIn URL
        companies: list[dict] = []
        llm = _ToolLoopLLM(
            companies,
            tool_name="fetch_page",
            tool_input={"url": "https://linkedin.com/company/acme"},
        )
        deps, _ = _make_deps(llm=llm, fetch_fn=_fake_fetch)

        for src in ("fetch_yc_candidates", "fetch_producthunt_candidates", "fetch_rss_candidates"):
            monkeypatch.setattr(
                f"agent_server.agents.discovery.{src}", lambda d, **kw: []
            )

        run_discovery("job-li", query_hint=None, target=5, deps=deps)

        # The real fetch function must NOT have been called with the LinkedIn URL
        linkedin_calls = [u for u in fetch_calls if "linkedin.com" in u]
        assert linkedin_calls == [], f"fetch_page was called with LinkedIn URL: {linkedin_calls}"


class TestRunDiscoveryAudit:
    def test_audit_called_on_start(self, monkeypatch):
        """deps.audit is called at the start of discovery."""
        llm = _ScriptedLLM([])
        deps, audit_log = _make_deps(llm=llm)

        for src in ("fetch_yc_candidates", "fetch_producthunt_candidates", "fetch_rss_candidates"):
            monkeypatch.setattr(
                f"agent_server.agents.discovery.{src}", lambda d, **kw: []
            )

        run_discovery("job-audit", query_hint="fintech", target=5, deps=deps)

        events = [(stage, event) for stage, event, _ in audit_log]
        assert ("discovery", "start") in events

    def test_audit_called_on_done(self, monkeypatch):
        """deps.audit is called when discovery completes."""
        llm = _ScriptedLLM([])
        deps, audit_log = _make_deps(llm=llm)

        for src in ("fetch_yc_candidates", "fetch_producthunt_candidates", "fetch_rss_candidates"):
            monkeypatch.setattr(
                f"agent_server.agents.discovery.{src}", lambda d, **kw: []
            )

        run_discovery("job-audit2", query_hint=None, target=5, deps=deps)

        events = [(stage, event) for stage, event, _ in audit_log]
        assert ("discovery", "done") in events


class TestRunDiscoveryEdgeCases:
    def test_bad_llm_json_is_tolerated(self, monkeypatch):
        """Garbled model output doesn't raise; returns floor candidates."""

        class _GarbageLLM:
            def complete(self, system, messages, *, tools=None):
                return {"text": "not json at all !!!!", "tool_calls": []}

        deps, _ = _make_deps(llm=_GarbageLLM())

        monkeypatch.setattr(
            "agent_server.agents.discovery.fetch_yc_candidates",
            lambda d, **kw: [_make_candidate("FloorCo", "floorco.com", "yc_oss")],
        )
        monkeypatch.setattr(
            "agent_server.agents.discovery.fetch_producthunt_candidates",
            lambda d, **kw: [],
        )
        monkeypatch.setattr(
            "agent_server.agents.discovery.fetch_rss_candidates",
            lambda d, **kw: [],
        )

        result = run_discovery("job-garbage", query_hint=None, target=5, deps=deps)

        assert isinstance(result, list)
        # At minimum floor candidates are there
        assert any(c.domain == "floorco.com" for c in result)

    def test_candidate_with_missing_name_skipped(self, monkeypatch):
        """LLM-emitted items without a name are skipped without crashing."""
        llm = _ScriptedLLM(
            [
                {"domain_or_url": "no-name.com"},  # missing name
                {"name": "HasName", "domain_or_url": "hasname.com"},
            ]
        )
        deps, _ = _make_deps(llm=llm)

        for src in ("fetch_yc_candidates", "fetch_producthunt_candidates", "fetch_rss_candidates"):
            monkeypatch.setattr(
                f"agent_server.agents.discovery.{src}", lambda d, **kw: []
            )

        result = run_discovery("job-noname", query_hint=None, target=5, deps=deps)

        domains = {c.domain for c in result}
        assert "hasname.com" in domains
        assert "no-name.com" not in domains

    def test_candidate_with_bad_domain_skipped(self, monkeypatch):
        """LLM-emitted items whose domain normalises to None are silently dropped."""
        llm = _ScriptedLLM(
            [
                {"name": "LI Corp", "domain_or_url": "https://linkedin.com/company/x"},
                {"name": "Good Corp", "domain_or_url": "goodcorp.com"},
            ]
        )
        deps, _ = _make_deps(llm=llm)

        for src in ("fetch_yc_candidates", "fetch_producthunt_candidates", "fetch_rss_candidates"):
            monkeypatch.setattr(
                f"agent_server.agents.discovery.{src}", lambda d, **kw: []
            )

        result = run_discovery("job-bad-domain", query_hint=None, target=5, deps=deps)

        domains = {c.domain for c in result}
        assert "linkedin.com" not in domains
        assert "goodcorp.com" in domains


# ---------------------------------------------------------------------------
# Regression: multi-turn tool-use message threading (Anthropic/Bedrock protocol)
#
# The Messages API requires that after an assistant turn containing `tool_use`
# blocks, the next user turn contains a `tool_result` for EVERY tool_use id.
# A real e2e run against Bedrock surfaced two ways the loop violated this:
#   1. breaking mid-batch on the tool-call cap (some tool_use got no result);
#   2. (research) appending the assistant turn as plain text, dropping the
#      tool_use blocks entirely.
# This protocol-enforcing fake LLM validates the message history on every call
# and would have caught both.
# ---------------------------------------------------------------------------


class _ProtocolEnforcingLLM:
    """Fake LLM that asserts the tool_use/tool_result invariant each turn, then
    emits several tool calls in one batch before a final answer."""

    def __init__(self, companies, n_tool_calls=3):
        self._companies = companies
        self._n = n_tool_calls
        self._turn = 0

    def complete(self, system, messages, *, tools=None):
        # Validate every prior assistant tool_use has a matching tool_result.
        pending_ids: list[str] = []
        for msg in messages:
            content = msg["content"]
            if msg["role"] == "assistant" and isinstance(content, list):
                for block in content:
                    if block.get("type") == "tool_use":
                        pending_ids.append(block["id"])
            elif msg["role"] == "user" and isinstance(content, list):
                for block in content:
                    if block.get("type") == "tool_result":
                        assert block["tool_use_id"] in pending_ids, (
                            f"tool_result for unknown id {block['tool_use_id']}"
                        )
                        pending_ids.remove(block["tool_use_id"])
        assert not pending_ids, f"tool_use ids without tool_result: {pending_ids}"

        self._turn += 1
        if self._turn == 1:
            # Emit a BATCH of tool calls in a single assistant turn.
            return {
                "text": "Searching.",
                "tool_calls": [
                    {"name": "web_search", "input": {"query": f"q{i}"}, "id": f"toolu_{i}"}
                    for i in range(self._n)
                ],
            }
        return {"text": json.dumps(self._companies), "tool_calls": []}


class TestToolUseMessageThreading:
    def test_batched_tool_calls_all_get_results(self, monkeypatch):
        for src in ("fetch_yc_candidates", "fetch_producthunt_candidates", "fetch_rss_candidates"):
            monkeypatch.setattr(f"agent_server.agents.discovery.{src}", lambda d, **kw: [])
        llm = _ProtocolEnforcingLLM([{"name": "Acme", "domain_or_url": "acme.com"}], n_tool_calls=3)
        deps, _ = _make_deps(llm=llm)
        # If threading is wrong, the fake LLM's asserts fire on turn 2.
        result = run_discovery("job-thread", query_hint=None, target=5, deps=deps)
        assert any(c.domain == "acme.com" for c in result)
