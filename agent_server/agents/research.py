"""Research agent — Phase 3, CONTRACTS.md §8.

Goal: for a single CandidateCompany, find:
  - funding_stage  (+ funding_amount)  — SHORTCUT if candidate already has them
  - founder_name
  - founder_linkedin_url — from PUBLIC SEARCH SNIPPETS ONLY, never fetch_page

Bounded tool-use loop (≤ MAX_TOOL_CALLS) driven by deps.llm with two tools:
  "web_search" and "fetch_page" (Anthropic tool-schema shape).

HARD rules (enforced in code, proved in tests):
  1. NEVER call deps.fetch_page on a linkedin.com URL.
  2. LinkedIn URL must be extracted from SearchResult.url / SearchResult.snippet only.
  3. SHORTCUT: if candidate has funding_stage AND/OR funding_amount, carry them
     through unchanged and set used_shortcut=True; skip LLM-based funding derivation.
  4. RESILIENT: never raise — return a ResearchResult (at min. domain+name) on any error.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent_server.agents.deps import AgentDeps
from agent_server.contracts.records import CandidateCompany, ResearchResult
from agent_server.log import get_logger

logger = get_logger(__name__)

# ── constants ──────────────────────────────────────────────────────────────────
MAX_TOOL_CALLS = 16          # hard cap on LLM-driven tool invocations per run
                             # (thorough mode: founder/funding + 5 company
                             # attributes each may need its own search)
_LINKEDIN_RE = re.compile(r"https?://(?:[\w-]+\.)?linkedin\.com/in/([\w%-]+)", re.I)


# ── Anthropic tool schemas (same shape as discovery) ──────────────────────────
_TOOLS: list[dict] = [
    {
        "name": "web_search",
        "description": (
            "Search the web for information about a company, its funding, and founders. "
            "Returns a list of results with title, url, and snippet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (1-10).",
                    "default": 8,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_page",
        "description": (
            "Fetch and read the text content of a public web page. "
            "Do NOT use this for LinkedIn URLs — those will always fail. "
            "Use for company sites, news articles, crunchbase, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch."},
                "render_js": {
                    "type": "boolean",
                    "description": "Whether to render JavaScript (slow; use only when needed).",
                    "default": False,
                },
            },
            "required": ["url"],
        },
    },
]

# ── system prompt ──────────────────────────────────────────────────────────────
_SYSTEM = """\
You are a thorough company-research agent. Find as much as you can about a startup:
1. Funding stage (e.g. "Seed", "Series A") and funding amount (e.g. "$5M").
2. The founder (CEO or primary founder) and their LinkedIn URL — the LinkedIn URL \
ONLY from search result snippets or URLs (never by fetching a LinkedIn page).
3. Company attributes: employee count, estimated revenue/ARR, HQ location, industry, \
and the date of the most recent funding round.

Work thoroughly: run SEPARATE targeted searches for the attributes you don't yet have, \
e.g. "[company] number of employees", "[company] headquarters location", \
"[company] revenue ARR", "[company] industry", "[company] latest funding round date". \
Read non-LinkedIn pages (news, Crunchbase-style summaries, the company about/team page) \
to extract facts. Prefer recent, credible sources.

When you have enough evidence (or have exhausted reasonable attempts), output a JSON \
object — and ONLY a JSON object — in this exact shape:
{
  "funding_stage": "<string or null>",
  "funding_amount": "<string or null>",
  "founder_name": "<string or null>",
  "founder_linkedin_url": "<full linkedin.com/in/... URL or null>",
  "employee_count": "<string or null>",
  "revenue": "<string or null>",
  "location": "<string or null>",
  "industry": "<string or null>",
  "last_round_date": "<string or null>",
  "sources": ["<url1>", "<url2>"]
}

