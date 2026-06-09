"""Discovery agent — Phase 3 agentic leaf (CONTRACTS.md §8).

Discovery-agent builder owns this file.

Two complementary strategies are merged together:

1. OPEN-WEB (differentiator): an LLM tool-use loop that forms search queries,
   reads tech-news / funding / accelerator articles via the injected web tools, and
   extracts candidate companies.  Bounded to MAX_TOOL_CALLS iterations so it can
   never run away.

2. STRUCTURED FLOOR (cheap reliable baseline): yc.fetch_yc_candidates,
   producthunt.fetch_producthunt_candidates, rss.fetch_rss_candidates are all
   called in parallel (conceptually) and merged in.

The agent NEVER deduplicates cross-batches, verifies, or delivers — that is the
orchestrator's job.  It does deduplicate exact domain repeats within a single run
to avoid trivial redundancy.

The agent aims to return ~2-3× target candidates so the downstream dedup/verify
pipeline has plenty to prune.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent_server.agents.deps import AgentDeps
from agent_server.agents.sources.producthunt import fetch_producthunt_candidates
from agent_server.agents.sources.rss import fetch_rss_candidates
from agent_server.agents.sources.yc import fetch_yc_candidates
from agent_server.contracts.records import CandidateCompany
from agent_server.log import get_logger

log = get_logger(__name__)

# Hard cap on the number of LLM tool calls per run to prevent runaway cost.
MAX_TOOL_CALLS = 12

# System prompt for the discovery LLM agent.
_SYSTEM_PROMPT = """\
You are a startup-lead discovery agent.  Your mission is to find recently funded,
recently launched, or actively hiring tech-startup companies that might need
engineering talent.  You have two tools: "web_search" and "fetch_page".

Strategy
--------
1. Form 2-4 focused search queries based on the query hint (e.g.
   "startups raised seed 2024", "YC W24 companies", "new SaaS companies funding 2024").
2. Search for each query and identify 3-5 promising article / directory URLs
   (TechCrunch, Crunchbase news, AngelList, accelerator demo-day pages, etc.).
   DO NOT use LinkedIn URLs.
3. Fetch the most promising pages to read details.
4. When you have enough candidates (aim for the requested count), output your
   final answer as a JSON array — nothing else in that last message.

Final output format (emit EXACTLY this, no other text in the final message)::

    [
      {
        "name": "Acme Corp",
        "domain_or_url": "acme.com",
        "funding_stage": "Seed",
        "funding_amount": "$3M",
        "description": "AI-powered widget factory"
      },
      ...
    ]

