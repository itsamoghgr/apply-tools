"""Generation modes:

1. generate_cover_letter(company, jd) -> bytes (PDF)
2. generate_application_email(company, jd, intent) -> {"subject", "body"}
3. generate_outreach_message(profile_text, channel, context) -> {"message", "char_count", "subject"?}
4. score_jd_fit(job_description, company) -> {"score": int 0-10, "verdict": str}

The first three share the resume.txt ground truth and anti-AI-slop voice rules.
The score function uses a separate, neutral-analytical prompt - it's a triage
tool, not a written artifact.
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv

from latex_utils import compile_latex, escape_latex


load_dotenv()

BACKEND_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BACKEND_DIR / "template.tex"
RESUMES_DIR = BACKEND_DIR / "resumes"
LEGACY_RESUME_PATH = BACKEND_DIR / "resume.txt"
RESUME_ID_RE = re.compile(r"^[a-z0-9_-]+$")
DEFAULT_RESUME_ID = "default"

DEFAULT_MODEL = os.environ.get("MODEL", "claude-opus-4-5")
MAX_TOKENS = 2048


# -----------------------------------------------------------------------------
# Shared voice rules - applied to every mode.
# -----------------------------------------------------------------------------

VOICE_RULES = """# Voice reference - this is Amogh's actual cover letter for Faire. Whatever you write should sound like the same person wrote it:

  "I'm applying for the Data Science Intern role at Faire. A marketplace that uses ML to help independent retailers compete with Amazon, not by copying them, but by connecting them to better products, is the kind of problem I want to work on.

  My background maps to what you're building. I've worked on search and retrieval systems using LangChain and semantic search, processing 100K+ records and optimizing for relevance. I've built demand forecasting models on 2M+ transaction records that reduced stockouts by 13%. And I've run A/B tests that actually moved conversion metrics, not just hit statistical significance.

  What draws me to Faire specifically is the two-sided marketplace complexity. Balancing retailer needs against brand discovery, managing cold start problems, optimizing for long-term retention rather than short term clicks, these are harder problems than single sided optimization, and more interesting.

  I'm most excited about the Search or Retailer Products teams. I've worked with GenAI and retrieval systems, and I've built predictive models that drove real business decisions. I'm comfortable going end-to-end: defining the problem, pulling the data, building the model, and communicating the results to people who don't care about the technical details.

  I'm finishing my MS in Data Science at GW this May. Happy to chat if there's a fit."

# Voice rules - non-negotiable

- Use contractions: "I'm", "I've", "don't", "it's". Never "I am applying" or "I have worked".
- Short sentences. Mix in short fragments. Vary rhythm.
- State opinions where relevant. Don't just praise - say WHY something is interesting.
- Cite concrete numbers and tools from the resume when relevant. Don't list everything.
- Sign off short and casual when a sign-off is needed.

# Hard bans (these are AI tells - never emit any of them)

- "passionate about", "deeply passionate", "thrilled", "excited to apply", "thrilled to apply"
- "uniquely positioned", "uniquely qualified", "perfect fit", "ideal candidate"
- "leverage", "synergize", "spearhead", "spearheaded", "drive impact", "deliver value", "value-add"
- "robust", "cutting-edge", "innovative solutions", "best-in-class", "world-class"
- "I would like to express my interest", "I am writing to apply", "Please find attached", "I look forward to hearing"
- "demonstrated ability to", "proven track record", "results-driven", "detail-oriented", "team player"
- "Reaching out", "I hope this finds you well", "I wanted to reach out"
- Em-dashes (-). Use commas, periods, or parentheses instead.
- Tricolons / lists of three abstract qualities ("hardworking, dedicated, and motivated").
- Restating the JD's bullet points back at them.
- Buzzword sentences with no concrete content.

# Substance rules

- Never invent experience, employers, projects, metrics, tools, or skills not in the resume.
- If something is asked for that's not in the resume, just don't mention it. Do not pretend.
"""


# -----------------------------------------------------------------------------
# Mode-specific system prompts.
# -----------------------------------------------------------------------------

COVER_LETTER_SYSTEM = f"""You are ghostwriting cover letters in the candidate's own voice. The candidate is Amogh Ramagiri, a Data Scientist finishing his MS at GW.

You will receive: his resume (ground truth about him), a company name, and a job description.

Return STRICT JSON only - no markdown fences, no commentary before or after.

Output schema (all fields required):
{{
  "role_title": "<the role title from the JD, e.g. 'Senior ML Engineer'>",
  "hiring_manager": "<a name if the JD mentions one, otherwise 'Hiring Team'>",
  "body_paragraphs": "<the entire letter body as LaTeX-safe text>"
}}

{VOICE_RULES}

# Cover letter structure rules

- 3-4 paragraphs total. ~250-350 words.
- First sentence: "I'm applying for the [Role] at [Company]." or a tight variation. Never "I am writing to express my interest...", "I am thrilled to apply...".
- The opening paragraph names the role and company and gives a one-sentence reason this matters.
- The middle paragraph(s) cite 2-4 specific resume items mapped to JD priorities.
- One paragraph shows you understand the company's specific problem space, not just the generic version of the role.
- The closing paragraph is one or two sentences. Mention finishing the MS at GW this May if relevant. End with a short, casual line.

