"""Product Hunt GraphQL connector.

Discovery-agent builder owns this file.

Queries the PH GraphQL API for recent posts and maps each product's website to a
CandidateCompany.  If CONFIG.product_hunt_token is not set, returns [] (logged at
info level — it is a soft optional source).

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

_PH_API_URL = "https://api.producthunt.com/v2/api/graphql"
_TIMEOUT = 20  # seconds

# GraphQL query — fetches the most recent `limit` posts with their website field.
_QUERY = """
query RecentPosts($first: Int!) {
  posts(first: $first, order: NEWEST) {
    edges {
      node {
        name
        tagline
        website
        fundingType: tagline
        topics {
          edges {
            node { name }
          }
        }
      }
    }
  }
}
"""


def fetch_producthunt_candidates(
    deps: AgentDeps, *, limit: int = 50
) -> list[CandidateCompany]:
    """Return up to *limit* CandidateCompany records from the Product Hunt API.

    Returns [] immediately if no token is configured, and [] on any error.
    """
    if not CONFIG.product_hunt_token:
        log.info("producthunt_skipped", reason="no_token")
        return []

    headers = {
        "Authorization": f"Bearer {CONFIG.product_hunt_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload: dict[str, Any] = {
        "query": _QUERY,
        "variables": {"first": min(limit, 50)},  # PH caps at 50 per page
    }

    try:
        resp = httpx.post(
            _PH_API_URL, json=payload, headers=headers, timeout=_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("producthunt_fetch_error", error=str(exc))
        return []

    if "errors" in data:
        log.warning("producthunt_graphql_errors", errors=data["errors"])
        return []

    edges = (
        data.get("data", {}).get("posts", {}).get("edges") or []
    )

    candidates: list[CandidateCompany] = []
    for edge in edges:
        node = edge.get("node") or {}
        website: str = node.get("website") or ""
        if not website:
            continue
        domain = deps.normalize_domain(website)
        if domain is None:
            continue

        candidates.append(
            CandidateCompany(
                name=node.get("name") or domain,
                domain=domain,
                source="product_hunt",
                source_url=website,
                description=node.get("tagline") or None,
            )
        )

    log.info("producthunt_candidates_fetched", count=len(candidates))
    return candidates
