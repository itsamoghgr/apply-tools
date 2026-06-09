"""FROZEN stage record shapes (see CONTRACTS.md §1).

These Pydantic v2 models are the records passed BETWEEN pipeline stages. Every
sub-agent imports from here. Do not change a field without routing through the
lead engineer — multiple stages depend on each shape.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CandidateCompany(BaseModel):
    """Output of discovery, input to dedup. Keyed by normalized `domain`.

    Noisy extraction from freeform articles is expected; dedup + verification
    clean it up downstream.
    """

    name: str
    domain: str  # NORMALIZED root domain, e.g. "acme.com"
    source: str  # "open_web" | "yc_oss" | "product_hunt" | "rss"
    source_url: str | None = None
    # Structured-floor enrichment — research may SHORTCUT on these:
    funding_stage: str | None = None
    funding_amount: str | None = None
    description: str | None = None
    discovered_at: datetime = Field(default_factory=_utcnow)


class ResearchResult(BaseModel):
    """Output of the research agent, input to verification."""

    domain: str
    name: str
    funding_stage: str | None = None
    funding_amount: str | None = None
    founder_name: str | None = None
    # From PUBLIC SEARCH SNIPPETS ONLY; the profile page is never read.
    founder_linkedin_url: str | None = None
    sources: list[str] = Field(default_factory=list)
    used_shortcut: bool = False


class VerifiedLead(BaseModel):
    """Output of verification, input to delivery.

    `confidence` is a continuous 0.0–1.0 SCORE, never a bool. Delivery sends
    regardless; the platform records the score.
    """

    domain: str
    name: str
    funding_stage: str | None = None
    funding_amount: str | None = None
    founder_name: str | None = None
    founder_linkedin_url: str | None = None
    founder_email: str | None = None
    confidence: float
    verification_detail: dict[str, Any] = Field(default_factory=dict)
    sources: list[str] = Field(default_factory=list)


class PlatformUpsertRequest(BaseModel):
    """Body the agent server POSTs to the platform /api/v1/leads/upsert.

    Idempotent, keyed on `domain` (ON CONFLICT (domain) DO UPDATE).
    """

    domain: str
    company_name: str
    funding_stage: str | None = None
    funding_amount: str | None = None
    founder_name: str | None = None
    founder_linkedin_url: str | None = None
    founder_email: str | None = None
    confidence: float
    source: str = "agent-server"
    sources: list[str] = Field(default_factory=list)

    @classmethod
    def from_verified(cls, lead: VerifiedLead) -> "PlatformUpsertRequest":
        return cls(
            domain=lead.domain,
            company_name=lead.name,
            funding_stage=lead.funding_stage,
            funding_amount=lead.funding_amount,
            founder_name=lead.founder_name,
            founder_linkedin_url=lead.founder_linkedin_url,
            founder_email=lead.founder_email,
            confidence=lead.confidence,
            sources=lead.sources,
        )
