"""AI assists for the Resume Builder.

Four capabilities, all routed through generate._call_llm (so they inherit the
configured provider + fallback chain):

1. rewrite_bullet(text, context)         -> {"bullet": str}
2. tailor_profile(profile, jd, company)  -> {"profile": {...}, "notes": str}
3. draft_profile_from_notes(notes)       -> {"profile": {...}}
4. suggest_skills(profile)               -> {"skills": [...]}

The structured `profile` shape matches resume_render.py. Bullet text may use a
whitelist of inline markup (\\textbf{...}, \\emph{...}); everything else is
escaped at render time, so the model is free to emit those two commands to bold
metrics and key terms.
"""

from __future__ import annotations

import json
import re
from typing import Any

from generate import _call_llm, score_resume_text


# Shared rules so AI-written bullets match the resume's existing voice: strong
# action verbs, quantified impact, no fluff, ATS-friendly.
_RESUME_VOICE = """Bullet style rules (this resume's house style):
- Start with a strong past-tense action verb (Built, Engineered, Designed, Led, Optimized, Deployed).
- Lead with impact and quantify it: percentages, counts, dollars, latency, record volumes.
- One sentence, dense but readable. No personal pronouns, no filler ("responsible for", "helped with").
- You MAY bold key metrics and technologies using markdown **double asterisks** (e.g. **91% recall**, **140,000+ records**). Use *single asterisks* sparingly for italics. Use plain text otherwise — NO LaTeX, NO other markdown.
- Never invent numbers or facts not supported by the input. If no metric is given, keep it truthful and specific rather than fabricating one."""

_PROFILE_SHAPE = """A resume profile is JSON:
{
  "header": {"fullName","phone","email","linkedin","github","portfolio","scholar","location"},
  "education": [{"school","dates","degree","location"}],
  "experience": [{"company","dates","title","location","bullets":[string,...]}],
  "skills": [{"category","items"}],
  "projects": [{"name","date","bullets":[string,...]}]
}"""


REWRITE_BULLET_SYSTEM = f"""You rewrite a single resume bullet point to be stronger and more impactful.

{_RESUME_VOICE}

Return JSON only: {{"bullet": "<the rewritten bullet>"}}. Do not add a leading dash or bullet character."""


HIGHLIGHT_BULLET_SYSTEM = """You add emphasis to a single resume bullet by bolding its most important words.

HOW TO PROCESS (do this every time):
1. First, mentally STRIP any existing ** markers from the input — start from the plain sentence. Never just hand back the input because it already has some bold; you must re-balance the emphasis from scratch.
2. Then choose the few highest-signal spans and wrap each in markdown **double asterisks**.

WHAT TO BOLD (pick the best, in this priority order):
  1. Quantified impact / metrics — e.g. **91% recall**, **140,000+ records**, **$2M**, **43%**, **sub-200ms**, **80%**.
  2. The signature technology, model, or method — e.g. **Random Forest**, **Azure**, **ETL pipelines**, **RAG**, **agentic AI**, **two-tower recommender**.
  3. The core outcome or domain term most relevant to the role/project context provided.

HARD RULES:
- Do NOT rewrite, reword, reorder, add, or delete ANY words. The words (ignoring ** markers) MUST be identical to the input, in the same order. Bolding is your ONLY edit.
- HARD CAP: bold at most 4-5 words TOTAL across the whole bullet, as 2-4 tight spans. A span like "91% recall" (2 words) counts toward the total. Never bold a whole clause or the entire sentence.
- Almost every accomplishment bullet has at least one metric, technology, or key outcome worth bolding — find it. Only return the bullet with zero bold if it is genuinely a single plain phrase with no metric, tool, or notable term.
- Output plain text with only ** markers — NO LaTeX, no other markdown, no leading dash.

Return JSON only: {"bullet": "<the same words, with ** added around 2-4 key spans>"}."""


TAILOR_SYSTEM = f"""You tailor a resume to a specific job description.

{_PROFILE_SHAPE}

You receive the candidate's current profile (GROUND TRUTH — never fabricate experience, employers, degrees, or metrics that aren't there) plus a target job description. Your job:
- Reorder and rewrite EXISTING bullets to foreground the skills, tools, and outcomes the JD emphasises.
- Reorder skill items / categories so the most relevant appear first.
- Keep every employer, title, date, school, and number truthful and unchanged in meaning.
- Do NOT add jobs, projects, or credentials the candidate doesn't have.

{_RESUME_VOICE}

Return JSON only: {{"profile": <the full tailored profile, same shape>, "notes": "<2-3 sentence summary of what you changed and why>"}}."""


