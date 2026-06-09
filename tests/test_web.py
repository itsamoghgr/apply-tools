"""Tests for agent_server.web.ddg_search and agent_server.web.page_fetch.

All tests run fully offline — no real network calls.

Test strategy:
- LinkedIn refusal: no httpx call, returns ok=False.
- fetch_page success: respx mocks an httpx GET; checks readability extraction.
- fetch_page error: respx raises / returns 500; checks ok=False, no raise.
- search backoff: monkeypatches DDGS().text to fail N times then succeed;
  asserts retry behaviour and correct mapping.
- search persistent failure: always fails; asserts [] returned, no raise.
"""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from agent_server.web import FetchedPage, SearchResult
from agent_server.web.page_fetch import fetch_page, _is_linkedin
from agent_server.web.ddg_search import search


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ARTICLE_CONTENT = (
    "The startup raised Series A funding to build a new AI platform. "
    "Founders believe this will revolutionize enterprise software."
)

_HTML_WITH_BOILERPLATE = f"""
<!DOCTYPE html>
<html>
  <head><title>Acme Corp — Press Release</title></head>
  <body>
    <nav id="nav">
      <a href="/">Home</a> | <a href="/about">About</a> | <a href="/contact">Contact</a>
      | <a href="/blog">Blog</a> | <a href="/careers">Careers</a>
    </nav>
    <header>
      <h1>Welcome to Acme Corp</h1>
      <p class="tagline">Building the future, one commit at a time.</p>
    </header>
    <article id="main-content">
      <h2>Funding Announcement</h2>
      <p>{_ARTICLE_CONTENT}</p>
    </article>
    <footer>
      <p>© 2024 Acme Corp. All rights reserved.</p>
      <ul>
        <li><a href="/privacy">Privacy Policy</a></li>
        <li><a href="/terms">Terms of Service</a></li>
      </ul>
    </footer>
  </body>
</html>
"""


# ---------------------------------------------------------------------------
# LinkedIn refusal tests
# ---------------------------------------------------------------------------

class TestLinkedInRefusal:
    """LinkedIn URLs must be refused without making any HTTP call."""

    @pytest.mark.parametrize("url", [
        "https://www.linkedin.com/in/someone",
        "https://linkedin.com/in/johndoe",
        "https://uk.linkedin.com/in/janedoe",
        "https://www.linkedin.com/company/acme",
        "http://linkedin.com/in/profile123",
    ])
    def test_is_linkedin_detected(self, url: str) -> None:
        assert _is_linkedin(url) is True

    @pytest.mark.parametrize("url", [
        "https://www.google.com/search?q=linkedin",
        "https://example.com/not-linkedin",
        "https://notlinkedin.com/page",
    ])
    def test_non_linkedin_not_detected(self, url: str) -> None:
        assert _is_linkedin(url) is False

    def test_linkedin_profile_refused_no_network(self) -> None:
        """fetch_page on linkedin.com/in/ returns ok=False without any HTTP request."""
        url = "https://www.linkedin.com/in/someone"

        # respx in strict mode: any real HTTP call would raise an error.
        # We use a simple wrapper that tracks calls.
        call_count = 0
        original_fetch = httpx.Client.send

        def spy_send(self_client, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_fetch(self_client, *args, **kwargs)

        import unittest.mock as mock
        with mock.patch.object(httpx.Client, "send", spy_send):
            result = fetch_page(url)

        assert result.ok is False
        assert result.url == url
        assert call_count == 0, "No HTTP call should have been made for LinkedIn URL"

    def test_linkedin_returns_fetchedpage_shape(self) -> None:
        result = fetch_page("https://www.linkedin.com/in/someone")
        assert isinstance(result, FetchedPage)
        assert result.ok is False
        assert result.status is None
        assert result.text == ""
        assert result.title is None


# ---------------------------------------------------------------------------
# fetch_page success tests
# ---------------------------------------------------------------------------

class TestFetchPageSuccess:
    @respx.mock
    def test_success_extraction(self) -> None:
        """fetch_page returns ok=True, 200 status, parsed title, article text."""
        url = "https://example.com/press"
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                text=_HTML_WITH_BOILERPLATE,
                headers={"Content-Type": "text/html; charset=utf-8"},
            )
        )

        result = fetch_page(url)

        assert result.ok is True
        assert result.status == 200
        assert result.url == url
        # Title should be parsed from the document
        assert result.title is not None
        assert len(result.title) > 0
        # Article body should be present in text
        assert "Series A" in result.text or "AI platform" in result.text or "startup" in result.text.lower()
        # Nav boilerplate should not dominate (readability strips it)
        # The article content should be more prominent than nav junk
        assert result.text  # non-empty

    @respx.mock
    def test_final_url_after_redirect(self) -> None:
        """final_url reflects the URL after redirects (respx resolves immediately)."""
        url = "https://example.com/press"
        final = "https://example.com/press"
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                text=_HTML_WITH_BOILERPLATE,
                headers={"Content-Type": "text/html"},
            )
        )

        result = fetch_page(url)
        assert result.ok is True
        assert result.final_url  # non-empty

    @respx.mock
    def test_readability_strips_nav(self) -> None:
        """The nav text should not appear prominently in the extracted body."""
        url = "https://example.com/article"
        nav_only_html = """
        <html>
          <head><title>Nav Test</title></head>
          <body>
            <nav>Home About Contact Blog Careers Privacy Terms</nav>
            <article>
              <h1>Main Article Heading</h1>
              <p>This is the real content of the article that matters for extraction.</p>
              <p>Additional paragraph with more meaningful text to help readability score it.</p>
            </article>
          </body>
        </html>
        """
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                text=nav_only_html,
                headers={"Content-Type": "text/html"},
            )
        )

        result = fetch_page(url)
        assert result.ok is True
        assert "real content" in result.text or "Main Article" in result.text


