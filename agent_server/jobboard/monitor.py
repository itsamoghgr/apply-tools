"""Monitor cycle — scrape every watched career page once.

Runs every 3 hours (APScheduler) or on demand via the trigger endpoint.

Per company:
    fetch -> role-filter -> upsert -> archive-missing -> stamp status

Two invariants shape the whole file:

SEEDING. The first time a company is monitored it has no history, so its entire
current board would otherwise read as "new" and flood the next digest. The seed
cycle writes every matched posting with isNew=false and stamps seededAt; only
postings appearing AFTER that count as new.

FAILURE ISOLATION. A per-company exception is caught, recorded on that company
and in the run table, and the loop continues — mirroring the lead-gen loop's
per-candidate try/except. One dead career page never aborts a cycle.

Novelty is decided by the platform's UNIQUE (watchedCompanyId, dedupKey) index,
never by a posted-date: most custom boards publish no date, and a board that
backfills or omits one must not be able to hide a role.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agent_server.config import CONFIG
from agent_server.jobboard import db as jb_db
from agent_server.jobboard import platform_client as pc
from agent_server.jobboard.adapters import AdapterError, RawPosting, detect, fetch_for
from agent_server.jobboard.adapters.generic import SOURCE as GENERIC
from agent_server.jobboard.location import parse_country
from agent_server.jobboard.matcher import DEFAULT_ROLE_MATCHERS, dedup_key, role_match
from agent_server.log import get_logger

logger = get_logger(__name__)


@dataclass
class CompanyResult:
    company_id: str
    name: str
    ok: bool
    seen: int = 0
    matched: int = 0
    new: int = 0
    archived: int = 0
    seeded: bool = False
    error: str | None = None


@dataclass
class MonitorSummary:
    run_id: str
    companies_total: int = 0
    companies_ok: int = 0
    companies_failed: int = 0
    postings_seen: int = 0
    postings_matched: int = 0
    postings_new: int = 0
    results: list[CompanyResult] = field(default_factory=list)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _matchers_for(company: dict) -> dict[str, list[str]]:
    """Per-company role filter, else the global default set.

    WatchedCompany.roleFilter is stored as a JSON list of ROLE NAMES (not
    aliases), so narrowing a board to one role keeps that role's full alias list.
    """
    raw = company.get("roleFilter")
    if not raw:
        return DEFAULT_ROLE_MATCHERS
    names = raw if isinstance(raw, list) else []
    subset = {r: a for r, a in DEFAULT_ROLE_MATCHERS.items() if r in names}
    return subset or DEFAULT_ROLE_MATCHERS


def _experience_allowed(posting: RawPosting, max_years: int) -> bool:
    """Whether a posting is within the scrape-wide experience ceiling.

    Fails OPEN twice over: a ceiling of 0 means "no limit", and a posting whose
    JD states no requirement is KEPT. Roughly a fifth of postings publish no
    number, and "unstated" is not evidence of seniority — many are junior roles
    at companies that simply don't list requirements.
    """
    if not max_years:
        return True
    if posting.min_years is None:
        return True
    return posting.min_years <= max_years


def _country_allowed(posting: RawPosting, allowed: list[str] | None) -> bool:
    """Whether a posting survives the per-company country filter.

    A posting whose country CANNOT be parsed is always kept. The parser resolves
    ~99% of real locations, but the remainder ("Remote", "n/a", placeholder
    strings) are unresolvable by construction — and a scrape-time filter deletes
    permanently, so an unknown location must never be grounds for discarding.
    """
    if not allowed:
        return True
    country = parse_country(posting.location)
    if country is None:
        return True
    return country in allowed


def _is_same_local_day(moment: datetime, now: datetime) -> bool:
    """Compare in the machine's LOCAL timezone, not UTC.

    "Posted today" has to mean today where the user is; a UTC boundary would
    drop or double-count roles for anyone west of Greenwich.
    """
    return moment.astimezone().date() == now.astimezone().date()


def _apply_day_filter(postings: list[RawPosting], now: datetime) -> list[RawPosting]:
    """Keep only same-day postings — but ONLY when the source dates them.

    Undated postings always pass: their novelty is settled by dedup against the
    DB, and silently dropping them would hide real roles on boards that publish
    no dates.
    """
    dated = [p for p in postings if p.posted_at is not None]
    if not dated:
        return postings
    undated = [p for p in postings if p.posted_at is None]
    return [p for p in dated if _is_same_local_day(p.posted_at, now)] + undated


def _to_payload(posting: RawPosting, matched_role: str, is_new: bool) -> dict:
    return {
        "dedup_key": dedup_key(posting),
        "external_id": posting.external_id,
        "title": posting.title,
        "matched_role": matched_role,
        "location": posting.location,
        "url": posting.url,
        "posted_at": posting.posted_at.isoformat() if posting.posted_at else None,
        "min_years": posting.min_years,
        "country": parse_country(posting.location),
        "is_new": is_new,
    }


def monitor_company(
    company: dict,
    run_id: str,
    *,
    now: datetime | None = None,
    max_years: int | None = None,
    default_countries: list[str] | None = None,
) -> CompanyResult:
    """Scrape and persist ONE company. Raises only on programmer error.

    Adapter/platform failures are converted into an `ok=False` result so the
    caller can record them and carry on.
    """
    now = now or _utcnow()
    company_id = company["id"]
    name = company.get("name") or company_id
    ats = company.get("ats")
    slug = company.get("atsSlug")
    career_url = company.get("careerUrl") or ""
    is_seed = company.get("seededAt") is None
    log = logger.bind(company=name, company_id=company_id, ats=ats, seed=is_seed)

    # A company saved before detection ran (or whose URL changed) gets resolved
    # now, and the result is persisted so the next cycle skips this.
    if not ats:
        ats, slug = detect(career_url)
        log = log.bind(ats=ats)
        try:
            pc.patch_company(company_id, {"ats": ats, "ats_slug": slug})
        except pc.PlatformError as exc:
            log.warning("jobboard.detect_persist_failed", error=str(exc))

    result = CompanyResult(company_id=company_id, name=name, ok=False, seeded=is_seed)

    try:
        postings = fetch_for(ats, slug, career_url)
        result.seen = len(postings)

        if CONFIG.jobboard_today_only and not is_seed:
            # Never day-filter a seed cycle: the backlog is precisely what we
            # need to record (as not-new) so later cycles can tell what changed.
            postings = _apply_day_filter(postings, now)

        # ── Scrape config ────────────────────────────────────────────────
        # These DISCARD postings rather than filtering them for display, so they
        # fail OPEN: an unparseable country keeps the posting. Age is handled
        # globally by the retention sweep at the end of the cycle, not here.
        matchers = _matchers_for(company)
        # Per-company filter wins; the global list is only a DEFAULT, so one
        # board can be pinned to the US while the rest stay worldwide.
        raw_countries = company.get("countryFilter")
        countries = (
            [str(c).upper() for c in raw_countries]
            if isinstance(raw_countries, list) and raw_countries
            else (default_countries or None)
        )
        # Scrape-wide ceiling, read once per company (cheap: one cached GET).
        ceiling = max_years if max_years is not None else 0

        matched: list[tuple[RawPosting, str]] = []
        for posting in postings:
            role = role_match(posting.title, matchers)
            if not role:
                continue
            if not _country_allowed(posting, countries):
                continue
            if not _experience_allowed(posting, ceiling):
                continue
            matched.append((posting, role))
        result.matched = len(matched)

        # Seed cycle: record the existing board as NOT new, so it is never
        # reported. Every later cycle marks genuinely-unseen postings as new.
        payload = [_to_payload(p, role, is_new=not is_seed) for p, role in matched]
        upserted = pc.upsert_postings(company_id, payload)
        result.new = len(upserted["new_ids"]) if not is_seed else 0

        # Archive anything that vanished. An empty key list is a no-op on the
        # platform side, so a board that returned nothing cannot wipe history.
        live_keys = [item["dedup_key"] for item in payload]
        result.archived = pc.archive_postings(company_id, live_keys)

        patch: dict = {
            "last_checked_at": now.isoformat(),
            "last_status": "ok",
            "last_error": None,  # explicit null clears a previous failure
        }
        if is_seed:
            patch["seeded_at"] = now.isoformat()
        pc.patch_company(company_id, patch)

        result.ok = True
        log.info(
            "jobboard.company_done",
            seen=result.seen,
            matched=result.matched,
            new=result.new,
            archived=result.archived,
        )

    except (AdapterError, pc.PlatformError) as exc:
        result.error = str(exc)[:1000]
        log.warning("jobboard.company_failed", error=result.error)
        try:
            pc.patch_company(
                company_id,
                {
                    "last_checked_at": now.isoformat(),
                    "last_status": "error",
                    "last_error": result.error,
                },
            )
        except pc.PlatformError as patch_exc:
            log.warning("jobboard.status_patch_failed", error=str(patch_exc))

    except Exception as exc:  # noqa: BLE001 — an unexpected bug in one adapter
        result.error = f"{exc.__class__.__name__}: {exc}"[:1000]
        log.error("jobboard.company_crashed", error=result.error, exc_info=True)
        try:
            pc.patch_company(
                company_id,
                {
                    "last_checked_at": now.isoformat(),
                    "last_status": "error",
                    "last_error": result.error,
                },
            )
        except pc.PlatformError:
            pass

    jb_db.record_company(
        run_id,
        watched_company_id=company_id,
        company_name=name,
        ats=ats,
        ok=result.ok,
        postings_seen=result.seen,
        postings_matched=result.matched,
        postings_new=result.new,
        pages_fetched=CONFIG.jobboard_max_pages if ats == GENERIC else 1,
        used_llm=(ats == GENERIC),
        error=result.error,
    )
    return result


def run_monitor_cycle(*, trigger: str = "schedule") -> MonitorSummary:
    """Scrape every active watched company once. Never raises.

    Companies are processed sequentially with a jittered sleep between them —
    right for a watchlist in the tens, which is what this is for.
    """
    run_id = jb_db.start_run("monitor", trigger=trigger)
    summary = MonitorSummary(run_id=run_id)
    log = logger.bind(run_id=run_id, trigger=trigger)
    log.info("jobboard.monitor_start")

    try:
        companies = pc.list_companies(active_only=True)
    except pc.PlatformError as exc:
        # No work list means nothing to do; record it and bail cleanly.
        log.error("jobboard.monitor_no_worklist", error=str(exc))
        jb_db.finish_run(run_id, status="failed", error=str(exc)[:1000])
        return summary

    summary.companies_total = len(companies)
    if not companies:
        log.info("jobboard.monitor_empty_worklist")
        jb_db.finish_run(run_id, status="succeeded", companies_total=0)
        return summary

    now = _utcnow()
    # Read the scrape-wide experience ceiling ONCE per cycle, not per company.
    try:
        ceiling = pc.max_years()
    except pc.PlatformError:
        ceiling = 0  # no limit, rather than risk discarding everything
    try:
        default_countries = pc.global_countries()
    except pc.PlatformError:
        default_countries = []  # anywhere, rather than risk discarding
    for index, company in enumerate(companies):
        result = monitor_company(
            company, run_id, now=now, max_years=ceiling,
            default_countries=default_countries,
        )
        summary.results.append(result)
        summary.postings_seen += result.seen
        summary.postings_matched += result.matched
        summary.postings_new += result.new
        if result.ok:
            summary.companies_ok += 1
        else:
            summary.companies_failed += 1

        # Be a polite client: jitter between companies. Skipped after the last.
        if index < len(companies) - 1:
            time.sleep(
                random.uniform(
                    CONFIG.jobboard_company_sleep_min_s,
                    CONFIG.jobboard_company_sleep_max_s,
                )
            )

    # Retention sweep — ONE pass after all companies, not per company. Archives
    # (never deletes) postings older than the global window, so narrowing the
    # window is reversible: widening it brings them straight back.
    try:
        weeks = pc.retention_weeks()
        archived = pc.archive_stale_postings(weeks)
        if archived:
            log.info("jobboard.retention_swept", weeks=weeks, archived=archived)
    except pc.PlatformError as exc:
        log.warning("jobboard.retention_failed", error=str(exc))

    # Experience sweep — brings STORED postings in line with the current
    # ceiling, so lowering it cleans up history and raising it restores.
    if ceiling:
        try:
            result = pc.apply_experience_threshold(ceiling)
            if result.get("archived") or result.get("restored"):
                log.info("jobboard.experience_swept", max_years=ceiling,
                         archived=result.get("archived"),
                         restored=result.get("restored"))
        except pc.PlatformError as exc:
            log.warning("jobboard.experience_sweep_failed", error=str(exc))

    jb_db.finish_run(
        run_id,
        status="succeeded",
        companies_total=summary.companies_total,
        companies_ok=summary.companies_ok,
        companies_failed=summary.companies_failed,
        postings_seen=summary.postings_seen,
        postings_matched=summary.postings_matched,
        postings_new=summary.postings_new,
    )
    log.info(
        "jobboard.monitor_done",
        companies=summary.companies_total,
        ok=summary.companies_ok,
        failed=summary.companies_failed,
        new=summary.postings_new,
    )
    return summary