DRAFT_SYSTEM = f"""You extract a structured resume profile from rough notes, an old resume, or a brain-dump.

{_PROFILE_SHAPE}

Parse the input into that structure. Preserve all real facts (employers, titles, dates, schools, metrics). Where a field is unknown, use an empty string or empty array — never invent. Turn loose phrasing into clean resume bullets following the style rules below.

{_RESUME_VOICE}

Return JSON only: {{"profile": <the full profile, same shape>}}."""


SUGGEST_SYSTEM = f"""You organise and lightly expand the technical skills for a resume.

{_PROFILE_SHAPE}

Given the candidate's profile (GROUND TRUTH), return a list of skill categories ({{"category","items"}}) that organise and lightly expand the skills implied by their experience and projects. Only include tools/skills clearly supported by their roles and projects — do not pad with unrelated buzzwords.

Return JSON only: {{"skills": [{{"category": "...", "items": "comma, separated, list"}}, ...]}}."""


ROLE_TO_JD_SYSTEM = """You write a concise, realistic job description for a given role title so a resume can be scored against it.

The user gives a role (e.g. "Senior Data Scientist", "ML Engineer at a fintech", "Junior Backend Developer"). Produce a representative job description that a real company would post for THAT role at THAT seniority and domain — typical responsibilities and a requirements list.

Rules:
- Match the seniority implied by the title (junior vs senior vs lead/staff) for years of experience and scope.
- Match any domain/industry signal in the title (fintech, healthcare, marketplace, research) in the responsibilities and required background.
- Include a realistic mix of: required ("must have") skills/experience, important day-to-day skills, and a few nice-to-have / preferred items. Make the required-vs-preferred split explicit (use headers like "Requirements" and "Preferred / nice to have").
- Cover the dimensions a scorer needs: core technical skills/tools, type of experience, seniority/years, domain, education, and 1-2 ownership/impact expectations.
- Be realistic and balanced — do NOT pad with an impossible laundry list, and do NOT tailor it to any particular candidate (you don't see the resume).
- 150-300 words. Plain text only.

Return JSON only: {"job_description": "<the job description text>", "title": "<a clean normalized role title>"}."""


def _profile_json(profile: dict[str, Any]) -> str:
    return json.dumps(profile, ensure_ascii=False, indent=2)


def rewrite_bullet(text: str, context: str | None = None) -> dict[str, str]:
    ctx = f"\nCONTEXT (the role/project this bullet belongs to):\n{context}\n" if context else ""
    user = f"CURRENT BULLET:\n{text}\n{ctx}\nReturn JSON only."
    payload, _ = _call_llm(REWRITE_BULLET_SYSTEM, user, {"bullet"})
    return {"bullet": str(payload["bullet"]).strip()}


def _strip_markup(s: str) -> str:
    """Normalise a bullet to its bare words for comparison: drop **/* emphasis
    markers and collapse whitespace. Used to verify highlight didn't reword."""
    no_marks = s.replace("**", "").replace("*", "")
    return " ".join(no_marks.split())


_BOLD_SPAN_RE = re.compile(r"\*\*(.+?)\*\*")

# Deterministic safety cap. The model is told 4-5 words / 2-4 spans, but doesn't
# count reliably, so we enforce it server-side: keep spans in document order
# until we'd exceed the limits, then unwrap the rest back to plain text.
_MAX_BOLD_SPANS = 4
_MAX_BOLD_WORDS = 5


def _trim_bold(text: str) -> str:
    """Enforce the bold cap by unwrapping excess **spans** (keeping the earliest,
    which the model is told to order by importance)."""
    kept_spans = 0
    kept_words = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal kept_spans, kept_words
        inner = m.group(1)
        n_words = len(inner.split())
        if kept_spans < _MAX_BOLD_SPANS and kept_words + n_words <= _MAX_BOLD_WORDS:
            kept_spans += 1
            kept_words += n_words
            return f"**{inner}**"
        return inner  # unwrap: keep the words, drop the emphasis

    return _BOLD_SPAN_RE.sub(repl, text)


def highlight_bullet(text: str, context: str | None = None) -> dict[str, Any]:
    """Add **bold** emphasis to ~4-5 key words in a bullet WITHOUT rewording it.

    The model re-balances emphasis (strips any existing bold, re-picks). We
    verify the returned text has the same underlying words as the input; if the
    model drifted (reworded), we fall back to the original text so highlighting
    can never silently mangle a bullet.
    """
    ctx = f"\nCONTEXT (the role/project this bullet belongs to):\n{context}\n" if context else ""
    user = f"CURRENT BULLET:\n{text}\n{ctx}\nReturn JSON only."
    payload, _ = _call_llm(HIGHLIGHT_BULLET_SYSTEM, user, {"bullet"})
    highlighted = str(payload["bullet"]).strip()
    # Guard: the words (ignoring emphasis markers) must be unchanged. If the
    # model drifted (reworded), keep the original text but flag it so the caller
    # can tell "the AI misbehaved" apart from "nothing worth bolding".
    if _strip_markup(highlighted) != _strip_markup(text):
        return {"bullet": text, "drifted": True}
    # Enforce the bold cap deterministically (the model overshoots sometimes).
    return {"bullet": _trim_bold(highlighted)}


