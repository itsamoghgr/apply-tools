"""Checks for the resume template registry and each shell's compilability.

The backend has no pytest suite, so this runs standalone:

    venv/bin/python test_resume_templates.py

Compiling every registered shell is the point — a template that references a
package Tectonic's XeTeX can't resolve (fontawesome5) or a pdfTeX-only primitive
(\\pdfgentounicode) compiles fine in isolation but aborts here. The profile below
deliberately contains LaTeX-special characters so the escaping path is exercised
too. Requires `tectonic` on PATH; skips the compile checks if it's missing.
"""

from __future__ import annotations

import shutil
import sys

from resume_render import render_resume_pdf_with_pages, render_resume_tex
from resume_templates import DEFAULT_TEMPLATE, TEMPLATES, get_template

# Specials in nearly every field: %, &, $, #, _, ^, ~, braces, backslash, plus
# markdown emphasis and legacy \textbf{}/\emph{} that must survive as emphasis.
PROFILE = {
    "header": {
        "fullName": "Ada Lovelace",
        "phone": "555-0100",
        "email": "ada@example.com",
        "linkedin": "linkedin.com/in/ada",          # no scheme -> https:// added
        "github": "https://github.com/ada",
        "portfolio": "example.com/work?a=1#top",    # '#' must be escaped in href
        "scholar": "",
        "location": "London, UK",
    },
    "summary": "Analyst with 100% delivery & $2M saved — focused on **engines**.",
    "education": [{
        "school": "University of London", "degree": "M.S. Mathematics",
        "location": "London, UK",
        "startMonth": 8, "startYear": 2023,
        "endMonth": 5, "endYear": 2025, "isPresent": False,
    }],
    "experience": [{
        "company": "Babbage & Co.", "title": "Engineer", "location": "Remote",
        "startMonth": 6, "startYear": 2025,
        "endMonth": None, "endYear": None, "isPresent": True,
        "bullets": [
            "Raised recall to **91%** on a $1.2M pipeline (cost_per_unit -30%).",
            "Built C++ & Python tooling with #tags and 100% coverage.",
            "Legacy markup \\textbf{91\\% recall} and \\emph{italics} still render.",
            "Specials: a^b, ~approx, {braces}, back\\slash.",
        ],
    }],
    "skills": [{"category": "Languages", "items": "Python, R, SQL, C++"}],
    "projects": [
        {"name": "Engine", "date": "2025", "bullets": ["100% ATS-parsable export."]},
        {"name": "Notes", "date": "2024", "bullets": ["Second entry."]},
    ],
}

# A profile with realistically LONG entry fields. This is the case that broke
# compact's first single-line \resumeSubheading: with `tabular*`, a left cell
# wider than the available space collapsed \extracolsep{\fill} to zero, so the
# dates ran into the degree text and off the page edge. Any shell that packs an
# entry onto one line must wrap instead of overflowing, so every template is
# compiled against this too.
LONG_PROFILE = {
    "header": dict(PROFILE["header"]),
    "summary": "",
    "education": [
        {"school": "George Washington University",
         "degree": "Master of Science in Data Science (CCAS Dean's Award)",
         "location": "Washington, DC",
         "startMonth": 8, "startYear": 2024,
         "endMonth": 5, "endYear": 2026, "isPresent": False},
        {"school": "Presidency University",
         "degree": "Bachelor's of Technology in Computer Science and Engineering "
                   "(Spl. in AI and ML)",
         "location": "Bengaluru, India",
         "startMonth": 9, "startYear": 2020,
         "endMonth": 5, "endYear": 2024, "isPresent": False},
    ],
    "experience": [
        {"company": "A Rather Long Financial Institution Name, LLC",
         "title": "Senior Machine Learning Engineer, Platform Infrastructure",
         "location": "San Francisco, CA",
         "startMonth": 5, "startYear": 2025,
         "endMonth": None, "endYear": None, "isPresent": True,
         "bullets": ["Shipped **things** at 100% reliability."]},
    ],
    "skills": [{"category": "Languages", "items": "Python, R, SQL"}],
    "projects": [],
}

# Slugs that must all resolve to the default rather than raising.
BAD_SLUGS = ["nope", "", "   ", None, 123, [], {"a": 1}]


def rightmost_text_edge(pdf: bytes) -> float | None:
    """Largest x reached by any glyph on page 1, in PDF points (None if unknown).

    Overflowing text *starts* inside the margin and extends past it, so a run's
    start position cannot detect the bug — the run's END is what matters.
    pdfplumber reports a per-character bounding box, giving the true right edge.
    (pypdf's text visitor exposes only start positions, in an untransformed
    space, so it can't answer this.) Returns None when pdfplumber is missing so
    callers can skip rather than fail spuriously.
    """
    try:
        import io

        import pdfplumber
    except Exception:
        return None

    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        page = doc.pages[0]
        return max((float(ch["x1"]) for ch in page.chars), default=0.0)


# Letter paper is 612pt wide and these shells leave roughly a 0.5in right margin,
# so the text block ends near x=580. A glyph reaching past this has escaped the
# margin and is running toward the paper edge — the signature of a table row that
# overflowed instead of wrapping. Verified against the real regression: the
# original tabular* compact heading reached ~639pt (past the 612pt page edge),
# while the fixed tabularx version tops out at ~580.
RIGHT_MARGIN_LIMIT = 590.0


