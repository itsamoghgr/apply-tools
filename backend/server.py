"""FastAPI server: thin HTTP wrapper around the three generation modes."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from log import configure_logging, get_logger
from db import (
    UniqueViolation,
    add_job_application_lead,
    delete_job_application,
    delete_lead,
    delete_setting,
    get_lead,
    get_setting,
    insert_job_application,
    insert_lead,
    list_job_applications,
    list_leads,
    list_leads_for_application,
    platform_leads_known_domains,
    platform_upsert_lead,
    remove_job_application_lead,
    set_setting,
    update_job_application,
    update_lead,
)
from generate import (
    AI_PROVIDER,
    EXTRACT_PROVIDER,
    SCORE_PROVIDER,
    answer_application_question,
    chat_reply,
    extract_jd_from_page,
    generate_cover_letter,
    generate_cover_letter_text,
    render_cover_letter_pdf,
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


class CoverLetterPdfRequest(BaseModel):
    # Render a PDF from already-generated (and possibly edited) letter text.
    company: str = Field(..., min_length=1, max_length=200)
    role_title: str = Field(default="", max_length=300)
    hiring_manager: str = Field(default="", max_length=200)
    body: str = Field(..., min_length=1, max_length=20000)


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


class CoverLetterMeta(BaseModel):
    # Captured at generation so the on-demand PDF render matches the saved letter.
    roleTitle: str | None = Field(default=None, max_length=300)
    hiringManager: str | None = Field(default=None, max_length=200)
    resumeId: str | None = RESUME_ID_FIELD


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
    coverLetter: str | None = Field(default=None, max_length=20000)
    coverLetterMeta: CoverLetterMeta | None = None


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
    coverLetter: str | None = Field(default=None, max_length=20000)
    coverLetterMeta: CoverLetterMeta | None = None


# -----------------------------------------------------------------------------
# Helpers.
# -----------------------------------------------------------------------------


_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename_part(s: str) -> str:
    cleaned = _FILENAME_SAFE_RE.sub("_", s.strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "Company"


def _resume_filename(full_name: str, resume_name: str) -> str:
    """`<first>_<last>_resume_<roletag>` (lowercase, underscores) — mirrors the
    frontend blob-download naming. The person part is first + last name only;
    any middle names/initials are dropped. The role tag (e.g. "ds12") is parsed
    out of the resume name by lowercasing and dropping non-alphanumerics."""
    words = [
        re.sub(r"[^a-z0-9]+", "", w) for w in full_name.lower().split()
    ]
    words = [w for w in words if w]
    if len(words) > 1:
        person = f"{words[0]}_{words[-1]}"
    else:
        person = words[0] if words else ""
    role_tag = re.sub(r"[^a-z0-9]+", "", resume_name.lower())
    base = f"{person}_resume" if person else "resume"
    return f"{base}_{role_tag}" if role_tag else base


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


@app.post("/cover-letter/pdf")
def cover_letter_pdf(req: CoverLetterPdfRequest) -> Response:
    # Render a PDF from edited letter text (no LLM, no audit-log row) so the
    # web-app "Download PDF" always matches the currently-saved letter body.
    try:
        pdf_bytes = render_cover_letter_pdf(
            req.company, req.role_title, req.hiring_manager, req.body
        )
    except Exception as e:
        raise _to_http_error(e)

    filename = f"CoverLetter_{_safe_filename_part(req.company)}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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

    header = req.profile.get("header") or {}
    full_name = str(header.get("fullName") or "")
    resume_name = str(req.filename or "")
    filename = f"{_resume_filename(full_name, resume_name)}.pdf"
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
# JobApplication ↔ Lead links.
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


# -----------------------------------------------------------------------------
# Leads: master record for people attached to applications.
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
    source: str | None = Field(default=None, max_length=100)


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
# The agent server (separate process, port 8002) discovers + verifies startups
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
    # Deep-research fields (all optional).
    brief: str | None = Field(default=None, max_length=4000)
    founding_year: str | None = Field(default=None, max_length=20)
    total_raised: str | None = Field(default=None, max_length=100)
    investors: list[str] = Field(default_factory=list, max_length=100)
    competitors: list[str] = Field(default_factory=list, max_length=100)
    key_people: list[str] = Field(default_factory=list, max_length=100)
    fit_score: float | None = Field(default=None, ge=0.0, le=1.0)
    fit_reason: str | None = Field(default=None, max_length=2000)
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
        port=int(os.getenv("PORT", "8001")),
        reload=os.getenv("RELOAD", "1") == "1",
        log_config=build_uvicorn_log_config(),
    )


if __name__ == "__main__":
    main()
