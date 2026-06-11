"""RUNNER — bridges FastAPI BackgroundTasks to run_pipeline.

Builds the `Stages` bundle.  `build_stages(job_id)` wires the REAL stages:
agentic leaves (discovery, research) get a job-bound `AgentDeps` carrying the
shared web tools + LLM client; the deterministic stages (dedup, verify, deliver)
are wired directly.  The fully-STUBBED bundle (`build_stub_stages()`) is kept for
tests and for running the whole loop without an LLM key or network.
"""

from __future__ import annotations

import functools
import random

from agent_server.agents.deps import AgentDeps
from agent_server.agents.discovery import run_discovery
from agent_server.agents.llm import AnthropicLLM
from agent_server.agents.research import run_research
from agent_server.contracts.records import (
    CandidateCompany,
    FitVerdict,
    PlatformUpsertRequest,
    ResearchResult,
    VerifiedLead,
)
from agent_server.db.agent_db import (
    audit_add,
    outbox_add,
    outbox_mark_sent,
)
from agent_server.log import get_logger
from agent_server.orchestrator.loop import Stages, run_pipeline
from agent_server.stages.dedup import dedup as _real_dedup
from agent_server.stages.deliver import deliver as _real_deliver
from agent_server.stages.fit_gate import run_fit_gate
from agent_server.stages.normalize import normalize_domain
from agent_server.stages.verify import verify as _real_verify
from agent_server.web import fetch_page, search

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# PHASE 1 STUBS — replaced in Phase 2/3 with real agent implementations
# ---------------------------------------------------------------------------
#
# Each stub is a plain function matching the contract signature in
# CONTRACTS.md §8.  They are fast, fully deterministic (given the same seed),
# and never make network calls.


def _stub_discover(
    job_id: str,
    query_hint: str,
    target: int,
) -> list[CandidateCompany]:
    """Return 80 fake candidates so the loop always has plenty to process.

    Emits more than any realistic `target` so the stop-at-target path is
    exercised end-to-end without needing a real discovery agent.
    """
    count = 80  # always > default target of 50
    logger.bind(job_id=job_id).info("stub_discover", count=count, query_hint=query_hint)
    return [
        CandidateCompany(
            name=f"Fake Company {i}",
            domain=f"fake-{i}.com",
            source="open_web",
            source_url=f"https://fake-{i}.com/about",
            description=f"Stub company number {i}",
        )
        for i in range(1, count + 1)
    ]


def _stub_dedup(
    job_id: str,
    candidates: list[CandidateCompany],
) -> list[CandidateCompany]:
    """Pass-through dedup: drop exact domain duplicates, preserve order.

    A trivial O(n) implementation — the real dedup (Phase 2) will also check
    the seen-cache and normalize domains.
    """
    seen: set[str] = set()
    result: list[CandidateCompany] = []
    for c in candidates:
        if c.domain not in seen:
            seen.add(c.domain)
            result.append(c)
    logger.bind(job_id=job_id).info(
        "stub_dedup", input=len(candidates), output=len(result)
    )
    return result


def _stub_fit_gate(
    job_id: str,
    candidate: CandidateCompany,
    fit_criteria: str,
) -> FitVerdict:
    """Always-pass fit gate for the stub bundle — never skips a candidate.

    Keeps the stubbed pipeline behaving exactly as before the gate existed.
    """
    return FitVerdict(passed=True, score=1.0, reason="stub_pass")


def _stub_research(
    job_id: str,
    candidate: CandidateCompany,
    fit_criteria: str = "",
) -> ResearchResult:
    """Echo the candidate back with a fake founder name.

    The real research agent (Phase 3) will do open-web search + LLM extraction.
    """
    return ResearchResult(
        domain=candidate.domain,
        name=candidate.name,
        funding_stage=candidate.funding_stage,
        funding_amount=candidate.funding_amount,
        founder_name=f"Founder of {candidate.name}",
        founder_linkedin_url=None,  # not queried in stubs
        sources=[candidate.source_url] if candidate.source_url else [],
        used_shortcut=False,
    )


def _stub_verify(
    job_id: str,
    research: ResearchResult,
) -> VerifiedLead:
    """Return a VerifiedLead with a deterministic (hash-based) confidence score.

    The real verifier (Phase 2) will use hunter.io, abstract-api, and SMTP
    probing to produce a real confidence score.
    """
    # Deterministic pseudo-random confidence in [0.5, 0.9] based on the domain.
    rng = random.Random(hash(research.domain))
    confidence = round(rng.uniform(0.5, 0.9), 3)

    return VerifiedLead(
        domain=research.domain,
        name=research.name,
        funding_stage=research.funding_stage,
        funding_amount=research.funding_amount,
        founder_name=research.founder_name,
        founder_linkedin_url=research.founder_linkedin_url,
        founder_email=f"founder@{research.domain}",
        confidence=confidence,
        verification_detail={"stub": True, "confidence_method": "hash_rng"},
        sources=research.sources,
    )


