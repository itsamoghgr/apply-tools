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
from groq import Groq
from openai import OpenAI

from db import fetch_resume, insert_application, list_resume_rows, save_pdf
from latex_utils import compile_latex, escape_latex
from log import get_logger


load_dotenv()

logger = get_logger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BACKEND_DIR / "template.tex"
RESUMES_DIR = BACKEND_DIR / "resumes"
LEGACY_RESUME_PATH = BACKEND_DIR / "resume.txt"
RESUME_ID_RE = re.compile(r"^[a-z0-9_-]+$")
DEFAULT_RESUME_ID = "default"

# ---------------------------------------------------------------------------
# Provider routing.  Set AI_PROVIDER to route ALL generation calls.
# SCORE_PROVIDER overrides just the score path (defaults to AI_PROVIDER).
# ---------------------------------------------------------------------------
AI_PROVIDER = os.environ.get("AI_PROVIDER", "anthropic").lower()
SCORE_PROVIDER = os.environ.get("SCORE_PROVIDER", AI_PROVIDER).lower()
# EXTRACT_PROVIDER overrides just the JD auto-detect path. Defaults to Groq
# (fast/consistent Llama) regardless of where generation runs; the extract path
# then falls back NVIDIA -> Bedrock -> Anthropic (see EXTRACT_FALLBACK_CHAIN).
# Override via env if you want auto-detect on a different primary.
EXTRACT_PROVIDER = os.environ.get("EXTRACT_PROVIDER", "groq").lower()

# Hard ceiling on a single LLM call. Without this, a slow upstream stalls
# the popup forever (no client-side timeout in popup.js for /score, /extract-jd).
LLM_TIMEOUT_SECS = float(os.environ.get("LLM_TIMEOUT_SECS", "45"))
# Tighter per-hop ceiling for the JD-extract path only. Auto-detect can span
# several fallback hops (Groq quota-out -> Bedrock -> ...), so each hop must fail
# fast for the whole chain to finish inside the popup's client timeout. Without
# this a single slow hop (e.g. NVIDIA NIM) eats the entire budget.
EXTRACT_TIMEOUT_SECS = float(os.environ.get("EXTRACT_TIMEOUT_SECS", "15"))

# Anthropic (Claude)
DEFAULT_MODEL = os.environ.get("MODEL", "claude-opus-4-5")
MAX_TOKENS = 2048

# Groq
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_MAX_TOKENS = 4096
SCORE_GROQ_MODEL = os.environ.get("SCORE_GROQ_MODEL", "llama-3.3-70b-versatile")

# NVIDIA NIM (OpenAI-compatible)
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
NIM_MODEL = os.environ.get("NIM_MODEL", "meta/llama-3.3-70b-instruct")
NIM_MAX_TOKENS = 4096

# AWS Bedrock (boto3, Converse API). Auth via the standard AWS credential
# chain (env vars / ~/.aws/credentials / IAM role); region from BEDROCK_REGION
# or AWS_REGION (default us-east-1). Generation/score/answer use Claude Sonnet;
# the JD extractor uses Llama 3.3 to match the groq/nvidia extract path.
BEDROCK_REGION = os.environ.get("BEDROCK_REGION") or os.environ.get(
    "AWS_REGION", "us-east-1"
)
BEDROCK_MODEL = os.environ.get(
    # Claude Sonnet 4.5 on Bedrock requires a region-prefixed inference profile
    # (the bare anthropic.claude-sonnet-4-5-... id raises ValidationException).
    "BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
)
BEDROCK_EXTRACT_MODEL = os.environ.get(
    "BEDROCK_EXTRACT_MODEL", "meta.llama3-3-70b-instruct-v1:0"
)
# The Chat tab is a free-form assistant (plain text, multi-turn), separate
# from the JSON generation path. Default to the same Sonnet 4.5 profile as
# generation but allow swapping it independently.
BEDROCK_CHAT_MODEL = os.environ.get("BEDROCK_CHAT_MODEL", BEDROCK_MODEL)
BEDROCK_MAX_TOKENS = 4096

# Extract JD (kept separate for backward compat; honours AI_PROVIDER)
EXTRACT_MODEL = os.environ.get("EXTRACT_MODEL", "llama-3.3-70b-versatile")
EXTRACT_MAX_TOKENS = 4096


# -----------------------------------------------------------------------------
# Shared voice rules - applied to every mode.
# -----------------------------------------------------------------------------

VOICE_RULES = """# Voice reference - this is Amogh's actual cover letter for Faire. Whatever you write should sound like the same person wrote it:

  "I'm applying for the Data Science Intern role at Faire. A marketplace that uses ML to help independent retailers compete with Amazon, not by copying them, but by connecting them to better products, is the kind of problem I want to work on.

  My background maps to what you're building. I've worked on search and retrieval systems using LangChain and semantic search, processing 100K+ records and optimizing for relevance. I've built demand forecasting models on 2M+ transaction records that reduced stockouts by 13%. And I've run A/B tests that actually moved conversion metrics, not just hit statistical significance.

  What draws me to Faire specifically is the two-sided marketplace complexity. Balancing retailer needs against brand discovery, managing cold start problems, optimizing for long-term retention rather than short term clicks, these are harder problems than single sided optimization, and more interesting.

  I'm most excited about the Search or Retailer Products teams. I've worked with GenAI and retrieval systems, and I've built predictive models that drove real business decisions. I'm comfortable going end-to-end: defining the problem, pulling the data, building the model, and communicating the results to people who don't care about the technical details.

  I recently earned my MS in Data Science at GW. Happy to chat if there's a fit."

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

COVER_LETTER_SYSTEM = f"""You are ghostwriting cover letters in the candidate's own voice. The candidate is Amogh Ramagiri, a Data Scientist with an MS in Data Science from GW.

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
- The closing paragraph is one or two sentences. Mention the MS in Data Science from GW if relevant. End with a short, casual line.

