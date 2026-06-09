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

MAX_TOOL_CALLS = 14   # deep open-web research needs room for several searches
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Personal/free mail providers — a WORK email at the company domain is almost
# always preferred over one of these for outreach.
_PERSONAL_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
    "proton.me", "protonmail.com", "aol.com", "me.com", "live.com", "msn.com",
}


def _email_quality(email: str, target_domain: str | None) -> float:
    """A 0–1 preference weight for an email candidate, independent of how it was
    found: same as the target company domain > other corporate domain > personal.
    Used to pick the best candidate when several were collected.
    """
    if not email or "@" not in email:
        return 0.0
    dom = email.split("@", 1)[1].lower()
    if target_domain and dom == target_domain.lower():
        return 1.0
    if dom in _PERSONAL_DOMAINS:
        return 0.2
    return 0.6  # some other corporate domain


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
        "name": "validate_email_smtp",
        "description": (
            "FREE, no paid API. Validate a specific candidate email via DNS/MX + a "
            "lightweight SMTP check. Use this on your best pattern guesses and on "
            "addresses you found in web snippets. Returns {email, score (0-1), "
            "method}. Prefer this over provider_lookup."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Candidate to validate."},
            },
            "required": ["email"],
        },
    },
    {
        "name": "provider_lookup",
        "description": (
            "LAST RESORT — costs a paid API credit (Apollo/Hunter/Abstract). Only "
            "call this AFTER open-web search, pattern guessing, and SMTP validation "
            "have failed to produce a confident email. Discovers/verifies an email "
            "from name + domain. Returns {email, score (0-1), method}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "full_name": {"type": "string"},
            },
            "required": ["domain"],
        },
    },
]

_SYSTEM = (
    "You are a contact-research agent. Find the single best WORK email for a named "
    "person at a company. PREFER THE OPEN WEB. Paid providers cost money — only "
    "use provider_lookup as a LAST RESORT. Be persistent across several searches.\n\n"
    "Strategy (in order):\n"
    "1. CONFIRM THE DOMAIN. The given domain may be a guess (e.g. .com when the "
    "real site is .ai/.io/.co). web_search '<company> official website' and read "
    "snippets to confirm the real domain. Use it for everything after.\n"
    "2. DEEP OPEN-WEB SEARCH for the address (this is your MAIN method). Run "
    "multiple web_search queries: '<name> <company> email', '<name> email address', "
    "'<company> contact <name>', '<name> <domain> contact'. Snippets from "
    "RocketReach, Crunchbase, press, and company team/contact pages often expose "
    "or hint the email. Emails found in snippets are captured automatically.\n"
    "3. guess_email_patterns on the confirmed domain, then validate_email_smtp "
    "(FREE) on the most likely candidates. If a web snippet and a pattern agree, "
    "confidence is high — you're done.\n"
    "4. ONLY IF the open web + SMTP fail to yield a confident email, call "
    "provider_lookup ONCE as a paid last resort.\n"
    "Spend your search budget before concluding null. When done, reply with ONLY a "
    'JSON object: {"email": <best email or null>, "score": <0-1>, "method": '
    '<source: web_snippet|smtp|pattern|apollo|hunter|...>, "domain": <confirmed '
    'domain>, "rationale": <one sentence>}. No prose outside the JSON.'
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

        if name == "validate_email_smtp":
            # FREE path — no paid API. Confirm the domain has mail (MX) records;
            # a well-formed candidate at a real MX domain is a weak-positive even
            # when the SMTP RCPT probe is inconclusive (port 25 blocked / accept-
            # all servers — the documented common case).
            email = (args.get("email") or "").strip().lower()
            if "@" not in email:
                return json.dumps({"error": "provide a full email to validate"})
            domain = email.split("@", 1)[1]
            has_mx = False
            try:
                import dns.resolver

                dns.resolver.resolve(domain, "MX")
                has_mx = True
            except Exception:
                has_mx = False
            score = 0.4 if has_mx else 0.1
            result = {"email": email, "score": score,
                      "method": "smtp" if has_mx else "smtp_no_mx"}
            found.append(dict(result))
            return json.dumps(result)

        if name == "provider_lookup":
            # LAST RESORT — paid API waterfall (Apollo/Hunter/Abstract).
            domain = args.get("domain", "")
            full_name = args.get("full_name") or None
            v = verifier.find_and_verify(domain, full_name)
            result = {"email": v.email, "score": v.score, "method": v.method}
            if result["email"]:
                found.append(dict(result))
            return json.dumps(result)

        return json.dumps({"error": f"unknown tool {name}"})
    except Exception as exc:  # never let a tool crash the agent
        logger.warning(
            "contact.tool_error",
            tool=name,
            error=str(exc),
            error_type=type(exc).__name__,
            args=args,
        )
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
                tname = tc.get("name", "")
                targs = tc.get("input", {}) or {}
                out = _run_tool(tname, targs, deps, verifier, found)
                deps.audit("contact", "tool", {"tool": tname, "domain": domain})
                # Visible in the agent-server log so the work is observable.
                logger.info(
                    "contact.tool",
                    domain=domain,
                    tool=tname,
                    query=targs.get("query"),
                    candidate=targs.get("email"),
                    result_preview=out[:140],
                )
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
    """Honor the model's final decision.

    The agent has seen all the tool results and reasons about which (if any) is
    the *right* person — e.g. a provider may return a high-scoring address for a
    DIFFERENT employee. So:
      - model gave an email  → use it (its considered choice);
      - model EXPLICITLY said email:null → respect "couldn't confirm" and return
        no email (do NOT override with a high-scored wrong candidate);
      - model returned nothing parseable → fall back to the best verified one.
    """
    if "email" in parsed:  # the model made an explicit decision
        email = parsed.get("email")
        if email and "@" in str(email):
            return ContactResult(
                email=str(email),
                score=float(parsed.get("score", 0.0) or 0.0),
                method=str(parsed.get("method", "agent")),
                rationale=str(parsed.get("rationale", "")),
                candidates=found,
            )
        # Explicit null/empty → trust it; surface candidates for transparency.
        return ContactResult(
            email=None,
            score=0.0,
            method="none",
            rationale=str(parsed.get("rationale", "") or "agent could not confirm"),
            candidates=found,
        )
    # No usable JSON at all → best-effort fallback.
    return _best_of(found, domain, rationale=str(parsed.get("rationale", "")))


def _best_of(found: list[dict], domain: str, *, rationale: str) -> ContactResult:
    """Pick the best candidate the tools produced.

    Rank by score × email-quality so a personal address (e.g. a founder's
    gmail/yahoo scraped from GitHub) never beats a work email at the company
    domain when both were collected.
    """
    if not found:
        return ContactResult(rationale=rationale or "no email found")
    best = max(
        found,
        key=lambda c: c.get("score", 0.0) * _email_quality(c.get("email", ""), domain),
    )
    return ContactResult(
        email=best.get("email"),
        score=float(best.get("score", 0.0)),
        method=str(best.get("method", "agent")),
        rationale=rationale or "best verified candidate",
        candidates=found,
    )
