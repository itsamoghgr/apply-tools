"""Outbox delivery (deterministic) — CONTRACTS.md §6 delivery rules.

The outbox pattern guarantees no verified lead is ever lost:
  1. write the lead to the agent-DB outbox as `pending` (durable);
  2. attempt the platform upsert (idempotent, keyed on domain);
  3. on success mark the row `sent` and record the domain as `verified` in the
     seen-cache; on a platform outage mark it `failed` (still in the outbox) so
     `retry_pending()` can re-deliver it later.

`dry_run` writes the outbox row but never calls the platform — used by
`POST /api/v1/hunt {dry_run:true}` to exercise the pipeline without pushing.

Shares ONE platform HTTP client with dedup (platform_client.py).
"""

from __future__ import annotations

from agent_server.contracts.records import PlatformUpsertRequest, VerifiedLead
from agent_server.db import agent_db
from agent_server.log import get_logger
from agent_server.stages.platform_client import PlatformUnreachable, leads_upsert

logger = get_logger(__name__)


def deliver(job_id: str, lead: VerifiedLead, dry_run: bool = False) -> None:
    """Persist a verified lead to the outbox and push it to the platform.

    Never raises — a delivery failure leaves a retryable `failed` outbox row and
    is logged, so a single bad push never aborts the orchestrator loop.
    """
    payload = PlatformUpsertRequest.from_verified(lead)
    # 1. Durable write FIRST, so the lead survives a crash before the push.
    outbox_id = agent_db.outbox_add(job_id, lead.domain, payload.model_dump())
    agent_db.audit_add(
        job_id, "deliver", "outbox_queued", {"outbox_id": outbox_id}, domain=lead.domain
    )

    if dry_run:
        logger.info("deliver.dry_run", job_id=job_id, domain=lead.domain, outbox_id=outbox_id)
        return

    _attempt(job_id, outbox_id, lead.domain, payload)


def _attempt(
    job_id: str, outbox_id: int, domain: str, payload: PlatformUpsertRequest
) -> bool:
    """Try one platform upsert for an outbox row. Returns True on delivery."""
    try:
        result = leads_upsert(payload)
    except PlatformUnreachable as exc:
        agent_db.outbox_mark_failed(outbox_id, str(exc))
        agent_db.audit_add(
            job_id, "deliver", "delivery_failed", {"outbox_id": outbox_id, "error": str(exc)},
            domain=domain,
        )
        logger.warning("deliver.failed", job_id=job_id, domain=domain, error=str(exc))
        return False

    agent_db.outbox_mark_sent(outbox_id)
    # Record in the seen-cache so future jobs never re-research this domain.
    agent_db.seen_add(domain, "verified", job_id=job_id)
    agent_db.audit_add(
        job_id, "deliver", "delivered",
        {"outbox_id": outbox_id, "lead_id": result.get("lead_id"), "created": result.get("created")},
        domain=domain,
    )
    logger.info(
        "deliver.sent", job_id=job_id, domain=domain,
        lead_id=result.get("lead_id"), created=result.get("created"),
    )
    return True


def retry_pending(limit: int = 100) -> int:
    """Re-attempt pending/failed outbox rows. Returns the count delivered.

    Idempotent: the platform upsert is keyed on domain (ON CONFLICT updates), so
    re-sending a row that actually made it through last time is harmless.
    """
    rows = agent_db.outbox_retryable(limit=limit)
    delivered = 0
    for row in rows:
        payload = PlatformUpsertRequest(**row["payload"])
        if _attempt(row["job_id"], row["id"], row["domain"], payload):
            delivered += 1
    if rows:
        logger.info("deliver.retry_pending", attempted=len(rows), delivered=delivered)
    return delivered