# Output format rules for body_paragraphs

- Plain text with blank lines between paragraphs. No markdown.
- The ONLY LaTeX commands you may emit are: \\emph{{...}}, \\textbf{{...}}, and \\\\ (forced line break, used sparingly). Use them rarely - the Faire letter above uses none.
- Do NOT emit: $, &, #, _, {{, }}, ~, ^, or backslashes outside the whitelist.
- Percent signs ARE allowed: write "43%" with the symbol, not "43 percent". (The renderer escapes it safely.) Ampersands and dollar signs are still banned - spell them out ("and", "USD" or the written amount). A bare number where a percentage was meant is a bug; always keep the % on the number.
- Do NOT include the salutation ("Dear ...") or sign-off ("Best Regards,") - those are in the template.
"""


APPLICATION_EMAIL_SYSTEM = f"""You are ghostwriting a job-application email in the candidate's own voice. The candidate is Amogh Ramagiri, a Data Scientist with an MS in Data Science from GW.

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


OUTREACH_INVITATION_SYSTEM = f"""You are ghostwriting a LinkedIn connection request note in the candidate's own voice. The candidate is Amogh Ramagiri, a Data Scientist with an MS in Data Science from GW.

You will receive: his resume (ground truth about him), the target person's LinkedIn profile (pasted text), and an optional context note describing the angle ("looking for referral at their company", "wanted to chat about their work in X", etc.).

LinkedIn caps these notes at 300 characters. Target 260-290 characters — use the budget.

Return STRICT JSON only - no markdown fences, no commentary before or after.

Output schema (all fields required):
{{
  "message": "<the connection request note, plain text. Includes greeting, brief intro of Amogh, and a soft ask. 260-290 characters.>"
}}

{VOICE_RULES}

# LinkedIn invitation rules

## Structure (in this order)

1. **Greeting** — "Hi {{first name}}," using their actual first name from the profile. If the profile has no first name, use "Hi,".
2. **Who Amogh is** — one short clause introducing him: role + program. e.g. "I'm a Data Scientist with an MS from GW" or "I'm a Data Scientist, recently finished my MS in DS at GW".
3. **Specific anchor** — name ONE concrete thing from their profile: a current role, a company, a project, a domain they work in. Not vague adjectives.
4. **Soft ask** — short, specific. "Would love to connect to hear how you got into <X>." / "Open to chatting about <Y> if you have a moment." Use the context note to pick the angle when one is given.

## Hard rules

- HARD MAXIMUM: 295 characters. If you're at or above 300, you've failed.
- Target 260-290 characters — don't leave 50+ characters on the table; use them to make the message specific.
- DO include the greeting. DO NOT include a signoff like "Thanks, Amogh" — connection requests show the sender's name automatically and a signoff wastes the budget.
- Lead with THEIR work, not Amogh's accomplishments. The intro of Amogh is a one-clause identifier ("I'm a DS with an MS from GW"), not a brag. No metrics, no "reduced X by Y%". Save numbers for follow-up messages.
- Banned filler words: "interesting", "impressive", "complex", "fascinating", "amazing", "great work". They add no information and burn characters.
- Anchor must be SPECIFIC: name the company, the team, the product, the topic. "your work in revenue management at Holland America Line" is acceptable; "your work in revenue management" alone is weaker; "your interesting work" is forbidden.
- One thought per sentence. Three sentences max after the greeting.
- Use contractions. No buzzwords. No em-dashes that look ChatGPT-generated.

## Example shape (for structure only — do not copy phrasing)

"Hi Abbie, I'm a Data Scientist with an MS from GW. Saw you're leading revenue management at Holland America Line — I've been working on forecasting and pricing problems and would love to hear how you got into RM."
"""


OUTREACH_LINKEDIN_MESSAGE_SYSTEM = f"""You are ghostwriting a LinkedIn message (DM or InMail) in the candidate's own voice. The candidate is Amogh Ramagiri, a Data Scientist with an MS in Data Science from GW.

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


OUTREACH_EMAIL_SYSTEM = f"""You are ghostwriting an outreach email to a specific person, in the candidate's own voice. The candidate is Amogh Ramagiri, a Data Scientist with an MS in Data Science from GW.

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


ANSWER_QUESTION_SYSTEM = f"""You are answering an application question in the candidate's own voice. The candidate is Amogh Ramagiri, a Data Scientist with an MS in Data Science from GW.

You will receive: his resume (ground truth about him), the company name, the job description, and the application question to answer.

Return STRICT JSON only - no markdown fences, no commentary before or after.

Output schema (all fields required):
{{
  "answer": "<plain-text answer to the question. First-person. No bullet lists, no markdown, no headings - the form renders plain text.>"
}}

{VOICE_RULES}

# Answer rules

- One short paragraph. Hard target: 60-120 words. Never more than 150.
- Yes/no or short-factual questions ("authorized to work?", "willing to relocate?"): one sentence.
- "in one sentence" / "briefly" / "in a few words": one or two sentences, <40 words.
- Even for "describe a time" / "tell us about" / "walk us through" questions: stay one paragraph. One line of situation, one or two lines of what you did, one line of outcome with a real metric if the resume has one. Don't expand into a multi-paragraph story.
- First-person. Use contractions. Concrete tools, projects, and numbers from the resume when relevant.
- Don't restate the question in the answer. Don't open with "Great question" or any preamble.
- Don't pad to hit a word count. If the honest answer is shorter, keep it shorter.
- Never invent experience, employers, projects, metrics, or tools not in the resume. If the question asks about something not in the resume, pivot honestly to the closest real experience and say what you have, not what you don't.
- One paragraph only. No line breaks, no blank lines, no bullet lists, no numbered lists, no markdown, no headings. Application forms strip formatting and a wall of prose reads cleaner than a fake-structured one.
- For "why this company" / "why this role" questions: anchor on something specific about the company or the role from the JD, not generic praise. State an opinion with a reason.
"""


