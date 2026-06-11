"""Frozen contracts shared across all stages. See CONTRACTS.md."""

from agent_server.contracts.records import (
    CandidateCompany,
    PlatformUpsertRequest,
    ResearchResult,
    VerifiedLead,
)

__all__ = [
    "CandidateCompany",
    "ResearchResult",
    "VerifiedLead",
    "PlatformUpsertRequest",
]
