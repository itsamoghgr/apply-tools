"""FastAPI server: thin HTTP wrapper around the three generation modes."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

import tracking
from log import configure_logging, get_logger
from db import (
    UniqueViolation,
    add_job_application_lead,
    delete_job_application,
    delete_lead,
    delete_reach_out,
    delete_setting,
    find_or_create_lead_by_email,
    get_lead,
    get_reach_out,
    get_setting,
    insert_job_application,
    insert_lead,
    insert_reach_out,
    list_job_applications,
    list_leads,
    list_leads_for_application,
    list_reach_outs,
    list_reach_outs_for_application,
    platform_leads_known_domains,
    platform_upsert_lead,
    remove_job_application_lead,
    set_setting,
    update_job_application,
    update_lead,
    update_reach_out,
)
from generate import (
    AI_PROVIDER,
    EXTRACT_PROVIDER,
    SCORE_PROVIDER,
    answer_application_question,
    chat_reply,
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
from resume_render import render_resume_pdf_with_pages
from resume_ai import (
    draft_profile_from_notes,
    highlight_bullet,
    rewrite_bullet,
    score_profile,
    suggest_skills,
    tailor_profile,
)
from mail import (
    GmailAuthError,
    GmailReadError,
    GmailSendError,
    fetch_inbox,
    fetch_message,
    send_gmail,
)


configure_logging()
logger = get_logger(__name__)

app = FastAPI(title="Cover Letter Generator", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
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


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=20000)


class ChatRequest(BaseModel):
    # Full transcript in chronological order, ending with the latest user turn.
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=50)


# Resume Builder. `profile` is the structured shape consumed by
# resume_render.py — kept open (dict) rather than a strict nested model so the
# frontend can evolve fields without lockstep schema changes here.
class ResumeProfileRequest(BaseModel):
    profile: dict[str, Any]
    filename: str | None = Field(default=None, max_length=200)


class ResumeRewriteBulletRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    context: str | None = Field(default=None, max_length=2000)


class ResumeTailorRequest(BaseModel):
    profile: dict[str, Any]
    job_description: str = Field(..., min_length=1, max_length=20000)
    company: str | None = Field(default=None, max_length=200)


class ResumeScoreRequest(BaseModel):
    # Score against a pasted JD OR a role title (one of the two is required;
    # validated in score_profile). job_description is optional here so a bare
    # role can be sent.
    profile: dict[str, Any]
    job_description: str | None = Field(default=None, max_length=20000)
    company: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=200)


class ResumeDraftRequest(BaseModel):
    notes: str = Field(..., min_length=1, max_length=40000)


class ResumeSuggestRequest(BaseModel):
    profile: dict[str, Any]


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
            logger.warning("unknown_resume_id", error=str(exc))
            return HTTPException(status_code=400, detail=msg)
        logger.error("tectonic_missing", error=str(exc))
        return HTTPException(status_code=500, detail=msg)
    if isinstance(exc, LatexCompileError):
        logger.error("latex_compile_failed", error=str(exc))
        return HTTPException(status_code=500, detail=f"LaTeX compile failed: {exc}")
    if isinstance(exc, ValueError):
        logger.warning("bad_input_or_model_output", error=str(exc))
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, RuntimeError):
        logger.error("runtime_error", error=str(exc))
        return HTTPException(status_code=500, detail=str(exc))
    logger.exception("unexpected_error")
    return HTTPException(
        status_code=fallback_status,
        detail=f"Unexpected error: {exc.__class__.__name__}: {exc}",
    )


# -----------------------------------------------------------------------------
# Endpoints.
# -----------------------------------------------------------------------------


PROVIDER_LABELS = {
    "anthropic": "Claude",
    "groq": "Groq",
    "nvidia": "NVIDIA NIM",
    "bedrock": "Claude (Bedrock)",
}


@app.get("/")
def health() -> dict:
    return {
        "ok": True,
        "service": "cover-letter-generator",
        "provider": AI_PROVIDER,
        "provider_label": PROVIDER_LABELS.get(AI_PROVIDER, AI_PROVIDER),
        "score_provider": SCORE_PROVIDER,
        "score_provider_label": PROVIDER_LABELS.get(SCORE_PROVIDER, SCORE_PROVIDER),
        "extract_provider": EXTRACT_PROVIDER,
        "extract_provider_label": PROVIDER_LABELS.get(
            EXTRACT_PROVIDER, EXTRACT_PROVIDER
        ),
    }


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


@app.post("/chat")
def chat(req: ChatRequest) -> dict[str, str]:
    """Free-form assistant turn, backed by Bedrock. Returns {'reply': ...}."""
    try:
        return chat_reply([m.model_dump() for m in req.messages])
    except Exception as e:
        raise _to_http_error(e)


# -----------------------------------------------------------------------------
# Resume Builder: structured profile -> LaTeX -> PDF, plus AI assists.
# -----------------------------------------------------------------------------


@app.post("/resume-builder/pdf")
def resume_builder_pdf(req: ResumeProfileRequest) -> Response:
    try:
        pdf_bytes, page_count = render_resume_pdf_with_pages(req.profile)
    except Exception as e:
        raise _to_http_error(e)

    name = req.filename or (req.profile.get("header") or {}).get("fullName") or "Resume"
    filename = f"Resume_{_safe_filename_part(str(name))}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Surfaced to the builder UI so it can warn / block export when the
            # resume spills past one page. Exposed via CORS below.
            "X-Page-Count": str(page_count),
            "Access-Control-Expose-Headers": "X-Page-Count",
        },
    )


@app.post("/resume-builder/rewrite-bullet")
def resume_builder_rewrite_bullet(req: ResumeRewriteBulletRequest) -> dict[str, str]:
    try:
        return rewrite_bullet(req.text, req.context)
    except Exception as e:
        raise _to_http_error(e)


@app.post("/resume-builder/highlight-bullet")
def resume_builder_highlight_bullet(req: ResumeRewriteBulletRequest) -> dict[str, Any]:
    try:
        return highlight_bullet(req.text, req.context)
    except Exception as e:
        raise _to_http_error(e)


@app.post("/resume-builder/tailor")
def resume_builder_tailor(req: ResumeTailorRequest) -> dict[str, Any]:
    try:
        return tailor_profile(req.profile, req.job_description, req.company)
    except Exception as e:
        raise _to_http_error(e)


@app.post("/resume-builder/score")
def resume_builder_score(req: ResumeScoreRequest) -> dict[str, Any]:
    try:
        return score_profile(
            req.profile, req.job_description, req.company, role=req.role
        )
    except Exception as e:
        raise _to_http_error(e)


@app.post("/resume-builder/draft")
def resume_builder_draft(req: ResumeDraftRequest) -> dict[str, Any]:
    try:
        return draft_profile_from_notes(req.notes)
    except Exception as e:
        raise _to_http_error(e)


@app.post("/resume-builder/suggest")
def resume_builder_suggest(req: ResumeSuggestRequest) -> dict[str, Any]:
    try:
        return suggest_skills(req.profile)
    except Exception as e:
        raise _to_http_error(e)


def _coerce_date(value: str | None) -> datetime | None:
    """Accept 'YYYY-MM-DD' or full ISO timestamps; return a NAIVE UTC datetime
    (no tzinfo). None and empty pass through as None.

    Why naive: appliedDate/decisionDate are Postgres `timestamp WITHOUT time
    zone` columns (Prisma's DateTime default). If we bind a timezone-AWARE
    datetime, psycopg converts it to the DB session timezone (e.g.
    America/New_York) before stripping the offset — turning UTC midnight of
    June 4 into `2026-06-03 20:00`, i.e. the date shifts back a day. By handing
    back a *naive* datetime we store the literal value verbatim, so
    'YYYY-MM-DD' lands as `<date> 00:00:00` and the dashboard / day-grouping
    (which read the UTC calendar date) stay correct regardless of DB timezone.
    """
    if value is None or value == "":
        return None
    try:
        # Plain date from <input type="date">: midnight of that date, naive.
        if len(value) == 10 and value[4] == "-" and value[7] == "-":
            dt = datetime.strptime(value, "%Y-%m-%d")
        else:
            # Full ISO; normalize to UTC, then drop tzinfo to store naive UTC.
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
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
        # Default to today's date at midnight (naive UTC) so the row gets a real
        # date, not NULL — and in the same 00:00 format an explicit date uses.
        now_utc = datetime.now(timezone.utc)
        fields["appliedDate"] = datetime(now_utc.year, now_utc.month, now_utc.day)
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


# -----------------------------------------------------------------------------
# JobApplication ↔ Lead links + per-application reach-out history.
# -----------------------------------------------------------------------------


class LinkLeadRequest(BaseModel):
    leadId: str = Field(..., min_length=1, max_length=64)
    role: str | None = Field(default=None, max_length=80)


@app.get("/track/{app_id}/leads")
def track_list_leads(app_id: str) -> dict[str, Any]:
    try:
        return {"leads": list_leads_for_application(app_id)}
    except Exception as e:
        raise _to_http_error(e)


@app.post("/track/{app_id}/leads")
def track_link_lead(app_id: str, req: LinkLeadRequest) -> dict[str, Any]:
    lead = get_lead(req.leadId)
    if not lead:
        raise HTTPException(status_code=404, detail=f"No lead {req.leadId}")
    try:
        created = add_job_application_lead(app_id, req.leadId, req.role)
    except Exception as e:
        raise _to_http_error(e)
    # Echo the linked lead back so the client can update its UI without
    # a full reload. `linkRole` mirrors the join column shape used by
    # `list_leads_for_application`.
    return {
        "ok": True,
        "created": created,
        "lead": {**lead, "linkRole": req.role},
    }


@app.delete("/track/{app_id}/leads/{lead_id}")
def track_unlink_lead(app_id: str, lead_id: str) -> dict[str, bool]:
    try:
        ok = remove_job_application_lead(app_id, lead_id)
    except Exception as e:
        raise _to_http_error(e)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"No link between application {app_id} and lead {lead_id}",
        )
    return {"ok": True}


@app.get("/track/{app_id}/reach-outs")
def track_list_reach_outs(app_id: str) -> dict[str, Any]:
    try:
        return {
            "reachOuts": [
                _serialize_reach_out(r) for r in list_reach_outs_for_application(app_id)
            ]
        }
    except Exception as e:
        raise _to_http_error(e)


# -----------------------------------------------------------------------------
# Leads: master record for "people I might reach out to". Each row can have
# zero or many ReachOuts pointing at it (auto-linked by recipientEmail).
# -----------------------------------------------------------------------------


class LeadCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=200)
    linkedinUrl: str | None = Field(default=None, max_length=2000)
    linkedinProfile: str | None = Field(default=None, max_length=30000)
    currentCompany: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=200)
    replied: bool = False
    repliedAt: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=10000)


class LeadPatchRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=200)
    linkedinUrl: str | None = Field(default=None, max_length=2000)
    linkedinProfile: str | None = Field(default=None, max_length=30000)
    currentCompany: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=200)
    replied: bool | None = None
    repliedAt: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=10000)


@app.post("/leads")
def leads_create(req: LeadCreateRequest) -> dict[str, str]:
    fields = req.model_dump(exclude_unset=False)
    try:
        new_id = insert_lead(fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except UniqueViolation as e:
        # Duplicate Lead.email. Surface a clean 409 so the UI can highlight
        # the email field.
        if e.column is None or e.column == "email":
            raise HTTPException(
                status_code=409,
                detail=f"A lead with email {req.email!r} already exists.",
            )
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise _to_http_error(e)
    return {"id": new_id}


@app.get("/leads")
def leads_list() -> dict[str, Any]:
    try:
        return {"leads": list_leads()}
    except Exception as e:
        raise _to_http_error(e)


@app.get("/leads/{lead_id}")
def leads_get(lead_id: str) -> dict[str, Any]:
    row = get_lead(lead_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"No lead {lead_id}")
    return row


@app.patch("/leads/{lead_id}")
def leads_patch(lead_id: str, req: LeadPatchRequest) -> dict[str, bool]:
    sent = req.model_dump(exclude_unset=True)
    try:
        ok = update_lead(lead_id, sent)
    except UniqueViolation as e:
        if e.column is None or e.column == "email":
            raise HTTPException(
                status_code=409,
                detail=f"A lead with email {sent.get('email')!r} already exists.",
            )
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise _to_http_error(e)
    if not ok:
        # Either no such id, or the request had no recognised fields. Tell
        # the user which it was so the UI can react.
        if not get_lead(lead_id):
            raise HTTPException(status_code=404, detail=f"No lead {lead_id}")
    return {"ok": True}


@app.delete("/leads/{lead_id}")
def leads_delete(lead_id: str) -> dict[str, bool]:
    try:
        ok = delete_lead(lead_id)
    except Exception as e:
        raise _to_http_error(e)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No lead {lead_id}")
    return {"ok": True}


# -----------------------------------------------------------------------------
# Domain-keyed lead intake for the lead-generation agent service.
#
# The agent server (separate process, port 8001) discovers + verifies startups
# and pushes clean verified leads here. Two endpoints, both keyed on the
# normalised root `domain`. Optional shared-secret auth via X-Agent-Token; when
# PLATFORM_API_TOKEN is unset, the endpoints accept unauthenticated calls
# (dev-friendly default). See agent_server/CONTRACTS.md §6.
# -----------------------------------------------------------------------------


def _require_agent_token(x_agent_token: str | None) -> None:
    """Enforce the shared secret only when PLATFORM_API_TOKEN is configured."""
    expected = os.environ.get("PLATFORM_API_TOKEN")
    if expected and x_agent_token != expected:
        raise HTTPException(status_code=401, detail="invalid or missing X-Agent-Token")


class LeadsExistsRequest(BaseModel):
    domains: list[str] = Field(default_factory=list, max_length=1000)


class LeadUpsertRequest(BaseModel):
    domain: str = Field(..., min_length=1, max_length=255)
    company_name: str | None = Field(default=None, max_length=300)
    funding_stage: str | None = Field(default=None, max_length=100)
    funding_amount: str | None = Field(default=None, max_length=100)
    founder_name: str | None = Field(default=None, max_length=200)
    founder_linkedin_url: str | None = Field(default=None, max_length=500)
    founder_email: str | None = Field(default=None, max_length=320)
    employee_count: str | None = Field(default=None, max_length=100)
    revenue: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=200)
    industry: str | None = Field(default=None, max_length=200)
    last_round_date: str | None = Field(default=None, max_length=100)
    confidence: float = Field(..., ge=0.0, le=1.0)
    source: str | None = Field(default="agent-server", max_length=100)
    sources: list[str] = Field(default_factory=list, max_length=100)


@app.post("/api/v1/leads/exists")
def leads_exists(
    req: LeadsExistsRequest,
    x_agent_token: str | None = Header(default=None),
) -> dict[str, list[str]]:
    """Return which of the supplied domains the platform already knows."""
    _require_agent_token(x_agent_token)
    try:
        known = platform_leads_known_domains(req.domains)
    except Exception as e:
        raise _to_http_error(e)
    return {"known": known}


@app.post("/api/v1/leads/upsert")
def leads_upsert(
    req: LeadUpsertRequest,
    x_agent_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Idempotently upsert a verified lead keyed on normalised domain."""
    _require_agent_token(x_agent_token)
    try:
        result = platform_upsert_lead(req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise _to_http_error(e)
    return {"ok": True, **result}


# -----------------------------------------------------------------------------
# Reach-out flow: draft an outreach email from a LinkedIn profile, edit it,
# then send via Gmail SMTP using a stored app password.
# -----------------------------------------------------------------------------


GMAIL_ADDRESS_KEY = "gmail_address"
GMAIL_APP_PASSWORD_KEY = "gmail_app_password"
GMAIL_FROM_NAME_KEY = "gmail_from_name"


REACH_OUT_CHANNELS = ("email", "linkedin_invitation", "linkedin_message")


class ReachOutGenerateRequest(BaseModel):
    recipientName: str = Field(..., min_length=1, max_length=200)
    # Email is only required for the `email` channel; the LinkedIn channels
    # are paste-into-LinkedIn flows where we don't have/need an address.
    recipientEmail: str | None = Field(default=None, max_length=200)
    linkedinProfile: str = Field(..., min_length=1, max_length=30000)
    contextNote: str | None = Field(default=None, max_length=2000)
    resumeId: str | None = RESUME_ID_FIELD
    jobApplicationId: str | None = Field(default=None, max_length=64)
    channel: str = Field(default="email", max_length=32)


class ReachOutBlankRequest(BaseModel):
    """Create a draft without calling the AI — for users writing from scratch.

    LinkedIn profile + context are optional here (unlike `/generate`), since
    the user is providing the content themselves. We still persist whatever
    they did fill in so they can convert this draft to AI-assisted later.
    """
    recipientName: str = Field(..., min_length=1, max_length=200)
    recipientEmail: str | None = Field(default=None, max_length=200)
    linkedinProfile: str | None = Field(default=None, max_length=30000)
    contextNote: str | None = Field(default=None, max_length=2000)
    resumeId: str | None = RESUME_ID_FIELD
    jobApplicationId: str | None = Field(default=None, max_length=64)
    channel: str = Field(default="email", max_length=32)


class ReachOutPatchRequest(BaseModel):
    recipientName: str | None = Field(default=None, max_length=200)
    recipientEmail: str | None = Field(default=None, max_length=200)
    subject: str | None = Field(default=None, max_length=400)
    body: str | None = Field(default=None, max_length=20000)
    contextNote: str | None = Field(default=None, max_length=2000)


class GmailSettingsRequest(BaseModel):
    address: str = Field(..., min_length=3, max_length=200)
    appPassword: str = Field(default="", max_length=200)
    fromName: str | None = Field(default=None, max_length=200)


def _serialize_reach_out(row: dict) -> dict:
    """Strip secrets and normalize for the wire.

    Note: tracking aggregates (openCount, clickCount, lastOpenedAt,
    lastClickedAt) are NOT in the local DB anymore — they live in the
    sidecar's Postgres and are fetched separately by the dashboard via
    `/reach-out/aggregates`.
    """
    if not row:
        return row
    return {
        "id": row.get("id"),
        "recipientName": row.get("recipientName"),
        "recipientEmail": row.get("recipientEmail"),
        "linkedinProfile": row.get("linkedinProfile"),
        "contextNote": row.get("contextNote"),
        "resumeId": row.get("resumeId"),
        "leadId": row.get("leadId"),
        "jobApplicationId": row.get("jobApplicationId"),
        "channel": row.get("channel") or "email",
        "subject": row.get("subject"),
        "body": row.get("body"),
        "status": row.get("status"),
        "sentAt": row.get("sentAt"),
        "errorMessage": row.get("errorMessage"),
        "createdAt": row.get("createdAt"),
        "updatedAt": row.get("updatedAt"),
    }


@app.post("/reach-out/generate")
def reach_out_generate(req: ReachOutGenerateRequest) -> dict[str, Any]:
    channel = req.channel if req.channel in REACH_OUT_CHANNELS else "email"
    if channel == "email" and not (req.recipientEmail and req.recipientEmail.strip()):
        raise HTTPException(
            status_code=400, detail="Email channel requires a recipient email."
        )

    # Fold the recipient's name into the context so the model addresses them
    # by name without us having to teach a separate prompt template.
    context_parts: list[str] = [f"Recipient name: {req.recipientName.strip()}"]
    if req.contextNote and req.contextNote.strip():
        context_parts.append(req.contextNote.strip())
    context = "\n".join(context_parts)

    try:
        result = generate_outreach_message(
            req.linkedinProfile,
            channel,
            context,
            resume_id=req.resumeId,
        )
    except Exception as e:
        raise _to_http_error(e)

    subject = (result.get("subject") or "").strip()
    body = (result.get("message") or "").strip()
    # Email needs subject + body; LinkedIn invitations are body-only (300
    # char note, no subject); LinkedIn messages have a subject too.
    if channel == "linkedin_invitation":
        if not body:
            raise HTTPException(
                status_code=500, detail="Generator did not return a message"
            )
    else:
        if not subject or not body:
            raise HTTPException(
                status_code=500, detail="Generator did not return subject and body"
            )

    # Only auto-link to a Lead when we have an email — that's the join key.
    lead_id = (
        find_or_create_lead_by_email(
            req.recipientName,
            req.recipientEmail,
            linkedin_profile=req.linkedinProfile,
        )
        if (req.recipientEmail and req.recipientEmail.strip())
        else None
    )

    try:
        new_id = insert_reach_out(
            {
                "recipientName": req.recipientName,
                "recipientEmail": req.recipientEmail or "",
                "linkedinProfile": req.linkedinProfile,
                "contextNote": req.contextNote,
                "resumeId": req.resumeId,
                "leadId": lead_id,
                "jobApplicationId": req.jobApplicationId,
                "channel": channel,
                "subject": subject,
                "body": body,
            }
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _to_http_error(e)

    return _serialize_reach_out(get_reach_out(new_id) or {})


@app.post("/reach-out/blank")
def reach_out_blank(req: ReachOutBlankRequest) -> dict[str, Any]:
    """Create an empty draft so the user can compose subject + body manually.

    Mirrors `/reach-out/generate` but skips the AI call. The frontend uses
    this when the user clicks "Compose manually" — they then land in the
    preview/edit step with empty subject and body fields ready to type into.
    """
    channel = req.channel if req.channel in REACH_OUT_CHANNELS else "email"
    if channel == "email" and not (req.recipientEmail and req.recipientEmail.strip()):
        raise HTTPException(
            status_code=400, detail="Email channel requires a recipient email."
        )

    lead_id = (
        find_or_create_lead_by_email(
            req.recipientName,
            req.recipientEmail,
            linkedin_profile=req.linkedinProfile,
        )
        if (req.recipientEmail and req.recipientEmail.strip())
        else None
    )

    try:
        new_id = insert_reach_out(
            {
                "recipientName": req.recipientName,
                "recipientEmail": req.recipientEmail or "",
                "linkedinProfile": req.linkedinProfile or "",
                "contextNote": req.contextNote,
                "resumeId": req.resumeId,
                "leadId": lead_id,
                "jobApplicationId": req.jobApplicationId,
                "channel": channel,
                "subject": "",
                "body": "",
            },
            require_content=False,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _to_http_error(e)

    return _serialize_reach_out(get_reach_out(new_id) or {})


@app.get("/reach-out")
def reach_out_list() -> dict[str, Any]:
    try:
        return {"reachOuts": [_serialize_reach_out(r) for r in list_reach_outs()]}
    except Exception as e:
        raise _to_http_error(e)


@app.get("/reach-out/{row_id}")
def reach_out_get(row_id: str) -> dict[str, Any]:
    row = get_reach_out(row_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"No reach-out {row_id}")
    return _serialize_reach_out(row)


@app.patch("/reach-out/{row_id}")
def reach_out_patch(row_id: str, req: ReachOutPatchRequest) -> dict[str, Any]:
    existing = get_reach_out(row_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"No reach-out {row_id}")
    if existing.get("status") == "sent":
        raise HTTPException(
            status_code=400, detail="This reach-out has already been sent."
        )
    fields = req.model_dump(exclude_unset=True)
    if not fields:
        return _serialize_reach_out(existing)
    try:
        update_reach_out(row_id, fields)
    except Exception as e:
        raise _to_http_error(e)
    return _serialize_reach_out(get_reach_out(row_id) or {})


@app.post("/reach-out/{row_id}/mark-sent")
def reach_out_mark_sent(row_id: str) -> dict[str, Any]:
    """Mark a LinkedIn draft as sent after the user pastes it on LinkedIn.

    There's no API to actually deliver invites/InMails, so this is a
    bookkeeping endpoint — the frontend calls it from the Copy & open flow.
    """
    row = get_reach_out(row_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"No reach-out {row_id}")
    if (row.get("channel") or "email") == "email":
        raise HTTPException(
            status_code=400,
            detail="Use /send for email reach-outs (it actually delivers via Gmail).",
        )
    if row.get("status") == "sent":
        return _serialize_reach_out(row)
    sent_at_iso = datetime.now(timezone.utc).isoformat()
    update_reach_out(
        row_id,
        {"status": "sent", "sentAt": sent_at_iso, "errorMessage": None},
    )
    return _serialize_reach_out(get_reach_out(row_id) or {})


@app.post("/reach-out/{row_id}/send")
def reach_out_send(row_id: str) -> dict[str, Any]:
    row = get_reach_out(row_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"No reach-out {row_id}")
    if (row.get("channel") or "email") != "email":
        raise HTTPException(
            status_code=400,
            detail="LinkedIn drafts can't be sent automatically. Copy the text and paste it on LinkedIn, then mark it sent.",
        )
    if row.get("status") == "sent":
        raise HTTPException(status_code=400, detail="Already sent.")
    # Manual drafts can have empty subject/body until the user fills them
    # in. Reject the send before it hits Gmail rather than letting Gmail
    # bounce it back with a less actionable error.
    if not (row.get("subject") or "").strip():
        raise HTTPException(
            status_code=400, detail="Subject is empty. Add one before sending."
        )
    if not (row.get("body") or "").strip():
        raise HTTPException(
            status_code=400, detail="Body is empty. Write your message before sending."
        )

    address = get_setting(GMAIL_ADDRESS_KEY)
    app_password = get_setting(GMAIL_APP_PASSWORD_KEY)
    from_name = get_setting(GMAIL_FROM_NAME_KEY)
    if not address or not app_password:
        raise HTTPException(
            status_code=400,
            detail="Gmail isn't connected. Add your address + app password first.",
        )

    # Build a tracking-enabled HTML alternative. We refuse to send when the
    # sidecar isn't configured — tracking is the whole point of this flow,
    # and a silent fallback to untracked email would mislead the UI's
    # open/click counters.
    try:
        plain_body, html_body = tracking.prepare_html(row["body"], row_id)
    except tracking.TrackingNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("tracked_html_build_failed", reach_out_id=row_id)
        raise HTTPException(status_code=500, detail=f"Tracking failure: {exc}")

    try:
        send_gmail(
            from_addr=address,
            app_password=app_password,
            to_addr=row["recipientEmail"],
            subject=row["subject"],
            body=plain_body,
            from_name=from_name,
            html_body=html_body,
        )
    except (GmailAuthError, GmailSendError) as exc:
        update_reach_out(row_id, {"status": "failed", "errorMessage": str(exc)})
        status_code = 401 if isinstance(exc, GmailAuthError) else 502
        raise HTTPException(status_code=status_code, detail=str(exc))
    except Exception as exc:
        update_reach_out(row_id, {"status": "failed", "errorMessage": str(exc)})
        raise _to_http_error(exc)

    sent_at_iso = datetime.now(timezone.utc).isoformat()
    update_reach_out(
        row_id,
        {
            "status": "sent",
            "sentAt": sent_at_iso,
            "errorMessage": None,
            "htmlBody": html_body,
        },
    )
    return _serialize_reach_out(get_reach_out(row_id) or {})


@app.delete("/reach-out/{row_id}")
def reach_out_delete(row_id: str) -> dict[str, bool]:
    try:
        ok = delete_reach_out(row_id)
    except Exception as e:
        raise _to_http_error(e)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No reach-out {row_id}")
    return {"ok": True}


# -----------------------------------------------------------------------------
# Sidecar proxies. The local backend doesn't track events directly anymore —
# /track/open and /track/click run on the deployed sidecar (see
# tracking-sidecar/) which is reachable from mail clients on the public
# internet. The dashboard reads back through these proxy routes so it can
# stay on localhost:8000 and not need the sidecar's bearer token in the
# browser.
# -----------------------------------------------------------------------------


def _sidecar_request(
    method: str,
    path: str,
    *,
    json: Any = None,
    timeout: float = 10.0,
) -> httpx.Response:
    base = tracking.get_base_url()
    token = tracking.get_api_token()
    if not base or not token:
        raise HTTPException(
            status_code=400,
            detail=(
                "Tracking sidecar is not configured. Set TRACKING_BASE_URL and "
                "TRACKING_API_TOKEN in backend/.env after deploying the sidecar "
                "(see tracking-sidecar/README.md)."
            ),
        )
    url = base.rstrip("/") + path
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=timeout) as client:
            return client.request(method, url, headers=headers, json=json)
    except httpx.HTTPError as exc:
        # Render's free tier puts the service to sleep; first request after
        # idle takes ~30-60s. Surface a clean 502 instead of a stack trace.
        raise HTTPException(
            status_code=502,
            detail=f"Tracking sidecar unreachable at {base}: {exc}",
        )


@app.get("/reach-out/{row_id}/events")
def reach_out_events(row_id: str) -> dict[str, Any]:
    if not get_reach_out(row_id):
        raise HTTPException(status_code=404, detail=f"No reach-out {row_id}")
    res = _sidecar_request("GET", f"/events/{row_id}")
    if res.status_code >= 400:
        raise HTTPException(status_code=res.status_code, detail=res.text)
    return res.json()


class AggregatesRequest(BaseModel):
    ids: list[str] = Field(..., max_length=500)


@app.post("/reach-out/aggregates")
def reach_out_aggregates(req: AggregatesRequest) -> dict[str, Any]:
    """Batched open/click counts for the list view.

    The frontend calls this once per page render with up to 500 reach-out
    ids and merges the result client-side. We tolerate a sidecar failure
    here gracefully (return empty aggregates) so a sleeping Render
    instance doesn't break the dashboard — it just shows zero counters
    until the sidecar wakes up.
    """
    if not req.ids:
        return {"aggregates": {}}
    try:
        res = _sidecar_request("POST", "/aggregates", json={"ids": req.ids})
    except HTTPException as exc:
        logger.info("aggregates_fetch_failed", detail=exc.detail)
        return {"aggregates": {}, "warning": exc.detail}
    if res.status_code >= 400:
        logger.warning("sidecar_error", endpoint="/aggregates", status=res.status_code)
        return {"aggregates": {}, "warning": res.text}
    return res.json()


@app.get("/settings/tracking")
def settings_tracking() -> dict[str, Any]:
    """Status endpoint the UI uses to show whether tracking is wired up."""
    base = tracking.get_base_url()
    return {
        "publicUrl": base,
        "ready": tracking.is_ready(),
    }


@app.get("/settings/gmail")
def settings_gmail_get() -> dict[str, Any]:
    address = get_setting(GMAIL_ADDRESS_KEY)
    app_password = get_setting(GMAIL_APP_PASSWORD_KEY)
    from_name = get_setting(GMAIL_FROM_NAME_KEY)
    return {
        "address": address,
        "fromName": from_name,
        "hasPassword": bool(app_password),
    }


@app.put("/settings/gmail")
def settings_gmail_put(req: GmailSettingsRequest) -> dict[str, Any]:
    address = req.address.strip()
    if "@" not in address:
        raise HTTPException(
            status_code=400, detail="address must look like an email."
        )
    set_setting(GMAIL_ADDRESS_KEY, address)
    if req.appPassword:
        # Gmail app passwords are 16 chars, optionally space-separated when
        # Google shows them. Strip whitespace before storing.
        cleaned = re.sub(r"\s+", "", req.appPassword)
        set_setting(GMAIL_APP_PASSWORD_KEY, cleaned)
    if req.fromName is not None:
        if req.fromName.strip():
            set_setting(GMAIL_FROM_NAME_KEY, req.fromName.strip())
        else:
            delete_setting(GMAIL_FROM_NAME_KEY)
    return settings_gmail_get()


@app.delete("/settings/gmail")
def settings_gmail_delete() -> dict[str, bool]:
    delete_setting(GMAIL_ADDRESS_KEY)
    delete_setting(GMAIL_APP_PASSWORD_KEY)
    delete_setting(GMAIL_FROM_NAME_KEY)
    return {"ok": True}


@app.get("/mail")
async def mail_inbox(limit: int = 50) -> dict[str, Any]:
    """Live read of the user's Gmail INBOX over IMAP using the stored app password.

    `imaplib` is sync/blocking, so we hand the call off to FastAPI's worker
    threadpool via asyncio.to_thread — that way one in-flight inbox or body
    fetch doesn't park the event loop and stall every other route.
    """
    capped = max(1, min(limit, 200))
    address = get_setting(GMAIL_ADDRESS_KEY)
    app_password = get_setting(GMAIL_APP_PASSWORD_KEY)
    if not address or not app_password:
        return {"configured": False, "address": address, "messages": []}

    try:
        messages = await asyncio.to_thread(
            fetch_inbox, address, app_password, capped
        )
    except GmailAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except GmailReadError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"configured": True, "address": address, "messages": messages}


@app.get("/mail/{uid}")
async def mail_message(uid: str) -> dict[str, Any]:
    """Fetch one message's full body by IMAP UID. Marks the message as read."""
    address = get_setting(GMAIL_ADDRESS_KEY)
    app_password = get_setting(GMAIL_APP_PASSWORD_KEY)
    if not address or not app_password:
        raise HTTPException(status_code=400, detail="Gmail isn't connected.")
    try:
        msg = await asyncio.to_thread(
            fetch_message, address, app_password, uid
        )
    except GmailAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except GmailReadError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if msg is None:
        raise HTTPException(status_code=404, detail=f"No message with UID {uid}")
    return msg


class MailSendRequest(BaseModel):
    to: str = Field(..., min_length=3, max_length=320)
    subject: str = Field(..., max_length=998)
    body: str = Field(..., max_length=200_000)
    inReplyTo: str | None = Field(default=None, max_length=998)
    references: str | None = Field(default=None, max_length=4000)


@app.post("/mail/send")
def mail_send(req: MailSendRequest) -> dict[str, Any]:
    """Send a one-off email (reply / forward / new) using stored Gmail creds.

    Unlike /reach-out/{id}/send, this does NOT persist a ReachOut row and
    does NOT add open/click tracking — it's a plain SMTP send for inbox
    interactions.
    """
    address = get_setting(GMAIL_ADDRESS_KEY)
    app_password = get_setting(GMAIL_APP_PASSWORD_KEY)
    from_name = get_setting(GMAIL_FROM_NAME_KEY)
    if not address or not app_password:
        raise HTTPException(
            status_code=400,
            detail="Gmail isn't connected. Add your address + app password first.",
        )
    if "@" not in req.to:
        raise HTTPException(status_code=400, detail="Recipient must be an email address.")
    if not req.subject.strip():
        raise HTTPException(status_code=400, detail="Subject is empty.")
    if not req.body.strip():
        raise HTTPException(status_code=400, detail="Body is empty.")

    try:
        send_gmail(
            from_addr=address,
            app_password=app_password,
            to_addr=req.to.strip(),
            subject=req.subject,
            body=req.body,
            from_name=from_name,
            in_reply_to=req.inReplyTo or None,
            references=req.references or None,
        )
    except GmailAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except GmailSendError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True}


@app.exception_handler(404)
def _not_found(_request, _exc):
    return JSONResponse(status_code=404, content={"detail": "Not found"})


def main() -> None:
    """Entrypoint used by start.sh.

    Runs uvicorn with our structlog log config so the reloader, error, and
    access logs all render in the same (structured) format as app events.
    Env vars: HOST, PORT, RELOAD ("1"/"0"). See log.py for LOG_* / ENV.
    """
    import os

    import uvicorn

    from log import build_uvicorn_log_config

    uvicorn.run(
        "server:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD", "1") == "1",
        log_config=build_uvicorn_log_config(),
    )


if __name__ == "__main__":
    main()