SCORE_SYSTEM = """You are evaluating fit between Amogh Ramagiri's resume and a job description. He's a Data Scientist with an MS in Data Science from GW, with prior roles at Fulton Bank, Factocart, NCUE, and Wodo. The resume provided is ground truth - do not assume anything not in it.

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


EXTRACT_JD_SYSTEM = """You extract structured job-posting data from raw web page text scraped from a browser tab.

Return STRICT JSON only - no markdown fences, no commentary before or after.

Output schema (all fields required, but job_role / location may be empty strings if not on the page):
{
  "company": "<the hiring company's name as a human would say it; no legal suffix unless that's how they brand themselves (e.g. 'OpenAI', 'Stripe', not 'OpenAI, Inc.'). Empty string if you can't tell.>",
  "job_role": "<the role/job title as displayed on the page (e.g. 'Senior ML Engineer', 'Data Science Intern'). No company suffix. Empty string if not visible.>",
  "location": "<the job location as displayed (e.g. 'San Francisco, CA', 'Remote', 'New York, NY (Hybrid)'). Prefer the most specific value the page shows. Empty string if not visible.>",
  "job_description": "<the cleaned job description as plain text. Start with a single line containing the role title if you can find one. Then include the about/responsibilities/requirements/qualifications sections. Preserve paragraph breaks with blank lines.>"
}

# Extraction rules

- Strip site chrome: navigation menus, search bars, footer links, cookie/consent banners, login or signup prompts, "apply now" / "save job" buttons, share buttons, similar/recommended jobs lists, breadcrumbs, copyright notices.
- Strip per-applicant noise: "you have applied", "X applicants", time-since-posted, view counts, salary calculators, "easy apply" prompts.
- Keep the substantive content: role summary, what the team does, responsibilities, qualifications (required and preferred), tech stack, compensation if listed, location/remote policy if listed.
- Do NOT invent content. If a section isn't on the page, leave it out (empty string for job_role/location).
- Do NOT translate. Keep the original language of the posting.
- If the page is clearly not a single job posting (job board listing page, login wall, 404, generic careers landing page), return {"company": "", "job_role": "", "location": "", "job_description": ""}.
- The page text may contain duplicate copies of the description (mobile + desktop renders). Pick one clean copy; don't concatenate duplicates.
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


def _require_groq_key() -> str:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY not set. Add it to backend/.env to use auto-detect."
        )
    return api_key


