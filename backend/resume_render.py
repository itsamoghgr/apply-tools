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
            parts.append(f"\\href{{{url}}}{{\\underline{{{label}}}}}")
    location = _g(header, "location")
    if location:
        parts.append(escape_latex(location))
    return " $|$ \n    ".join(parts)


def _education_section(education: list[dict[str, Any]]) -> str:
    rows = [e for e in education if _g(e, "school")]
    if not rows:
        return ""
    out = ["\n%-----------EDUCATION-----------", "\\section{EDUCATION}"]
    for e in rows:
        out.append("  \\resumeSubHeadingListStart")
        out.append("    \\resumeSubheading")
        out.append(
            f"      {{{escape_latex(_g(e, 'school'))}}}{{{escape_latex(_g(e, 'dates'))}}}"
        )
        out.append(
            f"      {{{escape_latex(_g(e, 'degree'))}}}{{{escape_latex(_g(e, 'location'))}}}"
        )
        out.append("  \\resumeSubHeadingListEnd")
    out.append("\\vspace{-5pt}")
    return "\n".join(out) + "\n"


def _experience_section(experience: list[dict[str, Any]]) -> str:
    rows = [x for x in experience if _g(x, "company")]
    if not rows:
        return ""
    out = [
        "\n%-----------PROFESSIONAL EXPERIENCE-----------",
        "\\section{PROFESSIONAL EXPERIENCE}",
        "  \\resumeSubHeadingListStart",
    ]
    for x in rows:
        out.append("  \\resumeSubheading")
        out.append(
            f"      {{{escape_latex(_g(x, 'company'))}}}{{{escape_latex(_g(x, 'dates'))}}}"
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
    out.append("\\vspace{-5pt}")
    return "\n".join(out) + "\n"


def _skills_section(skills: list[dict[str, Any]]) -> str:
    rows = [s for s in skills if _g(s, "category") and _g(s, "items")]
    if not rows:
        return ""
    out = [
        "\n%-----------TECHNICAL SKILLS-----------",
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
    out.append(" \\vspace{-12pt}")
    return "\n".join(out) + "\n"


def _projects_section(projects: list[dict[str, Any]]) -> str:
    rows = [p for p in projects if _g(p, "name")]
    if not rows:
        return ""
    out = [
        "\n%-----------PROJECTS-----------",
        "\\section{PROJECTS}",
        "    \\vspace{-5pt}",
        "    \\resumeSubHeadingListStart",
    ]
    for p in rows:
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
        out.append("          \\vspace{-10pt}")
    out.append("    \\resumeSubHeadingListEnd")
    return "\n".join(out) + "\n"


def render_resume_tex(profile: dict[str, Any]) -> str:
    """Render structured `profile` data into a complete .tex source string."""
    template = RESUME_TEMPLATE_PATH.read_text(encoding="utf-8")
    header = profile.get("header") or {}
    substitutions = {
        "FULL_NAME": escape_latex(_g(header, "fullName") or "Your Name"),
        "CONTACT_LINE": _contact_line(header),
        "EDUCATION_SECTION": _education_section(profile.get("education") or []),
        "EXPERIENCE_SECTION": _experience_section(profile.get("experience") or []),
        "SKILLS_SECTION": _skills_section(profile.get("skills") or []),
        "PROJECTS_SECTION": _projects_section(profile.get("projects") or []),
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
