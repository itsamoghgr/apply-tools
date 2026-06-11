"""Company roster builder — "find people at a company".

Given a company domain, enumerate a ROLE-FILTERED list of people who work
there, then find + verify each person's work email so they can be saved as
Leads.

Strategy (OPEN-WEB-FIRST, paid APIs LAST — the same philosophy as
`agents/contact.py`):
  1. ENUMERATE the roster cheaply via Hunter `domain-search` WITHOUT a name
     (`HunterProvider.list_people`). This is the only place we touch a paid
     provider, and only to get the people list (names + titles) — Hunter's
     per-person email is NOT trusted as the answer.
  2. ROLE-FILTER to high-signal hiring/leadership titles (CONFIG.roster_roles).
     A person whose `position` is missing/empty is EXCLUDED by default (we keep
     only people we can confirm hold a relevant role).
  3. For each surviving person, find + verify their email with the open-web-first
     contact agent (`agents/contact.find_contact`) — web search → pattern guess →
     free SMTP validation, with the paid provider waterfall only as a LAST
     resort. That agent never raises.

RESILIENT: never raises — returns a best-effort partial RosterResult on any
error. One bad person must not abort the roster (mirrors contact.py's contract).
"""

from __future__ import annotations

import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

from agent_server.config import CONFIG
from agent_server.log import get_logger
from agent_server.stages.verify import HunterProvider

logger = get_logger(__name__)

# Cost guard: never run the per-person contact agent on more than this many
# people in a single roster call. Each person is a deep open-web-first agent
# run, so this is deliberately small.
DEFAULT_MAX_PEOPLE = 10

# The contact agent is I/O-bound (web fetches + LLM round-trips), so we run a
# handful concurrently to keep the synchronous endpoint from taking minutes.
ROSTER_CONCURRENCY = 4

# Wall-clock budget for the whole per-person phase. Once exceeded we stop
# collecting and return a partial roster rather than hanging the request.
ROSTER_TIME_BUDGET_S = 90.0


@dataclass
class RosterPerson:
    name: str
    title: str | None = None
    email: str | None = None
    score: float = 0.0
    method: str = "none"      # which source produced the email
    rationale: str = ""       # the contact agent's short explanation


@dataclass
class RosterResult:
    domain: str
    company_name: str | None = None
    people: list[RosterPerson] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _role_match(position: str | None, keywords: frozenset[str]) -> bool:
    """True if `position` contains any role keyword (case-insensitive substring).

    Missing/empty position → False (EXCLUDE by default; high-signal only).
    """
    if not position:
        return False
    pos = position.lower()
    return any(kw in pos for kw in keywords)


def _full_name(entry: dict) -> str:
    """Build a display name from a Hunter people entry; fall back to local-part."""
    first = (entry.get("first_name") or "").strip()
    last = (entry.get("last_name") or "").strip()
    name = " ".join(p for p in (first, last) if p)
    if name:
        return name
    value = (entry.get("value") or "").strip()
    if "@" in value:
        local = value.split("@", 1)[0]
        return re.sub(r"[._]+", " ", local).strip().title()
    return ""


def _dedup_key(entry: dict) -> str:
    """Stable identity for dedup: normalized name, else email local-part."""
    name = _full_name(entry).lower()
    name = re.sub(r"[^a-z0-9]", "", name)
    if name:
        return name
    value = (entry.get("value") or "").lower()
    return value.split("@", 1)[0] if "@" in value else value


