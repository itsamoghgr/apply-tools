"""Adapter registry + ATS detection from a career-page URL.

`detect()` runs ONCE when a company is added to the watchlist; the result is
stored on WatchedCompany.ats/atsSlug so it stays visible and correctable in the
UI instead of being re-guessed every cycle.

Every adapter exposes `fetch(slug) -> list[RawPosting]`, so `fetch_for()` can
dispatch without the monitor ever branching on ATS type.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from agent_server.jobboard.adapters import (
    amazon,
    ashby,
    generic,
    greenhouse,
    lever,
    smartrecruiters,
)
from agent_server.jobboard.adapters.base import AdapterError, RawPosting

__all__ = ["AdapterError", "RawPosting", "detect", "fetch_for", "ADAPTERS"]

ADAPTERS = {
    amazon.SOURCE: amazon.fetch,
    greenhouse.SOURCE: greenhouse.fetch,
    lever.SOURCE: lever.fetch,
    ashby.SOURCE: ashby.fetch,
    smartrecruiters.SOURCE: smartrecruiters.fetch,
    generic.SOURCE: generic.fetch,
}

# host-suffix -> (ats, index of the path segment holding the board slug).
# Ordered most-specific-first; matched against the URL's hostname.
_HOST_RULES: list[tuple[str, str, int]] = [
    ("boards.greenhouse.io", greenhouse.SOURCE, 0),
    ("job-boards.greenhouse.io", greenhouse.SOURCE, 0),
    ("boards.eu.greenhouse.io", greenhouse.SOURCE, 0),
    ("greenhouse.io", greenhouse.SOURCE, 0),
    ("jobs.lever.co", lever.SOURCE, 0),
    ("lever.co", lever.SOURCE, 0),
    ("jobs.ashbyhq.com", ashby.SOURCE, 0),
    ("ashbyhq.com", ashby.SOURCE, 0),
    ("jobs.smartrecruiters.com", smartrecruiters.SOURCE, 0),
    ("careers.smartrecruiters.com", smartrecruiters.SOURCE, 0),
    ("smartrecruiters.com", smartrecruiters.SOURCE, 0),
    # amazon.jobs is a single global board backed by a public search API, so it
    # gets a real adapter instead of the LLM fallback. No slug: the whole site
    # is one board (it also serves AWS, Twitch, Audible, Zoox).
    ("amazon.jobs", amazon.SOURCE, -1),
]

# Some boards are embedded on a company's own domain and only reveal the ATS in
# a query param, e.g. https://acme.com/careers?gh_src=... or an iframe src.
_EMBED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"boards\.greenhouse\.io/([A-Za-z0-9_-]+)"), greenhouse.SOURCE),
    (re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)"), lever.SOURCE),
    (re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)"), ashby.SOURCE),
]


def detect(career_url: str) -> tuple[str, str | None]:
    """Resolve a career-page URL to ``(ats, slug)``.

    Returns ``("generic", None)`` when the URL is not a recognised ATS board —
    the fallback path then scrapes the page itself.

    The slug is the first non-empty path segment, which is where all four ATS
    platforms put the board id (``boards.greenhouse.io/<slug>``,
    ``jobs.lever.co/<slug>``, …). Trailing segments (a specific job id, ``/jobs``)
    are ignored.
    """
    url = (career_url or "").strip()
    if not url:
        return generic.SOURCE, None
    if "://" not in url:
        url = "https://" + url

    try:
        parsed = urlparse(url)
    except ValueError:
        return generic.SOURCE, None

    host = (parsed.hostname or "").lower().removeprefix("www.")
    segments = [s for s in (parsed.path or "").split("/") if s]

    for suffix, ats, seg_index in _HOST_RULES:
        if host == suffix or host.endswith("." + suffix):
            if seg_index < 0:
                return ats, None  # single global board, no per-company slug
            if len(segments) > seg_index:
                return ats, segments[seg_index]
            break  # right host, no slug in the path — fall through to embeds

    # Embedded board: look for a known ATS URL anywhere in the full string.
    for pattern, ats in _EMBED_PATTERNS:
        m = pattern.search(url)
        if m:
            return ats, m.group(1)

    return generic.SOURCE, None


def fetch_for(ats: str | None, slug: str | None, career_url: str) -> list[RawPosting]:
    """Dispatch to the right adapter.

    ATS adapters take the board slug; `generic` takes the career URL itself. A
    company stored as an ATS but missing its slug falls back to generic rather
    than failing, so a half-filled row still gets scraped.
    """
    name = (ats or generic.SOURCE).lower()
    fetch = ADAPTERS.get(name)
    if fetch is None:
        raise AdapterError(f"unknown ats adapter: {ats!r}")
    if name == generic.SOURCE:
        return fetch(career_url)
    if name == amazon.SOURCE:
        return fetch()  # single global board; no slug
    if not slug:
        return generic.fetch(career_url)
    return fetch(slug)
