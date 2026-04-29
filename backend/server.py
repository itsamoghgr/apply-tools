"""FastAPI server: thin HTTP wrapper around the three generation modes."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from db import (
    delete_job_application,
    insert_job_application,
    list_job_applications,
    update_job_application,
)
from generate import (
    answer_application_question,
    extract_jd_from_page,
    generate_application_email,
    generate_cover_letter,
    generate_cover_letter_text,
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


class ExtractJdRequest(BaseModel):
    url: str = Field(default="", max_length=2000)
    page_title: str | None = Field(default=None, max_length=500)
    page_text: str = Field(..., min_length=1, max_length=60000)


class AnswerQuestionRequest(BaseModel):
    company: str = Field(..., min_length=1, max_length=200)
    job_description: str = Field(..., min_length=1, max_length=20000)
    question: str = Field(..., min_length=1, max_length=4000)
    resume_id: str | None = RESUME_ID_FIELD


# JobApplication tracker. Status is constrained to the same enum the popup
# and dashboard expose so we never get a free-text mismatch from the UI;
# everything else is optional and free-form.
ALLOWED_STATUSES = (
    "Applied",
    "In-Progress",
    "Offer",
    "Rejected",
    "Withdrawn",
    "Ghosted",
)

ALLOWED_INTERVIEW_STATUSES = (
    "Assessment",
    "Interviewing",
    "Offer",
    "Rejected",
)


class TrackCreateRequest(BaseModel):
    companyName: str = Field(..., min_length=1, max_length=200)
    jobRole: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    interviewStatus: str | None = Field(default=None, max_length=200)
    status: str = Field(default="Applied", max_length=40)
    appliedDate: str | None = Field(default=None, max_length=40)  # ISO date
    resumeId: str | None = RESUME_ID_FIELD
    jobUrl: str | None = Field(default=None, max_length=2000)
    companyCareerPage: str | None = Field(default=None, max_length=2000)
    decisionDate: str | None = Field(default=None, max_length=40)
    decisionTime: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=10000)
    hrName: str | None = Field(default=None, max_length=200)
    hrLinkedin: str | None = Field(default=None, max_length=2000)
    hrEmail: str | None = Field(default=None, max_length=200)
    referral: str | None = Field(default=None, max_length=200)
    referralLinkedin: str | None = Field(default=None, max_length=2000)
    jobDescription: str | None = Field(default=None, max_length=40000)


class TrackPatchRequest(BaseModel):
    companyName: str | None = Field(default=None, max_length=200)
    jobRole: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    interviewStatus: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, max_length=40)
    appliedDate: str | None = Field(default=None, max_length=40)
    resumeId: str | None = RESUME_ID_FIELD
    jobUrl: str | None = Field(default=None, max_length=2000)
    companyCareerPage: str | None = Field(default=None, max_length=2000)
    decisionDate: str | None = Field(default=None, max_length=40)
    decisionTime: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=10000)
    hrName: str | None = Field(default=None, max_length=200)
    hrLinkedin: str | None = Field(default=None, max_length=2000)
    hrEmail: str | None = Field(default=None, max_length=200)
    referral: str | None = Field(default=None, max_length=200)
    referralLinkedin: str | None = Field(default=None, max_length=2000)
    jobDescription: str | None = Field(default=None, max_length=40000)


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


@app.post("/cover-text")
def cover_text(req: GenerateRequest) -> dict[str, str]:
    try:
        return generate_cover_letter_text(
            req.company, req.job_description, resume_id=req.resume_id
        )
    except Exception as e:
        raise _to_http_error(e)


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


@app.post("/extract-jd")
def extract_jd(req: ExtractJdRequest) -> dict[str, str]:
    try:
        return extract_jd_from_page(req.url, req.page_title, req.page_text)
    except Exception as e:
        raise _to_http_error(e)


@app.post("/answer-question")
def answer_question(req: AnswerQuestionRequest) -> dict[str, str]:
    try:
        return answer_application_question(
            req.company,
            req.job_description,
            req.question,
            resume_id=req.resume_id,
        )
    except Exception as e:
        raise _to_http_error(e)


def _coerce_date(value: str | None) -> int | None:
    """Accept 'YYYY-MM-DD' or full ISO timestamps; return Prisma-compatible
    epoch milliseconds (UTC). None and empty pass through as None.

    Prisma's SQLite adapter stores DateTime values as INTEGER ms-since-epoch
    and binds query inputs the same way. Storing strings here would make
    Prisma's range filters silently match every row (text vs integer in
    SQLite type-affinity rules).
    """
    if value is None or value == "":
        return None
    try:
        # Plain date from <input type="date">: midnight UTC of that date.
        if len(value) == 10 and value[4] == "-" and value[7] == "-":
            dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            # Full ISO; assume UTC if no offset given.
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid date {value!r}: {exc}"
        )


def _validate_track_status(status: str | None) -> None:
    if status is not None and status not in ALLOWED_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of {ALLOWED_STATUSES}, got {status!r}",
        )


def _validate_interview_status(value: str | None) -> None:
    # Empty / None is fine (means "not at this stage yet").
    if value is None or value == "":
        return
    if value not in ALLOWED_INTERVIEW_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"interviewStatus must be one of {ALLOWED_INTERVIEW_STATUSES} "
                f"or empty, got {value!r}"
            ),
        )


@app.post("/track")
def track_create(req: TrackCreateRequest) -> dict[str, str]:
    _validate_track_status(req.status)
    _validate_interview_status(req.interviewStatus)
    fields = req.model_dump(exclude_unset=False)
    fields["appliedDate"] = _coerce_date(fields.get("appliedDate"))
    fields["decisionDate"] = _coerce_date(fields.get("decisionDate"))
    if fields["appliedDate"] is None:
        # Default to "now" so the row gets a real date, not NULL.
        fields["appliedDate"] = int(
            datetime.now(timezone.utc).timestamp() * 1000
        )
    try:
        new_id = insert_job_application(fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _to_http_error(e)
    return {"id": new_id}


@app.get("/track")
def track_list() -> dict[str, Any]:
    try:
        return {"applications": list_job_applications()}
    except Exception as e:
        raise _to_http_error(e)


@app.patch("/track/{app_id}")
def track_patch(app_id: str, req: TrackPatchRequest) -> dict[str, bool]:
    # PATCH semantics: include keys the client explicitly sent (so empty string
    # means "clear this field"), drop keys that weren't sent at all.
    sent = req.model_dump(exclude_unset=True)
    fields = sent
    if "status" in fields:
        _validate_track_status(fields["status"])
    if "interviewStatus" in fields:
        _validate_interview_status(fields["interviewStatus"])
    if "appliedDate" in fields:
        fields["appliedDate"] = _coerce_date(fields["appliedDate"])
    if "decisionDate" in fields:
        fields["decisionDate"] = _coerce_date(fields["decisionDate"])
    try:
        ok = update_job_application(app_id, fields)
    except Exception as e:
        raise _to_http_error(e)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No application {app_id}")
    return {"ok": True}


@app.delete("/track/{app_id}")
def track_delete(app_id: str) -> dict[str, bool]:
    try:
        ok = delete_job_application(app_id)
    except Exception as e:
        raise _to_http_error(e)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No application {app_id}")
    return {"ok": True}


@app.exception_handler(404)
def _not_found(_request, _exc):
    return JSONResponse(status_code=404, content={"detail": "Not found"})
