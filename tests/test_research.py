"""Offline tests for agent_server.agents.research.run_research.

All dependencies (llm, search, fetch_page, audit, normalize_domain) are
replaced with simple fakes — zero network/LLM calls.

Key assertions:
  - shortcut path:  candidate WITH funding_stage/amount sets used_shortcut=True
    and does NOT invoke the LLM for funding derivation (no llm.complete call
    in the shortcut case, OR the funding is always the candidate's values).
  - non-shortcut:   LLM output populates funding + founder in ResearchResult.
  - LinkedIn URL:   extracted from search snippet; fetch_page is NEVER called
    with a linkedin URL.
  - failure path:   llm raises → run_research returns a ResearchResult (no
    exception), carrying candidate domain/name (+ structured funding if present).
  - domain/name:    always passed through verbatim from the candidate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_server.agents.deps import AgentDeps
from agent_server.agents.research import run_research
from agent_server.contracts.records import CandidateCompany, ResearchResult
from agent_server.web import FetchedPage, SearchResult


# ─────────────────────────────────────────────────────────────────────────────
# Fake helpers
# ─────────────────────────────────────────────────────────────────────────────

class FakeLLM:
    """Scriptable fake LLM.

    `responses` is a list; each call pops from the front.  If the list is
    exhausted it returns an empty final answer.
    """

    def __init__(self, responses: list[dict] | None = None):
        self.responses = list(responses or [])
        self.calls: list[dict] = []   # record of every complete() invocation

    def complete(
        self,
        system: str,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
    ) -> dict:
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        if self.responses:
            return self.responses.pop(0)
        # Default: empty final answer
        return {"text": '{"funding_stage": null, "funding_amount": null, "founder_name": null, "founder_linkedin_url": null, "sources": []}', "tool_calls": []}


class FakeLLMRaises:
    """Fake LLM that always raises."""

    def complete(self, system, messages, *, tools=None):
        raise RuntimeError("LLM unavailable in test")


# Audit collector
def _make_audit():
    events: list[dict] = []

    def audit(stage: str, event: str, data: dict) -> None:
        events.append({"stage": stage, "event": event, "data": data})

    audit.events = events  # type: ignore[attr-defined]
    return audit


def _make_fake_fetch(pages: dict[str, FetchedPage] | None = None):
    """Returns a fake fetch_page callable that records calls."""
    pages = pages or {}
    calls: list[str] = []

    def fetch_page(url: str, *, render_js: bool = False) -> FetchedPage:
        calls.append(url)
        if url in pages:
            return pages[url]
        return FetchedPage(
            url=url, final_url=url, title="Test page", text="Some content.", ok=True, status=200
        )

    fetch_page.calls = calls  # type: ignore[attr-defined]
    return fetch_page


def _make_fake_search(results_map: dict[str, list[SearchResult]] | None = None):
    """Returns a fake search callable."""
    results_map = results_map or {}
    calls: list[str] = []

    def search(query: str, *, max_results: int = 10) -> list[SearchResult]:
        calls.append(query)
        # Match by substring of query
        for key, results in results_map.items():
            if key.lower() in query.lower():
                return results[:max_results]
        return []

    search.calls = calls  # type: ignore[attr-defined]
    return search


def _make_candidate(
    name: str = "Acme Corp",
    domain: str = "acme.com",
    funding_stage: str | None = None,
    funding_amount: str | None = None,
    source: str = "open_web",
) -> CandidateCompany:
    return CandidateCompany(
        name=name,
        domain=domain,
        source=source,
        funding_stage=funding_stage,
        funding_amount=funding_amount,
        discovered_at=datetime.now(timezone.utc),
    )


def _make_deps(
    llm,
    search_fn=None,
    fetch_fn=None,
    audit_fn=None,
) -> AgentDeps:
    return AgentDeps(
        search=search_fn or _make_fake_search(),
        fetch_page=fetch_fn or _make_fake_fetch(),
        llm=llm,
        audit=audit_fn or _make_audit(),
        normalize_domain=lambda x: x,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Domain / name pass-through
# ─────────────────────────────────────────────────────────────────────────────

class TestDomainNamePassthrough:
    def test_domain_and_name_always_present(self):
        """ResearchResult always carries domain and name from the candidate."""
        candidate = _make_candidate(name="Widgets Inc", domain="widgets.io")
        llm = FakeLLM(
            responses=[
                {
                    "text": json.dumps(
                        {
                            "funding_stage": "Series A",
                            "funding_amount": "$5M",
                            "founder_name": "Alice",
                            "founder_linkedin_url": None,
                            "sources": [],
                        }
                    ),
                    "tool_calls": [],
                }
            ]
        )
        result = run_research("job-1", candidate, _make_deps(llm))
        assert result.domain == "widgets.io"
        assert result.name == "Widgets Inc"

    def test_domain_and_name_on_llm_failure(self):
        """Even when the LLM raises, domain and name are returned."""
        candidate = _make_candidate(name="Fail Co", domain="fail.co")
        result = run_research("job-2", candidate, _make_deps(FakeLLMRaises()))
        assert result.domain == "fail.co"
        assert result.name == "Fail Co"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Shortcut path
# ─────────────────────────────────────────────────────────────────────────────

class TestShortcutPath:
    """Candidate already has structured funding — skip LLM funding derivation."""

    def test_shortcut_sets_used_shortcut_true(self):
        candidate = _make_candidate(funding_stage="Series A", funding_amount="$10M")
        # Shortcut path still calls LLM for founder, but funding is carried verbatim.
        llm = FakeLLM(
            responses=[
                {
                    "text": json.dumps(
                        {"founder_name": "Bob Smith", "founder_linkedin_url": None, "sources": []}
                    ),
                    "tool_calls": [],
                }
            ]
        )
        result = run_research("job-3", candidate, _make_deps(llm))
        assert result.used_shortcut is True

    def test_shortcut_carries_funding_stage_verbatim(self):
        candidate = _make_candidate(funding_stage="Series B", funding_amount="$50M")
        llm = FakeLLM(
            responses=[
                {
                    "text": json.dumps(
                        {"founder_name": None, "founder_linkedin_url": None, "sources": []}
                    ),
                    "tool_calls": [],
                }
            ]
        )
        result = run_research("job-4", candidate, _make_deps(llm))
        assert result.funding_stage == "Series B"
        assert result.funding_amount == "50M" or result.funding_amount == "$50M"

    def test_shortcut_funding_stage_only(self):
        """funding_stage present without amount still triggers shortcut."""
        candidate = _make_candidate(funding_stage="Seed", funding_amount=None)
        llm = FakeLLM(
            responses=[
                {
                    "text": json.dumps({"founder_name": None, "founder_linkedin_url": None, "sources": []}),
                    "tool_calls": [],
                }
            ]
        )
        result = run_research("job-5", candidate, _make_deps(llm))
        assert result.used_shortcut is True
        assert result.funding_stage == "Seed"
        assert result.funding_amount is None

    def test_shortcut_funding_amount_only(self):
        """funding_amount present without stage still triggers shortcut."""
        candidate = _make_candidate(funding_stage=None, funding_amount="$2M")
        llm = FakeLLM(
            responses=[
                {
                    "text": json.dumps({"founder_name": None, "founder_linkedin_url": None, "sources": []}),
                    "tool_calls": [],
                }
            ]
        )
        result = run_research("job-6", candidate, _make_deps(llm))
        assert result.used_shortcut is True

    def test_shortcut_does_not_overwrite_funding_with_llm_output(self):
        """When shortcutting, the LLM's parsed funding fields must NOT overwrite
        the candidate's structured values."""
        candidate = _make_candidate(funding_stage="Series A", funding_amount="$10M")
        # Even if the LLM emits different funding, the shortcut values win.
        llm = FakeLLM(
            responses=[
                {
                    "text": json.dumps(
                        {
                            "funding_stage": "Series C",   # should be ignored
                            "funding_amount": "$200M",     # should be ignored
                            "founder_name": "Carol",
                            "founder_linkedin_url": None,
                            "sources": [],
                        }
                    ),
                    "tool_calls": [],
                }
            ]
        )
        result = run_research("job-7", candidate, _make_deps(llm))
        assert result.funding_stage == "Series A", "shortcut stage must win"
        assert result.funding_amount == "$10M", "shortcut amount must win"

    def test_no_shortcut_when_no_structured_funding(self):
        """Without structured funding, used_shortcut must be False."""
        candidate = _make_candidate(funding_stage=None, funding_amount=None)
        llm = FakeLLM(
            responses=[
                {
                    "text": json.dumps(
                        {
                            "funding_stage": "Pre-Seed",
                            "funding_amount": "$500K",
                            "founder_name": "Dave",
                            "founder_linkedin_url": None,
                            "sources": [],
                        }
                    ),
                    "tool_calls": [],
                }
            ]
        )
        result = run_research("job-8", candidate, _make_deps(llm))
        assert result.used_shortcut is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. Non-shortcut path (full LLM loop)
