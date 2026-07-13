"""Render a structured resume profile into the sb2nov LaTeX template, then PDF.

The structured shape (mirrored by the Prisma ``ResumeProfile`` model and the
frontend builder) is:

    {
      "header": {
        "fullName": str,
        "phone": str, "email": str,
        "linkedin": str, "github": str, "portfolio": str,
        "scholar": str, "location": str,
      },
      "education": [
        {"school": str, "dates": str, "degree": str, "location": str}
      ],
      "experience": [
        {"company": str, "dates": str, "title": str, "location": str,
         "bullets": [str, ...]}
      ],
      "skills": [
        {"category": str, "items": str}      # items: comma-separated string
      ],
      "projects": [
        {"name": str, "date": str, "bullets": [str, ...]}
      ],
    }

Bullet text may contain a *whitelist* of inline markup — ``\\textbf{...}`` and
``\\emph{...}`` — exactly like cover-letter bodies. Everything else is escaped
so a stray ``%``/``&``/``$`` can't crash tectonic. We reuse the same
sanitisation strategy as generate.py (stash markup → escape → restore).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from latex_utils import compile_latex, count_pdf_pages, escape_latex

BACKEND_DIR = Path(__file__).resolve().parent
RESUME_TEMPLATE_PATH = BACKEND_DIR / "resume_template.tex"


# --- inline markup -----------------------------------------------------------
# Bullet text is authored in the UI as lightweight markdown: **bold** and
# *italic*. The data layer never stores raw LaTeX. At render time we convert
# those markers to \textbf{}/\emph{} and escape every other LaTeX-special char
# (including specials *inside* the markers, like a `%` in **91% recall**), or
# tectonic aborts. We do it in a single tokenizing pass.
#
# Legacy data (and AI output) may still contain raw \textbf{...}/\emph{...}; we
# normalise those to **/* markers first so old bullets keep their emphasis.
_LEGACY_BF_RE = re.compile(r"\\textbf\{([^{}]*)\}")
_LEGACY_EMPH_RE = re.compile(r"\\emph\{([^{}]*)\}")

# **bold** (non-greedy, no empty) and *italic*. Bold is matched first.
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"\*(.+?)\*")


def _escape_specials(text: str) -> str:
    """Escape every LaTeX-special char in a plain run of text."""
    return (
        text.replace("\\", r"\textbackslash{}")
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


# Common LaTeX escapes that may already be present inside legacy \textbf{}/\emph{}
# content (e.g. the DS-9 import wrote `\textbf{91\% recall}`). We strip the
# backslash so the text becomes plain again before the escape pass re-applies it.
_LATEX_UNESCAPE = [
    (r"\&", "&"), (r"\%", "%"), (r"\$", "$"), (r"\#", "#"), (r"\_", "_"),
    (r"\{", "{"), (r"\}", "}"),
]


def _unescape_latex(text: str) -> str:
    for esc, raw in _LATEX_UNESCAPE:
        text = text.replace(esc, raw)
    return text


def _normalize_legacy_markup(text: str) -> str:
    """Convert any raw \\textbf{x}/\\emph{x} into **x**/*x* markdown markers,
    un-escaping LaTeX specials inside so they aren't double-escaped later."""
    text = _LEGACY_BF_RE.sub(lambda m: f"**{_unescape_latex(m.group(1))}**", text)
    text = _LEGACY_EMPH_RE.sub(lambda m: f"*{_unescape_latex(m.group(1))}*", text)
    return text


def sanitize_inline(text: str) -> str:
    """Convert **bold**/*italic* markdown to LaTeX and escape everything else.

    Used for free-text bullet content the user (or AI) writes. Bold/italic
    emphasis is honoured; every other LaTeX-special char is neutralised —
    including specials inside the emphasised runs.
    """
    if not text:
        return ""
    text = _normalize_legacy_markup(text)

    out: list[str] = []
    pos = 0
    n = len(text)
    while pos < n:
        bold = _BOLD_RE.search(text, pos)
        ital = _ITALIC_RE.search(text, pos)
        # Pick whichever emphasis run starts first.
        m = min(
            (x for x in (bold, ital) if x is not None),
            key=lambda x: x.start(),
            default=None,
        )
        if m is None:
            out.append(_escape_specials(text[pos:]))
            break
        out.append(_escape_specials(text[pos : m.start()]))
        cmd = "textbf" if m.re is _BOLD_RE else "emph"
        out.append(f"\\{cmd}{{{_escape_specials(m.group(1))}}}")
        pos = m.end()
    return "".join(out)


def _g(d: dict[str, Any], key: str, default: str = "") -> str:
    v = d.get(key)
    return v.strip() if isinstance(v, str) else default


_MONTHS = (
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _endpoint(month: Any, year: Any) -> str:
    mm = _MONTHS[month] if isinstance(month, int) and 1 <= month <= 12 else ""
    yy = str(year) if isinstance(year, int) and year else ""
    return " ".join(p for p in (mm, yy) if p)


def _date_range(entry: dict[str, Any]) -> str:
    """The display date string for an education/experience entry, derived from
    structured start/end fields (mirrors frontend formatDateRange). Falls back to
    a literal ``dates`` string when structured fields are absent — e.g. an AI
    draft that emitted free-text — so those still render.
    """
    has_structured = any(
        k in entry for k in ("startMonth", "startYear", "endYear", "isPresent")
    )
    if not has_structured:
        return _g(entry, "dates")
    start = _endpoint(entry.get("startMonth"), entry.get("startYear"))
    end = "Present" if entry.get("isPresent") else _endpoint(
        entry.get("endMonth"), entry.get("endYear")
    )
    if start and end:
        return f"{start} – {end}"
    return start or end or ""


def _href_url(url: str) -> str:
    """Normalise a user-entered URL into a value safe for ``\\href{...}``.

    Two things make a header link non-clickable in the exported PDF:
      1. No scheme — a user typing ``linkedin.com/in/me`` (or ``www.…``) yields a
         *relative* target that PDF viewers won't open. We prepend ``https://``
         when no scheme is present (``mailto:``/``http``/``https`` are left as-is).
      2. Unescaped specials — inside ``\\href``'s first arg, ``%`` and ``#`` are
         still LaTeX-active and corrupt the link (or the compile). We backslash
         them; other URL chars are literal in the verbatim-ish href argument.
    """
    url = url.strip()
    if url and not re.match(r"^(https?:|mailto:)", url, re.I):
        url = "https://" + url.lstrip("/")
    return url.replace("\\", "\\\\").replace("%", "\\%").replace("#", "\\#")


def _contact_line(header: dict[str, Any]) -> str:
    """Build the header contact line with hyperlinks, matching the DS-9 layout.

    Only includes parts the user filled in; joins with the `$|$` separator.
    """
    parts: list[str] = []
    phone = _g(header, "phone")
    if phone:
        parts.append(f"Tel: {escape_latex(phone)}")
    email = _g(header, "email")
    if email:
        parts.append(
            f"Email: \\href{{mailto:{email}}}{{\\underline{{{escape_latex(email)}}}}}"
        )
    for key, label in (
        ("linkedin", "LinkedIn"),
        ("github", "Github"),
        ("scholar", "Publications"),
        ("portfolio", "Portfolio"),
    ):
        url = _g(header, key)
        if url:
            parts.append(f"\\href{{{_href_url(url)}}}{{\\underline{{{label}}}}}")
    location = _g(header, "location")
    if location:
        parts.append(escape_latex(location))
    return " $|$ \n    ".join(parts)


# Section emitters. IMPORTANT: each block is self-contained and must NOT carry
# leading or trailing inter-SECTION spacing — the gap above a section is owned
# entirely by \sectionsep, inserted by render_resume_tex between blocks. Any
# \vspace inside a block is strictly INTERNAL layout (between entries/bullets),
# not a gap to the neighbouring section. This is what makes reordering safe.
def _summary_section(summary: Any) -> str:
    text = summary.strip() if isinstance(summary, str) else ""
    if not text:
        return ""
    return (
        "%-----------SUMMARY-----------\n"
        "\\section{SUMMARY}\n"
        f"{sanitize_inline(text)}\n"
    )


def _education_section(education: list[dict[str, Any]]) -> str:
    rows = [e for e in education if _g(e, "school")]
    if not rows:
        return ""
    out = ["%-----------EDUCATION-----------", "\\section{EDUCATION}"]
    for e in rows:
        out.append("  \\resumeSubHeadingListStart")
        out.append("    \\resumeSubheading")
        out.append(
            f"      {{{escape_latex(_g(e, 'school'))}}}{{{escape_latex(_date_range(e))}}}"
        )
        out.append(
            f"      {{{escape_latex(_g(e, 'degree'))}}}{{{escape_latex(_g(e, 'location'))}}}"
        )
        out.append("  \\resumeSubHeadingListEnd")
    return "\n".join(out) + "\n"


def _experience_section(experience: list[dict[str, Any]]) -> str:
    rows = [x for x in experience if _g(x, "company")]
    if not rows:
        return ""
    out = [
        "%-----------PROFESSIONAL EXPERIENCE-----------",
        "\\section{PROFESSIONAL EXPERIENCE}",
        "  \\resumeSubHeadingListStart",
    ]
    for x in rows:
        out.append("  \\resumeSubheading")
        out.append(
            f"      {{{escape_latex(_g(x, 'company'))}}}{{{escape_latex(_date_range(x))}}}"
        )
        out.append(
            f"      {{{escape_latex(_g(x, 'title'))}}}{{{escape_latex(_g(x, 'location'))}}}"
        )
        bullets = [b for b in (x.get("bullets") or []) if isinstance(b, str) and b.strip()]
        if bullets:
            out.append("      \\resumeItemListStart")
            for b in bullets:
                out.append(f"        \\resumeItem{{{sanitize_inline(b.strip())}}}")
            out.append("      \\resumeItemListEnd")
        out.append("\\vspace{1pt}")
    out.append("  \\resumeSubHeadingListEnd")
    return "\n".join(out) + "\n"


def _skills_section(skills: list[dict[str, Any]]) -> str:
    rows = [s for s in skills if _g(s, "category") and _g(s, "items")]
    if not rows:
        return ""
    out = [
        "%-----------TECHNICAL SKILLS-----------",
        "\\section{TECHNICAL SKILLS}",
        " \\begin{itemize}[leftmargin=0.15in, label={}]",
        "    \\small{\\item{",
    ]
    for s in rows:
        cat = escape_latex(_g(s, "category"))
        items = escape_latex(_g(s, "items"))
        out.append(f"     \\textbf{{{cat}:}} {items} \\\\")
        out.append("     \\vspace{1pt}")
    out.append("    }}")
    out.append(" \\end{itemize}")
    return "\n".join(out) + "\n"


def _projects_section(projects: list[dict[str, Any]]) -> str:
    rows = [p for p in projects if _g(p, "name")]
    if not rows:
        return ""
    out = [
        "%-----------PROJECTS-----------",
        "\\section{PROJECTS}",
        "    \\resumeSubHeadingListStart",
    ]
    for idx, p in enumerate(rows):
        name = sanitize_inline(_g(p, "name"))
        date = escape_latex(_g(p, "date"))
        out.append("      \\resumeProjectHeading")
        out.append(f"          {{\\textbf{{{name}}}}}{{{date}}}")
        bullets = [b for b in (p.get("bullets") or []) if isinstance(b, str) and b.strip()]
        if bullets:
            out.append("          \\resumeItemListStart")
            for b in bullets:
                out.append(f"            \\resumeItem{{{sanitize_inline(b.strip())}}}")
            out.append("          \\resumeItemListEnd")
        # -10pt tightens the gap BETWEEN entries only. Emitting it after the LAST
        # entry leaks past the section and tightens whatever section follows,
        # breaking uniform inter-section spacing — so skip it on the final entry.
        if idx < len(rows) - 1:
            out.append("          \\vspace{-10pt}")
    out.append("    \\resumeSubHeadingListEnd")
    return "\n".join(out) + "\n"


# Default section order (all visible) when a profile has no stored sectionOrder,
# mirroring the frontend defaultSectionOrder(). Header renders separately, above.
_DEFAULT_SECTION_ORDER = ("summary", "education", "experience", "skills", "projects")


def _section_order(profile: dict[str, Any]) -> list[tuple[str, bool]]:
    """[(key, visible), ...] from the profile, defaulting to the full order (all
    visible) when none is stored. Unknown keys ignored; missing known sections
    appended visible so a section is never silently dropped."""
    raw = profile.get("sectionOrder")
    out: list[tuple[str, bool]] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                key = item.get("key")
                if key in _DEFAULT_SECTION_ORDER and key not in seen:
                    out.append((key, item.get("visible") is not False))
                    seen.add(key)
    for key in _DEFAULT_SECTION_ORDER:
        if key not in seen:
            out.append((key, True))
    return out


# Per-key LaTeX emitters. Each takes the whole profile and returns "" when empty.
_SECTION_RENDERERS = {
    "summary": lambda p: _summary_section(p.get("summary")),
    "education": lambda p: _education_section(p.get("education") or []),
    "experience": lambda p: _experience_section(p.get("experience") or []),
    "skills": lambda p: _skills_section(p.get("skills") or []),
    "projects": lambda p: _projects_section(p.get("projects") or []),
}


def render_resume_tex(profile: dict[str, Any]) -> str:
    """Render structured `profile` data into a complete .tex source string."""
    template = RESUME_TEMPLATE_PATH.read_text(encoding="utf-8")
    header = profile.get("header") or {}

    # Build the body from the profile's ordered, visibility-aware section list.
    # Each rendered block is followed by \sectionend (defined in the template),
    # which normalizes the trailing vertical position — cancelling whatever
    # negative \vspace the section left (\resumeItemListEnd's -5pt, projects'
    # -10pt, etc.) and adding ONE fixed gap. That makes the gap above every
    # section EQUAL regardless of section type or order. The last section's
    # trailing gap is harmless (page has \raggedbottom).
    parts: list[str] = []
    for key, visible in _section_order(profile):
        if not visible:
            continue
        render = _SECTION_RENDERERS.get(key)
        if render:
            block = render(profile)
            if block:
                # \sectionsep BEFORE each block (including the first) so the gap
                # above every section — including the first after the header — is
                # the same. \sectionsep uses \addvspace, which merges rather than
                # stacks, so a preceding section's residual glue can't double it.
                parts.append("\\sectionsep\n" + block.rstrip() + "\n")
    sections = "".join(parts)

    substitutions = {
        "FULL_NAME": escape_latex(_g(header, "fullName") or "Your Name"),
        "CONTACT_LINE": _contact_line(header),
        "SECTIONS": sections,
    }
    out = template
    for key, value in substitutions.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def render_resume_pdf(profile: dict[str, Any]) -> bytes:
    """Render `profile` to a complete .tex source and compile it to PDF bytes."""
    tex_source = render_resume_tex(profile)
    return compile_latex(tex_source, jobname="resume")


def render_resume_pdf_with_pages(profile: dict[str, Any]) -> tuple[bytes, int]:
    """Render `profile` to PDF and also report the page count.

    A resume is expected to fit on a single page; the page count lets the UI
    surface a warning (and block export) when it spills over.
    """
    pdf_bytes = render_resume_pdf(profile)
    return pdf_bytes, count_pdf_pages(pdf_bytes)
