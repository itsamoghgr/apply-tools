"""Funding-announcement RSS connector.

Discovery-agent builder owns this file.

Parses a small built-in list of funding/startup RSS feeds with feedparser.
For each entry, extracts candidate company domains from the entry link and summary
via deps.normalize_domain.  Extraction is best-effort and noisy; downstream
dedup/verify clean up.

Resilient: a failing feed does NOT kill the rest.  All errors are logged as
warnings and the feed is skipped.
"""

from __future__ import annotations

import re
from typing import Any

import feedparser

from agent_server.agents.deps import AgentDeps
from agent_server.contracts.records import CandidateCompany
from agent_server.log import get_logger

log = get_logger(__name__)

# Built-in feed list.  Add more URLs here as the product grows; keep it small so
# the default run stays fast.  All are public, no auth required.
DEFAULT_FEEDS: list[str] = [
    "https://techcrunch.com/category/startups/feed/",
    "https://news.crunchbase.com/feed/",
    "https://vcnewsdaily.com/feed/",
    "https://feeds.feedburner.com/TechCrunch/",  # backup TC feed
]

# Regex to pull bare URLs / domains out of raw HTML/text snippets
_URL_RE = re.compile(
    r"https?://(?:www\.)?([a-zA-Z0-9\-]+(?:\.[a-zA-Z0-9\-]+)+)"
    r"(?:/[^\s\"'<>]*)?"
)


def _extract_domains_from_text(text: str, deps: AgentDeps) -> list[str]:
    """Best-effort: find all URLs in *text* and normalise them."""
    domains: list[str] = []
    for match in _URL_RE.finditer(text or ""):
        full_url = match.group(0)
        domain = deps.normalize_domain(full_url)
        if domain and domain not in domains:
            domains.append(domain)
    return domains


def _entry_to_name(entry: Any) -> str:
    """Derive a human-readable company name from a feed entry (best-effort)."""
    title: str = getattr(entry, "title", "") or ""
    # Many TC/Crunchbase headlines look like "Acme raises $5M …"; grab first word(s).
    # Just return the title as the name — downstream research refines it.
    return title.strip() or "unknown"


def fetch_rss_candidates(
    deps: AgentDeps,
    *,
    feeds: list[str] | None = None,
    limit: int = 100,
) -> list[CandidateCompany]:
    """Return up to *limit* CandidateCompany records scraped from RSS feeds.

    Args:
        deps:   Injected agent dependencies.
        feeds:  Override the built-in feed list (useful in tests).
        limit:  Maximum number of candidates to return across all feeds.
    """
    feed_urls = feeds if feeds is not None else DEFAULT_FEEDS
    candidates: list[CandidateCompany] = []
    seen_domains: set[str] = set()

    for feed_url in feed_urls:
        if len(candidates) >= limit:
            break
        try:
            parsed = feedparser.parse(feed_url)
            entries = parsed.get("entries") or []
        except Exception as exc:
            log.warning("rss_feed_error", feed=feed_url, error=str(exc))
            continue

        if not entries:
            log.warning("rss_feed_empty", feed=feed_url)
            continue

        for entry in entries:
            if len(candidates) >= limit:
                break
            try:
                # Collect text to mine for URLs.
                link: str = getattr(entry, "link", "") or ""
                summary: str = getattr(entry, "summary", "") or ""
                title: str = getattr(entry, "title", "") or ""

                raw_text = f"{link} {summary} {title}"
                domains = _extract_domains_from_text(raw_text, deps)

                for domain in domains:
                    if domain in seen_domains:
                        continue
                    seen_domains.add(domain)
                    candidates.append(
                        CandidateCompany(
                            name=_entry_to_name(entry),
                            domain=domain,
                            source="rss",
                            source_url=link or None,
                            description=title or None,
                        )
                    )
                    if len(candidates) >= limit:
                        break
            except Exception as exc:
                log.warning("rss_entry_error", feed=feed_url, error=str(exc))
                continue

        log.info("rss_feed_done", feed=feed_url, total_so_far=len(candidates))

    log.info("rss_candidates_fetched", count=len(candidates))
    return candidates