# ─────────────────────────────────────────────────────────────────────────────

class TestNonShortcutPath:
    """No structured funding — the agent must derive everything via LLM."""

    def test_llm_final_answer_populates_result(self):
        """A single-turn LLM final answer fills all fields."""
        candidate = _make_candidate()
        llm = FakeLLM(
            responses=[
                {
                    "text": json.dumps(
                        {
                            "funding_stage": "Series A",
                            "funding_amount": "$12M",
                            "founder_name": "Eve Johnson",
                            "founder_linkedin_url": None,
                            "sources": ["https://techcrunch.com/acme"],
                        }
                    ),
                    "tool_calls": [],
                }
            ]
        )
        result = run_research("job-9", candidate, _make_deps(llm))
        assert result.funding_stage == "Series A"
        assert result.funding_amount == "$12M"
        assert result.founder_name == "Eve Johnson"
        assert result.used_shortcut is False

    def test_llm_tool_call_then_final_answer(self):
        """LLM first requests a web_search tool call, then emits a final answer."""
        candidate = _make_candidate(name="BetaCo", domain="betaco.com")

        search_fn = _make_fake_search({
            "betaco": [
                SearchResult(
                    title="BetaCo raises $8M",
                    url="https://techcrunch.com/betaco",
                    snippet="BetaCo, led by founder Frank Lee, raised $8M Seed round.",
                )
            ]
        })
        fetch_fn = _make_fake_fetch({
            "https://techcrunch.com/betaco": FetchedPage(
                url="https://techcrunch.com/betaco",
                final_url="https://techcrunch.com/betaco",
                title="BetaCo raises $8M",
                text="BetaCo raised $8M Seed funding. Founder Frank Lee said the company...",
                ok=True,
                status=200,
            )
        })

        llm = FakeLLM(
            responses=[
                # Turn 1: request a web_search
                {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "tc_001",
                            "name": "web_search",
                            "input": {"query": "BetaCo funding founder", "max_results": 5},
                        }
                    ],
                },
                # Turn 2: final answer
                {
                    "text": json.dumps(
                        {
                            "funding_stage": "Seed",
                            "funding_amount": "$8M",
                            "founder_name": "Frank Lee",
                            "founder_linkedin_url": None,
                            "sources": ["https://techcrunch.com/betaco"],
                        }
                    ),
                    "tool_calls": [],
                },
            ]
        )

        result = run_research("job-10", candidate, _make_deps(llm, search_fn, fetch_fn))
        assert result.funding_stage == "Seed"
        assert result.funding_amount == "$8M"
        assert result.founder_name == "Frank Lee"
        assert len(llm.calls) == 2   # two LLM turns

    def test_llm_fetch_tool_call_reads_page(self):
        """LLM requests fetch_page for a non-LinkedIn URL — it is executed."""
        candidate = _make_candidate(name="GammaCo", domain="gammaco.io")

        article_url = "https://news.example.com/gammaco-funding"
        fetch_fn = _make_fake_fetch({
            article_url: FetchedPage(
                url=article_url,
                final_url=article_url,
                title="GammaCo Series B",
                text="GammaCo announced a $30M Series B led by founder Grace.",
                ok=True,
                status=200,
            )
        })

        llm = FakeLLM(
            responses=[
                {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "tc_002",
                            "name": "fetch_page",
                            "input": {"url": article_url, "render_js": False},
                        }
                    ],
                },
                {
                    "text": json.dumps(
                        {
                            "funding_stage": "Series B",
                            "funding_amount": "$30M",
                            "founder_name": "Grace",
                            "founder_linkedin_url": None,
                            "sources": [article_url],
                        }
                    ),
                    "tool_calls": [],
                },
            ]
        )

        result = run_research("job-11", candidate, _make_deps(llm, fetch_fn=fetch_fn))
        assert result.funding_stage == "Series B"
        # The article URL should have been fetched
        assert article_url in fetch_fn.calls