Rules:
- funding_stage: short label like "Seed", "Series A", "Pre-Seed", "Series B", etc.
- funding_amount: human-readable like "$5M", "$12.5M".  null if not found.
- founder_linkedin_url: MUST be a real linkedin.com/in/<slug> URL seen in a search \
snippet or result URL.  null if not found.
- employee_count: a range or count like "11-50", "~200".  null if not found.
- revenue: like "$10M ARR", "$2M-$5M".  null if not found (common for startups).
- location: HQ city/region, e.g. "San Francisco, CA" or "London, UK".
- industry: short sector tag, e.g. "Developer tools", "Fintech", "Robotics".
- last_round_date: month/year of the most recent round, e.g. "2024-09" or "Sep 2024".
- Use null for anything you cannot find — never guess or fabricate.
- sources: list of URLs you actually read or searched.
- Output ONLY the JSON object — no markdown fences, no extra prose.
"""


# ── helpers ────────────────────────────────────────────────────────────────────

def _is_linkedin_url(url: str) -> bool:
    """Return True if url is a linkedin.com URL (any subdomain)."""
    return bool(re.search(r"(?i)linkedin\.com", url))


def _extract_linkedin_from_snippets(search_results: list[Any]) -> str | None:
    """Scan SearchResult list for a linkedin.com/in/<slug> URL in .url or .snippet."""
    for sr in search_results:
        # Check .url directly
        if _LINKEDIN_RE.search(getattr(sr, "url", "") or ""):
            return _LINKEDIN_RE.search(sr.url).group(0)  # type: ignore[union-attr]
        # Check .snippet text
        m = _LINKEDIN_RE.search(getattr(sr, "snippet", "") or "")
        if m:
            return m.group(0)
    return None


def _parse_llm_json(text: str) -> dict:
    """Tolerantly parse the LLM's final JSON emission from raw text."""
    # Strip markdown fences if present
    cleaned = re.sub(r"^```[a-z]*\n?", "", text.strip(), flags=re.M)
    cleaned = re.sub(r"\n?```$", "", cleaned.strip(), flags=re.M)
    cleaned = cleaned.strip()
    # Find the first JSON object in the text
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        # Try to extract the first {...} block
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                pass
    return {}


# ── main entry point ────────────────────────────────────────────────────────────

def run_research(
    job_id: str,
    candidate: CandidateCompany,
    deps: AgentDeps,
) -> ResearchResult:
    """Research a single candidate company; return a ResearchResult.

    Never raises — returns a partial result on any error.
    """
    log = logger.bind(job_id=job_id, domain=candidate.domain)
    log.info("research_start", name=candidate.name)

    try:
        return _do_research(job_id, candidate, deps, log)
    except Exception as exc:  # noqa: BLE001
        log.exception("research_fatal_error", error=str(exc))
        deps.audit("research", "fatal_error", {"domain": candidate.domain, "error": str(exc)})
        # Resilient fallback — carry whatever we can from the candidate
        return ResearchResult(
            domain=candidate.domain,
            name=candidate.name,
            funding_stage=candidate.funding_stage,
            funding_amount=candidate.funding_amount,
            used_shortcut=bool(candidate.funding_stage or candidate.funding_amount),
        )


