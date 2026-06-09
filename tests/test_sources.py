"""Unit tests for the three structured-floor source connectors.

All network calls are mocked:
- YC: httpx via respx
- Product Hunt: httpx via respx
- RSS: monkeypatched feedparser.parse

No real API keys or network required.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import respx
import httpx

from agent_server.agents.deps import AgentDeps
from agent_server.agents.sources.yc import fetch_yc_candidates
from agent_server.agents.sources.producthunt import fetch_producthunt_candidates, _PH_API_URL
from agent_server.agents.sources.rss import fetch_rss_candidates
from agent_server.config import CONFIG


# ---------------------------------------------------------------------------
# Shared fake AgentDeps
# ---------------------------------------------------------------------------


def _make_deps(normalize_fn=None) -> AgentDeps:
    """Build a minimal AgentDeps with a realistic normalize_domain stub."""
    if normalize_fn is None:
        def normalize_fn(raw: str) -> str | None:
            # Strip scheme, www, path — return registrable root or None for blocklist
            import re
            blocked = {
                "linkedin.com", "twitter.com", "x.com", "facebook.com",
                "medium.com", "github.com", "youtube.com", "crunchbase.com",
            }
            raw = raw.strip().lower()
            # Remove scheme
            raw = re.sub(r"^https?://", "", raw)
            # Remove www.
            raw = re.sub(r"^www\.", "", raw)
            # Remove path/query
            raw = raw.split("/")[0].split("?")[0].split("#")[0].lower()
            if not raw or "." not in raw:
                return None
            if raw in blocked:
                return None
            return raw

    return AgentDeps(
        search=MagicMock(return_value=[]),
        fetch_page=MagicMock(),
        llm=MagicMock(),
        audit=MagicMock(),
        normalize_domain=normalize_fn,
    )


# ---------------------------------------------------------------------------
# YC source tests
# ---------------------------------------------------------------------------


class TestFetchYcCandidates:
    def test_maps_valid_companies(self):
        """Valid YC companies with websites are mapped to CandidateCompany."""
        fake_json = [
            {
                "name": "Acme Inc",
                "website": "https://www.acme.com",
                "stage": "Seed",
                "one_liner": "Makes widgets",
            },
            {
                "name": "Beta Corp",
                "website": "https://beta.io",
                "one_liner": "Does things",
            },
        ]
        with respx.mock:
            respx.get(CONFIG.yc_oss_url).mock(
                return_value=httpx.Response(200, json=fake_json)
            )
            deps = _make_deps()
            result = fetch_yc_candidates(deps, limit=10)

        assert len(result) == 2
        domains = {c.domain for c in result}
        assert "acme.com" in domains
        assert "beta.io" in domains

        acme = next(c for c in result if c.domain == "acme.com")
        assert acme.name == "Acme Inc"
        assert acme.source == "yc_oss"
        assert acme.funding_stage == "Seed"
        assert acme.description == "Makes widgets"

    def test_skips_missing_website(self):
        """Companies with no website field are skipped."""
        fake_json = [
            {"name": "NoSite", "website": ""},
            {"name": "HasSite", "website": "https://valid.com"},
        ]
        with respx.mock:
            respx.get(CONFIG.yc_oss_url).mock(
                return_value=httpx.Response(200, json=fake_json)
            )
            deps = _make_deps()
            result = fetch_yc_candidates(deps, limit=10)

        assert len(result) == 1
        assert result[0].domain == "valid.com"

    def test_skips_bad_domains(self):
        """Companies whose website normalises to None (e.g. LinkedIn) are skipped."""
        fake_json = [
            {"name": "LI", "website": "https://linkedin.com/company/foo"},
            {"name": "Good", "website": "https://goodcompany.com"},
        ]
        with respx.mock:
            respx.get(CONFIG.yc_oss_url).mock(
                return_value=httpx.Response(200, json=fake_json)
            )
            deps = _make_deps()
            result = fetch_yc_candidates(deps, limit=10)

        assert len(result) == 1
        assert result[0].domain == "goodcompany.com"

    def test_network_error_returns_empty(self):
        """A network error returns [] without raising."""
        with respx.mock:
            respx.get(CONFIG.yc_oss_url).mock(
                side_effect=httpx.ConnectError("timeout")
            )
            deps = _make_deps()
            result = fetch_yc_candidates(deps, limit=10)

        assert result == []

    def test_http_error_returns_empty(self):
        """An HTTP 5xx error returns [] without raising."""
        with respx.mock:
            respx.get(CONFIG.yc_oss_url).mock(
                return_value=httpx.Response(503, text="Service Unavailable")
            )
            deps = _make_deps()
            result = fetch_yc_candidates(deps, limit=10)

        assert result == []

    def test_limit_respected(self):
        """Result count is capped at `limit`."""
        fake_json = [
            {"name": f"Co{i}", "website": f"https://co{i}.com"}
            for i in range(20)
        ]
        with respx.mock:
            respx.get(CONFIG.yc_oss_url).mock(
                return_value=httpx.Response(200, json=fake_json)
            )
            deps = _make_deps()
            result = fetch_yc_candidates(deps, limit=5)

        assert len(result) == 5

    def test_domains_are_normalized(self):
        """Domains are lower-cased and www/scheme stripped."""
        fake_json = [{"name": "X", "website": "HTTPS://WWW.EXAMPLE.COM/path?q=1"}]
        with respx.mock:
            respx.get(CONFIG.yc_oss_url).mock(
                return_value=httpx.Response(200, json=fake_json)
            )
            deps = _make_deps()
            result = fetch_yc_candidates(deps, limit=10)

        assert len(result) == 1
        assert result[0].domain == "example.com"


# ---------------------------------------------------------------------------
# Product Hunt source tests
# ---------------------------------------------------------------------------


class TestFetchProductHuntCandidates:
    def test_returns_empty_when_no_token(self):
        """Returns [] immediately when CONFIG.product_hunt_token is not set."""
        deps = _make_deps()
        import agent_server.agents.sources.producthunt as ph_mod
        import agent_server.config as config_mod

        original_config = ph_mod.CONFIG
        from agent_server.config import Config
        patched = Config.__new__(Config)
        for field in Config.__dataclass_fields__:
            val = None if field == "product_hunt_token" else getattr(original_config, field)
            object.__setattr__(patched, field, val)

        ph_mod.CONFIG = patched
        try:
            result = fetch_producthunt_candidates(deps, limit=10)
        finally:
            ph_mod.CONFIG = original_config

        assert result == []

    def test_maps_posts_to_candidates(self):
        """Valid PH posts are mapped to CandidateCompany records."""
        fake_response = {
            "data": {
                "posts": {
                    "edges": [
                        {
                            "node": {
                                "name": "CoolApp",
                                "tagline": "The best app",
                                "website": "https://coolapp.io",
                            }
                        },
                        {
                            "node": {
                                "name": "Another",
                                "tagline": "Another app",
                                "website": "https://another.com",
                            }
                        },
                    ]
                }
            }
        }
        import agent_server.agents.sources.producthunt as ph_mod
        from agent_server.config import Config

        original_config = ph_mod.CONFIG
        patched = Config.__new__(Config)
        for field in Config.__dataclass_fields__:
            val = "fake-token" if field == "product_hunt_token" else getattr(original_config, field)
            object.__setattr__(patched, field, val)
        ph_mod.CONFIG = patched

        with respx.mock:
            respx.post(_PH_API_URL).mock(
                return_value=httpx.Response(200, json=fake_response)
            )
            deps = _make_deps()
            try:
                result = fetch_producthunt_candidates(deps, limit=10)
            finally:
                ph_mod.CONFIG = original_config

        assert len(result) == 2
        domains = {c.domain for c in result}
        assert "coolapp.io" in domains
        assert "another.com" in domains
        assert all(c.source == "product_hunt" for c in result)

    def test_skips_posts_with_no_website(self):
        """Posts without a website are skipped."""
        fake_response = {
            "data": {
                "posts": {
                    "edges": [
                        {"node": {"name": "NoSite", "tagline": "", "website": ""}},
                        {"node": {"name": "HasSite", "tagline": "", "website": "https://good.com"}},
                    ]
                }
            }
        }
        import agent_server.agents.sources.producthunt as ph_mod
        from agent_server.config import Config

        original_config = ph_mod.CONFIG
        patched = Config.__new__(Config)
        for field in Config.__dataclass_fields__:
            val = "tok" if field == "product_hunt_token" else getattr(original_config, field)
            object.__setattr__(patched, field, val)
        ph_mod.CONFIG = patched

        with respx.mock:
            respx.post(_PH_API_URL).mock(
                return_value=httpx.Response(200, json=fake_response)
            )
            deps = _make_deps()
            try:
                result = fetch_producthunt_candidates(deps, limit=10)
            finally:
                ph_mod.CONFIG = original_config

        assert len(result) == 1
        assert result[0].domain == "good.com"

    def test_network_error_returns_empty(self):
        """Network failure returns [] without raising."""
        import agent_server.agents.sources.producthunt as ph_mod
        from agent_server.config import Config

        original_config = ph_mod.CONFIG
        patched = Config.__new__(Config)
        for field in Config.__dataclass_fields__:
            val = "tok" if field == "product_hunt_token" else getattr(original_config, field)
            object.__setattr__(patched, field, val)
        ph_mod.CONFIG = patched

        with respx.mock:
            respx.post(_PH_API_URL).mock(
                side_effect=httpx.ConnectError("down")
            )
            deps = _make_deps()
            try:
                result = fetch_producthunt_candidates(deps, limit=10)
            finally:
                ph_mod.CONFIG = original_config

        assert result == []


# ---------------------------------------------------------------------------
# RSS source tests
# ---------------------------------------------------------------------------


def _fake_feedparser_result(entries: list[dict]) -> dict:
    """Build a minimal feedparser-like result dict."""
    fake_entries = []
    for e in entries:
        entry = MagicMock()
        entry.title = e.get("title", "")
        entry.link = e.get("link", "")
        entry.summary = e.get("summary", "")
        fake_entries.append(entry)
    return {"entries": fake_entries}


class TestFetchRssCandidates:
    def test_extracts_domain_from_entry_link(self, monkeypatch):
        """Domains from the entry link field are extracted."""
        fake_result = _fake_feedparser_result(
            [
                {
                    "title": "Acme raises $5M",
                    "link": "https://techcrunch.com/2024/acme-raises",
                    "summary": "Acme Inc (acme.com) raised money.",
                }
            ]
        )
        monkeypatch.setattr(
            "agent_server.agents.sources.rss.feedparser.parse",
            lambda url: fake_result,
        )
        deps = _make_deps()
        result = fetch_rss_candidates(
            deps, feeds=["https://fake-feed.com/rss"], limit=10
        )

        # Should have extracted at least one candidate
        assert len(result) >= 1
        # techcrunch.com is not in blocklist in stub normalizer
        domains = {c.domain for c in result}
        assert len(domains) >= 1

    def test_extracts_domain_from_summary(self, monkeypatch):
        """Domains found in the summary text are extracted."""
        fake_result = _fake_feedparser_result(
            [
                {
                    "title": "Startup news",
                    "link": "",
                    "summary": "See https://startup-example.com for details",
                }
            ]
        )
        monkeypatch.setattr(
            "agent_server.agents.sources.rss.feedparser.parse",
            lambda url: fake_result,
        )
        deps = _make_deps()
        result = fetch_rss_candidates(
            deps, feeds=["https://fake.com/rss"], limit=10
        )

        domains = {c.domain for c in result}
        assert "startup-example.com" in domains

    def test_bad_feed_does_not_kill_rest(self, monkeypatch):
        """A feedparser exception on one feed doesn't prevent others from running."""
        call_count = 0

        def selective_parse(url: str):
            nonlocal call_count
            call_count += 1
            if "bad" in url:
                raise RuntimeError("feed broken")
            return _fake_feedparser_result(
                [
                    {
                        "title": "Good startup",
                        "link": "https://goodco.com",
                        "summary": "",
                    }
                ]
            )

        monkeypatch.setattr(
            "agent_server.agents.sources.rss.feedparser.parse",
            selective_parse,
        )
        deps = _make_deps()
        result = fetch_rss_candidates(
            deps,
            feeds=["https://bad-feed.com/rss", "https://good-feed.com/rss"],
            limit=10,
        )

        assert call_count == 2  # Both feeds were attempted
        # The good feed's results are present
        domains = {c.domain for c in result}
        assert "goodco.com" in domains

    def test_empty_feed_returns_no_candidates(self, monkeypatch):
        """A feed with no entries contributes nothing."""
        monkeypatch.setattr(
            "agent_server.agents.sources.rss.feedparser.parse",
            lambda url: {"entries": []},
        )
        deps = _make_deps()
        result = fetch_rss_candidates(
            deps, feeds=["https://empty.com/rss"], limit=10
        )
        assert result == []

    def test_limit_respected(self, monkeypatch):
        """Result count is capped at `limit`."""
        many_entries = [
            {
                "title": f"Company {i}",
                "link": f"https://company{i}.com/article",
                "summary": "",
            }
            for i in range(20)
        ]
        monkeypatch.setattr(
            "agent_server.agents.sources.rss.feedparser.parse",
            lambda url: _fake_feedparser_result(many_entries),
        )
        deps = _make_deps()
        result = fetch_rss_candidates(
            deps, feeds=["https://feed.com/rss"], limit=5
        )
        assert len(result) <= 5

    def test_source_field_is_rss(self, monkeypatch):
        """All extracted candidates have source="rss"."""
        monkeypatch.setattr(
            "agent_server.agents.sources.rss.feedparser.parse",
            lambda url: _fake_feedparser_result(
                [{"title": "T", "link": "https://rsscorp.com", "summary": ""}]
            ),
        )
        deps = _make_deps()
        result = fetch_rss_candidates(
            deps, feeds=["https://feed.com/rss"], limit=10
        )
        assert all(c.source == "rss" for c in result)
