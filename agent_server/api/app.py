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

from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agent_server.config import CONFIG
from agent_server.db.agent_db import create_job, get_job, seen_add
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
        target_count=row.get("target_count"),
        candidates_total=row.get("candidates_total"),
        candidates_processed=row.get("candidates_processed"),
        stop_reason=row.get("stop_reason"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        finished_at=row.get("finished_at"),
    )


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