# ─────────────────────────────────────────────────────────────────────────────
# 4. LinkedIn URL from snippets ONLY
# ─────────────────────────────────────────────────────────────────────────────

class TestLinkedInFromSnippets:
    """LinkedIn URL must come from search snippets/URLs — never from fetch_page."""

    def _li_search_fn(self, li_url: str):
        """Fake search that returns a result containing a linkedin URL in the snippet."""
        return _make_fake_search({
            "linkedin": [
                SearchResult(
                    title="Henry Booth | LinkedIn",
                    url=li_url,   # the URL itself is a linkedin.com/in/... URL
                    snippet=f"View Henry Booth's profile on LinkedIn. {li_url}",
                )
            ],
            "funding": [
                SearchResult(
                    title="AcmeCorp raises $5M",
                    url="https://techcrunch.com/acmecorp",
                    snippet="AcmeCorp raised $5M Seed.",
                )
            ],
        })

    def test_linkedin_url_extracted_from_search_snippet(self):
        """LinkedIn URL is extracted from a search result, not fetched."""
        li_url = "https://www.linkedin.com/in/henry-booth"
        candidate = _make_candidate()
        search_fn = self._li_search_fn(li_url)
        fetch_fn = _make_fake_fetch()

        llm = FakeLLM(
            responses=[
                {
                    "text": json.dumps(
                        {
                            "funding_stage": "Seed",
                            "funding_amount": "$5M",
                            "founder_name": "Henry Booth",
                            "founder_linkedin_url": li_url,  # LLM saw it in snippet
                            "sources": ["https://techcrunch.com/acmecorp"],
                        }
                    ),
                    "tool_calls": [],
                }
            ]
        )
        result = run_research("job-12", candidate, _make_deps(llm, search_fn, fetch_fn))
        assert result.founder_linkedin_url == li_url

    def test_fetch_page_never_called_with_linkedin_url(self):
        """The agent must NEVER call fetch_page with a linkedin URL."""
        li_url = "https://www.linkedin.com/in/henry-booth"
        candidate = _make_candidate()
        fetch_fn = _make_fake_fetch()

        # LLM tries to fetch linkedin (bad behaviour) — the guard must block it
        llm = FakeLLM(
            responses=[
                {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "tc_li_01",
                            "name": "fetch_page",
                            "input": {"url": li_url},
                        }
                    ],
                },
                {
                    "text": json.dumps(
                        {
                            "funding_stage": None,
                            "funding_amount": None,
                            "founder_name": "Henry",
                            "founder_linkedin_url": None,
                            "sources": [],
                        }
                    ),
                    "tool_calls": [],
                },
            ]
        )
        run_research("job-13", candidate, _make_deps(llm, fetch_fn=fetch_fn))
        # The real fetch_page must NEVER have been called with the linkedin URL
        assert li_url not in fetch_fn.calls, (
            f"fetch_page was called with a LinkedIn URL: {fetch_fn.calls}"
        )

    def test_linkedin_url_extracted_from_snippet_text(self):
        """LinkedIn URL embedded in a snippet string (not the result URL) is found."""
        li_url = "https://linkedin.com/in/irene-davis"
        candidate = _make_candidate(name="DeltaCo", domain="deltaco.com")

        search_fn = _make_fake_search({
            "deltaco": [
                SearchResult(
                    title="DeltaCo | Irene Davis",
                    url="https://deltaco.com/team",     # NOT a linkedin URL
                    snippet=f"Irene Davis, CEO of DeltaCo. Profile: {li_url}",
                )
            ]
        })
        fetch_fn = _make_fake_fetch()

        # LLM emits no linkedin url, but it's in the snippet
        llm = FakeLLM(
            responses=[
                {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "tc_s01",
                            "name": "web_search",
                            "input": {"query": "DeltaCo funding founder"},
                        }
                    ],
                },
                {
                    "text": json.dumps(
                        {
                            "funding_stage": None,
                            "funding_amount": None,
                            "founder_name": "Irene Davis",
                            "founder_linkedin_url": None,  # LLM missed it
                            "sources": ["https://deltaco.com/team"],
                        }
                    ),
                    "tool_calls": [],
                },
            ]
        )
        result = run_research("job-14", candidate, _make_deps(llm, search_fn, fetch_fn))
        # Agent must still find the LinkedIn URL from the snippet
        assert result.founder_linkedin_url == li_url
        # Confirm fetch_page was NOT called with any linkedin URL
        assert not any("linkedin.com" in c for c in fetch_fn.calls), (
            "fetch_page should never be called with a linkedin URL"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Failure resilience
# ─────────────────────────────────────────────────────────────────────────────

class TestFailureResilience:
    def test_llm_raises_returns_result_not_exception(self):
        """When the LLM raises, run_research returns a ResearchResult, never raises."""
        candidate = _make_candidate(name="Crash Co", domain="crash.co")
        result = run_research("job-15", candidate, _make_deps(FakeLLMRaises()))
        assert isinstance(result, ResearchResult)
        assert result.domain == "crash.co"
        assert result.name == "Crash Co"

    def test_llm_raises_preserves_structured_funding(self):
        """When LLM raises, structured funding from the candidate is preserved."""
        candidate = _make_candidate(
            name="Crash B", domain="crash-b.io",
            funding_stage="Series A", funding_amount="$20M"
        )
        result = run_research("job-16", candidate, _make_deps(FakeLLMRaises()))
        assert isinstance(result, ResearchResult)
        assert result.funding_stage == "Series A"
        assert result.funding_amount == "$20M"
        assert result.used_shortcut is True

    def test_llm_raises_no_structured_funding_partial_result(self):
        """When LLM raises with no structured funding, funding fields are None."""
        candidate = _make_candidate(name="Crash C", domain="crash-c.io")
        result = run_research("job-17", candidate, _make_deps(FakeLLMRaises()))
        assert isinstance(result, ResearchResult)
        assert result.funding_stage is None
        assert result.funding_amount is None

    def test_malformed_json_from_llm_does_not_crash(self):
        """Malformed LLM JSON is handled tolerantly."""
        candidate = _make_candidate()
        llm = FakeLLM(
            responses=[
                {"text": "This is NOT valid JSON at all ```", "tool_calls": []}
            ]
        )
        result = run_research("job-18", candidate, _make_deps(llm))
        assert isinstance(result, ResearchResult)
        assert result.domain == "acme.com"

    def test_partial_json_from_llm(self):
        """Partially valid JSON (missing fields) is handled gracefully."""
        candidate = _make_candidate()
        llm = FakeLLM(
            responses=[
                {
                    "text": json.dumps({"founder_name": "Jane"}),  # missing other fields
                    "tool_calls": [],
                }
            ]
        )
        result = run_research("job-19", candidate, _make_deps(llm))
        assert result.founder_name == "Jane"
        assert result.funding_stage is None

    def test_search_raises_does_not_crash(self):
        """If search raises, the agent recovers without raising."""
        candidate = _make_candidate()

        def bad_search(query, *, max_results=10):
            raise RuntimeError("search failure")

        # Give the LLM a search tool call so it exercises the error path
        llm = FakeLLM(
            responses=[
                {
                    "text": "",
                    "tool_calls": [
                        {"id": "tc_s1", "name": "web_search", "input": {"query": "acme"}}
                    ],
                },
                {
                    "text": json.dumps({"founder_name": None, "founder_linkedin_url": None, "sources": []}),
                    "tool_calls": [],
                },
            ]
        )
        deps = _make_deps(llm, search_fn=bad_search)
        result = run_research("job-20", candidate, deps)
        assert isinstance(result, ResearchResult)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Audit events
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditEvents:
    def test_audit_done_event_emitted(self):
        """At minimum a 'done' audit event is emitted on success."""
        candidate = _make_candidate()
        audit = _make_audit()
        llm = FakeLLM(
            responses=[
                {
                    "text": json.dumps(
                        {"funding_stage": "Seed", "funding_amount": "$1M",
                         "founder_name": "X", "founder_linkedin_url": None, "sources": []}
                    ),
                    "tool_calls": [],
                }
            ]
        )
        run_research("job-21", candidate, _make_deps(llm, audit_fn=audit))
        event_names = [e["event"] for e in audit.events]
        assert "done" in event_names

    def test_audit_shortcut_event_when_shortcutting(self):
        """A 'shortcut_taken' audit event is emitted when the shortcut is used."""
        candidate = _make_candidate(funding_stage="Series A", funding_amount="$10M")
        audit = _make_audit()
        llm = FakeLLM(
            responses=[
                {
                    "text": json.dumps({"founder_name": "Y", "founder_linkedin_url": None, "sources": []}),
                    "tool_calls": [],
                }
            ]
        )
        run_research("job-22", candidate, _make_deps(llm, audit_fn=audit))
        event_names = [e["event"] for e in audit.events]
        assert "shortcut_taken" in event_names

    def test_audit_fatal_error_on_llm_raise(self):
        """A 'fatal_error' audit event is emitted when the LLM raises."""
        candidate = _make_candidate()
        audit = _make_audit()
        run_research("job-23", candidate, _make_deps(FakeLLMRaises(), audit_fn=audit))
        event_names = [e["event"] for e in audit.events]
        assert "fatal_error" in event_names


# ─────────────────────────────────────────────────────────────────────────────
# 7. Sources list
# ─────────────────────────────────────────────────────────────────────────────

class TestSources:
    def test_sources_populated_from_llm_output(self):
        """Sources from the LLM's final JSON are included in ResearchResult.sources."""
        candidate = _make_candidate()
        llm = FakeLLM(
            responses=[
                {
                    "text": json.dumps(
                        {
                            "funding_stage": "Seed",
                            "funding_amount": "$1M",
                            "founder_name": "X",
                            "founder_linkedin_url": None,
                            "sources": ["https://crunchbase.com/acme", "https://acme.com/about"],
                        }
                    ),
                    "tool_calls": [],
                }
            ]
        )
        result = run_research("job-24", candidate, _make_deps(llm))
        for url in ["https://crunchbase.com/acme", "https://acme.com/about"]:
            assert url in result.sources

    def test_sources_deduplicated(self):
        """Duplicate URLs in sources are removed."""
        candidate = _make_candidate()
        dup_url = "https://crunchbase.com/acme"
        llm = FakeLLM(
            responses=[
                {
                    "text": "",
                    "tool_calls": [
                        {"id": "tc_s1", "name": "web_search", "input": {"query": "acme funding"}}
                    ],
                },
                {
                    "text": json.dumps(
                        {
                            "funding_stage": "Seed",
                            "funding_amount": None,
                            "founder_name": None,
                            "founder_linkedin_url": None,
                            "sources": [dup_url, dup_url],
                        }
                    ),
                    "tool_calls": [],
                },
            ]
        )
        search_fn = _make_fake_search(
            {"acme": [SearchResult(title="T", url=dup_url, snippet="S")]}
        )
        result = run_research("job-25", candidate, _make_deps(llm, search_fn))
        # Dedup: should appear only once
        assert result.sources.count(dup_url) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Regression: multi-turn tool-use message threading (Anthropic/Bedrock protocol)
#
# A real Bedrock run failed with "messages.N.content.0: unexpected tool_use"
# because the loop appended the assistant turn as PLAIN TEXT, dropping the
# tool_use blocks, while still sending tool_result blocks. The Messages API
# requires the assistant turn to carry the tool_use blocks and the next user
# turn to carry one tool_result per tool_use id. This protocol-enforcing fake
# LLM validates that invariant on every call.
# ─────────────────────────────────────────────────────────────────────────────


class _ProtocolEnforcingLLM:
    def __init__(self, n_tool_calls: int = 2):
        self._n = n_tool_calls
        self._turn = 0

    def complete(self, system, messages, *, tools=None):
        pending: list[str] = []
        for msg in messages:
            content = msg["content"]
            if msg["role"] == "assistant" and isinstance(content, list):
                for b in content:
                    if b.get("type") == "tool_use":
                        pending.append(b["id"])
            elif msg["role"] == "user" and isinstance(content, list):
                for b in content:
                    if b.get("type") == "tool_result":
                        assert b["tool_use_id"] in pending, (
                            f"tool_result for unknown id {b['tool_use_id']}"
                        )
                        pending.remove(b["tool_use_id"])
        assert not pending, f"tool_use ids without tool_result: {pending}"

        self._turn += 1
        if self._turn == 1:
            return {
                "text": "Looking it up.",
                "tool_calls": [
                    {"name": "web_search", "input": {"query": f"q{i}"}, "id": f"toolu_{i}"}
                    for i in range(self._n)
                ],
            }
        return {
            "text": '{"funding_stage": "Seed", "funding_amount": "$3M", '
            '"founder_name": "Jane Doe", "founder_linkedin_url": null, "sources": []}',
            "tool_calls": [],
        }


class TestToolUseMessageThreading:
    def test_batched_tool_calls_all_get_results(self):
        # No structured funding -> forces the LLM tool loop (no shortcut).
        candidate = _make_candidate(funding_stage=None, funding_amount=None)
        result = run_research("job-thread", candidate, _make_deps(_ProtocolEnforcingLLM(n_tool_calls=2)))
        # If threading were wrong, the fake LLM's asserts fire on turn 2.
        assert result.domain == candidate.domain
        assert result.funding_stage == "Seed"
        assert result.founder_name == "Jane Doe"