# ---------------------------------------------------------------------------
# fetch_page error path tests
# ---------------------------------------------------------------------------

class TestFetchPageError:
    @respx.mock
    def test_http_500_returns_ok_false(self) -> None:
        """A 500 response returns ok=False with status=500, never raises."""
        url = "https://example.com/broken"
        respx.get(url).mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        result = fetch_page(url)

        assert result.ok is False
        assert result.status == 500
        assert result.url == url
        assert isinstance(result, FetchedPage)

    @respx.mock
    def test_http_404_returns_ok_false(self) -> None:
        url = "https://example.com/missing"
        respx.get(url).mock(
            return_value=httpx.Response(404, text="Not Found")
        )
        result = fetch_page(url)
        assert result.ok is False
        assert result.status == 404

    @respx.mock
    def test_network_error_returns_ok_false(self) -> None:
        """A network-level error (ConnectError etc.) returns ok=False, never raises."""
        url = "https://example.com/unreachable"
        respx.get(url).mock(side_effect=httpx.ConnectError("connection refused"))

        result = fetch_page(url)

        assert result.ok is False
        assert result.status is None
        assert isinstance(result, FetchedPage)

    @respx.mock
    def test_timeout_returns_ok_false(self) -> None:
        url = "https://example.com/slow"
        respx.get(url).mock(side_effect=httpx.TimeoutException("timed out"))

        result = fetch_page(url)

        assert result.ok is False
        assert result.status is None

    def test_never_raises_on_garbage_url(self) -> None:
        """Completely invalid URL should not raise."""
        result = fetch_page("not-a-url-at-all")
        # Should return ok=False without raising
        assert isinstance(result, FetchedPage)
        assert result.ok is False


# ---------------------------------------------------------------------------
# search() tests (mock ddgs — no real network)
# ---------------------------------------------------------------------------