def _stub_deliver(
    job_id: str,
    lead: VerifiedLead,
    dry_run: bool,
) -> None:
    """Write lead to the outbox; if not dry_run, immediately mark it sent.

    In Phase 1 there is no real platform call.  The seam for the real
    platform HTTP client is marked with TODO below.

    TODO (Phase 2): replace the `outbox_mark_sent` shortcut with a real call
    to `stages/deliver.py` which POSTs to PLATFORM_API_BASE/api/v1/leads/upsert
    and only marks sent on HTTP 2xx.
    """
    payload = PlatformUpsertRequest.from_verified(lead).model_dump()
    outbox_id = outbox_add(job_id, lead.domain, payload)

    if not dry_run:
        # Phase 1: no real platform call — mark sent immediately.
        # TODO (Phase 2): replace with actual HTTP upsert to the platform API.
        outbox_mark_sent(outbox_id)
        audit_add(
            job_id,
            "deliver",
            "sent",
            {"domain": lead.domain, "outbox_id": outbox_id, "stub": True},
            domain=lead.domain,
        )
    else:
        audit_add(
            job_id,
            "deliver",
            "dry_run",
            {"domain": lead.domain, "outbox_id": outbox_id},
            domain=lead.domain,
        )

    logger.bind(job_id=job_id).debug(
        "stub_deliver", domain=lead.domain, outbox_id=outbox_id, dry_run=dry_run
    )


# ---------------------------------------------------------------------------
# Stage factories
# ---------------------------------------------------------------------------


def build_stub_stages() -> Stages:
    """Return a fully-stubbed Stages bundle (Phase 1).

    All stages are fast, in-memory, and deterministic.  Safe to call in tests
    without a live LLM or network.
    """
    return Stages(
        discover=_stub_discover,
        dedup=_stub_dedup,
        fit_gate=_stub_fit_gate,
        research=_stub_research,
        verify=_stub_verify,
        deliver=_stub_deliver,
    )


def _build_deps(job_id: str) -> AgentDeps:
    """Assemble the AgentDeps the runtime leaves need, bound to one job.

    `audit` is closed over `job_id` so the agents call deps.audit(stage, event,
    data) without knowing the job. The LLM client is the shared AnthropicLLM.
    """

    def _audit(stage: str, event: str, data: dict) -> None:
        # Resilient: an audit-write failure must never crash an agent.
        try:
            audit_add(job_id, stage, event, data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("audit_write_failed", job_id=job_id, stage=stage, error=str(exc))

    return AgentDeps(
        search=search,
        fetch_page=fetch_page,
        llm=AnthropicLLM(),
        audit=_audit,
        normalize_domain=normalize_domain,
    )


def build_stages(job_id: str) -> Stages:
    """Return the production Stages bundle wired to real implementations.

    The agentic leaves (discover, research) are wrapped with a job-bound
    AgentDeps so they match the loop's stage signatures. The deterministic
    stages are wired directly.
    """
    deps = _build_deps(job_id)

    def discover(jid: str, query_hint: str, target: int) -> list[CandidateCompany]:
        return run_discovery(jid, query_hint=query_hint, target=target, deps=deps)

    def fit_gate(jid: str, candidate: CandidateCompany, fit_criteria: str) -> FitVerdict:
        return run_fit_gate(jid, candidate, fit_criteria, deps=deps)

    def research(
        jid: str, candidate: CandidateCompany, fit_criteria: str = ""
    ) -> ResearchResult:
        return run_research(jid, candidate, deps, fit_criteria)

    return Stages(
        discover=discover,
        dedup=_real_dedup,
        fit_gate=fit_gate,
        research=research,
        verify=_real_verify,
        deliver=_real_deliver,
    )


# ---------------------------------------------------------------------------
# Background-task entry point (called from api/app.py)
# ---------------------------------------------------------------------------


def launch_pipeline(
    job_id: str,
    *,
    query_hint: str,
    target: int,
    dry_run: bool,
    fit_criteria: str = "",
) -> None:
    """Entry point for FastAPI BackgroundTasks.

    Builds the stage bundle and delegates to run_pipeline.  `fit_criteria` is the
    user's ICP for the cheap fit gate (empty → pass-through, no skipping). Errors
    are already caught inside run_pipeline (job marked failed); this function
    never raises.
    """
    logger.info(
        "launch_pipeline",
        job_id=job_id,
        target=target,
        dry_run=dry_run,
        query_hint=query_hint,
        has_fit_criteria=bool(fit_criteria and fit_criteria.strip()),
    )
    stages = build_stages(job_id)
    run_pipeline(
        job_id,
        query_hint=query_hint,
        target=target,
        dry_run=dry_run,
        stages=stages,
        fit_criteria=fit_criteria,
    )