def _do_research(
    job_id: str,
    candidate: CandidateCompany,
    deps: AgentDeps,
    log: Any,
) -> ResearchResult:
    """Core research logic — may raise; callers catch."""

    # ── SHORTCUT CHECK ──────────────────────────────────────────────────────
    has_structured_funding = bool(candidate.funding_stage or candidate.funding_amount)
    used_shortcut = has_structured_funding  # funding shortcut — still find founder

    if has_structured_funding:
        log.info(
            "research_shortcut",
            funding_stage=candidate.funding_stage,
            funding_amount=candidate.funding_amount,
        )
        deps.audit(
            "research",
            "shortcut_taken",
            {
                "domain": candidate.domain,
                "funding_stage": candidate.funding_stage,
                "funding_amount": candidate.funding_amount,
            },
        )

    # ── INITIALISE result ───────────────────────────────────────────────────
    result_funding_stage = candidate.funding_stage
    result_funding_amount = candidate.funding_amount
    result_founder_name: str | None = None
    result_founder_linkedin_url: str | None = None
    result_employee_count: str | None = None
    result_revenue: str | None = None
    result_location: str | None = None
    result_industry: str | None = None
    result_last_round_date: str | None = None
    sources: list[str] = []

    # ── TOOL-USE LOOP ───────────────────────────────────────────────────────
    # Build a focused system prompt and conversation history
    system = _SYSTEM
    if has_structured_funding:
        # Tell the LLM we already have funding; focus on founder
        system = (
            "You are a thorough company-research agent. Funding is already known "
            "(do NOT re-derive it). Find the founder AND company attributes.\n\n"
            "1. Founder name (CEO or primary founder).\n"
            "2. Their LinkedIn URL — from search snippets/URLs ONLY; never fetch a "
            "LinkedIn page.\n"
            "3. Company attributes via targeted searches: employee count, revenue/ARR, "
            "HQ location, industry, and the most recent funding round date.\n\n"
            "Use web_search and fetch_page tools as needed. "
            "When done, output ONLY a JSON object:\n"
            '{"founder_name": "<string or null>", "founder_linkedin_url": "<url or null>", '
            '"employee_count": "<string or null>", "revenue": "<string or null>", '
            '"location": "<string or null>", "industry": "<string or null>", '
            '"last_round_date": "<string or null>", "sources": ["<url1>"]}\n\n'
            "Rules: founder_linkedin_url must be a real linkedin.com/in/<slug> URL "
            "seen in a search snippet or URL. Use null for anything not found; never "
            "fabricate. Output ONLY the JSON — no fences, no prose."
        )

    initial_message = {
        "role": "user",
        "content": (
            f"Research the company: {candidate.name} (domain: {candidate.domain})\n"
            + (
                f"Known funding: stage={candidate.funding_stage!r}, "
                f"amount={candidate.funding_amount!r}\n"
                if has_structured_funding
                else ""
            )
            + "Please find the requested information using the available tools."
        ),
    }

    messages: list[dict] = [initial_message]
    tool_calls_made = 0

    # All linkedin URLs seen in search results (for snippet extraction)
    all_search_results: list[Any] = []

    while tool_calls_made < MAX_TOOL_CALLS:
        deps.audit(
            "research",
            "llm_call",
            {"domain": candidate.domain, "turn": tool_calls_made},
        )
        response = deps.llm.complete(system, messages, tools=_TOOLS)
        response_text: str = response.get("text", "") or ""
        tool_calls: list[dict] = response.get("tool_calls", []) or []

        log.debug(
            "research_llm_response",
            text_preview=response_text[:120],
            tool_call_count=len(tool_calls),
        )

        if not tool_calls:
            # LLM produced a final answer — parse the JSON
            parsed = _parse_llm_json(response_text)
            log.info("research_llm_final", parsed=parsed)
            deps.audit(
                "research",
                "llm_final",
                {"domain": candidate.domain, "parsed": parsed},
            )

            if not has_structured_funding:
                result_funding_stage = parsed.get("funding_stage") or result_funding_stage
                result_funding_amount = parsed.get("funding_amount") or result_funding_amount

            result_founder_name = parsed.get("founder_name")

            # LinkedIn from parsed JSON — only accept if looks like a real URL
            candidate_linkedin = parsed.get("founder_linkedin_url")
            if candidate_linkedin and _LINKEDIN_RE.search(candidate_linkedin):
                result_founder_linkedin_url = _LINKEDIN_RE.search(candidate_linkedin).group(0)  # type: ignore[union-attr]

            # Richer company attributes (best-effort; null when not found).
            result_employee_count = parsed.get("employee_count") or result_employee_count
            result_revenue = parsed.get("revenue") or result_revenue
            result_location = parsed.get("location") or result_location
            result_industry = parsed.get("industry") or result_industry
            result_last_round_date = (
                parsed.get("last_round_date") or result_last_round_date
            )

            parsed_sources = parsed.get("sources") or []
            if isinstance(parsed_sources, list):
                sources.extend(str(s) for s in parsed_sources if s)

            break  # done

        # ── Append the assistant turn EXACTLY as the model produced it ─────
        # The assistant message must carry the real tool_use blocks (id/name/
        # input); the following user message must carry a tool_result for EVERY
        # one of them. Appending the assistant turn as plain text (dropping the
        # tool_use blocks), or skipping some results mid-batch, makes the
        # Messages/Bedrock API reject the next request.
        assistant_content: list[dict] = []
        if response_text:
            assistant_content.append({"type": "text", "text": response_text})
        for tc in tool_calls:
            assistant_content.append(
                {
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": tc.get("name", ""),
                    "input": tc.get("input", {}) or {},
                }
            )
        messages.append({"role": "assistant", "content": assistant_content})

        # ── Execute EVERY tool call; one tool_result per tool_use ──────────
        tool_results_content: list[dict] = []
        for tc in tool_calls:
            tool_calls_made += 1
            tool_name: str = tc.get("name", "")
            tool_input: dict = tc.get("input", {}) or {}
            tool_id: str = tc.get("id", f"tool_{tool_calls_made}")

            result_str = _execute_tool(
                tool_name=tool_name,
                tool_input=tool_input,
                deps=deps,
                candidate=candidate,
                all_search_results=all_search_results,
                sources=sources,
                log=log,
            )
            tool_results_content.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result_str,
                }
            )

        messages.append({"role": "user", "content": tool_results_content})

    else:
        # Hit loop cap — ask LLM to summarise what it has
        log.warning("research_tool_cap_reached", domain=candidate.domain, cap=MAX_TOOL_CALLS)
        deps.audit("research", "tool_cap_reached", {"domain": candidate.domain})
        try:
            cap_response = deps.llm.complete(
                system,
                messages + [
                    {
                        "role": "user",
                        "content": (
                            "Output your best JSON answer now based on what you have "
                            "found. Include ALL fields in the schema (funding, founder, "
                            "founder_linkedin_url, employee_count, revenue, location, "
                            "industry, last_round_date) — use null only for what you "
                            "truly couldn't find; fill in any you saw in the results."
                        ),
                    }
                ],
                tools=None,
            )
            parsed = _parse_llm_json(cap_response.get("text", ""))
            if not has_structured_funding:
                result_funding_stage = parsed.get("funding_stage") or result_funding_stage
                result_funding_amount = parsed.get("funding_amount") or result_funding_amount
            result_founder_name = result_founder_name or parsed.get("founder_name")
            candidate_linkedin = parsed.get("founder_linkedin_url")
            if candidate_linkedin and _LINKEDIN_RE.search(candidate_linkedin):
                result_founder_linkedin_url = (
                    result_founder_linkedin_url
                    or _LINKEDIN_RE.search(candidate_linkedin).group(0)  # type: ignore[union-attr]
                )
            result_employee_count = result_employee_count or parsed.get("employee_count")
            result_revenue = result_revenue or parsed.get("revenue")
            result_location = result_location or parsed.get("location")
            result_industry = result_industry or parsed.get("industry")
            result_last_round_date = result_last_round_date or parsed.get("last_round_date")
        except Exception as exc:  # noqa: BLE001
            log.warning("research_cap_summary_failed", error=str(exc))

    # ── Try to extract LinkedIn from all accumulated search results ─────────
    # (This is the canonical source — even if the LLM already found one,
    #  snippet extraction is the ground truth per CONTRACTS §8)
    if all_search_results:
        snippet_linkedin = _extract_linkedin_from_snippets(all_search_results)
        if snippet_linkedin:
            # Snippet-extracted URL takes precedence / fills gap
            result_founder_linkedin_url = result_founder_linkedin_url or snippet_linkedin

    # ── If we still need the LinkedIn and haven't run the search yet,
    #    do a targeted snippet search ─────────────────────────────────────────
    if not result_founder_linkedin_url and result_founder_name:
        try:
            linkedin_query = (
                f"{result_founder_name} {candidate.name} linkedin"
            )
            li_results = deps.search(linkedin_query, max_results=5)
            all_search_results.extend(li_results)
            snippet_linkedin = _extract_linkedin_from_snippets(li_results)
            if snippet_linkedin:
                result_founder_linkedin_url = snippet_linkedin
                log.info("research_linkedin_from_snippets", url=snippet_linkedin)
                deps.audit(
                    "research",
                    "linkedin_from_snippets",
                    {"domain": candidate.domain, "url": snippet_linkedin},
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("research_linkedin_search_failed", error=str(exc))

    # ── Dedicated company-attribute pass (thorough mode) ─────────────────────
    # The main loop prioritises founder+funding and often exhausts its budget
    # before researching company attributes. So we ALWAYS run a focused pass:
    # a couple of targeted searches + ONE extraction LLM call for just the
    # attributes still missing. This makes attribute fill-rate reliable instead
    # of dependent on how the founder loop happened to spend its calls.
    missing = {
        "employee_count": result_employee_count,
        "revenue": result_revenue,
        "location": result_location,
        "industry": result_industry,
        "last_round_date": result_last_round_date,
    }
    if any(v is None for v in missing.values()):
        try:
            snippets: list[str] = []
            for q in (
                f"{candidate.name} {candidate.domain} headquarters employees industry",
                f"{candidate.name} number of employees revenue funding round",
            ):
                for r in deps.search(q, max_results=5):
                    snippets.append(f"{r.title} — {r.snippet} ({r.url})")
                    if r.url not in sources:
                        sources.append(r.url)
            if snippets:
                attr_sys = (
                    "Extract company attributes from the search snippets. Output "
                    "ONLY a JSON object with keys employee_count, revenue, location, "
                    "industry, last_round_date. Use null for anything not present in "
                    "the snippets. Do not guess or fabricate."
                )
                attr_msg = [{
                    "role": "user",
                    "content": f"Company: {candidate.name} ({candidate.domain})\n\n"
                    + "\n".join(snippets[:12]),
                }]
                attr_resp = deps.llm.complete(attr_sys, attr_msg, tools=None)
                ap = _parse_llm_json(attr_resp.get("text", ""))
                result_employee_count = result_employee_count or ap.get("employee_count")
                result_revenue = result_revenue or ap.get("revenue")
                result_location = result_location or ap.get("location")
                result_industry = result_industry or ap.get("industry")
                result_last_round_date = result_last_round_date or ap.get("last_round_date")
                deps.audit("research", "attributes_pass", {
                    "domain": candidate.domain,
                    "employee_count": result_employee_count,
                    "industry": result_industry,
                    "location": result_location,
                })
                log.info(
                    "research_attributes",
                    domain=candidate.domain,
                    industry=result_industry,
                    employees=result_employee_count,
                    location=result_location,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("research_attributes_failed", error=str(exc))

    # Deduplicate sources
    seen_sources: set[str] = set()
    unique_sources: list[str] = []
    for s in sources:
        if s not in seen_sources:
            seen_sources.add(s)
            unique_sources.append(s)

    result = ResearchResult(
        domain=candidate.domain,
        name=candidate.name,
        funding_stage=result_funding_stage,
        funding_amount=result_funding_amount,
        founder_name=result_founder_name,
        founder_linkedin_url=result_founder_linkedin_url,
        employee_count=result_employee_count,
        revenue=result_revenue,
        location=result_location,
        industry=result_industry,
        last_round_date=result_last_round_date,
        sources=unique_sources,
        used_shortcut=used_shortcut,
    )

    log.info(
        "research_done",
        funding_stage=result.funding_stage,
        funding_amount=result.funding_amount,
        founder_name=result.founder_name,
        has_linkedin=bool(result.founder_linkedin_url),
        used_shortcut=result.used_shortcut,
    )
    deps.audit(
        "research",
        "done",
        {
            "domain": candidate.domain,
            "funding_stage": result.funding_stage,
            "founder_name": result.founder_name,
            "used_shortcut": result.used_shortcut,
        },
    )
    return result


def _execute_tool(
    *,
    tool_name: str,
    tool_input: dict,
    deps: AgentDeps,
    candidate: CandidateCompany,
    all_search_results: list,
    sources: list[str],
    log: Any,
) -> str:
    """Execute a single LLM-requested tool and return a JSON string result."""

    if tool_name == "web_search":
        query: str = tool_input.get("query", "")
        max_results: int = int(tool_input.get("max_results", 8))
        max_results = max(1, min(max_results, 10))

        log.debug("research_tool_search", query=query)
        try:
            results = deps.search(query, max_results=max_results)
        except Exception as exc:  # noqa: BLE001
            log.warning("research_tool_search_error", query=query, error=str(exc))
            results = []

        all_search_results.extend(results)

        formatted = [
            {
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
            }
            for r in results
        ]
        # Add non-linkedin URLs to sources
        for r in results:
            if r.url and not _is_linkedin_url(r.url):
                sources.append(r.url)

        return json.dumps({"results": formatted})

    elif tool_name == "fetch_page":
        url: str = tool_input.get("url", "")
        render_js: bool = bool(tool_input.get("render_js", False))

        # HARD RULE: never fetch linkedin URLs
        if _is_linkedin_url(url):
            log.warning("research_tool_fetch_blocked_linkedin", url=url)
            return json.dumps({
                "ok": False,
                "error": "LinkedIn pages cannot be fetched. Extract the URL from search snippets instead.",
                "url": url,
            })

        log.debug("research_tool_fetch", url=url)
        try:
            page = deps.fetch_page(url, render_js=render_js)
        except Exception as exc:  # noqa: BLE001
            log.warning("research_tool_fetch_error", url=url, error=str(exc))
            return json.dumps({"ok": False, "error": str(exc), "url": url})

        if page.ok:
            sources.append(page.final_url or url)

        return json.dumps({
            "ok": page.ok,
            "url": page.final_url or url,
            "title": page.title,
            "text": page.text[:4000] if page.text else "",  # truncate for token budget
            "status": page.status,
        })

    else:
        log.warning("research_unknown_tool", tool_name=tool_name)
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
