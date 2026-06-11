"""ORCHESTRATOR LOOP — deterministic pipeline trunk (see CONTRACTS.md §0/§8).

This is PLAIN CODE, never an LLM. It owns: counter, cursor, sleep, stop-logic,
dedup invocation, checkpoint writing, job-status updates, and error handling.
The agentic leaves (discover, research) are injected via the `Stages` bundle.

Usage::

    from agent_server.orchestrator.loop import run_pipeline, Stages

    run_pipeline(
        job_id,
        query_hint="B2B SaaS seed-funded 2024",
        target=50,
        dry_run=False,
        stages=stages_bundle,
    )
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from agent_server.config import CONFIG
from agent_server.contracts.records import (
    CandidateCompany,
    FitVerdict,
    ResearchResult,
    VerifiedLead,
)
from agent_server.db.agent_db import (
    add_checkpoint,
    audit_add,
    seen_add,
    update_job,
)
from agent_server.log import get_logger

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Stage bundle
# ---------------------------------------------------------------------------


@dataclass
class Stages:
    """Holds all injectable stage callables.

    The orchestrator calls each in order; actual implementations (stubs in
    Phase 1, real agents in Phase 3) are provided by the runner.

    Signatures (see CONTRACTS.md §8):
      discover(job_id, query_hint, target)        -> list[CandidateCompany]
      dedup(job_id, candidates)                   -> list[CandidateCompany]
      fit_gate(job_id, candidate, fit_criteria)   -> FitVerdict
      research(job_id, candidate, fit_criteria)   -> ResearchResult
      verify(job_id, research_result)             -> VerifiedLead
      deliver(job_id, verified_lead, dry_run)     -> None
    """

    discover: Callable[[str, str, int], list[CandidateCompany]]
    dedup: Callable[[str, list[CandidateCompany]], list[CandidateCompany]]
    fit_gate: Callable[[str, CandidateCompany, str], FitVerdict]
    research: Callable[[str, CandidateCompany, str], ResearchResult]
    verify: Callable[[str, ResearchResult], VerifiedLead]
    deliver: Callable[[str, VerifiedLead, bool], None]


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------


def run_pipeline(
    job_id: str,
    *,
    query_hint: str,
    target: int,
    dry_run: bool,
    stages: Stages,
    fit_criteria: str = "",
) -> None:
    """Drive the full lead-generation pipeline for one job.

    Steps (exactly per CONTRACTS.md §0):
      1. Discover candidates.
      2. Dedup survivors.
      3. Set candidates_total; mark job running.
      4. Iterate survivors with a cursor:
           - research -> verify -> deliver
           - increment verified_count
           - checkpoint the cursor
           - sleep a short random interval
           - stop when verified_count >= target OR cursor exhausts the list
      5. Mark job succeeded (or failed on any exception).

    Never indexes past the end of the candidates list. On any exception:
    marks the job failed with the error text, logs it, and re-raises
    only if you need the caller to know — here we log but do NOT re-raise
    (background task; crashing the process is worse than a failed job).
    """
    log = logger.bind(job_id=job_id, target=target, dry_run=dry_run)
    log.info("pipeline_start", query_hint=query_hint)

    try:
        # ------------------------------------------------------------------
        # Phase 1: discover
        # ------------------------------------------------------------------
        update_job(job_id, status="running")
        audit_add(job_id, "discovery", "start", {"query_hint": query_hint, "target": target})

        candidates: list[CandidateCompany] = stages.discover(job_id, query_hint, target)
        log.info("discover_done", raw_count=len(candidates))
        audit_add(
            job_id,
            "discovery",
            "done",
            {"raw_count": len(candidates)},
        )
        add_checkpoint(
            job_id,
            "discovery",
            cursor=None,
            state={"raw_count": len(candidates)},
        )

        # ------------------------------------------------------------------
        # Phase 2: dedup
        # ------------------------------------------------------------------
        audit_add(job_id, "dedup", "start", {"input_count": len(candidates)})
        survivors: list[CandidateCompany] = stages.dedup(job_id, candidates)
        log.info("dedup_done", survivors=len(survivors))
        audit_add(job_id, "dedup", "done", {"survivors": len(survivors)})
        add_checkpoint(
            job_id,
            "dedup",
            cursor=None,
            state={"survivors": len(survivors)},
        )

        # Set total and reset processed counter now that we know the list.
        candidates_total = len(survivors)
        update_job(
            job_id,
            candidates_total=candidates_total,
            candidates_processed=0,
            verified_count=0,
        )

        # ------------------------------------------------------------------
        # Phase 3: iterate survivors with cursor
        # ------------------------------------------------------------------
        verified_count = 0
        skipped_count = 0
        stop_reason: str | None = None

        for cursor, candidate in enumerate(survivors):
            # Guard: stop as soon as we've hit the target.
            if verified_count >= target:
                stop_reason = "target_reached"
                break

            domain = candidate.domain
            loop_log = log.bind(cursor=cursor, domain=domain)
            loop_log.info("loop_iteration")
            audit_add(
                job_id,
                "loop",
                "iteration_start",
                {"cursor": cursor, "domain": domain},
                domain=domain,
            )

            try:
                # -- fit gate (cheap pre-filter) --
                # Score the candidate against the user's ICP BEFORE the expensive
                # deep-research pass. On FAIL: record the domain in the seen-cache
                # (so future hunts never re-surface it), audit the skip, bump the
                # skipped counter, and move on WITHOUT researching or saving it.
                # The gate never raises; with empty fit_criteria it passes through.
                verdict: FitVerdict = stages.fit_gate(job_id, candidate, fit_criteria)
                if not verdict.passed:
                    seen_add(domain, "skipped", reason=f"fit:{verdict.score:.2f}", job_id=job_id)
                    audit_add(
                        job_id,
                        "fit",
                        "skipped",
                        {
                            "domain": domain,
                            "score": verdict.score,
                            "reason": verdict.reason,
                        },
                        domain=domain,
                    )
                    skipped_count += 1
                    update_job(
                        job_id,
                        candidates_processed=cursor + 1,
                        skipped_count=skipped_count,
                    )
                    loop_log.info("fit_skipped", domain=domain, score=verdict.score)
                    continue
                audit_add(
                    job_id,
                    "fit",
                    "passed",
                    {"domain": domain, "score": verdict.score, "reason": verdict.reason},
                    domain=domain,
                )

                # -- research --
                research_result: ResearchResult = stages.research(
                    job_id, candidate, fit_criteria
                )
                audit_add(
                    job_id,
                    "research",
                    "done",
                    {
                        "domain": domain,
                        "founder": research_result.founder_name,
                        "used_shortcut": research_result.used_shortcut,
                    },
                    domain=domain,
                )

                # -- verify --
                verified_lead: VerifiedLead = stages.verify(job_id, research_result)
                audit_add(
                    job_id,
                    "verify",
                    "done",
                    {"domain": domain, "confidence": verified_lead.confidence},
                    domain=domain,
                )

                # -- deliver --
                stages.deliver(job_id, verified_lead, dry_run)
                audit_add(
                    job_id,
                    "deliver",
                    "done",
                    {"domain": domain, "dry_run": dry_run},
                    domain=domain,
                )

                verified_count += 1

            except Exception as stage_exc:  # noqa: BLE001
                # A per-candidate failure is logged but does NOT abort the whole run.
                loop_log.error(
                    "candidate_failed",
                    domain=domain,
                    error=str(stage_exc),
                    exc_info=True,
                )
                audit_add(
                    job_id,
                    "loop",
                    "candidate_error",
                    {"domain": domain, "error": str(stage_exc)},
                    domain=domain,
                )

            # Update progress regardless of per-candidate outcome.
            update_job(
                job_id,
                candidates_processed=cursor + 1,
                verified_count=verified_count,
            )
            add_checkpoint(
                job_id,
                "loop",
                cursor=cursor,
                state={
                    "domain": domain,
                    "verified_count": verified_count,
                    "candidates_processed": cursor + 1,
                },
            )

            # Stop check AFTER updating counts (may have just hit the target).
            if verified_count >= target:
                stop_reason = "target_reached"
                break

            # Sleep a random interval to avoid thundering-herd behaviour.
            sleep_s = random.uniform(CONFIG.loop_sleep_min_s, CONFIG.loop_sleep_max_s)
            loop_log.debug("loop_sleep", sleep_s=round(sleep_s, 3))
            time.sleep(sleep_s)

        # If we exhausted the list without reaching the target:
        if stop_reason is None:
            stop_reason = "exhausted"

        log.info(
            "pipeline_done",
            stop_reason=stop_reason,
            verified_count=verified_count,
            skipped_count=skipped_count,
            candidates_total=candidates_total,
        )
        audit_add(
            job_id,
            "loop",
            "pipeline_done",
            {
                "stop_reason": stop_reason,
                "verified_count": verified_count,
                "skipped_count": skipped_count,
                "candidates_total": candidates_total,
            },
        )

        update_job(
            job_id,
            status="succeeded",
            verified_count=verified_count,
            skipped_count=skipped_count,
            stop_reason=stop_reason,
            finished_at=_utcnow(),
        )

    except Exception as exc:  # noqa: BLE001
        # Top-level failure — mark the job failed and log; do NOT re-raise
        # (this runs as a background task; crashing the process is worse).
        error_msg = str(exc)
        log.error("pipeline_failed", error=error_msg, exc_info=True)
        try:
            update_job(
                job_id,
                status="failed",
                error=error_msg,
                stop_reason="error",
                finished_at=_utcnow(),
            )
            audit_add(job_id, "loop", "pipeline_error", {"error": error_msg})
        except Exception:  # noqa: BLE001
            # If even the DB write fails, just log it and move on.
            log.error("pipeline_failed_db_write_error", exc_info=True)