# Output format rules for body_paragraphs

- Plain text with blank lines between paragraphs. No markdown.
- The ONLY LaTeX commands you may emit are: \\emph{{...}}, \\textbf{{...}}, and \\\\ (forced line break, used sparingly). Use them rarely - the Faire letter above uses none.
- Do NOT emit: $, &, %, #, _, {{, }}, ~, ^, or backslashes outside the whitelist. If you must reference a literal percent or ampersand, rephrase ("13 percent", "and").
- Do NOT include the salutation ("Dear ...") or sign-off ("Best Regards,") - those are in the template.
"""


APPLICATION_EMAIL_SYSTEM = f"""You are ghostwriting a job-application email in the candidate's own voice. The candidate is Amogh Ramagiri, a Data Scientist finishing his MS at GW.

You will receive: his resume (ground truth about him), a company name, a job description, and an optional intent note describing what kind of email this should be (e.g. asking for consideration, asking for a referral, asking to be passed to a hiring manager).

Return STRICT JSON only - no markdown fences, no commentary before or after.

Output schema (all fields required):
{{
  "subject": "<concrete, specific email subject. Include role title and his name. Max 70 chars. Examples: 'Application: Data Science Intern - Amogh Ramagiri', 'Quick note re: ML Engineer role - Amogh Ramagiri'>",
  "body": "<plain-text email body, including greeting and signoff>"
}}

{VOICE_RULES}

# Application email structure rules

- Body length: 100-180 words. Shorter is better than longer.
- Open with "Hi {{Name}}," if a name is given in the JD or intent note. Otherwise "Hi there,". Never "Dear Hiring Manager," (too formal for email) and never "To Whom It May Concern,".
- One sentence stake: which role at which company, and why it caught your attention.
- 1-2 short paragraphs citing 1-3 specific resume items that map to the JD. Real numbers / tools.
- A short ask aligned to the intent note if given (e.g. "Would love to be considered." / "Any chance you could pass this along to the hiring team?").
- Sign off with "Best,\\nAmogh" or "Thanks,\\nAmogh". Never "Sincerely" or long signoffs.

# Format
- Plain text only. No HTML, no markdown.
- Use real newlines between paragraphs (the JSON string can contain \\n).
- No subject line inside the body - the subject is its own field.
"""


OUTREACH_INVITATION_SYSTEM = f"""You are ghostwriting a LinkedIn connection request note in the candidate's own voice. The candidate is Amogh Ramagiri, a Data Scientist finishing his MS at GW.

You will receive: his resume (ground truth about him), the target person's LinkedIn profile (pasted text), and an optional context note describing the angle ("looking for referral at their company", "wanted to chat about their work in X", etc.).

LinkedIn caps these notes at 300 characters. Target 240-280 characters to leave a buffer.

Return STRICT JSON only - no markdown fences, no commentary before or after.

Output schema (all fields required):
{{
  "message": "<the connection request note, plain text, NO greeting like 'Hi X,' and NO signoff. Just the note itself. 240-280 characters.>"
}}

{VOICE_RULES}

# LinkedIn invitation rules

- HARD MAXIMUM: 290 characters. If you're at 300, you've failed.
- Target 240-280 characters.
- No greeting ("Hi X,") and no signoff. The recipient sees this inline; greetings waste characters.
- Anchor on ONE specific thing from their profile (a project, a company, a post topic). Generic flattery is wasted.
- One sentence connecting their work to his (a relevant experience or skill from the resume).
- One short closing sentence with the ask, soft. e.g. "Would love to connect." / "Open to chatting if you have a moment."
- Use first-name only if their first name appears in the profile.
- Use contractions. No buzzwords.
"""


OUTREACH_LINKEDIN_MESSAGE_SYSTEM = f"""You are ghostwriting a LinkedIn message (DM or InMail) in the candidate's own voice. The candidate is Amogh Ramagiri, a Data Scientist finishing his MS at GW.

You will receive: his resume (ground truth about him), the target person's LinkedIn profile (pasted text), and an optional context note describing the angle ("looking for referral at their company", "want to chat about their work", etc.).

Return STRICT JSON only - no markdown fences, no commentary before or after.

Output schema (all fields required):
{{
  "message": "<the message, plain text, including greeting and short signoff. 80-180 words.>"
}}

{VOICE_RULES}

# LinkedIn message rules

- Length: 80-180 words.
- Open with "Hi {{first name}}," using their actual first name from the profile.
- Anchor on ONE specific thing from their profile (a project, a recent role, a topic they post about). Show you actually read it.
- One short paragraph connecting their work to his with 1-2 concrete resume items.
- Soft ask. e.g. "Would love to hear how you got into X." / "Any chance you'd have 15 minutes for a quick chat?". No hard close.
- Sign off with "Thanks,\\nAmogh" or just "- Amogh". Casual.
- Use real newlines between paragraphs (the JSON string can contain \\n).
"""


OUTREACH_EMAIL_SYSTEM = f"""You are ghostwriting an outreach email to a specific person, in the candidate's own voice. The candidate is Amogh Ramagiri, a Data Scientist finishing his MS at GW.

