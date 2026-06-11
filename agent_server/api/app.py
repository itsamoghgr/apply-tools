"""FastAPI application — Agent Server HTTP API (see CONTRACTS.md §5).

Endpoints:
  POST /api/v1/hunt      — start a hunt job (202 + job_id immediately)
  GET  /api/v1/hunt/{id} — poll job status (404 if unknown)
  GET  /health           — liveness probe (also accepts HEAD)
  HEAD /health           — same

The pipeline runs as a FastAPI BackgroundTask so the POST returns immediately.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncio
import json

from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agent_server.config import CONFIG
from agent_server.db.agent_db import audit_since, create_job, get_job, seen_add
from agent_server.log import configure_logging, get_logger
from agent_server.orchestrator.runner import launch_pipeline
from agent_server.stages.normalize import normalize_domain

# Configure structlog at import time (idempotent).
configure_logging()

logger = get_logger(__name__)

app = FastAPI(
    title="Lead-Generation Agent Server",
    version="0.1.0",
    description="Phase-1 skeleton: full pipeline with stubbed stages.",
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class HuntRequest(BaseModel):
    """Body for POST /api/v1/hunt."""

    target_count: int = Field(
        default_factory=lambda: CONFIG.target_count,
        ge=1,
        description="How many verified leads to collect before stopping.",
    )
    query_hint: str = Field(
        default="",
        description="Optional free-text hint passed to the discovery agent.",
    )
    fit_criteria: str = Field(
        default="",
        description=(
            "Optional ICP for the cheap fit gate; low-fit companies are skipped "
            "before deep research. Defaults to query_hint; if both are empty the "
            "gate degrades to pass-through (no skipping)."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description="If true, leads go to the outbox only — no platform push.",
    )


class HuntAccepted(BaseModel):
    """Immediate 202 response for POST /api/v1/hunt."""

    job_id: str
    status: str = "pending"


class JobStatus(BaseModel):
    """Response for GET /api/v1/hunt/{job_id}."""

    job_id: str
    status: str
    verified_count: int | None
    skipped_count: int | None
    target_count: int | None
    candidates_total: int | None
    candidates_processed: int | None
    stop_reason: str | None
    created_at: str | None
    updated_at: str | None
    finished_at: str | None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post(
    "/api/v1/hunt",
    status_code=202,
    response_model=HuntAccepted,
    summary="Start a new lead-generation hunt",
)
def start_hunt(
    body: HuntRequest,
    background_tasks: BackgroundTasks,
) -> HuntAccepted:
    """Accept a hunt request, create a job row, and schedule the pipeline.

    Returns 202 immediately — poll GET /api/v1/hunt/{job_id} for progress.
    """
    job_id = create_job(target_count=body.target_count)
    logger.info(
        "hunt_accepted",
        job_id=job_id,
        target_count=body.target_count,
        dry_run=body.dry_run,
        query_hint=body.query_hint,
    )

    background_tasks.add_task(
        launch_pipeline,
        job_id,
        query_hint=body.query_hint,
        target=body.target_count,
        dry_run=body.dry_run,
        # Default the ICP to query_hint; if BOTH are empty the gate is pass-through.
        fit_criteria=body.fit_criteria or body.query_hint,
    )

    return HuntAccepted(job_id=job_id, status="pending")


@app.get(
    "/api/v1/hunt/{job_id}",
    response_model=JobStatus,
    responses={404: {"description": "Job not found"}},
    summary="Poll hunt job status",
)
def get_hunt(job_id: str) -> Any:
    """Return the current state of a hunt job, or 404 if the ID is unknown."""
    row = get_job(job_id)
    if row is None:
        logger.info("hunt_not_found", job_id=job_id)
        return JSONResponse(status_code=404, content={"detail": "Job not found"})

    return JobStatus(
        job_id=row["id"],
        status=row["status"],
        verified_count=row.get("verified_count"),
        skipped_count=row.get("skipped_count"),
        target_count=row.get("target_count"),
        candidates_total=row.get("candidates_total"),
        candidates_processed=row.get("candidates_processed"),
        stop_reason=row.get("stop_reason"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        finished_at=row.get("finished_at"),
    )


_TERMINAL = {"succeeded", "failed", "stopped"}


@app.get(
    "/api/v1/hunt/{job_id}/events",
    summary="Live SSE stream of agent activity for a hunt",
)
async def hunt_events(job_id: str) -> Any:
    """Server-Sent Events: stream audit traces as the agent works.

    Each `event: activity` carries one audit row (stage, event, domain, data).
    A periodic `event: status` carries job counters so the client can update its
    progress bar. The stream closes with `event: done` once the job is terminal.
    The client reconnects with no state — we always start from the beginning so a
    late-opened stream still shows the full timeline.
    """

    async def gen():
        last_id = 0
        # Replay from the start so opening the stream mid-run shows history.
        while True:
            rows = await asyncio.to_thread(audit_since, job_id, last_id)
            for r in rows:
                last_id = r["id"]
                payload = {
                    "id": r["id"],
                    "stage": r["stage"],
                    "event": r["event"],
                    "domain": r.get("domain"),
                    "data": r.get("data"),
                    "created_at": r.get("created_at"),
                }
                yield f"event: activity\ndata: {json.dumps(payload)}\n\n"

            job = await asyncio.to_thread(get_job, job_id)
            if job is None:
                yield f'event: error\ndata: {json.dumps({"detail": "job not found"})}\n\n'
                return

            status = {
                "status": job["status"],
                "verified_count": job.get("verified_count"),
                "skipped_count": job.get("skipped_count"),
                "target_count": job.get("target_count"),
                "candidates_total": job.get("candidates_total"),
                "candidates_processed": job.get("candidates_processed"),
                "stop_reason": job.get("stop_reason"),
            }
            yield f"event: status\ndata: {json.dumps(status)}\n\n"

            if job["status"] in _TERMINAL:
                yield f"event: done\ndata: {json.dumps(status)}\n\n"
                return

            await asyncio.sleep(1.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering
        },
    )


class VerifyEmailRequest(BaseModel):
    """Body for POST /api/v1/verify/email."""

    domain: str | None = Field(
        default=None, description="Company root domain (preferred)."
    )
    company: str | None = Field(
        default=None, description="Company name (used to derive a domain if needed)."
    )
    founder_name: str | None = Field(
        default=None, description="Person to find the email for."
    )


@app.post(
    "/api/v1/verify/email",
    summary="Find + verify an email on demand (agentic contact-finder)",
)
def verify_email(body: VerifyEmailRequest) -> dict[str, Any]:
    """Find + verify the best work email for one person + domain.

    Uses the agentic contact-finder (think/plan/act over web_search +
    guess_email_patterns + the provider waterfall) when an LLM is configured;
    falls back to the plain waterfall otherwise. Returns the best email, a 0–1
    score, the method/source, and the agent's rationale. email=null (not an
    error) when nothing is found.
    """
    from agent_server.stages.verify import WaterfallVerifier

    domain = body.domain and (normalize_domain(body.domain) or body.domain.strip())
    if not domain and body.company:
        # Best-effort: turn "Acme Inc" into "acme.com" as a guess.
        slug = "".join(ch for ch in body.company.lower() if ch.isalnum())
        domain = f"{slug}.com" if slug else None
    if not domain:
        return {"email": None, "score": 0.0, "method": "none", "domain": None,
                "rationale": "no domain to search"}

    # Try the agentic finder first; fall back to the deterministic waterfall if
    # no LLM is configured or the agent errors.
    try:
        from agent_server.agents.contact import find_contact
        from agent_server.agents.deps import AgentDeps
        from agent_server.agents.llm import AnthropicLLM
        from agent_server.stages.normalize import normalize_domain as _nd
        from agent_server.web import fetch_page, search

        deps = AgentDeps(
            search=search,
            fetch_page=fetch_page,
            llm=AnthropicLLM(),
            audit=lambda *a, **k: None,  # on-demand: no job to attach traces to
            normalize_domain=_nd,
        )
        res = find_contact(domain, body.founder_name, deps)
        if res.email:
            logger.info("verify_email_agentic", domain=domain, method=res.method,
                        score=res.score)
            return {"email": res.email, "score": res.score, "method": res.method,
                    "domain": domain, "rationale": res.rationale}
    except Exception as exc:
        logger.warning("verify_email_agent_unavailable", domain=domain, error=str(exc))

    verdict = WaterfallVerifier().find_and_verify(domain, body.founder_name)
    logger.info("verify_email_waterfall", domain=domain, found=bool(verdict.email),
                method=verdict.method, score=verdict.score)
    return {"email": verdict.email, "score": verdict.score, "method": verdict.method,
            "domain": domain, "rationale": "deterministic waterfall"}


class RosterRequest(BaseModel):
    """Body for POST /api/v1/companies/roster."""

    domain: str | None = Field(
        default=None, description="Company root domain (preferred)."
    )
    company: str | None = Field(
        default=None, description="Company name (used to derive a domain if needed)."
    )
    roles: list[str] | None = Field(
        default=None,
        description="Optional role-keyword override; defaults to CONFIG.roster_roles.",
    )


@app.post(
    "/api/v1/companies/roster",
    summary="Find a role-filtered roster of people at a company (+ their emails)",
)
def companies_roster(body: RosterRequest) -> dict[str, Any]:
    """Enumerate a ROLE-FILTERED roster of people at a company and find each
    person's verified work email.

    Enumeration is cheap (Hunter domain-search, name-free); per-person email
    discovery uses the OPEN-WEB-FIRST contact agent (web → pattern → free SMTP,
    paid providers last). Never 500s on a miss — returns an empty roster when no
    domain can be resolved or no matching people are found.
    """
    domain = body.domain and (normalize_domain(body.domain) or body.domain.strip())
    if not domain and body.company:
        slug = "".join(ch for ch in body.company.lower() if ch.isalnum())
        domain = f"{slug}.com" if slug else None
    if not domain:
        return {"domain": None, "company": body.company, "people": [], "count": 0}

    try:
        from agent_server.agents.roster import find_roster

        res = find_roster(domain, body.company, roles=body.roles)
        people = [
            {
                "name": p.name,
                "title": p.title,
                "email": p.email,
                "score": p.score,
                "method": p.method,
            }
            for p in res.people
        ]
    except Exception as exc:  # find_roster never raises, but never 500 regardless
        logger.warning("companies_roster_error", domain=domain, error=str(exc))
        people = []

    logger.info(
        "companies_roster",
        domain=domain,
        count=len(people),
        with_email=sum(1 for p in people if p.get("email")),
    )
    return {
        "domain": domain,
        "company": body.company,
        "people": people,
        "count": len(people),
    }


class DropRequest(BaseModel):
    """Body for POST /api/v1/seen/drop."""

    domain: str = Field(..., min_length=1, description="Domain to mark as dropped.")
    reason: str | None = Field(default=None, description="Why it was dropped.")


@app.post(
    "/api/v1/seen/drop",
    summary="Mark a domain as dropped so future hunts skip it",
)
def drop_domain(body: DropRequest) -> dict[str, Any]:
    """Record a domain as 'dropped' in the seen-cache.

    Used when a user deletes a discovered company in the UI — we remember the
    domain so a later hunt does not re-surface it. Normalises the domain first
    so it matches what discovery/dedup store.
    """
    norm = normalize_domain(body.domain) or body.domain.strip().lower()
    seen_add(norm, "dropped", reason=body.reason or "user_deleted")
    logger.info("seen_dropped", domain=norm, reason=body.reason)
    return {"ok": True, "domain": norm}


@app.get("/health", summary="Liveness probe")
@app.head("/health", summary="Liveness probe (HEAD)")
def health() -> dict[str, str]:
    """Return 200 + {status: ok}.  Accepts both GET and HEAD."""
    return {"status": "ok"}
