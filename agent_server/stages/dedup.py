"""Deterministic deduplication stage (§2 of the build spec, §0 CONTRACTS.md).

dedup(job_id, candidates) -> list[CandidateCompany]

Steps (in order):
  1. Re-normalize each candidate.domain via normalize_domain; drop None.
  2. Dedup the batch against itself by normalized domain (keep first; if a later
     duplicate has structured funding the first lacked, merge it in).
  3. Filter out domains already in the seen-cache (seen_has).
  4. Batch-check the platform exists endpoint; drop known domains and seed them
     into seen-cache via seen_bulk_add. If the platform is unreachable, log a
     warning and skip the remote check (don't lose the batch).

NEVER delegates to an LLM. Fully deterministic.
"""

from __future__ import annotations

from agent_server.contracts.records import CandidateCompany
from agent_server.db import agent_db
from agent_server.log import get_logger
from agent_server.stages import normalize as _norm
from agent_server.stages import platform_client as _pc

logger = get_logger(__name__)


def dedup(job_id: str, candidates: list[CandidateCompany]) -> list[CandidateCompany]:
    """Return deduplicated, novel candidates ready for the research loop.

    Resilient: a bad candidate never crashes the whole batch — it's silently
    dropped (domain normalizes to None) or skipped.
    """
    if not candidates:
        return []

    # ------------------------------------------------------------------
    # Step 1 + 2: normalize & self-dedup
    # ------------------------------------------------------------------
    seen_domains: dict[str, CandidateCompany] = {}
    dropped_non_domain = 0
    dropped_self_dedup = 0

    for c in candidates:
        try:
            root = _norm.normalize_domain(c.domain)
        except Exception as exc:
            logger.warning(
                "dedup.normalize_error",
                job_id=job_id,
                domain=c.domain,
                error=str(exc),
            )
            dropped_non_domain += 1
            continue

        if root is None:
            dropped_non_domain += 1
            continue

        # Build a fresh CandidateCompany with the normalized domain.
        normalized = c.model_copy(update={"domain": root})

        if root not in seen_domains:
            seen_domains[root] = normalized
        else:
            # Merge structured funding from a later duplicate if the first lacked it.
            existing = seen_domains[root]
            merge: dict = {}
            if existing.funding_stage is None and normalized.funding_stage is not None:
                merge["funding_stage"] = normalized.funding_stage
            if existing.funding_amount is None and normalized.funding_amount is not None:
                merge["funding_amount"] = normalized.funding_amount
            if existing.description is None and normalized.description is not None:
                merge["description"] = normalized.description
            if merge:
                seen_domains[root] = existing.model_copy(update=merge)
            dropped_self_dedup += 1

    logger.info(
        "dedup.self_dedup_done",
        job_id=job_id,
        input=len(candidates),
        after_normalize_dedup=len(seen_domains),
        dropped_non_domain=dropped_non_domain,
        dropped_self_dedup=dropped_self_dedup,
    )

    batch: list[CandidateCompany] = list(seen_domains.values())

    # ------------------------------------------------------------------
    # Step 3: filter against seen-cache (DB)
    # ------------------------------------------------------------------
    novel: list[CandidateCompany] = []
    dropped_seen = 0
    for c in batch:
        try:
            in_cache = agent_db.seen_has(c.domain)
        except Exception as exc:
            logger.warning(
                "dedup.seen_check_error",
                job_id=job_id,
                domain=c.domain,
                error=str(exc),
            )
            # Be conservative: include it so we don't silently lose candidates.
            novel.append(c)
            continue

        if in_cache:
            dropped_seen += 1
        else:
            novel.append(c)

    logger.info(
        "dedup.seen_cache_filter",
        job_id=job_id,
        before=len(batch),
        after=len(novel),
        dropped_seen=dropped_seen,
    )

    if not novel:
        return []

    # ------------------------------------------------------------------
    # Step 4: platform exists check (remote, resilient)
    # ------------------------------------------------------------------
    domains_to_check = [c.domain for c in novel]
    try:
        known = _pc.leads_exists(domains_to_check)
    except Exception as exc:
        # leads_exists itself never raises, but guard anyway
        logger.warning(
            "dedup.platform_check_error",
            job_id=job_id,
            error=str(exc),
        )
        known = []

    if known:
        known_set = set(known)
        # Seed known domains into seen-cache so we skip them next time.
        try:
            agent_db.seen_bulk_add(
                [{"domain": d, "outcome": "verified", "job_id": job_id} for d in known_set]
            )
        except Exception as exc:
            logger.warning(
                "dedup.seen_bulk_add_error",
                job_id=job_id,
                error=str(exc),
            )

        survivors = [c for c in novel if c.domain not in known_set]
        logger.info(
            "dedup.platform_filter",
            job_id=job_id,
            checked=len(domains_to_check),
            known_on_platform=len(known_set),
            survivors=len(survivors),
        )
    else:
        survivors = novel

    return survivors
