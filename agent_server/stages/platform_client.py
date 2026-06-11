"""Tiny httpx-based client for the platform API (§6 of CONTRACTS.md).

Two methods:
  - leads_exists(domains)  → list of known domains ([] + warning on error)
  - leads_upsert(payload)  → response dict (raises PlatformUnreachable on error)

Reads CONFIG.platform_api_base and CONFIG.platform_api_token. Shared by both
dedup.py and deliver.py — single client, single place to change timeouts/auth.
"""

from __future__ import annotations

import httpx

from agent_server.config import CONFIG
from agent_server.contracts.records import PlatformUpsertRequest
from agent_server.log import get_logger

logger = get_logger(__name__)

# Default timeouts (connect, read) in seconds.
_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 10.0


class PlatformUnreachable(Exception):
    """Raised by leads_upsert when the platform cannot be reached or returns 5xx."""


def _headers() -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if CONFIG.platform_api_token:
        headers["X-Agent-Token"] = CONFIG.platform_api_token
    return headers


def _base() -> str:
    return CONFIG.platform_api_base.rstrip("/")


def leads_exists(domains: list[str]) -> list[str]:
    """POST /api/v1/leads/exists — return list of known domains.

    On any network error or non-2xx response: logs a warning and returns [].
    The caller (dedup) must handle this gracefully (skip remote check, keep batch).
    """
    if not domains:
        return []

    url = f"{_base()}/api/v1/leads/exists"
    try:
        resp = httpx.post(
            url,
            json={"domains": domains},
            headers=_headers(),
            timeout=httpx.Timeout(_CONNECT_TIMEOUT, read=_READ_TIMEOUT),
        )
        resp.raise_for_status()
        data = resp.json()
        known: list[str] = data.get("known", [])
        logger.info(
            "platform.exists_check",
            queried=len(domains),
            known=len(known),
        )
        return known
    except Exception as exc:
        logger.warning(
            "platform.exists_unreachable",
            url=url,
            error=str(exc),
        )
        return []


def leads_upsert(payload: PlatformUpsertRequest) -> dict:
    """POST /api/v1/leads/upsert — returns the response dict.

    Raises PlatformUnreachable on any network error or 5xx so the caller
    (deliver) can mark the outbox row as failed and retry later.
    """
    url = f"{_base()}/api/v1/leads/upsert"
    try:
        resp = httpx.post(
            url,
            json=payload.model_dump(),
            headers=_headers(),
            timeout=httpx.Timeout(_CONNECT_TIMEOUT, read=_READ_TIMEOUT),
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(
            "platform.upserted",
            domain=payload.domain,
            lead_id=result.get("lead_id"),
            created=result.get("created"),
        )
        return result
    except httpx.HTTPStatusError as exc:
        raise PlatformUnreachable(
            f"Platform returned HTTP {exc.response.status_code} for {payload.domain}: "
            f"{exc.response.text[:200]}"
        ) from exc
    except Exception as exc:
        raise PlatformUnreachable(
            f"Platform unreachable for {payload.domain}: {exc}"
        ) from exc