# NOTE: _call_llm's key validation requires every listed key to be a non-empty
# *string*. These resume calls return a dict (`profile`) or list (`skills`),
# so we pass no required keys and validate the structure ourselves below.


def _require_profile(payload: dict[str, Any]) -> dict[str, Any]:
    prof = payload.get("profile")
    if not isinstance(prof, dict):
        raise ValueError("model did not return a 'profile' object")
    return prof


def tailor_profile(
    profile: dict[str, Any], job_description: str, company: str | None = None
) -> dict[str, Any]:
    company_block = f"COMPANY:\n{company}\n\n" if company else ""
    user = (
        f"CURRENT PROFILE (ground truth):\n{_profile_json(profile)}\n\n"
        f"{company_block}"
        f"TARGET JOB DESCRIPTION:\n{job_description}\n\n"
        "Return JSON only."
    )
    payload, _ = _call_llm(TAILOR_SYSTEM, user, set(), max_tokens=4096)
    return {
        "profile": _require_profile(payload),
        "notes": str(payload.get("notes", "")).strip(),
    }


def draft_profile_from_notes(notes: str) -> dict[str, Any]:
    user = f"NOTES / OLD RESUME TEXT:\n{notes}\n\nReturn JSON only."
    payload, _ = _call_llm(DRAFT_SYSTEM, user, set(), max_tokens=4096)
    return {"profile": _require_profile(payload)}


def suggest_skills(profile: dict[str, Any]) -> dict[str, Any]:
    user = f"PROFILE:\n{_profile_json(profile)}\n\nReturn JSON only."
    payload, _ = _call_llm(SUGGEST_SYSTEM, user, set())
    skills = payload.get("skills")
    if not isinstance(skills, list):
        raise ValueError("model did not return a 'skills' list")
    return {"skills": skills}


def _plain(s: Any) -> str:
    """Coerce to a stripped string with markdown emphasis markers removed."""
    if not isinstance(s, str):
        return ""
    return s.replace("**", "").replace("*", "").strip()


