"""Web search implementation using ddgs (free, key-less).

Implements `search(query, *, max_results=10) -> list[SearchResult]` per CONTRACTS.md §2.

Behavior:
- Uses DDGS().text(...) from the `ddgs` library.
- Retries on RatelimitException / TimeoutException / any DDGSException with
  exponential backoff + jitter (up to MAX_ATTEMPTS attempts).
- Returns [] on persistent failure. NEVER raises to the caller.
- Maps ddgs TextResult dicts (keys: title, href, body) to SearchResult.
  Also handles legacy-style dicts (link, snippet, url) defensively.
"""

from __future__ import annotations

import random
import time

import structlog
from ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from agent_server.web import SearchResult

log = structlog.get_logger(__name__)

# Backoff parameters
_MAX_ATTEMPTS = 4
_BASE_DELAY_S = 1.0   # seconds; doubles each retry
_JITTER_S = 0.5       # random [0, jitter) added on top


def _map_result(raw: dict) -> SearchResult | None:
    """Map a ddgs result dict to SearchResult.  Returns None if essential fields missing."""
    # Primary keys from ddgs TextResult: title, href, body
    # Legacy / alternate keys also handled defensively.
    title = (
        raw.get("title")
        or raw.get("name")
        or ""
    )
    url = (
        raw.get("href")
        or raw.get("url")
        or raw.get("link")
        or ""
    )
    snippet = (
        raw.get("body")
        or raw.get("snippet")
        or raw.get("description")
        or ""
    )

    if not url:
        return None  # can't use a result without a URL

    return SearchResult(
        title=str(title),
        url=str(url),
        snippet=str(snippet),
    )


def search(query: str, *, max_results: int = 10) -> list[SearchResult]:
    """Free, key-less web search via ddgs with backoff-retry.

    Returns a (possibly empty) list of SearchResult.  Never raises.
    """
    attempt = 0
    delay = _BASE_DELAY_S

    while attempt < _MAX_ATTEMPTS:
        attempt += 1
        try:
            raw_results: list[dict] = DDGS().text(query, max_results=max_results)
            results: list[SearchResult] = []
            for raw in raw_results or []:
                mapped = _map_result(raw)
                if mapped is not None:
                    results.append(mapped)
            log.debug(
                "search_ok",
                query=query,
                attempt=attempt,
                count=len(results),
            )
            return results

        except (RatelimitException, TimeoutException) as exc:
            if attempt >= _MAX_ATTEMPTS:
                log.warning(
                    "search_failed_persistent",
                    query=query,
                    attempts=attempt,
                    error=str(exc),
                    exc_type=type(exc).__name__,
                )
                return []
            jitter = random.uniform(0, _JITTER_S)
            sleep_for = delay + jitter
            log.info(
                "search_retry",
                query=query,
                attempt=attempt,
                reason=type(exc).__name__,
                sleep_s=round(sleep_for, 2),
            )
            time.sleep(sleep_for)
            delay *= 2

        except DDGSException as exc:
            # Other ddgs errors (e.g. bad response, parse failure) — retry once,
            # then give up.
            if attempt >= _MAX_ATTEMPTS:
                log.warning(
                    "search_failed_persistent",
                    query=query,
                    attempts=attempt,
                    error=str(exc),
                    exc_type=type(exc).__name__,
                )
                return []
            jitter = random.uniform(0, _JITTER_S)
            sleep_for = delay + jitter
            log.info(
                "search_retry",
                query=query,
                attempt=attempt,
                reason=type(exc).__name__,
                sleep_s=round(sleep_for, 2),
            )
            time.sleep(sleep_for)
            delay *= 2

        except Exception as exc:  # noqa: BLE001
            # Totally unexpected error — log and give up immediately (don't retry
            # unknown failures indefinitely).
            log.error(
                "search_unexpected_error",
                query=query,
                attempt=attempt,
                error=str(exc),
                exc_type=type(exc).__name__,
                exc_info=True,
            )
            return []

    # Should not reach here, but be safe.
    return []