You will receive: his resume (ground truth about him), the target person's LinkedIn profile (pasted text), and an optional context note describing the angle.

Return STRICT JSON only - no markdown fences, no commentary before or after.

Output schema (all fields required):
{{
  "subject": "<concrete subject line, max 60 chars. Never 'Reaching out' or 'Hello'. Reference something specific from their profile or a clear mutual interest.>",
  "message": "<plain-text email body including greeting and signoff. 100-200 words.>"
}}

{VOICE_RULES}

# Outreach email rules

- Body length: 100-200 words.
- Open with "Hi {{first name}}," using their actual first name from the profile.
- First sentence anchors on one specific thing from their profile.
- One short paragraph connecting their work to his with 1-2 concrete resume items.
- Soft, specific ask. No "let me know if you have any questions" filler.
- Sign off with "Thanks,\\nAmogh" or "Best,\\nAmogh". Casual.
- Plain text only, no HTML or markdown. Use real newlines between paragraphs.
"""


SCORE_SYSTEM = """You are evaluating fit between Amogh Ramagiri's resume and a job description. He's a Data Scientist finishing his MS in Data Science at GW (May 2026), with prior roles at Fulton Bank, Factocart, NCUE, and Wodo. The resume provided is ground truth - do not assume anything not in it.

Your job is to (1) extract the JD's actual requirements as a rubric, (2) grade the resume against each requirement with evidence, (3) produce a one-line summary. The numeric score is computed automatically from your rubric grades - DO NOT output a score yourself.

Return STRICT JSON only - no markdown fences, no commentary before or after.

Output schema (all fields required):
{
  "rubric": [
    {"id": "r1", "requirement": "<short phrase, e.g. 'Python production experience'>", "tier": "must_have"|"important"|"nice_to_have", "category": "skill"|"experience"|"impact"|"education"}
  ],
  "coverage": [
    {"id": "r1", "status": "yes"|"partial"|"no", "evidence": "<short quote or paraphrase from the resume, or empty string if status is 'no'>"}
  ],
  "verdict_summary": "<one line, format: '<strongest match>; <biggest gap>'. NO leading score. Max 130 characters.>"
}

# Rubric extraction rules

- Extract 8-12 requirements. Fewer than 8 means you missed something; more than 12 means you're padding.
- Cover ALL of these dimensions when the JD touches them: technical skills/tools, type of experience (modeling/analytics/engineering), seniority/years, domain (industry, business problem), education, soft requirements (communication, ownership, etc.).
- Tier each requirement honestly:
  - `must_have`: the JD says "required", "must have", "X+ years", or names a specific stack the role is built around. Missing this should hurt.
  - `important`: clearly emphasized in the JD, repeated, or part of the day-to-day. A reasonable hiring manager would weight it heavily.
  - `nice_to_have`: listed under "bonus", "preferred", "plus", or mentioned once in passing.
- Tag each requirement with one category:
  - `skill`: a tool, language, framework, library, or technical method named in the JD (e.g. Python, SQL, Spark, A/B testing, RAG).
  - `experience`: type of role, seniority/years, domain, or industry the JD requires (e.g. "5+ years industry", "fintech background", "marketplace experience").
  - `impact`: a JD-stated outcome, ownership scope, or responsibility (e.g. "ship to production", "own end-to-end model lifecycle", "drive measurable lift on KPIs", "lead a team of 3").
  - `education`: a degree or certification (e.g. "MS in CS or DS", "PhD", "AWS cert").
- DO NOT use any other category values. In particular, do NOT use `formatting`, `quantifiability`, `clarity`, or anything that measures resume polish - those are not JD-fit signals.
- Use stable ids `r1`, `r2`, ... in order. Coverage entries must reference the same ids.
- Phrase requirements as the JD frames them, not as the resume frames them. Don't bias the rubric toward the resume.

# Coverage grading rules

- One coverage entry per rubric id. Same length as `rubric`.
- `yes`: the resume directly demonstrates this requirement. Cite the evidence (project name, role, metric, or short paraphrase). Be specific.
- `partial`: adjacent or related but not a clean match. Examples: JD wants 5+ years, resume shows 2-3 in-role years; JD wants Spark, resume shows PySpark; JD wants production NLP, resume shows NLP coursework. Always cite what's actually there.
- `no`: not present in the resume in any form. Empty evidence string is fine.
- DO NOT invent evidence. If you can't find it in the resume, mark `no`.

# verdict_summary rules

- One sentence. No leading number (e.g. NOT "8/10 - ..."). Server adds the score.
- Format: "<strongest match>; <biggest gap>". Two clauses separated by a semicolon.
- Be specific. Strongest match should name a concrete thing (tool, domain, metric, project). Biggest gap should name what's actually missing or weak.
- If there's no real gap, say so explicitly: "no major gaps".
- If the role is fundamentally mismatched, say so: "Backend SWE role; resume is data/ML focused, not aligned".

# Calibration

