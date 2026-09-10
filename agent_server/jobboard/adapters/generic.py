"""Generic career-page adapter — the fallback for non-ATS boards.

This is the only path that has to work for its data. The four ATS adapters get a
whole board with exact dates in one JSON call; a bespoke career page gives us
HTML, often client-rendered, usually with no posted-date at all.

Strategy, in order:

1. Fetch the page (`web/page_fetch.py`). If the first pass yields almost no
   text, retry with render_js=True — many custom boards are React-rendered.
2. DATE FILTER: if the page exposes a recency control (?posted=today,
   ?date_posted=1, "last 24 hours"), request that variant and use it. This is
   the cheap path and matches the user's "scrape that day only" rule.
3. OTHERWISE PAGINATE up to JOBBOARD_MAX_PAGES (default 10), following
   ?page=N. Stop early when a page repeats the previous page's content or adds
   no new links — a page param many sites simply ignore.
4. Extract postings from the collected text with ONE bounded LLM call, using
   the same client the other agents use.

The LLM runs ONLY here. A watchlist of ATS-hosted companies costs zero LLM calls
per cycle, which is why pointing a company at its real ATS board URL is always
preferable to its marketing /careers page.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from agent_server.config import CONFIG
from agent_server.jobboard.adapters.base import AdapterError, RawPosting, clean, parse_iso
from agent_server.log import get_logger

log = get_logger(__name__)

SOURCE = "generic"

# Below this much extracted text we assume the page is client-rendered and retry
# with a headless browser.
_MIN_TEXT_CHARS = 400
# Hard cap on characters handed to the LLM: ~40k chars keeps one call cheap and
# well inside the context window even after several pages are concatenated.
_MAX_LLM_CHARS = 40_000

# Query params a site might use to filter to recently-posted roles. Tried in
# order; the first that changes the page meaningfully wins.
_DATE_FILTER_PARAMS: list[tuple[str, str]] = [
    ("date_posted", "1"),
    ("posted", "today"),
    ("posted_date", "today"),
    ("days", "1"),
]

_SYSTEM_PROMPT = """\
You extract job postings from the text of a company careers page.

Return ONLY a JSON array — no prose, no markdown fence. Each element:
  {"title": str, "location": str|null, "url": str|null, "posted_at": str|null}

Rules:
- One object per DISTINCT open role actually listed on the page.
- "title" is the role title exactly as written. Never invent or normalise it.
- "url" must be an absolute link to that specific posting when the page shows
  one; otherwise null. Never guess a URL.
- "posted_at" only when the page states a date; ISO-8601. Relative phrases
  ("2 days ago") -> null. Never estimate.
- Ignore navigation, benefits copy, boilerplate, and department headings that
  are not themselves roles.
- If no roles are listed, return [].
"""


def _with_params(url: str, extra: dict[str, str]) -> str:
    """Return `url` with `extra` merged into its query string."""
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    query.update(extra)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _fetch_text(url: str) -> str:
    """Fetch one page's readable text, retrying with JS rendering if it's thin."""
    from agent_server.web import fetch_page  # local import: keeps module import cheap

    page = fetch_page(url)
    if page.ok and len(page.text) >= _MIN_TEXT_CHARS:
        return page.text
    rendered = fetch_page(url, render_js=True)
    if rendered.ok and len(rendered.text) > len(page.text if page.ok else ""):
        return rendered.text
    if page.ok:
        return page.text
    if rendered.ok:
        return rendered.text
    raise AdapterError(f"generic: could not fetch {url}")


def _try_date_filter(url: str) -> str | None:
    """Return page text for a same-day-filtered variant, or None if unsupported.

    A site that ignores an unknown query param returns the identical page — that
    is indistinguishable from "no filter exists", so we only accept the variant
    when it actually differs from the unfiltered page AND still lists something.
    """
    try:
        base = _fetch_text(url)
    except AdapterError:
        return None
    for key, value in _DATE_FILTER_PARAMS:
        try:
            variant = _fetch_text(_with_params(url, {key: value}))
        except AdapterError:
            continue
        if variant and variant != base and len(variant) >= _MIN_TEXT_CHARS:
            log.info("jobboard.generic.date_filter_applied", url=url, param=key)
            return variant
    return None


def _paginate(url: str, max_pages: int) -> str:
    """Walk up to `max_pages` pages, concatenating their text.

    Stops as soon as a page's text repeats one already seen — the common signal
    that the site ignores `?page=` and is serving page 1 over and over.
    """
    chunks: list[str] = []
    seen: set[int] = set()
    for page_num in range(1, max_pages + 1):
        target = url if page_num == 1 else _with_params(url, {"page": str(page_num)})
        try:
            text = _fetch_text(target)
        except AdapterError:
            break
        fingerprint = hash(text)
        if fingerprint in seen:
            log.debug("jobboard.generic.pagination_repeat", url=url, page=page_num)
            break
        seen.add(fingerprint)
        chunks.append(text)
        if len(" ".join(chunks)) >= _MAX_LLM_CHARS:
            break
    if not chunks:
        raise AdapterError(f"generic: no pages fetched for {url}")
    log.info("jobboard.generic.paginated", url=url, pages=len(chunks))
    return "\n\n".join(chunks)


def _extract_json_array(raw: str) -> list:
    """Pull a JSON array out of an LLM reply, tolerating a stray fence/prose."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except ValueError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
        except ValueError:
            return []
    return parsed if isinstance(parsed, list) else []


def fetch(career_url: str, *, llm=None) -> list[RawPosting]:
    """Scrape a custom career page into RawPostings.

    `llm` is injectable so tests can run the whole path without a live model.
    """
    url = (career_url or "").strip()
    if not url:
        raise AdapterError("generic: empty career url")

    # Prefer a same-day view when the site supports one; else walk pages.
    text = None
    if CONFIG.jobboard_today_only:
        text = _try_date_filter(url)
    if text is None:
        text = _paginate(url, CONFIG.jobboard_max_pages)
    text = text[:_MAX_LLM_CHARS]

    if llm is None:
        from agent_server.agents.llm import build_llm  # local: avoids import cost

        llm = build_llm()

    try:
        reply = llm.complete(
            _SYSTEM_PROMPT,
            [{"role": "user", "content": f"Careers page: {url}\n\n{text}"}],
        )
    except Exception as exc:  # noqa: BLE001 — any model failure is an adapter failure
        raise AdapterError(f"generic: LLM extraction failed: {exc}") from exc

    out: list[RawPosting] = []
    for item in _extract_json_array(reply.get("text", "") if isinstance(reply, dict) else ""):
        if not isinstance(item, dict):
            continue
        title = clean(item.get("title"))
        if not title:
            continue
        out.append(
            RawPosting(
                external_id=None,  # custom pages have no stable id; dedup hashes instead
                title=title,
                location=clean(item.get("location")),
                url=clean(item.get("url")) or url,
                posted_at=parse_iso(item.get("posted_at")),
                source=SOURCE,
            )
        )
    log.info("jobboard.generic.extracted", url=url, count=len(out))
    return out