def overflows_margin(pdf: bytes) -> bool | None:
    """True if page-1 text runs past the right margin; None if undeterminable."""
    edge = rightmost_text_edge(pdf)
    if edge is None:
        return None
    return edge > RIGHT_MARGIN_LIMIT


def check(cond: bool, msg: str, failures: list[str]) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {msg}")
    if not cond:
        failures.append(msg)


def main() -> int:
    failures: list[str] = []

    print("registry:")
    check(DEFAULT_TEMPLATE in TEMPLATES,
          f"default slug {DEFAULT_TEMPLATE!r} is registered", failures)
    for slug, tpl in TEMPLATES.items():
        check(tpl.path.is_file(), f"{slug}: shell file exists", failures)
        src = tpl.read()
        for ph in ("{{FULL_NAME}}", "{{CONTACT_LINE}}", "{{SECTIONS}}"):
            check(ph in src, f"{slug}: has {ph}", failures)
        # The macro contract resume_render.py emits against.
        # \resumeEducationSubheading is emitted for education entries so a shell
        # can style them apart from experience (compact keeps education on two
        # lines). Shells that want them identical alias it to \resumeSubheading,
        # but every shell MUST define it or education silently fails to compile.
        for macro in ("\\sectionsep", "\\resumeItem", "\\resumeSubheading",
                      "\\resumeEducationSubheading",
                      "\\resumeProjectHeading", "\\resumeSubHeadingListStart",
                      "\\resumeSubHeadingListEnd", "\\resumeItemListStart",
                      "\\resumeItemListEnd"):
            check(macro in src, f"{slug}: defines {macro}", failures)
        # Known Tectonic/XeTeX traps. Check for an actual \usepackage of
        # fontawesome5 — the string also appears in each shell's comment
        # explaining why it is deliberately NOT loaded.
        loads_fa = any(
            "fontawesome" in ln and "usepackage" in ln and not ln.lstrip().startswith("%")
            for ln in src.splitlines()
        )
        check(not loads_fa,
              f"{slug}: does not load fontawesome5 via usepackage (XeTeX aborts)",
              failures)
        for line in src.splitlines():
            if "\\pdfgentounicode" in line or "glyphtounicode" in line:
                check("ifdefined" in line or "\\ifdefined\\pdfgentounicode" in src,
                      f"{slug}: pdfTeX primitives are \\ifdefined-guarded", failures)
                break

    print("fallback:")
    for bad in BAD_SLUGS:
        check(get_template(bad).slug == DEFAULT_TEMPLATE,
              f"{bad!r} falls back to default", failures)
    check(get_template("  classic  ").slug == "classic",
          "surrounding whitespace is tolerated", failures)

    print("substitution:")
    tex = render_resume_tex(dict(PROFILE, template=DEFAULT_TEMPLATE))
    # Check the specific placeholder tokens — a bare "{{" scan false-positives on
    # legitimately escaped braces in the rendered LaTeX.
    leftover = [ph for ph in ("{{FULL_NAME}}", "{{CONTACT_LINE}}", "{{SECTIONS}}")
                if ph in tex]
    check(not leftover, f"no unsubstituted placeholder remains (found {leftover})",
          failures)
    check("Ada Lovelace" in tex, "name reached the output", failures)
    # A raw, unescaped '100%' would comment out the rest of its line in LaTeX.
    check("100\\%" in tex, "percent signs are escaped", failures)
    check("\\textbf{91\\%}" in tex, "**bold** became \\textbf with escaped inner",
          failures)
    check("https://linkedin.com/in/ada" in tex, "scheme-less URL got https://",
          failures)

    # Every shell must render the SAME content — only the look may differ.
    print("compile:")
    if shutil.which("tectonic") is None:
        print("  SKIP  tectonic not on PATH — compile checks skipped")
    else:
        for slug in TEMPLATES:
            try:
                pdf, pages = render_resume_pdf_with_pages(dict(PROFILE, template=slug))
                ok = pdf.startswith(b"%PDF") and pages == 1
                check(ok, f"{slug}: compiles to a 1-page PDF "
                          f"({len(pdf):,} bytes, {pages}p)", failures)
            except Exception as e:  # noqa: BLE001 - report, don't abort the run
                check(False, f"{slug}: compiles ({type(e).__name__}: {e})", failures)

        print("long content (must wrap, not overflow):")
        for slug in TEMPLATES:
            try:
                pdf, _ = render_resume_pdf_with_pages(dict(LONG_PROFILE, template=slug))
                over = overflows_margin(pdf)
                if over is None:
                    print(f"  SKIP  {slug}: pdfplumber missing — margin check skipped")
                    continue
                edge = rightmost_text_edge(pdf)
                check(not over,
                      f"{slug}: long entries stay inside the right margin "
                      f"(rightmost glyph x={edge:.0f}, limit {RIGHT_MARGIN_LIMIT:.0f})",
                      failures)
            except Exception as e:  # noqa: BLE001
                check(False, f"{slug}: long-content compile "
                             f"({type(e).__name__}: {e})", failures)

    print(f"\n{'FAILED: ' + '; '.join(failures) if failures else 'ALL PASSED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
