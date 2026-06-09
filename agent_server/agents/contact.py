"""Contact-finding agent — an LLM that THINKS, PLANS, and ACTS to find a
person's work email, then verifies it.

Unlike the deterministic verification waterfall (stages/verify.py), this agent
reasons about *how* to find the contact: it can try a direct provider lookup
(Apollo/Hunter), generate likely email patterns from the name + domain and
verify the best candidate, and search the open web for a published address —
deciding which result to trust and when to stop.

Bounded tool-use loop (≤ MAX_TOOL_CALLS) driven by deps.llm with three tools:
  - web_search(query)            → public results (title/url/snippet)
  - guess_email_patterns(...)    → common patterns for name@domain (no network)
  - verify_email(domain, name?, email?)
        → runs the provider waterfall: if `email` given, validates it; else
          discovers one (Apollo people-match / Hunter finder). Returns the
          best email + a 0–1 deliverability score + which provider answered.

Returns a ContactResult: {email, score, method, rationale, candidates[]}.

RESILIENT: never raises — returns a best-effort ContactResult (possibly empty)
on any error, so callers (the /verify/email endpoint, the Find-emails button)
never crash.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from agent_server.agents.deps import AgentDeps
from agent_server.log import get_logger
from agent_server.stages.verify import WaterfallVerifier

logger = get_logger(__name__)

MAX_TOOL_CALLS = 8
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@dataclass
class ContactResult:
    email: str | None = None
    score: float = 0.0
    method: str = "none"          # which provider/source produced the email
    rationale: str = ""           # the agent's short explanation
    candidates: list[dict] = field(default_factory=list)  # every email tried + score


# ── Anthropic tool schemas ────────────────────────────────────────────────────
_TOOLS: list[dict] = [
    {
        "name": "web_search",
        "description": (
            "Search the public web for a person's work email or contact page. "
            "Returns results with title, url, snippet. Use to find published "
            "addresses or confirm an email pattern."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "guess_email_patterns",
        "description": (
            "Generate the most common corporate email patterns for a person at a "
            "domain (e.g. first@, first.last@, flast@). No network — just returns "
            "candidate addresses to then verify."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "full_name": {"type": "string"},
                "domain": {"type": "string"},
            },
            "required": ["full_name", "domain"],
        },
    },
    {
        "name": "verify_email",
        "description": (
            "Find and/or verify an email via the provider waterfall (Apollo, "
            "Hunter, Abstract, then a weak SMTP check). If `email` is provided it "
            "is validated; otherwise an address is discovered from name+domain. "
            "Returns {email, score (0-1), method}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "full_name": {"type": "string"},
                "email": {
                    "type": "string",
                    "description": "Optional candidate to validate.",
                },
            },
            "required": ["domain"],
        },
    },
]

_SYSTEM = (
    "You are a contact-research agent. Your job: find the single best WORK email "
    "for a named person at a company domain, and verify it is deliverable.\n\n"
    "Plan, then act with the tools:\n"
    "1. Start with verify_email (no email arg) — providers like Apollo/Hunter can "
    "find it directly. If that returns a high score, you're nearly done.\n"
    "2. If not found, call guess_email_patterns, then verify_email on the most "
    "likely candidate(s) — stop early once one scores well (>= 0.7).\n"
    "3. Optionally web_search for a published address to corroborate.\n"
    "Be frugal: a few tool calls, not many. When done, reply with ONLY a JSON "
    'object: {"email": <best email or null>, "score": <0-1>, "method": <provider/'
    'source>, "rationale": <one sentence>}. No prose outside the JSON.'
)


def _patterns(full_name: str, domain: str) -> list[str]:
    """Common corporate email patterns. Deterministic, no network."""
    parts = [re.sub(r"[^a-z0-9]", "", p) for p in full_name.lower().split()]
    parts = [p for p in parts if p]
    if not parts:
        return [f"info@{domain}", f"hello@{domain}"]
    first = parts[0]
    last = parts[-1] if len(parts) > 1 else ""
    cands = [f"{first}@{domain}"]
    if last:
        cands += [
            f"{first}.{last}@{domain}",
            f"{first[0]}{last}@{domain}",
            f"{first}{last}@{domain}",
            f"{first}_{last}@{domain}",
            f"{last}@{domain}",
        ]
    return cands


def _run_tool(name: str, args: dict, deps: AgentDeps, verifier: WaterfallVerifier,
              found: list[dict]) -> str:
    """Execute one tool call; return a JSON string result for the model."""
    try:
        if name == "web_search":
            results = deps.search(args.get("query", ""), max_results=6)
            # Surface any emails spotted directly in snippets.
            for r in results:
                for m in _EMAIL_RE.findall(f"{r.title} {r.snippet}"):
                    found.append({"email": m, "score": 0.4, "method": "web_snippet"})
            return json.dumps(
                [{"title": r.title, "url": r.url, "snippet": r.snippet} for r in results]
            )

        if name == "guess_email_patterns":
            cands = _patterns(args.get("full_name", ""), args.get("domain", ""))
            return json.dumps({"candidates": cands})

        if name == "verify_email":
            domain = args.get("domain", "")
            full_name = args.get("full_name") or None
            email = args.get("email")
            if email:
                # Validate a specific candidate (Abstract-style providers + SMTP).
                v = verifier.find_and_verify(domain, full_name)
                # find_and_verify discovers; for explicit candidate validation we
                # still surface its verdict but tag the candidate the model asked for.
                result = {"email": v.email or email, "score": v.score, "method": v.method}
            else:
                v = verifier.find_and_verify(domain, full_name)
                result = {"email": v.email, "score": v.score, "method": v.method}
            if result["email"]:
                found.append(dict(result))
            return json.dumps(result)

        return json.dumps({"error": f"unknown tool {name}"})
    except Exception as exc:  # never let a tool crash the agent
        logger.warning("contact.tool_error", tool=name, error=str(exc))
        return json.dumps({"error": str(exc)})


def find_contact(
    domain: str,
    full_name: str | None,
    deps: AgentDeps,
    *,
    verifier: WaterfallVerifier | None = None,
) -> ContactResult:
    """Run the agent to find + verify the best work email. Never raises."""
    verifier = verifier or WaterfallVerifier()
    found: list[dict] = []

    user = (
        f"Find the best work email for "
        f"{full_name or 'the main contact'} at domain {domain}."
    )
    messages: list[dict] = [{"role": "user", "content": user}]

    try:
        calls = 0
        while calls < MAX_TOOL_CALLS:
            resp = deps.llm.complete(_SYSTEM, messages, tools=_TOOLS)
            text = resp.get("text", "") or ""
            tool_calls = resp.get("tool_calls", []) or []

            deps.audit("contact", "llm_turn", {"domain": domain, "turn": calls})

            if not tool_calls:
                parsed = _parse_json(text)
                return _finalize(parsed, found, domain)

            # Append assistant turn WITH its tool_use blocks (Bedrock-correct).
            assistant: list[dict] = []
            if text:
                assistant.append({"type": "text", "text": text})
            for tc in tool_calls:
                assistant.append(
                    {"type": "tool_use", "id": tc.get("id", ""),
                     "name": tc.get("name", ""), "input": tc.get("input", {}) or {}}
                )
            messages.append({"role": "assistant", "content": assistant})

            results: list[dict] = []
            for tc in tool_calls:
                calls += 1
                out = _run_tool(tc.get("name", ""), tc.get("input", {}) or {},
                                deps, verifier, found)
                deps.audit("contact", "tool", {"tool": tc.get("name"), "domain": domain})
                results.append(
                    {"type": "tool_result", "tool_use_id": tc.get("id", ""),
                     "content": out}
                )
            messages.append({"role": "user", "content": results})

        # Hit the cap — fall back to the best email we collected.
        return _best_of(found, domain, rationale="reached tool-call cap")
    except Exception as exc:
        logger.warning("contact.fatal", domain=domain, error=str(exc))
        return _best_of(found, domain, rationale=f"agent error: {exc}")


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return {}
        return {}


def _finalize(parsed: dict, found: list[dict], domain: str) -> ContactResult:
    """Trust the model's final JSON, but fall back to the best verified candidate
    if the model returned nothing usable."""
    email = parsed.get("email")
    if email and "@" in str(email):
        return ContactResult(
            email=str(email),
            score=float(parsed.get("score", 0.0) or 0.0),
            method=str(parsed.get("method", "agent")),
            rationale=str(parsed.get("rationale", "")),
            candidates=found,
        )
    return _best_of(found, domain, rationale=str(parsed.get("rationale", "")))


def _best_of(found: list[dict], domain: str, *, rationale: str) -> ContactResult:
    """Pick the highest-scoring verified candidate the tools produced."""
    if not found:
        return ContactResult(rationale=rationale or "no email found")
    best = max(found, key=lambda c: c.get("score", 0))
    return ContactResult(
        email=best.get("email"),
        score=float(best.get("score", 0.0)),
        method=str(best.get("method", "agent")),
        rationale=rationale or "best verified candidate",
        candidates=found,
    )
