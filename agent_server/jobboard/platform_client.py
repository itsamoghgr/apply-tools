"""HTTP client for the platform's Job Board endpoints (backend/, port 8001).

The agent service never opens a connection to the platform database — all
WatchedCompany / JobPosting / AlertRun access goes through here, the same rule
stages/platform_client.py follows for leads.

Every call raises PlatformError on failure rather than returning a sentinel: a
monitor cycle that cannot read its work list or persist its findings has nothing
useful to do, and the caller records the failure on the run row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from agent_server.config import CONFIG
from agent_server.log import get_logger

logger = get_logger(__name__)

_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 30.0

# Matches the platform's max_length on JobPostingsUpsertRequest.postings. A
# single Lever board can exceed this (palantir: 307 postings), so the monitor
# chunks rather than letting the request 422.
UPSERT_CHUNK = 500


class PlatformError(Exception):
    """The platform API could not be reached or returned an error status."""


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if CONFIG.platform_api_token:
        headers["X-Agent-Token"] = CONFIG.platform_api_token
    return headers


def _base() -> str:
    return CONFIG.platform_api_base.rstrip("/")


def _request(method: str, path: str, **kwargs) -> Any:
    url = f"{_base()}{path}"
    try:
        resp = httpx.request(
            method,
            url,
            headers=_headers(),
            timeout=httpx.Timeout(_CONNECT_TIMEOUT, read=_READ_TIMEOUT),
            **kwargs,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:  # noqa: BLE001
            detail = exc.response.text[:200]
        raise PlatformError(f"{method} {path} -> HTTP {exc.response.status_code}: {detail}") from exc
    except httpx.HTTPError as exc:
        raise PlatformError(f"{method} {path} -> {exc}") from exc


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def list_companies(active_only: bool = True) -> list[dict]:
    """The monitor's work list."""
    data = _request("GET", "/api/v1/jobboard/companies", params={"active": active_only})
    return data.get("companies", [])


def patch_company(company_id: str, fields: dict) -> None:
    """Stamp per-cycle status. Only the supplied keys are written.

    An explicit None clears a column (that is how lastError is reset once a
    previously-failing board recovers); an omitted key leaves it alone.
    """
    _request("PATCH", f"/api/v1/jobboard/companies/{company_id}", json=fields)


def upsert_postings(company_id: str, postings: list[dict]) -> dict:
    """Bulk-upsert one company's postings, chunked to the platform's cap.

    Returns aggregated ``{"inserted", "updated", "new_ids"}`` across chunks.
    """
    total = {"inserted": 0, "updated": 0, "new_ids": []}
    if not postings:
        return total
    for start in range(0, len(postings), UPSERT_CHUNK):
        chunk = postings[start : start + UPSERT_CHUNK]
        data = _request(
            "POST",
            "/api/v1/jobboard/postings/upsert",
            json={"company_id": company_id, "postings": chunk},
        )
        total["inserted"] += data.get("inserted", 0)
        total["updated"] += data.get("updated", 0)
        total["new_ids"].extend(data.get("new_ids", []))
    return total


def archive_postings(company_id: str, live_dedup_keys: list[str]) -> int:
    """Archive postings absent from the latest fetch. An empty list is a no-op."""
    data = _request(
        "POST",
        "/api/v1/jobboard/postings/archive",
        json={"company_id": company_id, "live_dedup_keys": live_dedup_keys},
    )
    return data.get("archived", 0)


def unalerted_postings(since: datetime | None = None) -> list[dict]:
    params = {"since": since.isoformat()} if since else None
    data = _request("GET", "/api/v1/jobboard/postings/unalerted", params=params)
    return data.get("postings", [])


def alert_watermark() -> datetime | None:
    """finishedAt of the last SENT alert, or None before the first send."""
    data = _request("GET", "/api/v1/jobboard/alert-runs/watermark")
    raw = data.get("watermark")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def create_alert_run(window_start: datetime | None, window_end: datetime) -> str:
    data = _request(
        "POST",
        "/api/v1/jobboard/alert-runs",
        json={"window_start": _iso(window_start), "window_end": _iso(window_end)},
    )
    return data["id"]


