"""Web page fetch implementation per CONTRACTS.md §2.

Implements `fetch_page(url, *, render_js=False) -> FetchedPage`.

Behavior:
- HARD-REFUSES any linkedin.com host (especially /in/ profiles): returns
  FetchedPage(ok=False) without making any network call.
- Default path: httpx GET with a browser-like User-Agent, follows redirects,
  sane timeout.  Main content extracted via readability-lxml; plain text via
  BeautifulSoup.  Returns ok=True with status, final_url, title, text on
  success; ok=False on any HTTP/network/parse error.  Never raises.
- render_js=True path: Playwright (chromium, headless) loads the page and
  returns HTML, then same readability extraction.  If the browser binary is
  missing (not installed in this env) gracefully falls back to plain httpx and
  logs a warning.  Never used for search.
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
import structlog
from bs4 import BeautifulSoup
from readability import Document

from agent_server.web import FetchedPage

log = structlog.get_logger(__name__)

# httpx client defaults
_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _USER_AGENT}


# ---------------------------------------------------------------------------
# LinkedIn refusal helper
# ---------------------------------------------------------------------------

def _is_linkedin(url: str) -> bool:
    """Return True if the URL host is linkedin.com (any subdomain)."""
    try:
        host = urlparse(url).hostname or ""
        # e.g. www.linkedin.com, linkedin.com, uk.linkedin.com
        return host == "linkedin.com" or host.endswith(".linkedin.com")
    except Exception:  # noqa: BLE001
        return False


def _linkedin_refused(url: str) -> FetchedPage:
    log.info("fetch_linkedin_refused", url=url)
    return FetchedPage(
        url=url,
        final_url=url,
        title=None,
        text="",
        ok=False,
        status=None,
    )


# ---------------------------------------------------------------------------
# Readability + text extraction
# ---------------------------------------------------------------------------

def _extract(html: str, base_url: str) -> tuple[str | None, str]:
    """Run readability on html, return (title, plain_text).

    Falls back to empty strings on any parse failure.
    """
    try:
        doc = Document(html, url=base_url)
        title: str | None = doc.title() or None
        if title:
            title = title.strip() or None

        summary_html = doc.summary(html_partial=True)
        soup = BeautifulSoup(summary_html, "lxml")

        # get_text with separator for whitespace; collapse runs of whitespace.
        raw_text = soup.get_text(separator=" ", strip=True)
        # Collapse multiple spaces/newlines to single space.
        import re
        text = re.sub(r"\s{2,}", " ", raw_text).strip()
        return title, text
    except Exception as exc:  # noqa: BLE001
        log.warning("readability_parse_error", url=base_url, error=str(exc))
        return None, ""


# ---------------------------------------------------------------------------
# Plain httpx fetch
# ---------------------------------------------------------------------------

def _fetch_plain(url: str) -> FetchedPage:
    """Fetch url with httpx, extract main content, return FetchedPage."""
    try:
        with httpx.Client(
            timeout=_TIMEOUT,
            headers=_HEADERS,
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            final_url = str(resp.url)
            status = resp.status_code

            if not resp.is_success:
                log.info(
                    "fetch_http_error",
                    url=url,
                    final_url=final_url,
                    status=status,
                )
                return FetchedPage(
                    url=url,
                    final_url=final_url,
                    title=None,
                    text="",
                    ok=False,
                    status=status,
                )

            html = resp.text
            title, text = _extract(html, final_url)
            log.debug(
                "fetch_ok",
                url=url,
                final_url=final_url,
                status=status,
                text_len=len(text),
            )
            return FetchedPage(
                url=url,
                final_url=final_url,
                title=title,
                text=text,
                ok=True,
                status=status,
            )

    except httpx.HTTPStatusError as exc:
        log.info(
            "fetch_http_status_error",
            url=url,
            status=exc.response.status_code,
            error=str(exc),
        )
        return FetchedPage(
            url=url,
            final_url=url,
            title=None,
            text="",
            ok=False,
            status=exc.response.status_code,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "fetch_network_error",
            url=url,
            error=str(exc),
            exc_type=type(exc).__name__,
        )
        return FetchedPage(
            url=url,
            final_url=url,
            title=None,
            text="",
            ok=False,
            status=None,
        )


# ---------------------------------------------------------------------------
# Playwright fetch (render_js=True)
# ---------------------------------------------------------------------------

def _fetch_playwright(url: str) -> FetchedPage:
    """Fetch url via Playwright headless chromium.

    Falls back to plain httpx if the browser binary is missing.
    """
    try:
        from playwright.sync_api import sync_playwright, Error as PlaywrightError  # noqa: PLC0415

        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as browser_exc:  # noqa: BLE001
                log.warning(
                    "playwright_browser_missing_fallback",
                    url=url,
                    error=str(browser_exc),
                )
                return _fetch_plain(url)

            try:
                page = browser.new_page(
                    user_agent=_USER_AGENT,
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                )
                response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                final_url = page.url
                status = response.status if response else None
                html = page.content()
                browser.close()
            except PlaywrightError as exc:
                log.warning(
                    "playwright_page_error",
                    url=url,
                    error=str(exc),
                )
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
                return FetchedPage(
                    url=url,
                    final_url=url,
                    title=None,
                    text="",
                    ok=False,
                    status=None,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "playwright_unexpected_error",
                    url=url,
                    error=str(exc),
                )
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
                return FetchedPage(
                    url=url,
                    final_url=url,
                    title=None,
                    text="",
                    ok=False,
                    status=None,
                )

            if status is not None and status >= 400:
                log.info(
                    "playwright_http_error",
                    url=url,
                    final_url=final_url,
                    status=status,
                )
                return FetchedPage(
                    url=url,
                    final_url=final_url,
                    title=None,
                    text="",
                    ok=False,
                    status=status,
                )

            title, text = _extract(html, final_url)
            log.debug(
                "playwright_fetch_ok",
                url=url,
                final_url=final_url,
                status=status,
                text_len=len(text),
            )
            return FetchedPage(
                url=url,
                final_url=final_url,
                title=title,
                text=text,
                ok=True,
                status=status,
            )

    except ImportError:
        log.warning(
            "playwright_not_importable_fallback",
            url=url,
        )
        return _fetch_plain(url)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_page(url: str, *, render_js: bool = False) -> FetchedPage:
    """Fetch a page, extract main content, return FetchedPage.

    - Hard-refuses linkedin.com hosts.
    - render_js=True uses Playwright (falls back to httpx if browser missing).
    - Never raises.
    """
    if _is_linkedin(url):
        return _linkedin_refused(url)

    if render_js:
        return _fetch_playwright(url)
    return _fetch_plain(url)