def find_roster(
    domain: str,
    company_name: str | None,
    *,
    roles: list[str] | None = None,
    deps=None,
    max_people: int = DEFAULT_MAX_PEOPLE,
) -> RosterResult:
    """Enumerate + role-filter people at `domain` and find each one's email.

    Parameters
    ----------
    domain:       company root domain (already normalized by the caller).
    company_name: optional display name (echoed back; aids the contact agent).
    roles:        optional override role keywords; defaults to CONFIG.roster_roles.
    deps:         AgentDeps for the contact agent. If None, one is built lazily
                  (search/fetch/LLM) so email-finding still runs. If the LLM is
                  unavailable, each person is returned without an email rather
                  than aborting.
    max_people:   cost guard — cap the number of people we run the contact agent
                  on (after role-filter + dedup).

    Never raises — returns a best-effort partial RosterResult on any error.
    """
    result = RosterResult(domain=domain, company_name=company_name)
    keywords = (
        frozenset(r.strip().lower() for r in roles if r and r.strip())
        if roles
        else CONFIG.roster_role_keywords
    )

    # 1. Enumerate (paid, name-free) — the only paid call. We over-fetch
    # relative to max_people since role-filtering discards most entries, but
    # cap the page so a low max_people doesn't pay to pull a huge list.
    enumerate_limit = min(max(max_people * 5, 25), 100)
    try:
        entries = HunterProvider().list_people(domain, limit=enumerate_limit)
    except Exception as exc:  # never raises, but be defensive
        logger.warning("roster.enumerate_error", domain=domain, error=str(exc))
        result.errors.append(f"enumerate: {exc}")
        return result

    if not entries:
        logger.info("roster.no_people", domain=domain)
        return result

    # 2. Role-filter + dedup.
    survivors: list[dict] = []
    seen: set[str] = set()
    for e in entries:
        if not _role_match(e.get("position"), keywords):
            continue
        key = _dedup_key(e)
        if not key or key in seen:
            continue
        seen.add(key)
        survivors.append(e)

    logger.info(
        "roster.filtered",
        domain=domain,
        enumerated=len(entries),
        kept=len(survivors),
        cap=max_people,
    )
    survivors = survivors[:max_people]  # 3. cost guard

    # Build deps lazily for the open-web-first contact agent.
    find_contact = None
    if survivors:
        try:
            from agent_server.agents.contact import find_contact as _fc
            find_contact = _fc
            if deps is None:
                from agent_server.agents.deps import AgentDeps
                from agent_server.agents.llm import AnthropicLLM
                from agent_server.stages.normalize import normalize_domain as _nd
                from agent_server.web import fetch_page, search

                deps = AgentDeps(
                    search=search,
                    fetch_page=fetch_page,
                    llm=AnthropicLLM(),
                    audit=lambda *a, **k: None,
                    normalize_domain=_nd,
                )
        except Exception as exc:
            logger.warning("roster.contact_unavailable", domain=domain, error=str(exc))
            result.errors.append(f"contact_agent_unavailable: {exc}")
            find_contact = None

    # 4. Per-person open-web-first email-finding, run concurrently (I/O-bound)
    # under a wall-clock budget. One failure never aborts; people we don't get
    # to before the budget expires are returned without an email.
    def _resolve(entry: dict) -> RosterPerson:
        name = _full_name(entry)
        title = (entry.get("position") or "").strip() or None
        person = RosterPerson(name=name, title=title)
        if find_contact is not None and name:
            cr = find_contact(domain, name, deps)
            person.email = cr.email
            person.score = cr.score
            person.method = cr.method
            person.rationale = cr.rationale
        return person

    if survivors and find_contact is not None:
        deadline = time.monotonic() + ROSTER_TIME_BUDGET_S
        pool = ThreadPoolExecutor(max_workers=ROSTER_CONCURRENCY)
        futures = {pool.submit(_resolve, e): e for e in survivors}
        try:
            # Drain completed futures, but never wait past the deadline: we wait
            # in slices bounded by the remaining budget so a stuck agent can't
            # hold the whole request hostage. `concurrent.futures.wait` returns
            # as soon as anything finishes OR the timeout elapses.
            pending = set(futures)
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                done, pending = wait(
                    pending, timeout=remaining, return_when=FIRST_COMPLETED
                )
                for fut in done:
                    entry = futures.pop(fut)
                    name = _full_name(entry)
                    try:
                        result.people.append(fut.result())
                    except Exception as exc:  # a stray agent error
                        logger.warning(
                            "roster.person_error",
                            domain=domain,
                            person=name,
                            error=str(exc),
                        )
                        result.errors.append(f"{name}: {exc}")
                        title = (entry.get("position") or "").strip() or None
                        result.people.append(RosterPerson(name=name, title=title))
        finally:
            # Stop waiting on anyone still running once we're done / over budget.
            # cancel_futures drops not-yet-started work; in-flight threads are
            # daemonic and abandoned (we don't block the request on them).
            pool.shutdown(wait=False, cancel_futures=True)
        # Surface people we never got a result for (over budget) without email.
        for entry in futures.values():
            name = _full_name(entry)
            if name:
                result.errors.append(f"{name}: skipped (time budget exceeded)")
            title = (entry.get("position") or "").strip() or None
            result.people.append(RosterPerson(name=name, title=title))
    else:
        # No contact agent (LLM unavailable) — return people without emails.
        for e in survivors:
            title = (e.get("position") or "").strip() or None
            result.people.append(RosterPerson(name=_full_name(e), title=title))

    logger.info(
        "roster.done",
        domain=domain,
        people=len(result.people),
        with_email=sum(1 for p in result.people if p.email),
        errors=len(result.errors),
    )
    return result