def _require_nim_key() -> str:
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        raise RuntimeError(
            "NVIDIA_API_KEY not set. Add it to backend/.env "
            "(get one at https://build.nvidia.com)."
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
    """Read a resume by id from the SQLite DB, returning its content.

    When no id is given, returns the first active resume. Validates the id is a
    safe slug. Raises FileNotFoundError if no row matches (so the existing
    error-translation path in server.py turns it into a 400).
    """
    rid = resume_id.strip() if resume_id else None
    if rid and not RESUME_ID_RE.match(rid):
        raise ValueError(f"Invalid resume_id: {rid!r}")

    row = fetch_resume(rid)
    if row is None:
        raise FileNotFoundError(f"Unknown resume_id: {rid or '<active>'}")
    _, content = row
    return _strip_label_header(content)


def list_resumes() -> list[dict[str, str]]:
    """List available resumes as [{"id", "label"}], alpha-sorted."""
    return list_resume_rows()


def _call_claude(
    system_prompt: str,
    user_message: str,
    required_keys: set[str],
    *,
    extra_messages: list[dict[str, str]] | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    timeout: float = LLM_TIMEOUT_SECS,
) -> tuple[dict[str, Any], str]:
    """Single Claude messages.create call, JSON-parsed and key-validated."""
    api_key = _require_api_key()
    client = Anthropic(api_key=api_key, timeout=timeout)

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    if extra_messages:
        messages.extend(extra_messages)

    response = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=max_tokens or MAX_TOKENS,
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


def _call_groq(
    system_prompt: str,
    user_message: str,
    required_keys: set[str],
    *,
    extra_messages: list[dict[str, str]] | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    timeout: float = LLM_TIMEOUT_SECS,
) -> tuple[dict[str, Any], str]:
    """Groq chat completion, JSON-parsed and key-validated."""
    api_key = _require_groq_key()
    client = Groq(api_key=api_key, timeout=timeout)

    msgs: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    if extra_messages:
        msgs.extend(extra_messages)

    try:
        response = client.chat.completions.create(
            model=model or GROQ_MODEL,
            max_tokens=max_tokens or GROQ_MAX_TOKENS,
            response_format={"type": "json_object"},
            messages=msgs,
        )
    except Exception as exc:
        raise RuntimeError(f"Groq request failed: {exc}") from exc

    raw_text = response.choices[0].message.content or ""
    payload = _parse_claude_json(raw_text)
    _validate_keys(payload, required_keys)
    return payload, raw_text


def _call_nim(
    system_prompt: str,
    user_message: str,
    required_keys: set[str],
    *,
    extra_messages: list[dict[str, str]] | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    timeout: float = LLM_TIMEOUT_SECS,
) -> tuple[dict[str, Any], str]:
    """NVIDIA NIM chat completion (OpenAI-compatible), JSON-parsed and key-validated."""
    api_key = _require_nim_key()
    client = OpenAI(base_url=NIM_BASE_URL, api_key=api_key, timeout=timeout)

    msgs: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    if extra_messages:
        msgs.extend(extra_messages)

    try:
        response = client.chat.completions.create(
            model=model or NIM_MODEL,
            max_tokens=max_tokens or NIM_MAX_TOKENS,
            response_format={"type": "json_object"},
            messages=msgs,
        )
    except Exception as exc:
        raise RuntimeError(f"NVIDIA NIM request failed: {exc}") from exc

    raw_text = response.choices[0].message.content or ""
    payload = _parse_claude_json(raw_text)
    _validate_keys(payload, required_keys)
    return payload, raw_text


def _call_bedrock(
    system_prompt: str,
    user_message: str,
    required_keys: set[str],
    *,
    extra_messages: list[dict[str, str]] | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    timeout: float = LLM_TIMEOUT_SECS,
) -> tuple[dict[str, Any], str]:
    """AWS Bedrock Converse API call, JSON-parsed and key-validated.

    The Converse API is model-agnostic, so the same code path serves both the
    Claude default (generation/score) and the Llama extract model. The system
    prompt already instructs "STRICT JSON only", which both model families
    honour; Bedrock has no OpenAI-style response_format toggle.
    """
    import boto3  # local import so a missing optional dep doesn't break others
    from botocore.config import Config

    # botocore reads creds from the standard chain (env / ~/.aws / IAM role).
    # Cap the read timeout at the shared ceiling so a slow Bedrock call falls
    # through to the next provider instead of stalling the popup.
    client = boto3.client(
        "bedrock-runtime",
        region_name=BEDROCK_REGION,
        config=Config(
            read_timeout=timeout,
            connect_timeout=min(10.0, timeout),
            retries={"max_attempts": 1},
        ),
    )

    # Converse splits the system prompt out; user/assistant turns go in messages.
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"text": user_message}]}
    ]
    for m in extra_messages or []:
        messages.append({"role": m["role"], "content": [{"text": m["content"]}]})

    try:
        response = client.converse(
            modelId=model or BEDROCK_MODEL,
            system=[{"text": system_prompt}],
            messages=messages,
            inferenceConfig={"maxTokens": max_tokens or BEDROCK_MAX_TOKENS},
        )
    except Exception as exc:
        raise RuntimeError(f"AWS Bedrock request failed: {exc}") from exc

    blocks = response.get("output", {}).get("message", {}).get("content", [])
    raw_text = "".join(b.get("text", "") for b in blocks)
    payload = _parse_claude_json(raw_text)
    _validate_keys(payload, required_keys)
    return payload, raw_text