Rules
-----
- NEVER fetch or follow any linkedin.com URL.
- Each tool call costs money; be efficient.
- If a page returns no useful data, move on quickly.
- Aim for variety: different sectors, stages, geographies.
"""

# Tool schemas in Anthropic tool format.
_TOOLS: list[dict[str, Any]] = [
    {
        "name": "web_search",
        "description": (
            "Search the open web for startup / funding news. "
            "Returns a list of {title, url, snippet} objects."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query string.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of results to return (default 10).",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_page",
        "description": (
            "Fetch and read a web page.  Returns its title and readable text. "
            "NEVER use this on LinkedIn URLs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch.",
                },
            },
            "required": ["url"],
        },
    },
]


def _is_linkedin(url: str) -> bool:
    return "linkedin.com" in url.lower()


def _run_tool_call(tool_name: str, tool_input: dict, deps: AgentDeps) -> str:
    """Execute a single tool call and return its result as a JSON string."""
    if tool_name == "web_search":
        query: str = tool_input.get("query", "")
        max_results: int = int(tool_input.get("max_results", 10))
        results = deps.search(query, max_results=max_results)
        serialised = [
            {"title": r.title, "url": r.url, "snippet": r.snippet}
            for r in results
            if not _is_linkedin(r.url)
        ]
        return json.dumps(serialised)

    if tool_name == "fetch_page":
        url: str = tool_input.get("url", "")
        if _is_linkedin(url):
            return json.dumps({"error": "LinkedIn URLs are not allowed."})
        page = deps.fetch_page(url)
        return json.dumps(
            {
                "url": page.final_url,
                "title": page.title,
                "ok": page.ok,
                "text": page.text[:4000] if page.text else "",
            }
        )

    return json.dumps({"error": f"Unknown tool: {tool_name}"})


def _parse_candidate_list(text: str) -> list[dict[str, Any]]:
    """Try to extract a JSON array from the model's final text output.

    The model is asked to emit ONLY the JSON array, but may include surrounding
    prose.  We try three approaches:
    1. Direct JSON parse.
    2. Extract the first [...] block with a regex.
    3. Return [] if both fail.
    """
    text = text.strip()
    if not text:
        return []

    # Attempt 1: direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 2: regex extraction of first [...] block (possibly multi-line)
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    return []


def _candidates_from_llm_items(
    items: list[dict[str, Any]], deps: AgentDeps
) -> list[CandidateCompany]:
    """Convert raw model-emitted dicts to CandidateCompany, skipping bad ones."""
    results: list[CandidateCompany] = []
    for item in items:
        try:
            raw_domain: str = item.get("domain_or_url") or item.get("domain") or ""
            name: str = item.get("name") or ""
            if not raw_domain or not name:
                continue
            domain = deps.normalize_domain(raw_domain)
            if domain is None:
                continue
            results.append(
                CandidateCompany(
                    name=name,
                    domain=domain,
                    source="open_web",
                    funding_stage=item.get("funding_stage") or None,
                    funding_amount=item.get("funding_amount") or None,
                    description=item.get("description") or None,
                )
            )
        except Exception as exc:
            log.warning("llm_item_parse_error", item=item, error=str(exc))
    return results


def _run_open_web_agent(
    job_id: str, query_hint: str | None, target: int, deps: AgentDeps
) -> list[CandidateCompany]:
    """Run the bounded LLM tool-use loop; return candidates found via open web."""
    hint = query_hint or "recently funded tech startups 2024"
    user_content = (
        f"Find at least {target * 2} recently funded or launched tech startup "
        f"companies.  Focus on: {hint}.  Aim for variety."
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
    tool_calls_used = 0

    deps.audit(
        "discovery",
        "open_web_start",
        {"job_id": job_id, "query_hint": hint, "target": target},
    )

    while tool_calls_used < MAX_TOOL_CALLS:
        response = deps.llm.complete(_SYSTEM_PROMPT, messages, tools=_TOOLS)
        text: str = response.get("text", "")
        calls: list[dict[str, Any]] = response.get("tool_calls", [])

        # If no tool calls, the model has given its final answer.
        if not calls:
            log.info(
                "open_web_agent_done",
                job_id=job_id,
                tool_calls_used=tool_calls_used,
            )
            deps.audit(
                "discovery",
                "open_web_final",
                {"tool_calls_used": tool_calls_used, "text_len": len(text)},
            )
            items = _parse_candidate_list(text)
            return _candidates_from_llm_items(items, deps)

        # Build the assistant message with the tool-use blocks for conversation history.
        assistant_content: list[dict[str, Any]] = []
        if text:
            assistant_content.append({"type": "text", "text": text})
        for call in calls:
            assistant_content.append(
                {
                    "type": "tool_use",
                    "id": call["id"],
                    "name": call["name"],
                    "input": call["input"],
                }
            )
        messages.append({"role": "assistant", "content": assistant_content})

        # Execute EVERY tool call in this batch and collect a result for each.
        # The cap is enforced at the turn level (the outer while loop), never
        # mid-batch: every `tool_use` block in the assistant message above MUST
        # get a matching `tool_result` or the Messages/Bedrock API rejects the
        # next request ("tool_use ids without tool_result").
        tool_results: list[dict[str, Any]] = []
        for call in calls:
            tool_name: str = call["name"]
            tool_input: dict = call.get("input") or {}

            # Safety check: refuse LinkedIn even if the model tries
            if tool_name == "fetch_page" and _is_linkedin(
                tool_input.get("url", "")
            ):
                result_text = json.dumps(
                    {"error": "LinkedIn URLs are not allowed."}
                )
                deps.audit(
                    "discovery",
                    "linkedin_refused",
                    {"url": tool_input.get("url", "")},
                )
            else:
                result_text = _run_tool_call(tool_name, tool_input, deps)
                deps.audit(
                    "discovery",
                    "tool_called",
                    {"tool": tool_name, "call_index": tool_calls_used},
                )
                log.debug(
                    "discovery_tool_call",
                    job_id=job_id,
                    tool=tool_name,
                    call_n=tool_calls_used,
                )

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": result_text,
                }
            )
            tool_calls_used += 1

        messages.append({"role": "user", "content": tool_results})

    # Exhausted MAX_TOOL_CALLS — ask the model for its final answer now.
    log.info(
        "open_web_cap_reached",
        job_id=job_id,
        tool_calls_used=tool_calls_used,
    )
    deps.audit(
        "discovery",
        "open_web_cap",
        {"tool_calls_used": tool_calls_used},
    )
    messages.append(
        {
            "role": "user",
            "content": (
                "You have reached the tool-call limit.  Output your final JSON array "
                "of candidate companies now — nothing else."
            ),
        }
    )
    final_response = deps.llm.complete(_SYSTEM_PROMPT, messages, tools=None)
    final_text = final_response.get("text", "")
    items = _parse_candidate_list(final_text)
    return _candidates_from_llm_items(items, deps)


def run_discovery(
    job_id: str,
    *,
    query_hint: str | None,
    target: int,
    deps: AgentDeps,
) -> list[CandidateCompany]:
    """Discover candidate companies and return them as a flat list.

    Never raises — on LLM/tool failure, falls back to the structured-floor
    candidates so the pipeline always gets *something*.

    Args:
        job_id:     Passed through to audit entries for traceability.
        query_hint: Optional free-text to steer the LLM's search queries.
        target:     Approximate number the orchestrator wants; we aim for 2-3×.
        deps:       Injected agent dependencies (search, fetch_page, llm, audit,
                    normalize_domain).

    Returns:
        List of CandidateCompany, keyed by domain (exact domain dupes within this
        run are collapsed; cross-run dedup is the orchestrator's job).
    """
    log.info("discovery_start", job_id=job_id, query_hint=query_hint, target=target)
    deps.audit(
        "discovery",
        "start",
        {"job_id": job_id, "query_hint": query_hint, "target": target},
    )

    # --- 1. Structured floor (cheap, reliable baseline) ---
    floor_limit = max(target, 50)

    yc_candidates: list[CandidateCompany] = []
    ph_candidates: list[CandidateCompany] = []
    rss_candidates: list[CandidateCompany] = []

    try:
        yc_candidates = fetch_yc_candidates(deps, limit=floor_limit)
    except Exception as exc:
        log.warning("yc_source_error", error=str(exc))

    try:
        ph_candidates = fetch_producthunt_candidates(deps, limit=floor_limit)
    except Exception as exc:
        log.warning("ph_source_error", error=str(exc))

    try:
        rss_candidates = fetch_rss_candidates(deps, limit=floor_limit)
    except Exception as exc:
        log.warning("rss_source_error", error=str(exc))

    floor = yc_candidates + ph_candidates + rss_candidates
    deps.audit(
        "discovery",
        "floor_fetched",
        {
            "yc": len(yc_candidates),
            "producthunt": len(ph_candidates),
            "rss": len(rss_candidates),
            "total_floor": len(floor),
        },
    )
    log.info(
        "discovery_floor",
        job_id=job_id,
        yc=len(yc_candidates),
        ph=len(ph_candidates),
        rss=len(rss_candidates),
    )

    # --- 2. Open-web agent (differentiator) ---
    open_web: list[CandidateCompany] = []
    try:
        open_web = _run_open_web_agent(job_id, query_hint, target, deps)
        deps.audit(
            "discovery", "open_web_done", {"open_web_count": len(open_web)}
        )
        log.info("discovery_open_web", job_id=job_id, count=len(open_web))
    except Exception as exc:
        log.warning(
            "open_web_agent_error",
            job_id=job_id,
            error=str(exc),
            exc_info=True,
        )
        deps.audit("discovery", "open_web_error", {"error": str(exc)})
        # Fall back to floor only — we still return something useful.

    # --- 3. Merge and dedup (domain-level) ---
    all_candidates = open_web + floor  # open_web first = higher priority
    seen_domains: set[str] = set()
    merged: list[CandidateCompany] = []
    for candidate in all_candidates:
        if candidate.domain not in seen_domains:
            seen_domains.add(candidate.domain)
            merged.append(candidate)

    log.info(
        "discovery_done",
        job_id=job_id,
        total=len(merged),
        open_web=len(open_web),
        floor=len(floor),
    )
    deps.audit(
        "discovery",
        "done",
        {
            "total": len(merged),
            "open_web": len(open_web),
            "floor": len(floor),
        },
    )

    return merged