_MONTHS = (
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _date_range(entry: dict[str, Any]) -> str:
    """Display date string from structured start/end fields, mirroring the
    frontend formatDateRange (and resume_render._date_range). Falls back to a
    literal ``dates`` string when structured fields are absent (e.g. AI draft)."""
    def endpoint(month: Any, year: Any) -> str:
        mm = _MONTHS[month] if isinstance(month, int) and 1 <= month <= 12 else ""
        yy = str(year) if isinstance(year, int) and year else ""
        return " ".join(p for p in (mm, yy) if p)

    if not any(k in entry for k in ("startMonth", "startYear", "endYear", "isPresent")):
        return _plain(entry.get("dates"))
    start = endpoint(entry.get("startMonth"), entry.get("startYear"))
    end = "Present" if entry.get("isPresent") else endpoint(
        entry.get("endMonth"), entry.get("endYear")
    )
    if start and end:
        return f"{start} – {end}"
    return start or end or ""


# Default section order (all visible) when a profile has no stored sectionOrder.
# Mirrors defaultSectionOrder() in frontend types.ts.
_DEFAULT_SECTION_ORDER = ("summary", "education", "experience", "skills", "projects")


def _section_order(profile: dict[str, Any]) -> list[tuple[str, bool]]:
    """Return [(key, visible), ...] from the profile, falling back to the default
    order (all visible) when none is stored. Unknown keys are ignored and any
    missing known section is appended visible, so text output never silently
    drops a section."""
    raw = profile.get("sectionOrder")
    out: list[tuple[str, bool]] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if key in _DEFAULT_SECTION_ORDER and key not in seen:
                out.append((key, item.get("visible") is not False))
                seen.add(key)
    for key in _DEFAULT_SECTION_ORDER:
        if key not in seen:
            out.append((key, True))
    return out


def _summary_text(p: dict[str, Any]) -> str:
    s = _plain(p.get("summary"))
    return f"\nSUMMARY\n{s}" if s else ""


def _education_text(p: dict[str, Any]) -> str:
    rows = [e for e in (p.get("education") or []) if _plain(e.get("school"))]
    if not rows:
        return ""
    lines = ["\nEDUCATION"]
    for e in rows:
        head = ", ".join(v for v in (_plain(e.get("degree")), _plain(e.get("school"))) if v)
        tail = " | ".join(v for v in (_plain(e.get("location")), _date_range(e)) if v)
        lines.append(f"- {head}{(' (' + tail + ')') if tail else ''}")
    return "\n".join(lines)


def _experience_text(p: dict[str, Any]) -> str:
    rows = [x for x in (p.get("experience") or []) if _plain(x.get("company"))]
    if not rows:
        return ""
    lines = ["\nPROFESSIONAL EXPERIENCE"]
    for x in rows:
        head = " — ".join(v for v in (_plain(x.get("title")), _plain(x.get("company"))) if v)
        tail = " | ".join(v for v in (_plain(x.get("location")), _date_range(x)) if v)
        lines.append(f"\n{head}{(' (' + tail + ')') if tail else ''}")
        for b in x.get("bullets") or []:
            bt = _plain(b)
            if bt:
                lines.append(f"  - {bt}")
    return "\n".join(lines)


def _skills_text(p: dict[str, Any]) -> str:
    rows = [s for s in (p.get("skills") or []) if _plain(s.get("items"))]
    if not rows:
        return ""
    lines = ["\nTECHNICAL SKILLS"]
    for s in rows:
        cat = _plain(s.get("category"))
        items = _plain(s.get("items"))
        lines.append(f"- {cat + ': ' if cat else ''}{items}")
    return "\n".join(lines)


def _projects_text(p: dict[str, Any]) -> str:
    rows = [pr for pr in (p.get("projects") or []) if _plain(pr.get("name"))]
    if not rows:
        return ""
    lines = ["\nPROJECTS"]
    for pr in rows:
        nm = _plain(pr.get("name"))
        dt = _plain(pr.get("date"))
        lines.append(f"\n{nm}{(' (' + dt + ')') if dt else ''}")
        for b in pr.get("bullets") or []:
            bt = _plain(b)
            if bt:
                lines.append(f"  - {bt}")
    return "\n".join(lines)


_SECTION_TEXT = {
    "summary": _summary_text,
    "education": _education_text,
    "experience": _experience_text,
    "skills": _skills_text,
    "projects": _projects_text,
}


def profile_to_text(profile: dict[str, Any]) -> str:
    """Render an in-memory builder profile to a clean plain-text resume.

    Used to score the resume the user is *currently editing* (which may be
    unsaved) against a job description, reusing the same scorer the rest of the
    app uses. Markdown bold/italic markers are stripped so the model reads prose.
    Sections are emitted in the profile's `sectionOrder`, skipping hidden ones —
    kept in lockstep with frontend renderText.ts profileToText.
    """
    p = profile or {}
    lines: list[str] = []

    header = p.get("header") or {}
    lines.append(_plain(header.get("fullName")) or "Resume")
    contact = " | ".join(
        v for v in (
            _plain(header.get("location")),
            _plain(header.get("email")),
            _plain(header.get("phone")),
            _plain(header.get("linkedin")),
            _plain(header.get("github")),
            _plain(header.get("portfolio")),
        ) if v
    )
    if contact:
        lines.append(contact)

    for key, visible in _section_order(p):
        if not visible:
            continue
        emit = _SECTION_TEXT.get(key)
        if emit:
            block = emit(p)
            if block:
                lines.append(block)

    return "\n".join(lines).strip()


def role_to_jd(role: str) -> dict[str, str]:
    """Synthesize a realistic job description from a role title.

    Lets the user score against a role ("Senior ML Engineer") without pasting a
    full JD — the model generates representative requirements for that role,
    which the scorer then grades against. Returns {"job_description", "title"}.
    """
    if not role or not role.strip():
        raise ValueError("Enter a role to generate requirements for.")
    user = f"ROLE:\n{role.strip()}\n\nReturn JSON only."
    payload, _ = _call_llm(ROLE_TO_JD_SYSTEM, user, {"job_description"}, max_tokens=1200)
    return {
        "job_description": str(payload["job_description"]).strip(),
        "title": str(payload.get("title", role)).strip() or role.strip(),
    }


def score_profile(
    profile: dict[str, Any],
    job_description: str | None = None,
    company: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    """Score the in-memory builder profile against a job description or a role.

    Pass `job_description` to score against a pasted JD, or `role` (e.g.
    "Senior Data Scientist") to have a realistic JD generated for that role
    first. If both are given, `job_description` wins. Renders the profile to
    plain text and runs it through the shared scorer.

    Returns {"score", "score_100", "verdict", "breakdown"} plus, when a role was
    used, "generated_jd" and "role_title" so the UI can show what was scored.
    """
    resume_text = profile_to_text(profile)
    if not resume_text.strip():
        raise ValueError("Add some resume content before scoring.")

    jd = (job_description or "").strip()
    generated: dict[str, str] | None = None
    if not jd:
        if not (role or "").strip():
            raise ValueError("Provide a role or a job description to score against.")
        generated = role_to_jd(role)  # type: ignore[arg-type]
        jd = generated["job_description"]

    out = score_resume_text(jd, resume_text, company)
    if generated is not None:
        out["generated_jd"] = generated["job_description"]
        out["role_title"] = generated["title"]
    return out
