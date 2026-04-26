"""FastAPI server: thin HTTP wrapper around the three generation modes."""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from generate import (
    generate_application_email,
    generate_cover_letter,
    generate_outreach_message,
    list_resumes,
    score_jd_fit,
    score_jd_fit_all,
)
from latex_utils import LatexCompileError


logger = logging.getLogger("coverletter")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Cover Letter Generator", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# Request / response schemas.
# -----------------------------------------------------------------------------


RESUME_ID_FIELD = Field(
    default=None, max_length=64, pattern=r"^[a-z0-9_-]+$"
)


class GenerateRequest(BaseModel):
    company: str = Field(..., min_length=1, max_length=200)
    job_description: str = Field(..., min_length=1, max_length=20000)
    resume_id: str | None = RESUME_ID_FIELD


class EmailRequest(BaseModel):
    company: str = Field(..., min_length=1, max_length=200)
    job_description: str = Field(..., min_length=1, max_length=20000)
    intent: str | None = Field(default=None, max_length=2000)
    resume_id: str | None = RESUME_ID_FIELD


class OutreachChannel(str, Enum):
    linkedin_invitation = "linkedin_invitation"
    linkedin_message = "linkedin_message"
    email = "email"


class OutreachRequest(BaseModel):
    profile_text: str = Field(..., min_length=1, max_length=30000)
    channel: OutreachChannel
    context: str | None = Field(default=None, max_length=2000)
    resume_id: str | None = RESUME_ID_FIELD


class ScoreRequest(BaseModel):
    job_description: str = Field(..., min_length=1, max_length=20000)
    company: str | None = Field(default=None, max_length=200)
    resume_id: str | None = RESUME_ID_FIELD


class ScoreAllRequest(BaseModel):
    job_description: str = Field(..., min_length=1, max_length=20000)
    company: str | None = Field(default=None, max_length=200)


# -----------------------------------------------------------------------------
# Helpers.
# -----------------------------------------------------------------------------


_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename_part(s: str) -> str:
    cleaned = _FILENAME_SAFE_RE.sub("_", s.strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "Company"


def _to_http_error(exc: Exception, fallback_status: int = 500) -> HTTPException:
    """Translate generation errors into HTTP responses."""
    if isinstance(exc, FileNotFoundError):
        msg = str(exc)
        # Resume-id misses raise FileNotFoundError("Unknown resume_id: ..."); user-fixable -> 400.
        if msg.lower().startswith("unknown resume_id"):
            logger.warning("Unknown resume_id: %s", exc)
            return HTTPException(status_code=400, detail=msg)
        logger.error("Tectonic missing: %s", exc)
        return HTTPException(status_code=500, detail=msg)
    if isinstance(exc, LatexCompileError):
        logger.error("LaTeX compile failed:\n%s", exc)
        return HTTPException(status_code=500, detail=f"LaTeX compile failed: {exc}")
    if isinstance(exc, ValueError):
        logger.warning("Bad input or bad model output: %s", exc)
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, RuntimeError):
        logger.error("Runtime error: %s", exc)
        return HTTPException(status_code=500, detail=str(exc))
    logger.exception("Unexpected error")
    return HTTPException(
        status_code=fallback_status,
        detail=f"Unexpected error: {exc.__class__.__name__}: {exc}",
    )


# -----------------------------------------------------------------------------
# Endpoints.
# -----------------------------------------------------------------------------


@app.get("/")
def health() -> dict:
    return {"ok": True, "service": "cover-letter-generator"}


@app.get("/resumes")
def resumes() -> dict[str, Any]:
    return {"resumes": list_resumes()}


@app.post("/generate")
def generate(req: GenerateRequest) -> Response:
    try:
        pdf_bytes = generate_cover_letter(
            req.company, req.job_description, resume_id=req.resume_id
        )
    except Exception as e:
        raise _to_http_error(e)

    filename = f"CoverLetter_{_safe_filename_part(req.company)}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/email")
def email(req: EmailRequest) -> dict[str, str]:
    try:
        return generate_application_email(
            req.company,
            req.job_description,
            req.intent,
            resume_id=req.resume_id,
        )
    except Exception as e:
        raise _to_http_error(e)


@app.post("/outreach")
def outreach(req: OutreachRequest) -> dict[str, Any]:
    try:
        return generate_outreach_message(
            req.profile_text,
            req.channel.value,
            req.context,
            resume_id=req.resume_id,
        )
    except Exception as e:
        raise _to_http_error(e)


@app.post("/score")
def score(req: ScoreRequest) -> dict[str, Any]:
    try:
        return score_jd_fit(
            req.job_description, req.company, resume_id=req.resume_id
        )
    except Exception as e:
        raise _to_http_error(e)


@app.post("/score-all")
def score_all(req: ScoreAllRequest) -> dict[str, Any]:
    try:
        return {"results": score_jd_fit_all(req.job_description, req.company)}
    except Exception as e:
        raise _to_http_error(e)


@app.exception_handler(404)
def _not_found(_request, _exc):
    return JSONResponse(status_code=404, content={"detail": "Not found"})
