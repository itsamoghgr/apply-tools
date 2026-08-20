"""Shared adapter types and HTTP helper.

Every adapter exposes exactly one callable::

    fetch(slug: str) -> list[RawPosting]

One signature for all of them, so the monitor never branches on ATS type.

The four ATS adapters each return a company's ENTIRE board in a single
unauthenticated JSON call, with a real posted-date. That is what makes the
3-hourly cycle cheap and exact: no pagination to walk, no LLM in the loop, no
guessing at what "recent" means. Only `generic` (custom career pages) has to
work for its data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from agent_server.log import get_logger

log = get_logger(__name__)

# Matches web/page_fetch.py: a browser-like UA and generous-but-bounded timeouts.
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _USER_AGENT, "Accept": "application/json"}


@dataclass(frozen=True)
class RawPosting:
    """One posting as returned by a source, before role-matching or dedup.

    `posted_at` is timezone-aware UTC when the source provides a date, else None.
    A missing date is normal and never blocks ingestion — novelty is decided by
    the dedup key against what is already stored, never by this field.
    """

    external_id: str | None
    title: str
    location: str | None
    url: str
    posted_at: datetime | None
    source: str
    # Minimum years of experience stated in the JD, else None ("not stated").
    # Parsed deterministically by jobboard/experience.py from description text
    # the source already returns — no extra request, no LLM.
    min_years: int | None = None


class AdapterError(Exception):
    """Raised when a source is unusable (HTTP error, unparseable body).

    The monitor catches this per-company: one dead board must never abort a cycle.
    """


def get_json(url: str, *, params: dict | None = None) -> object:
    """GET a URL and parse JSON, or raise AdapterError.

    Wraps every httpx/JSON failure mode in one exception type so adapters stay
    free of error-handling noise and the monitor has a single thing to catch.
    """
    try:
        with httpx.Client(timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
            resp = client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise AdapterError(f"request failed: {url}: {exc}") from exc

    if resp.status_code != 200:
        raise AdapterError(f"HTTP {resp.status_code} from {url}")
    try:
        return resp.json()
    except ValueError as exc:
        raise AdapterError(f"invalid JSON from {url}: {exc}") from exc


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp to aware UTC. Returns None on anything odd.

    Handles the trailing 'Z' that fromisoformat rejects before Python 3.11, and
    stamps UTC on naive values rather than assuming local time.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        text = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_epoch_ms(value: object) -> datetime | None:
    """Parse epoch milliseconds (Lever's createdAt) to aware UTC."""
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def clean(value: object) -> str | None:
    """Trim a string field to None when empty/absent."""
    if not isinstance(value, str):
        return None
    return value.strip() or None