def close_alert_run(
    run_id: str,
    *,
    status: str,
    new_count: int = 0,
    company_count: int = 0,
    posting_ids: list[str] | None = None,
    error: str | None = None,
) -> None:
    _request(
        "POST",
        f"/api/v1/jobboard/alert-runs/{run_id}/close",
        json={
            "status": status,
            "new_count": new_count,
            "company_count": company_count,
            "posting_ids": posting_ids or [],
            "error": error,
        },
    )


# ---------------------------------------------------------------------------
# Global settings (platform Setting table)
# ---------------------------------------------------------------------------

RETENTION_KEY = "jobboard.retentionWeeks"
DEFAULT_RETENTION_WEEKS = 4

COUNTRIES_KEY = "jobboard.countries"

MAX_YEARS_KEY = "jobboard.maxYears"
# 0 means "no limit" rather than "zero years": an unset threshold must not
# silently reject everything.
NO_EXPERIENCE_LIMIT = 0


def retention_weeks() -> int:
    """How many weeks of postings to keep in the live feed.

    A SCRAPE-LEVEL setting, not per-company: it is about how much history you
    want to look at, which is a property of you rather than of any one board.
    Falls back to the default when unset or unreadable, so a settings outage
    never widens or narrows the window unexpectedly.
    """
    try:
        data = _request("GET", f"/settings/{RETENTION_KEY}")
        value = int(data.get("value") or DEFAULT_RETENTION_WEEKS)
        return value if 1 <= value <= 52 else DEFAULT_RETENTION_WEEKS
    except (PlatformError, TypeError, ValueError):
        return DEFAULT_RETENTION_WEEKS


def archive_stale_postings(weeks: int) -> int:
    """Archive postings older than `weeks`. Returns how many were archived."""
    data = _request("POST", "/api/v1/jobboard/postings/archive-stale",
                    json={"weeks": weeks})
    return data.get("archived", 0)


def max_years() -> int:
    """Maximum years of experience to store, scrape-wide.

    Returns NO_EXPERIENCE_LIMIT (0) when unset or unreadable, meaning no limit —
    a settings outage must never start silently discarding roles.
    """
    try:
        data = _request("GET", f"/settings/{MAX_YEARS_KEY}")
        raw = data.get("value")
        value = int(raw) if raw else NO_EXPERIENCE_LIMIT
        return value if 0 <= value <= 50 else NO_EXPERIENCE_LIMIT
    except (PlatformError, TypeError, ValueError):
        return NO_EXPERIENCE_LIMIT


def apply_experience_threshold(max_years_value: int) -> dict:
    """Archive postings over the threshold and restore any that now fit."""
    return _request("POST", "/api/v1/jobboard/postings/apply-experience-threshold",
                    json={"max_years": max_years_value})


def global_countries() -> list[str]:
    """Scrape-wide country allow-list (ISO alpha-2), or [] for anywhere.

    A DEFAULT, not an override: a company with its own countryFilter keeps it,
    so you can watch everything globally but pin one board to the US. Returns []
    on any failure — a settings outage must never start discarding roles.
    """
    try:
        data = _request("GET", f"/settings/{COUNTRIES_KEY}")
        raw = (data.get("value") or "").strip()
        if not raw:
            return []
        codes = [c.strip().upper() for c in raw.split(",") if c.strip()]
        # Two letters is necessary but not sufficient — "XX" is well-formed and
        # meaningless. Validate against the codes the parser can actually
        # produce, so a typo can't quietly filter every posting away.
        from agent_server.jobboard.location import KNOWN_COUNTRY_CODES

        return [c for c in codes if c in KNOWN_COUNTRY_CODES]
    except (PlatformError, TypeError, ValueError, AttributeError):
        return []