- Be honest. The resume already has real strengths and real gaps. Your job is to map them, not to flatter or deflate.
- A `must_have` graded `no` is a real signal. Don't soften it to `partial` to be nice.
- A `nice_to_have` graded `no` is barely a signal. Don't promote it to `partial` to seem balanced.
- If the JD is vague, extract fewer requirements rather than padding with imagined ones.
"""


# -----------------------------------------------------------------------------
# Generic Claude call + JSON parsing helpers.
# -----------------------------------------------------------------------------


def _strip_json_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` fences if Claude wrapped its output."""
    text = text.strip()
    fence_re = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)
    m = fence_re.match(text)
    if m:
        return m.group(1).strip()
    return text


def _parse_claude_json(raw: str) -> dict[str, Any]:
    cleaned = _strip_json_fences(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise ValueError(
            f"Could not parse Claude response as JSON: {e}\n"
            f"--- raw response ---\n{raw}"
        ) from e


def _validate_keys(payload: dict[str, Any], required: set[str]) -> None:
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"Claude response missing required keys: {missing}")
    for key in required:
        v = payload[key]
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"Claude response field '{key}' is empty or non-string")


def _require_api_key() -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Copy backend/.env.example to backend/.env "
            "and add your key."
        )
    return api_key


def _strip_label_header(text: str) -> str:
    """Drop a leading `# Label: ...` line if present."""
    lines = text.splitlines()
    if lines and lines[0].lstrip().lower().startswith("# label:"):
        return "\n".join(lines[1:]).lstrip("\n")
    return text


def _label_for(path: Path) -> str:
    """Return the friendly label for a resume file.

    If the file's first line is `# Label: <name>`, use that; otherwise derive a
    label from the filename stem (replace -/_ with spaces, title case).
    """
    try:
        first_line = path.open("r", encoding="utf-8").readline().strip()
    except OSError:
        first_line = ""
    if first_line.lower().startswith("# label:"):
        label = first_line.split(":", 1)[1].strip()
        if label:
            return label
    return path.stem.replace("-", " ").replace("_", " ").title()


def _read_resume(resume_id: str | None = None) -> str:
    """Read a resume by id, returning its content (label header stripped).

    Defaults to "default" when no id is supplied. Validates that the id is a
    safe slug. Falls back to the legacy resume.txt for back-compat. If no id
    was specified and there's no `default.txt`, falls back to the first
    available resume file - makes CLI / curl callers work without forcing the
    user to maintain a file literally named `default.txt`.
    """
    is_implicit_default = resume_id is None or not str(resume_id).strip()
    rid = (resume_id or DEFAULT_RESUME_ID).strip()
    if not RESUME_ID_RE.match(rid):
        raise ValueError(f"Invalid resume_id: {rid!r}")

    path = RESUMES_DIR / f"{rid}.txt"
    if path.is_file():
        return _strip_label_header(path.read_text(encoding="utf-8"))

    # Legacy fallback: pre-multi-resume installs only had backend/resume.txt.
    if rid == DEFAULT_RESUME_ID and LEGACY_RESUME_PATH.is_file():
        return _strip_label_header(LEGACY_RESUME_PATH.read_text(encoding="utf-8"))

    # Implicit-default fallback: caller didn't pick anything and there is no
    # `default.txt` on disk. Use the first available resume so the call works
    # instead of forcing every caller to know the active filename.
    if is_implicit_default and RESUMES_DIR.is_dir():
        candidates = sorted(p for p in RESUMES_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".txt")
        if candidates:
            return _strip_label_header(candidates[0].read_text(encoding="utf-8"))

    raise FileNotFoundError(f"Unknown resume_id: {rid}")


def list_resumes() -> list[dict[str, str]]:
    """List available resumes as [{"id", "label"}], 'default' first then alpha."""
    entries: list[dict[str, str]] = []
    if RESUMES_DIR.is_dir():
        for path in RESUMES_DIR.iterdir():
            if path.is_file() and path.suffix.lower() == ".txt":
                entries.append({"id": path.stem, "label": _label_for(path)})
    elif LEGACY_RESUME_PATH.is_file():
        # Synthesize a default entry so the popup has something to show during
        # an upgrade where the user hasn't moved the file yet.
        entries.append({"id": DEFAULT_RESUME_ID, "label": "Default"})

    entries.sort(
        key=lambda e: (0 if e["id"] == DEFAULT_RESUME_ID else 1, e["id"])
    )
    return entries


