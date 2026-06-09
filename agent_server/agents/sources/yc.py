"""YC OSS API connector (free, static JSON, no auth required).

Discovery-agent builder owns this file.

Fetches the full YC company list from CONFIG.yc_oss_url, maps each company to a
CandidateCompany, drops entries whose website normalises to None (social sites,
missing domains, etc.).  Uses httpx directly because the response is structured
JSON, not HTML that needs readability extraction.

Resilient: any network or parsing error -> return [] + log warning.
"""

from __future__ import annotations

from typing import Any

import httpx

from agent_server.agents.deps import AgentDeps
from agent_server.config import CONFIG
from agent_server.contracts.records import CandidateCompany
from agent_server.log import get_logger

log = get_logger(__name__)

_TIMEOUT = 30  # seconds


def fetch_yc_candidates(deps: AgentDeps, *, limit: int = 200) -> list[CandidateCompany]:
    """Return up to *limit* CandidateCompany records from the YC OSS JSON feed.

    Skips companies whose website field normalises to None.
    Returns [] on any network/parsing error (logs a warning).
    """
    url = CONFIG.yc_oss_url
    try:
        resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        companies: list[dict[str, Any]] = resp.json()
    except Exception as exc:
        log.warning("yc_fetch_error", url=url, error=str(exc))
        return []

    candidates: list[CandidateCompany] = []
    for company in companies:
        if len(candidates) >= limit:
            break
        website: str = company.get("website") or company.get("url") or ""
        if not website:
            continue
        domain = deps.normalize_domain(website)
        if domain is None:
            continue

        name: str = company.get("name") or domain
        funding_stage: str | None = company.get("stage") or None
        description: str | None = (
            company.get("one_liner") or company.get("description") or None
        )

        candidates.append(
            CandidateCompany(
                name=name,
                domain=domain,
                source="yc_oss",
                source_url=website,
                funding_stage=funding_stage,
                description=description,
            )
        )

    log.info("yc_candidates_fetched", count=len(candidates))
    return candidates
