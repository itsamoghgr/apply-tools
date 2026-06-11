"""FROZEN shared web-tooling interface (see CONTRACTS.md §2).

Built ONCE by the web-tooling agent; both runtime agents import `search` and
`fetch_page` from here. The dataclasses and function signatures below are the
contract. The web-tooling agent fills in the implementations in web/search.py
and web/fetch.py and re-exports them here.

Rules baked into the contract:
- `search` is free + key-less (ddgs), with backoff-and-retry; never JS-rendered;
  returns [] on persistent failure rather than raising.
- `fetch_page` does HTTP GET + readability extraction; headless browser ONLY when
  render_js=True and only for content sites; it HARD-REFUSES linkedin.com/in/
  profile URLs (returns ok=False) as a safety net.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass
class FetchedPage:
    url: str
    final_url: str
    title: str | None
    text: str
    ok: bool
    status: int | None


# Implementations are provided by web/search.py and web/fetch.py and re-exported
# here by the web-tooling agent. Until then these names exist for type-checking
# and imports; calling them raises NotImplementedError.

# Implementation modules are named ddg_search / page_fetch (NOT search / fetch)
# so they never collide with the `search` / `fetch_page` function names exported
# here. A submodule named `search` would shadow the `search` function in this
# package namespace once imported — `from agent_server.web import search` would
# then return a module, breaking `deps.search(...)` with "'module' object is not
# callable". Distinct names make the exports unambiguous.
from agent_server.web.ddg_search import search  # noqa: E402,F401
from agent_server.web.page_fetch import fetch_page  # noqa: E402,F401

__all__ = ["SearchResult", "FetchedPage", "search", "fetch_page"]