def _call_claude(
    system_prompt: str,
    user_message: str,
    required_keys: set[str],
    *,
    extra_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Single Claude messages.create call, JSON-parsed and key-validated.

    `extra_messages` lets a caller continue a conversation (e.g. for the
    invitation retry-on-too-long flow).
    """
    api_key = _require_api_key()
    client = Anthropic(api_key=api_key)

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    if extra_messages:
        messages.extend(extra_messages)

    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=messages,
    )

    raw_text = "".join(
        block.text
        for block in response.content
        if getattr(block, "type", None) == "text"
    )
    payload = _parse_claude_json(raw_text)
    _validate_keys(payload, required_keys)
    return payload, raw_text


def _substitute(template: str, mapping: dict[str, str]) -> str:
    out = template
    for key, value in mapping.items():
        out = out.replace("{{" + key + "}}", value)
    return out


# -----------------------------------------------------------------------------
# User-message builders.
# -----------------------------------------------------------------------------


def _user_msg_cover_letter(company: str, jd: str, resume: str) -> str:
    return (
        "RESUME (ground truth - do not invent beyond this):\n"
        f"{resume}\n\n"
        "COMPANY:\n"
        f"{company}\n\n"
        "JOB DESCRIPTION:\n"
        f"{jd}\n\n"
        "Return JSON only."
    )


def _user_msg_application_email(
    company: str, jd: str, intent: str | None, resume: str
) -> str:
    intent_block = (
        f"INTENT NOTE (what kind of email this is):\n{intent}\n\n" if intent else ""
    )
    return (
        "RESUME (ground truth - do not invent beyond this):\n"
        f"{resume}\n\n"
        "COMPANY:\n"
        f"{company}\n\n"
        "JOB DESCRIPTION:\n"
        f"{jd}\n\n"
        f"{intent_block}"
        "Return JSON only."
    )


def _user_msg_outreach(
    profile_text: str, context: str | None, resume: str
) -> str:
    context_block = (
        f"CONTEXT (what this is about, the angle):\n{context}\n\n" if context else ""
    )
    return (
        "RESUME (ground truth about Amogh - do not invent beyond this):\n"
        f"{resume}\n\n"
        "TARGET PERSON'S LINKEDIN PROFILE (their ground truth):\n"
        f"{profile_text}\n\n"
        f"{context_block}"
        "Return JSON only."
    )


# -----------------------------------------------------------------------------
# Public entry points.
# -----------------------------------------------------------------------------


def generate_cover_letter(
    company: str, jd: str, resume_id: str | None = None
) -> bytes:
    """Generate a tailored cover letter PDF for the given company + job description.

    Returns PDF bytes. Raises if the API key is missing, Claude returns
    unparseable output, the template is malformed, or tectonic fails.
    """
    if not company or not company.strip():
        raise ValueError("company must not be empty")
    if not jd or not jd.strip():
        raise ValueError("job_description must not be empty")

    resume = _read_resume(resume_id)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    payload, _ = _call_claude(
        COVER_LETTER_SYSTEM,
        _user_msg_cover_letter(company.strip(), jd.strip(), resume),
        required_keys={"role_title", "hiring_manager", "body_paragraphs"},
    )

    substitutions = {
        "COMPANY_NAME": escape_latex(company.strip()),
        "ROLE_TITLE": escape_latex(payload["role_title"].strip()),
        "HIRING_MANAGER_OR_TEAM": escape_latex(payload["hiring_manager"].strip()),
        # Body is intentionally NOT escaped - Claude is instructed to only emit
        # the whitelist of \emph, \textbf, \\ and blank-line paragraph breaks.
        "BODY_PARAGRAPHS": payload["body_paragraphs"].strip(),
    }
    tex_source = _substitute(template, substitutions)
    return compile_latex(tex_source, jobname="cover_letter")


def generate_application_email(
    company: str,
    jd: str,
    intent: str | None = None,
    resume_id: str | None = None,
) -> dict[str, str]:
    """Generate a job-application email. Returns {'subject': ..., 'body': ...}."""
    if not company or not company.strip():
        raise ValueError("company must not be empty")
    if not jd or not jd.strip():
        raise ValueError("job_description must not be empty")

    resume = _read_resume(resume_id)
    payload, _ = _call_claude(
        APPLICATION_EMAIL_SYSTEM,
        _user_msg_application_email(
            company.strip(), jd.strip(), intent.strip() if intent else None, resume
        ),
        required_keys={"subject", "body"},
    )
    return {"subject": payload["subject"].strip(), "body": payload["body"].strip()}


VALID_OUTREACH_CHANNELS = ("linkedin_invitation", "linkedin_message", "email")

# LinkedIn caps invitation notes at 300 chars. We target ≤280 with a 290 hard limit.
INVITATION_HARD_MAX = 290
INVITATION_RETRY_TARGET = 270


def _system_for_channel(channel: str) -> str:
    if channel == "linkedin_invitation":
        return OUTREACH_INVITATION_SYSTEM
    if channel == "linkedin_message":
        return OUTREACH_LINKEDIN_MESSAGE_SYSTEM
    if channel == "email":
        return OUTREACH_EMAIL_SYSTEM
    raise ValueError(
        f"channel must be one of {VALID_OUTREACH_CHANNELS}, got {channel!r}"
    )


def _required_keys_for_channel(channel: str) -> set[str]:
    if channel == "email":
        return {"subject", "message"}
    return {"message"}


def generate_outreach_message(
    profile_text: str,
    channel: str,
    context: str | None = None,
    resume_id: str | None = None,
) -> dict[str, Any]:
    """Generate an outreach message for the given channel.

    Returns:
      - linkedin_invitation: {"message": str, "char_count": int}
      - linkedin_message: {"message": str, "char_count": int}
      - email: {"subject": str, "message": str, "char_count": int}
    """
    if channel not in VALID_OUTREACH_CHANNELS:
        raise ValueError(
            f"channel must be one of {VALID_OUTREACH_CHANNELS}, got {channel!r}"
        )
    if not profile_text or not profile_text.strip():
        raise ValueError("profile_text must not be empty")

    resume = _read_resume(resume_id)
    system_prompt = _system_for_channel(channel)
    required_keys = _required_keys_for_channel(channel)

    user_message = _user_msg_outreach(
        profile_text.strip(), context.strip() if context else None, resume
    )

    payload, raw = _call_claude(system_prompt, user_message, required_keys)

    message = payload["message"].strip()

    # LinkedIn invitation char-limit enforcement: one retry if over 290.
    if channel == "linkedin_invitation" and len(message) > INVITATION_HARD_MAX:
        retry_messages: list[dict[str, str]] = [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    f"That was {len(message)} characters. Hard maximum is "
                    f"{INVITATION_HARD_MAX}. Rewrite shorter, target "
                    f"{INVITATION_RETRY_TARGET} characters. Same JSON schema, "
                    "no fences, no commentary."
                ),
            },
        ]
        payload, _ = _call_claude(
            system_prompt,
            user_message,
            required_keys,
            extra_messages=retry_messages,
        )
        message = payload["message"].strip()
        if len(message) > 300:
            raise ValueError(
                f"LinkedIn invitation still {len(message)} characters after retry "
                f"(max 300). Try shortening the context note or regenerating."
            )

    result: dict[str, Any] = {"message": message, "char_count": len(message)}
    if channel == "email":
        result["subject"] = payload["subject"].strip()
    return result


def _user_msg_score(jd: str, company: str | None, resume: str) -> str:
    company_block = f"COMPANY:\n{company}\n\n" if company else ""
    return (
        "RESUME (ground truth - this is what Amogh actually has):\n"
        f"{resume}\n\n"
        f"{company_block}"
        "JOB DESCRIPTION:\n"
        f"{jd}\n\n"
        "Return JSON only with fields: rubric, coverage, verdict_summary."
    )


# -----------------------------------------------------------------------------
# Scoring composite + tuning knobs.
#
# Edit these in one place to retune. The model emits the rubric and per-
# requirement coverage; we compose the final 0-100 score deterministically.
# -----------------------------------------------------------------------------

TIER_WEIGHT = {"must_have": 3.0, "important": 2.0, "nice_to_have": 1.0}
STATUS_VALUE = {"yes": 1.0, "partial": 0.5, "no": 0.0}
VALID_TIERS = frozenset(TIER_WEIGHT)
VALID_STATUSES = frozenset(STATUS_VALUE)
RUBRIC_MAX_ITEMS = 15

# Caps when the resume misses hard requirements - missing one must-have should
# never read as 8/10. Edit thresholds here to retune severity.
MISSING_MUST_HAVE_CAP_1 = 65  # one must-have missing
MISSING_MUST_HAVE_CAP_2 = 50  # two or more must-haves missing

# Categories the model assigns to each rubric item (informational only - the
# composite score does NOT use category weights). Used for surfacing
# per-category subscores in the breakdown so the user can see WHERE the
# strengths and gaps land. We deliberately omit resume-quality categories
# (formatting, quantifiability) since they're roughly constant per resume
# and would just add noise across JDs.
VALID_CATEGORIES = ("skill", "experience", "impact", "education")
DEFAULT_CATEGORY = "skill"


def _normalize_rubric_and_coverage(
    raw_rubric: Any, raw_coverage: Any
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Validate and align the model's rubric + coverage lists.

    Drops malformed rubric/coverage entries, caps rubric size, and pads the
    coverage list so every rubric item has a status (defaulting to 'no' if
    the model forgot one). Raises ValueError if the rubric ends up empty.
    """
    if not isinstance(raw_rubric, list):
        raise ValueError("rubric must be a list")
    if not isinstance(raw_coverage, list):
        raise ValueError("coverage must be a list")

    rubric: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for item in raw_rubric:
        if not isinstance(item, dict):
            continue
        rid = item.get("id")
        req = item.get("requirement")
        tier = item.get("tier")
        category = item.get("category")
        if not isinstance(rid, str) or not rid.strip():
            continue
        if not isinstance(req, str) or not req.strip():
            continue
        if tier not in VALID_TIERS:
            continue
        if rid in seen_ids:
            continue
        # Category is informational; default rather than drop the requirement
        # over a typo or missing field. The model is told the allowed values.
        if not isinstance(category, str) or category not in VALID_CATEGORIES:
            category = DEFAULT_CATEGORY
        seen_ids.add(rid)
        rubric.append(
            {
                "id": rid.strip(),
                "requirement": req.strip(),
                "tier": tier,
                "category": category,
            }
        )
        if len(rubric) >= RUBRIC_MAX_ITEMS:
            break

    if not rubric:
        raise ValueError("Claude returned no usable rubric items")

    cov_by_id: dict[str, dict[str, str]] = {}
    for item in raw_coverage:
        if not isinstance(item, dict):
            continue
        rid = item.get("id")
        status = item.get("status")
        evidence = item.get("evidence", "")
        if not isinstance(rid, str) or rid not in seen_ids:
            continue
        if status not in VALID_STATUSES:
            continue
        if not isinstance(evidence, str):
            evidence = ""
        cov_by_id[rid] = {
            "id": rid,
            "status": status,
            "evidence": evidence.strip(),
        }

    coverage = [
        cov_by_id.get(r["id"], {"id": r["id"], "status": "no", "evidence": ""})
        for r in rubric
    ]
    return rubric, coverage


def _compose_score(
    rubric: list[dict[str, str]], coverage: list[dict[str, str]]
) -> dict[str, Any]:
    """Deterministic composite score from rubric tiers + per-requirement status.

    Returns a dict with score (0-10), score_100 (0-100), and a breakdown of
    coverage per tier plus the list of missing must-have requirements.
    """
    pairs = list(zip(rubric, coverage))

    earned = sum(TIER_WEIGHT[r["tier"]] * STATUS_VALUE[c["status"]] for r, c in pairs)
    total = sum(TIER_WEIGHT[r["tier"]] for r in rubric)
    score_100 = round(100 * earned / total) if total else 0

    missing_must_haves = [
        r["requirement"]
        for r, c in pairs
        if r["tier"] == "must_have" and c["status"] == "no"
    ]
    if len(missing_must_haves) >= 2:
        score_100 = min(score_100, MISSING_MUST_HAVE_CAP_2)
    elif len(missing_must_haves) == 1:
        score_100 = min(score_100, MISSING_MUST_HAVE_CAP_1)

    score_100 = max(0, min(100, score_100))
    # Half-up rounding for the user-visible 0-10 display (Python's round() is
    # banker's). With caps at 65/50, this maps to 7/5 which matches the bands.
    score_10 = max(0, min(10, (score_100 + 5) // 10))

    def _count(tier: str, status: str) -> int:
        return sum(1 for r, c in pairs if r["tier"] == tier and c["status"] == status)

    def _total(tier: str) -> int:
        return sum(1 for r in rubric if r["tier"] == tier)

    breakdown = {
        "must_haves_total": _total("must_have"),
        "must_haves_yes": _count("must_have", "yes"),
        "must_haves_partial": _count("must_have", "partial"),
        "important_total": _total("important"),
        "important_yes": _count("important", "yes"),
        "important_partial": _count("important", "partial"),
        "nice_total": _total("nice_to_have"),
        "nice_yes": _count("nice_to_have", "yes"),
        "nice_partial": _count("nice_to_have", "partial"),
        "missing_must_haves": missing_must_haves,
    }

    # Per-category subscores. Informational only - composite math above is
    # unchanged. Empty categories are omitted (most JDs don't touch all 4).
    categories: dict[str, dict[str, int]] = {}
    for cat in VALID_CATEGORIES:
        cat_pairs = [(r, c) for r, c in pairs if r.get("category") == cat]
        if not cat_pairs:
            continue
        cat_earned = sum(
            TIER_WEIGHT[r["tier"]] * STATUS_VALUE[c["status"]] for r, c in cat_pairs
        )
        cat_total = sum(TIER_WEIGHT[r["tier"]] for r, _ in cat_pairs)
        cat_100 = round(100 * cat_earned / cat_total) if cat_total else 0
        cat_100 = max(0, min(100, cat_100))
        cat_10 = max(0, min(10, (cat_100 + 5) // 10))
        categories[cat] = {
            "count": len(cat_pairs),
            "yes": sum(1 for _, c in cat_pairs if c["status"] == "yes"),
            "partial": sum(1 for _, c in cat_pairs if c["status"] == "partial"),
            "no": sum(1 for _, c in cat_pairs if c["status"] == "no"),
            "score_10": int(cat_10),
            "score_100": int(cat_100),
        }
    breakdown["categories"] = categories

    return {"score": score_10, "score_100": int(score_100), "breakdown": breakdown}


def score_jd_fit(
    job_description: str,
    company: str | None = None,
    resume_id: str | None = None,
) -> dict[str, Any]:
    """Score how well the resume fits the JD using rubric-based grading.

    Single Claude call extracts the JD's requirements, grades the resume on
    each one with evidence, and returns a one-line verdict summary. The
    composite score is computed deterministically from those grades.

    Returns {"score": int 0-10, "score_100": int 0-100, "verdict": str,
    "breakdown": dict}. The frontend uses score+verdict; score_100 is for
    finer ranking; breakdown is for debug/CLI.
    """
    if not job_description or not job_description.strip():
        raise ValueError("job_description must not be empty")

    resume = _read_resume(resume_id)
    user_message = _user_msg_score(
        job_description.strip(),
        company.strip() if company and company.strip() else None,
        resume,
    )

    payload, _ = _call_claude(
        SCORE_SYSTEM, user_message, required_keys={"verdict_summary"}
    )

    rubric, coverage = _normalize_rubric_and_coverage(
        payload.get("rubric"), payload.get("coverage")
    )

    composite = _compose_score(rubric, coverage)
    score = composite["score"]

    summary = payload["verdict_summary"].strip()
    # Strip any leading "X/10 -" the model may have emitted despite instructions.
    summary = re.sub(r"^\s*\d+\s*/\s*10\s*[-:]\s*", "", summary)
    if len(summary) > 160:
        summary = summary[:157].rstrip() + "..."
    verdict = f"{score}/10 - {summary}" if summary else f"{score}/10"

    return {
        "score": score,
        "score_100": composite["score_100"],
        "verdict": verdict,
        "breakdown": composite["breakdown"],
    }


def score_jd_fit_all(
    job_description: str, company: str | None = None
) -> list[dict[str, Any]]:
    """Score the JD against every resume in backend/resumes/ in parallel.

    Returns a list of result rows. Successful rows look like
    {"resume_id", "label", "score", "verdict"}. Per-resume failures append
    {"resume_id", "label", "error": str(exc)} so one bad model response
    doesn't sink the whole batch.

    Successes come first, sorted by score desc (ties broken by id alpha).
    Errors come last, ordered by id alpha.
    """
    if not job_description or not job_description.strip():
        raise ValueError("job_description must not be empty")

    resumes = list_resumes()
    if not resumes:
        return []

    jd = job_description.strip()
    co = company.strip() if company and company.strip() else None

    def _score_one(entry: dict[str, str]) -> dict[str, Any]:
        try:
            out = score_jd_fit(jd, co, resume_id=entry["id"])
            return {
                "resume_id": entry["id"],
                "label": entry["label"],
                "score": out["score"],
                "score_100": out["score_100"],
                "verdict": out["verdict"],
                "breakdown": out["breakdown"],
            }
        except Exception as exc:
            return {
                "resume_id": entry["id"],
                "label": entry["label"],
                "error": f"{exc.__class__.__name__}: {exc}",
            }

    workers = max(1, min(8, len(resumes)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(_score_one, resumes))

    successes = [r for r in rows if "score" in r]
    errors = [r for r in rows if "error" in r]
    # Sort by score_100 for finer tie-breaking; falls through to id alpha.
    successes.sort(key=lambda r: (-r["score_100"], r["resume_id"]))
    errors.sort(key=lambda r: r["resume_id"])
    return successes + errors


# -----------------------------------------------------------------------------
# CLI smoke test entry point.
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    usage = (
        "Usage:\n"
        "  python generate.py cover <company> <job_description> [--resume ID]\n"
        "  python generate.py email <company> <job_description> [intent] [--resume ID]\n"
        "  python generate.py outreach <channel> <profile_text> [context] [--resume ID]\n"
        "    where <channel> is one of: linkedin_invitation, linkedin_message, email\n"
        "  python generate.py score <job_description> [company] [--resume ID]\n"
        "  python generate.py score-all <job_description> [company]\n"
        "  python generate.py resumes\n"
    )

    if len(sys.argv) < 2:
        print(usage, file=sys.stderr)
        sys.exit(2)

    # Pull --resume <id> out of argv before per-mode parsing.
    raw_args = sys.argv[1:]
    cli_resume_id: str | None = None
    if "--resume" in raw_args:
        idx = raw_args.index("--resume")
        if idx + 1 >= len(raw_args):
            print(usage, file=sys.stderr)
            sys.exit(2)
        cli_resume_id = raw_args[idx + 1]
        del raw_args[idx : idx + 2]
    args = [sys.argv[0], *raw_args]

    mode = args[1]
    if mode == "cover":
        if len(args) != 4:
            print(usage, file=sys.stderr)
            sys.exit(2)
        pdf_bytes = generate_cover_letter(args[2], args[3], resume_id=cli_resume_id)
        Path("test.pdf").write_bytes(pdf_bytes)
        print(f"Wrote test.pdf ({len(pdf_bytes)} bytes)")
    elif mode == "email":
        if len(args) not in (4, 5):
            print(usage, file=sys.stderr)
            sys.exit(2)
        intent = args[4] if len(args) == 5 else None
        out = generate_application_email(
            args[2], args[3], intent, resume_id=cli_resume_id
        )
        print(json.dumps(out, indent=2))
    elif mode == "outreach":
        if len(args) not in (4, 5):
            print(usage, file=sys.stderr)
            sys.exit(2)
        context = args[4] if len(args) == 5 else None
        out = generate_outreach_message(
            args[3], args[2], context, resume_id=cli_resume_id
        )
        print(json.dumps(out, indent=2))
    elif mode == "score":
        if len(args) not in (3, 4):
            print(usage, file=sys.stderr)
            sys.exit(2)
        company = args[3] if len(args) == 4 else None
        out = score_jd_fit(args[2], company, resume_id=cli_resume_id)
        print(json.dumps(out, indent=2))
    elif mode == "score-all":
        if len(args) not in (3, 4):
            print(usage, file=sys.stderr)
            sys.exit(2)
        company = args[3] if len(args) == 4 else None
        out = score_jd_fit_all(args[2], company)
        print(json.dumps(out, indent=2))
    elif mode == "resumes":
        print(json.dumps(list_resumes(), indent=2))
    else:
        print(usage, file=sys.stderr)
        sys.exit(2)