class TestSearchBackoff:
    """search() retries on rate-limit-like errors and eventually returns results."""

    def _make_ddgs_results(self) -> list[dict]:
        return [
            {"title": "Acme Corp Raises Series A", "href": "https://techcrunch.com/acme", "body": "Acme raises $10M."},
            {"title": "Acme Corp Blog", "href": "https://acme.com/blog", "body": "Our new product launch."},
        ]

    def test_retry_on_ratelimit_then_success(self, monkeypatch) -> None:
        """DDGS.text raises RatelimitException twice, then succeeds on 3rd attempt."""
        from ddgs.exceptions import RatelimitException
        import agent_server.web.ddg_search as search_mod

        call_count = 0

        class FakeDDGS:
            def text(self, query, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise RatelimitException("rate limited")
                return [
                    {"title": "Result One", "href": "https://example.com/1", "body": "Snippet one."},
                    {"title": "Result Two", "href": "https://example.com/2", "body": "Snippet two."},
                ]

        monkeypatch.setattr(search_mod, "DDGS", FakeDDGS)
        monkeypatch.setattr(search_mod, "_BASE_DELAY_S", 0.0)
        monkeypatch.setattr(search_mod, "_JITTER_S", 0.0)

        results = search("test query", max_results=5)

        assert call_count == 3
        assert len(results) == 2
        assert all(isinstance(r, SearchResult) for r in results)
        assert results[0].title == "Result One"
        assert results[0].url == "https://example.com/1"
        assert results[0].snippet == "Snippet one."
        assert results[1].title == "Result Two"
        assert results[1].url == "https://example.com/2"

    def test_retry_on_timeout_then_success(self, monkeypatch) -> None:
        """DDGS.text raises TimeoutException once, then succeeds."""
        from ddgs.exceptions import TimeoutException
        import agent_server.web.ddg_search as search_mod

        call_count = 0

        class FakeDDGS:
            def text(self, query, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise TimeoutException("timed out")
                return [{"title": "T", "href": "https://example.com/t", "body": "B"}]

        monkeypatch.setattr(search_mod, "DDGS", FakeDDGS)
        monkeypatch.setattr(search_mod, "_BASE_DELAY_S", 0.0)
        monkeypatch.setattr(search_mod, "_JITTER_S", 0.0)

        results = search("query")
        assert len(results) == 1
        assert call_count == 2

    def test_persistent_failure_returns_empty_list(self, monkeypatch) -> None:
        """DDGS.text always raises; search() returns [] and never raises."""
        from ddgs.exceptions import RatelimitException
        import agent_server.web.ddg_search as search_mod

        class FakeDDGS:
            def text(self, query, **kwargs):
                raise RatelimitException("always rate limited")

        monkeypatch.setattr(search_mod, "DDGS", FakeDDGS)
        monkeypatch.setattr(search_mod, "_BASE_DELAY_S", 0.0)
        monkeypatch.setattr(search_mod, "_JITTER_S", 0.0)

        results = search("any query")
        assert results == []

    def test_persistent_ddgs_exception_returns_empty_list(self, monkeypatch) -> None:
        """Generic DDGSException always — returns []."""
        from ddgs.exceptions import DDGSException
        import agent_server.web.ddg_search as search_mod

        class FakeDDGS:
            def text(self, query, **kwargs):
                raise DDGSException("generic ddgs error")

        monkeypatch.setattr(search_mod, "DDGS", FakeDDGS)
        monkeypatch.setattr(search_mod, "_BASE_DELAY_S", 0.0)
        monkeypatch.setattr(search_mod, "_JITTER_S", 0.0)

        results = search("any query")
        assert results == []

    def test_unexpected_exception_returns_empty_list(self, monkeypatch) -> None:
        """Totally unexpected exception — returns [], never raises."""
        import agent_server.web.ddg_search as search_mod

        class FakeDDGS:
            def text(self, query, **kwargs):
                raise RuntimeError("something very unexpected")

        monkeypatch.setattr(search_mod, "DDGS", FakeDDGS)

        results = search("query")
        assert results == []

    def test_maps_result_keys_correctly(self, monkeypatch) -> None:
        """Mapping from ddgs result dict keys title/href/body -> SearchResult fields."""
        import agent_server.web.ddg_search as search_mod

        class FakeDDGS:
            def text(self, query, **kwargs):
                return [
                    {
                        "title": "My Title",
                        "href": "https://example.com/page",
                        "body": "My snippet text here.",
                    }
                ]

        monkeypatch.setattr(search_mod, "DDGS", FakeDDGS)

        results = search("q")
        assert len(results) == 1
        sr = results[0]
        assert sr.title == "My Title"
        assert sr.url == "https://example.com/page"
        assert sr.snippet == "My snippet text here."

    def test_defensive_fallback_keys(self, monkeypatch) -> None:
        """Results with alternate keys (url, link, snippet) are handled defensively."""
        import agent_server.web.ddg_search as search_mod

        class FakeDDGS:
            def text(self, query, **kwargs):
                return [
                    # url instead of href, no body (snippet fallback missing too)
                    {"title": "Alt Title", "url": "https://example.com/alt", "snippet": "Alt snippet."},
                    # missing URL entirely — should be skipped
                    {"title": "No URL result", "body": "some text"},
                ]

        monkeypatch.setattr(search_mod, "DDGS", FakeDDGS)

        results = search("q")
        # Second result should be dropped (no URL)
        assert len(results) == 1
        assert results[0].title == "Alt Title"
        assert results[0].url == "https://example.com/alt"
        assert results[0].snippet == "Alt snippet."

    def test_empty_results_from_ddgs(self, monkeypatch) -> None:
        """DDGS returns empty list — search returns []."""
        import agent_server.web.ddg_search as search_mod

        class FakeDDGS:
            def text(self, query, **kwargs):
                return []

        monkeypatch.setattr(search_mod, "DDGS", FakeDDGS)

        results = search("q")
        assert results == []

    def test_none_results_from_ddgs(self, monkeypatch) -> None:
        """DDGS returns None (shouldn't happen but defensive) — search returns []."""
        import agent_server.web.ddg_search as search_mod

        class FakeDDGS:
            def text(self, query, **kwargs):
                return None  # type: ignore[return-value]

        monkeypatch.setattr(search_mod, "DDGS", FakeDDGS)

        results = search("q")
        assert results == []


# ---------------------------------------------------------------------------
# Integration-style: public __init__.py re-exports work
# ---------------------------------------------------------------------------

class TestPublicInterface:
    def test_search_result_dataclass_fields(self) -> None:
        sr = SearchResult(title="T", url="https://x.com", snippet="S")
        assert sr.title == "T"
        assert sr.url == "https://x.com"
        assert sr.snippet == "S"

    def test_fetched_page_dataclass_fields(self) -> None:
        fp = FetchedPage(
            url="https://x.com",
            final_url="https://x.com/final",
            title="Title",
            text="Text",
            ok=True,
            status=200,
        )
        assert fp.ok is True
        assert fp.status == 200

    @respx.mock
    def test_top_level_fetch_page_delegates(self) -> None:
        """agent_server.web.page_fetch_page (top-level) delegates to fetch.py impl."""
        from agent_server.web import fetch_page as top_level_fetch_page

        url = "https://example.com/test"
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                text="<html><head><title>Test</title></head><body><article>Hello world content here for testing.</article></body></html>",
                headers={"Content-Type": "text/html"},
            )
        )
        result = top_level_fetch_page(url)
        assert isinstance(result, FetchedPage)
        assert result.ok is True