def _dispatch_provider(
    provider: str,
    system_prompt: str,
    user_message: str,
    required_keys: set[str],
    *,
    extra_messages: list[dict[str, str]] | None,
    model: str | None,
    max_tokens: int | None,
    timeout: float,
) -> tuple[dict[str, Any], str]:
    kwargs = dict(
        extra_messages=extra_messages,
        model=model,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    if provider == "nvidia":
        return _call_nim(system_prompt, user_message, required_keys, **kwargs)
    if provider == "groq":
        return _call_groq(system_prompt, user_message, required_keys, **kwargs)
    if provider == "bedrock":
        return _call_bedrock(system_prompt, user_message, required_keys, **kwargs)
    return _call_claude(system_prompt, user_message, required_keys, **kwargs)


# When the primary provider hits a recoverable error (rate-limit / quota /
# timeout) we walk down this chain, trying each subsequent provider until one
# succeeds. Ordered fastest-and-most-reliable-first: Bedrock (Claude Sonnet) and
# Groq (free, 3-15s) lead; Anthropic is the reliable paid backstop; NVIDIA NIM is
# LAST because its public endpoint frequently times out (>45s, sometimes >90s),
# so it should only ever be reached when everything else is unavailable. The
# primary is removed from the chain before walking (no point retrying the one
# that just failed), and we keep going even if an intermediate fallback also
# fails recoverably — that's the bug this replaces, where groq quota-out +
# nvidia timeout dead-ended with no further hop.
FALLBACK_CHAIN = ("bedrock", "groq", "anthropic", "nvidia")

# JD auto-detect (extract) prefers the cheap/fast Llama providers, in order:
# Groq (Llama 3.3 70b) first — fast and consistent (~2-3s) — then Bedrock
# (Llama 3.3 on a reliable endpoint), with Anthropic as the backstop and NVIDIA
# NIM LAST. NVIDIA's public endpoint frequently times out (>45s), so once Groq's
# daily quota is exhausted we must NOT fall to it ahead of Bedrock/Anthropic —
# that dead-ended every auto-detect on a hung hop. This mirrors FALLBACK_CHAIN's
# ordering rationale. Used only by extract_jd_from_page, not generation/scoring.
EXTRACT_FALLBACK_CHAIN = ("groq", "bedrock", "anthropic", "nvidia")

# Substrings that mark an exception as "provider is unusable right now"
# rather than "the request itself is malformed". Lowercased before match.
_FALLBACK_TRIGGERS = (
    "rate limit",
    "rate_limit",
    "rate-limit",
    "429",
    "quota",
    "tokens per day",
    "tpd",
    "tpm",
    "timed out",
    "timeout",
    "service unavailable",
    "503",
)


def _should_fallback(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(t in msg for t in _FALLBACK_TRIGGERS)


def _provider_unconfigured(exc: BaseException) -> bool:
    """True when the error is a missing API key / credentials for a provider.

    A fallback provider that isn't configured should be skipped (try the next
    one), not treated as a hard failure that aborts the whole chain.
    """
    msg = str(exc).lower()
    return (
        "not set" in msg  # _require_*_key messages
        or "no credentials" in msg  # botocore NoCredentialsError
        or "unable to locate credentials" in msg
        or "could not connect to the endpoint" in msg
    )


def _call_llm(
    system_prompt: str,
    user_message: str,
    required_keys: set[str],
    *,
    extra_messages: list[dict[str, str]] | None = None,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    fallback_chain: tuple[str, ...] | None = None,
    timeout: float = LLM_TIMEOUT_SECS,
) -> tuple[dict[str, Any], str]:
    """Unified dispatcher: routes to the configured AI provider.

    `provider` defaults to AI_PROVIDER.  Pass explicitly for score/extract
    overrides. On a recoverable failure (rate limit, timeout) we walk down the
    fallback chain, trying each remaining provider until one succeeds — Groq's
    free tier caps at 100k tokens/day (easy to hit) and NIM can time out, so a
    single fallback hop isn't enough; we keep going and finally Anthropic, which
    is the reliable backstop.

    `fallback_chain` overrides the global FALLBACK_CHAIN for this call only
    (e.g. JD auto-detect wants NVIDIA -> Groq -> Bedrock -> Anthropic). The
    primary is always tried first and de-duped out of the chain regardless.
    """
    primary = (provider or AI_PROVIDER).lower()

    # Try the primary first (honouring its provider-specific `model`), then walk
    # the rest of the chain. De-dupe so the primary isn't retried mid-chain.
    walk = fallback_chain or FALLBACK_CHAIN
    chain = [primary] + [p for p in walk if p != primary]

    last_exc: Exception | None = None
    for i, prov in enumerate(chain):
        try:
            return _dispatch_provider(
                prov,
                system_prompt,
                user_message,
                required_keys,
                extra_messages=extra_messages,
                # `model` is provider-specific; only the primary gets the
                # caller's model. Every fallback uses its own default.
                model=model if i == 0 else None,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        except Exception as exc:
            last_exc = exc
            is_last = i == len(chain) - 1
            # Walk to the next provider on a recoverable error (rate limit /
            # quota / timeout) OR when this fallback simply isn't configured
            # (missing key/creds) — an unconfigured backstop shouldn't abort
            # the chain. Anything else (e.g. malformed request) surfaces as-is.
            recoverable = _should_fallback(exc) or (
                i > 0 and _provider_unconfigured(exc)
            )
            if not recoverable or is_last:
                raise
            logger.warning(
                "provider_fallback",
                provider=prov,
                error=str(exc),
                next_provider=chain[i + 1],
            )

    # Unreachable (loop either returns or raises), but keeps type-checkers happy.
    raise last_exc if last_exc else RuntimeError("no provider available")


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


def _user_msg_answer_question(
    company: str, jd: str, question: str, resume: str
) -> str:
    return (
        "RESUME (ground truth - do not invent beyond this):\n"
        f"{resume}\n\n"
        "COMPANY:\n"
        f"{company}\n\n"
        "JOB DESCRIPTION:\n"
        f"{jd}\n\n"
        "APPLICATION QUESTION:\n"
        f"{question}\n\n"
        "Return JSON only."
    )


# -----------------------------------------------------------------------------
# Public entry points.
# -----------------------------------------------------------------------------


def _cover_letter_payload(
    company: str, jd: str, resume_id: str | None
) -> dict[str, str]:
    """Run the cover-letter LLM call and validate inputs. Shared by PDF + text paths."""
    if not company or not company.strip():
        raise ValueError("company must not be empty")
    if not jd or not jd.strip():
        raise ValueError("job_description must not be empty")

    resume = _read_resume(resume_id)
    payload, _ = _call_llm(
        COVER_LETTER_SYSTEM,
        _user_msg_cover_letter(company.strip(), jd.strip(), resume),
        required_keys={"role_title", "hiring_manager", "body_paragraphs"},
    )
    return payload


# Whitelist of LaTeX commands the cover-letter prompt is allowed to emit.
_LATEX_EMPH_RE = re.compile(r"\\emph\{([^{}]*)\}")
_LATEX_TEXTBF_RE = re.compile(r"\\textbf\{([^{}]*)\}")


def _cover_body_to_plain_text(body: str) -> str:
    """Strip the whitelisted LaTeX commands so the body reads cleanly as plain text."""
    out = _LATEX_EMPH_RE.sub(r"\1", body)
    out = _LATEX_TEXTBF_RE.sub(r"\1", out)
    out = out.replace("\\\\", "\n")
    return out.strip()


# Sentinel placeholders unlikely to appear in any cover letter body. We
# stash whitelisted markup behind these tokens, escape every other
# LaTeX-special char, then restore the markup. This way a stray `%`,
# `$`, `&`, `_`, etc. from the model can't crash tectonic.
_TOK_EMPH_OPEN = "\x01\x02\x03"
_TOK_EMPH_CLOSE = "\x01\x02\x04"
_TOK_BF_OPEN = "\x01\x02\x05"
_TOK_BF_CLOSE = "\x01\x02\x06"
_TOK_BREAK = "\x01\x02\x07"


def _sanitize_cover_body(body: str) -> str:
    """Escape stray LaTeX specials in the body while preserving the whitelisted
    commands the prompt is allowed to emit (\\emph{}, \\textbf{}, \\\\).

    Models occasionally slip through a `%`, `$`, `&`, etc. despite prompt
    instructions; without sanitisation tectonic aborts the compile. Sanitising
    after generation is cheaper than re-prompting.
    """
    # 1) Stash whitelisted markup behind sentinels.
    s = _LATEX_EMPH_RE.sub(
        lambda m: f"{_TOK_EMPH_OPEN}{m.group(1)}{_TOK_EMPH_CLOSE}", body
    )
    s = _LATEX_TEXTBF_RE.sub(
        lambda m: f"{_TOK_BF_OPEN}{m.group(1)}{_TOK_BF_CLOSE}", s
    )
    s = s.replace("\\\\", _TOK_BREAK)

    # 2) Escape every remaining LaTeX-special character. (Don't reuse
    # escape_latex on the whole body because the sentinels themselves
    # contain bytes we want to preserve verbatim, and we want braces
    # *outside* sentinels escaped too if present.)
    s = (
        s.replace("\\", r"\textbackslash{}")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("$", r"\$")
        .replace("#", r"\#")
        .replace("_", r"\_")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("~", r"\textasciitilde{}")
        .replace("^", r"\textasciicircum{}")
    )

    # 3) Restore whitelisted markup.
    s = s.replace(_TOK_EMPH_OPEN, r"\emph{").replace(_TOK_EMPH_CLOSE, "}")
    s = s.replace(_TOK_BF_OPEN, r"\textbf{").replace(_TOK_BF_CLOSE, "}")
    s = s.replace(_TOK_BREAK, r"\\")
    return s


def generate_cover_letter(
    company: str, jd: str, resume_id: str | None = None
) -> bytes:
    """Generate a tailored cover letter PDF for the given company + job description.

    Returns PDF bytes. Raises if the API key is missing, Claude returns
    unparseable output, the template is malformed, or tectonic fails.
    """
    payload = _cover_letter_payload(company, jd, resume_id)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    substitutions = {
        "COMPANY_NAME": escape_latex(company.strip()),
        "ROLE_TITLE": escape_latex(payload["role_title"].strip()),
        "HIRING_MANAGER_OR_TEAM": escape_latex(payload["hiring_manager"].strip()),
        # Body keeps the whitelisted \emph / \textbf / \\ commands. All other
        # LaTeX-special characters get escaped so a stray % or & from the
        # model can't break tectonic.
        "BODY_PARAGRAPHS": _sanitize_cover_body(payload["body_paragraphs"].strip()),
    }
    tex_source = _substitute(template, substitutions)
    pdf_bytes = compile_latex(tex_source, jobname="cover_letter")
    pdf_path = save_pdf(company.strip(), pdf_bytes)
    insert_application(
        mode="cover_letter",
        company=company.strip(),
        job_description=jd.strip(),
        resume_id=resume_id,
        pdf_path=pdf_path,
    )
    return pdf_bytes


def generate_cover_letter_text(
    company: str, jd: str, resume_id: str | None = None
) -> dict[str, str]:
    """Generate a tailored cover letter as plain text (no PDF compile).

    Returns {'role_title', 'hiring_manager', 'body'} where body is plain text
    with whitelisted LaTeX commands stripped.
    """
    payload = _cover_letter_payload(company, jd, resume_id)
    out = {
        "role_title": payload["role_title"].strip(),
        "hiring_manager": payload["hiring_manager"].strip(),
        "body": _cover_body_to_plain_text(payload["body_paragraphs"]),
    }
    insert_application(
        mode="cover_letter_text",
        company=company.strip(),
        job_description=jd.strip(),
        resume_id=resume_id,
        output=json.dumps(out),
    )
    return out


def render_cover_letter_pdf(
    company: str,
    role_title: str,
    hiring_manager: str,
    body: str,
) -> bytes:
    """Render a cover-letter PDF from already-generated (and possibly user-edited)
    text — no LLM call, no audit-log write.

    Powers the "edit the letter, then download a matching PDF" flow: the body may
    be plain text with the whitelisted \\emph{}/\\textbf{}/\\\\ markup. Everything
    else LaTeX-special is escaped so a stray character can't break tectonic.
    """
    if not company or not company.strip():
        raise ValueError("company must not be empty")
    if not body or not body.strip():
        raise ValueError("cover letter body must not be empty")

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    substitutions = {
        "COMPANY_NAME": escape_latex(company.strip()),
        "ROLE_TITLE": escape_latex((role_title or "").strip()),
        "HIRING_MANAGER_OR_TEAM": escape_latex((hiring_manager or "Hiring Team").strip()),
        "BODY_PARAGRAPHS": _sanitize_cover_body(body.strip()),
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
    payload, _ = _call_llm(
        APPLICATION_EMAIL_SYSTEM,
        _user_msg_application_email(
            company.strip(), jd.strip(), intent.strip() if intent else None, resume
        ),
        required_keys={"subject", "body"},
    )
    out = {"subject": payload["subject"].strip(), "body": payload["body"].strip()}
    insert_application(
        mode="email",
        company=company.strip(),
        job_description=jd.strip(),
        resume_id=resume_id,
        output=json.dumps(out),
    )
    return out


def answer_application_question(
    company: str,
    jd: str,
    question: str,
    resume_id: str | None = None,
) -> dict[str, str]:
    """Answer a free-text application question. Returns {'answer': ...}."""
    if not company or not company.strip():
        raise ValueError("company must not be empty")
    if not jd or not jd.strip():
        raise ValueError("job_description must not be empty")
    if not question or not question.strip():
        raise ValueError("question must not be empty")

    resume = _read_resume(resume_id)
    payload, _ = _call_llm(
        ANSWER_QUESTION_SYSTEM,
        _user_msg_answer_question(
            company.strip(), jd.strip(), question.strip(), resume
        ),
        required_keys={"answer"},
    )
    out = {"answer": payload["answer"].strip()}
    insert_application(
        mode="answer_question",
        company=company.strip(),
        job_description=jd.strip(),
        resume_id=resume_id,
        output=json.dumps({"question": question.strip(), **out}),
    )
    return out


# -----------------------------------------------------------------------------
# Chat: a free-form, multi-turn assistant backed by Bedrock (Converse API).
# Unlike the generation paths above, this returns plain text — no JSON schema
# — so it has its own Bedrock call rather than going through _call_llm.
# -----------------------------------------------------------------------------

CHAT_SYSTEM = (
    "You are Apply Tools' built-in assistant, helping the user with their job "
    "search: cover letters, outreach, interview prep, resume questions, and "
    "general career advice. Be concise, direct, and practical. Use plain text "
    "(short paragraphs or simple lists); avoid heavy markdown. If you don't "
    "know something, say so rather than inventing details."
)

# Keep the request bounded: only the most recent turns are sent to the model.
CHAT_MAX_HISTORY_MESSAGES = 20


def chat_reply(messages: list[dict[str, str]]) -> dict[str, str]:
    """Generate the assistant's next turn for a chat conversation.

    `messages` is the running transcript as ``[{"role": "user"|"assistant",
    "content": str}, ...]`` in chronological order, ending with the latest
    user message. Returns ``{"reply": <assistant text>}``.

    Runs directly on Bedrock (Converse) using BEDROCK_CHAT_MODEL — plain text,
    no JSON parsing or fallback chain.
    """
    if not messages:
        raise ValueError("messages must not be empty")

    # Trim to the most recent turns and normalise roles. Bedrock's Converse
    # API requires the first message to be a user turn and roles to alternate,
    # so we drop any leading assistant messages after trimming.
    trimmed = messages[-CHAT_MAX_HISTORY_MESSAGES:]
    while trimmed and trimmed[0].get("role") != "user":
        trimmed = trimmed[1:]
    if not trimmed:
        raise ValueError("conversation must contain a user message")
    if trimmed[-1].get("role") != "user":
        raise ValueError("the last message must be from the user")

    converse_messages: list[dict[str, Any]] = []
    for m in trimmed:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        converse_messages.append({"role": role, "content": [{"text": content}]})

    import boto3  # local import: optional dep, mirrors _call_bedrock
    from botocore.config import Config

    client = boto3.client(
        "bedrock-runtime",
        region_name=BEDROCK_REGION,
        config=Config(
            read_timeout=LLM_TIMEOUT_SECS,
            connect_timeout=min(10.0, LLM_TIMEOUT_SECS),
            retries={"max_attempts": 1},
        ),
    )

    try:
        response = client.converse(
            modelId=BEDROCK_CHAT_MODEL,
            system=[{"text": CHAT_SYSTEM}],
            messages=converse_messages,
            inferenceConfig={"maxTokens": BEDROCK_MAX_TOKENS},
        )
    except Exception as exc:
        raise RuntimeError(f"AWS Bedrock chat request failed: {exc}") from exc

    blocks = response.get("output", {}).get("message", {}).get("content", [])
    reply = "".join(b.get("text", "") for b in blocks).strip()
    if not reply:
        raise RuntimeError("Bedrock returned an empty chat response")
    return {"reply": reply}


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

    payload, raw = _call_llm(system_prompt, user_message, required_keys)

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
        payload, _ = _call_llm(
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
    insert_application(
        mode="outreach",
        resume_id=resume_id,
        output=json.dumps({"channel": channel, **result}),
    )
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


def score_resume_text(
    job_description: str,
    resume_text: str,
    company: str | None = None,
) -> dict[str, Any]:
    """Score a raw resume-text string against a JD (rubric-based grading).

    The scoring core, decoupled from where the resume comes from. `score_jd_fit`
    reads a stored resume by id then calls this; the Resume Builder renders its
    in-memory profile to text and calls this directly. Returns the same shape:
    {"score": int 0-10, "score_100": int 0-100, "verdict": str, "breakdown": dict}.
    """
    if not job_description or not job_description.strip():
        raise ValueError("job_description must not be empty")
    if not resume_text or not resume_text.strip():
        raise ValueError("resume_text must not be empty")

    user_message = _user_msg_score(
        job_description.strip(),
        company.strip() if company and company.strip() else None,
        resume_text,
    )

    score_model = SCORE_GROQ_MODEL if SCORE_PROVIDER == "groq" else None
    payload, _ = _call_llm(
        SCORE_SYSTEM,
        user_message,
        required_keys={"verdict_summary"},
        provider=SCORE_PROVIDER,
        model=score_model,
        max_tokens=4096,
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


def score_jd_fit(
    job_description: str,
    company: str | None = None,
    resume_id: str | None = None,
    *,
    _log: bool = True,
) -> dict[str, Any]:
    """Score how well a stored resume fits the JD using rubric-based grading.

    Reads the resume by id (or the active one) and delegates to
    `score_resume_text`. Returns {"score", "score_100", "verdict", "breakdown"}.
    The frontend uses score+verdict; score_100 is for finer ranking.
    """
    resume = _read_resume(resume_id)
    out = score_resume_text(job_description, resume, company)
    if _log:
        insert_application(
            mode="score",
            company=company.strip() if company and company.strip() else None,
            job_description=job_description.strip(),
            resume_id=resume_id,
            output=out["verdict"],
            score_data=json.dumps(out),
        )
    return out


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
            out = score_jd_fit(jd, co, resume_id=entry["id"], _log=False)
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
# JD extractor (used by the popup's auto-detect button when site selectors miss).
# -----------------------------------------------------------------------------

# Cap the scraped page text before sending it to the extractor. The binding
# constraint is the PRIMARY extract provider's token budget: Groq's free tier
# caps at 12000 tokens/min (TPM), and the whole request (system prompt + wrapper
# + page text) must fit under it or Groq hard-rejects with a 413 — which it did
# at 40000 chars (~14.8k tokens), making the primary provider 413 on EVERY
# auto-detect and forcing the slow Bedrock/Anthropic fallback each time. At
# ~3.3-4 chars/token for noisy scraped text, 24000 chars ≈ 6-7k tokens of page
# text, leaving comfortable headroom under 12k TPM. A real job posting's content
# is well under this; the rest is nav/footer noise we don't need anyway.
EXTRACT_JD_MAX_PAGE_TEXT = int(os.environ.get("EXTRACT_JD_MAX_PAGE_TEXT", "24000"))


def extract_jd_from_page(
    url: str, page_title: str | None, page_text: str
) -> dict[str, str]:
    """Extract {company, job_description} from raw scraped page text.

    Routes through AI_PROVIDER so it works with Anthropic, Groq, or NVIDIA NIM.
    Unlike the other modes, empty strings are a valid response: they signal
    "this page isn't a job posting" and the popup surfaces that cleanly.
    """
    if not page_text or not page_text.strip():
        raise ValueError("page_text must not be empty")

    trimmed = page_text[:EXTRACT_JD_MAX_PAGE_TEXT]

    user_msg = (
        f"URL:\n{url or ''}\n\n"
        f"PAGE TITLE:\n{page_title or ''}\n\n"
        f"PAGE TEXT (raw, may contain noise):\n{trimmed}\n\n"
        "Return JSON only."
    )

    # Auto-detect routes through EXTRACT_PROVIDER (default NVIDIA NIM) and uses
    # the extract-specific fallback order NVIDIA -> Groq -> Bedrock -> Anthropic
    # (EXTRACT_FALLBACK_CHAIN), keeping extraction on cheap/fast Llama providers
    # before the Anthropic backstop. It uses a lighter Llama model where the
    # provider offers one (Groq and Bedrock); nvidia uses NIM_MODEL by default.
    # Note: this only sets the *primary* model — each fallback uses its own
    # default (so a fail-over to Bedrock lands on BEDROCK_MODEL/Claude, not
    # Llama). Acceptable for the rare fallback path.
    if EXTRACT_PROVIDER == "groq":
        extract_model: str | None = EXTRACT_MODEL
    elif EXTRACT_PROVIDER == "bedrock":
        extract_model = BEDROCK_EXTRACT_MODEL
    else:
        extract_model = None
    # _validate_keys would reject empty strings which are valid for extract,
    # so pass an empty required_keys set and validate manually below.
    payload, _ = _call_llm(
        EXTRACT_JD_SYSTEM,
        user_msg,
        required_keys=set(),
        provider=EXTRACT_PROVIDER,
        model=extract_model,
        max_tokens=EXTRACT_MAX_TOKENS,
        fallback_chain=EXTRACT_FALLBACK_CHAIN,
        # Tighter per-hop cap so a slow/hung hop fails fast and the chain can
        # walk Groq -> Bedrock -> ... within the popup's client timeout.
        timeout=EXTRACT_TIMEOUT_SECS,
    )

    missing = {"company", "job_description"} - payload.keys()
    if missing:
        raise ValueError(f"LLM response missing required keys: {missing}")
    if not isinstance(payload["company"], str) or not isinstance(
        payload["job_description"], str
    ):
        raise ValueError(
            "LLM response fields 'company' and 'job_description' must be strings"
        )

    # job_role / location are newer fields; older models may omit them. Default
    # to empty strings rather than failing extraction.
    job_role = payload.get("job_role", "")
    location = payload.get("location", "")
    if not isinstance(job_role, str):
        job_role = ""
    if not isinstance(location, str):
        location = ""

    company = payload["company"].strip()
    jd = payload["job_description"].strip()
    job_role = job_role.strip()
    location = location.strip()
    logger.info(
        "extract_jd",
        url=url,
        company_len=len(company),
        jd_len=len(jd),
        role=job_role[:120],
        location=location[:120],
        company=company[:200],
    )
    return {
        "company": company,
        "job_description": jd,
        "job_role": job_role,
        "location": location,
    }


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
        "  python generate.py question <company> <job_description> <question> [--resume ID]\n"
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
    elif mode == "question":
        if len(args) != 5:
            print(usage, file=sys.stderr)
            sys.exit(2)
        out = answer_application_question(
            args[2], args[3], args[4], resume_id=cli_resume_id
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
