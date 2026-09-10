"""Registry of resume LaTeX shells (the "templates" a user picks in the builder).

A template is a complete .tex file in ``resume_templates/`` containing the same
three placeholders the renderer substitutes — ``{{FULL_NAME}}``,
``{{CONTACT_LINE}}`` and ``{{SECTIONS}}``. Templates vary the *shell* only:
preamble packages, page margins, fonts, section-heading style, and the
definitions of the ``\\resume*`` layout macros. The per-section LaTeX itself is
emitted by the shared code in resume_render.py, so every template consumes the
same structured profile and the same (security-sensitive) escaping path.

That split is deliberate: the sanitisation in resume_render.py is what keeps a
stray ``%``/``&``/``$`` in user text from crashing tectonic, and duplicating it
per template is exactly how that would rot. A template that needs a different
*structure* (two columns, say) would require moving the section emitters behind
this registry too — reachable from here, but not what these shells do today.

Every shell MUST compile under Tectonic's XeTeX. Two known traps, both handled
in classic.tex and worth copying rather than rediscovering:
  * ``fontawesome5`` needs loadable OTF fonts XeTeX can't resolve here and
    aborts with SIGABRT — don't load it.
  * ``\\pdfgentounicode`` / ``glyphtounicode`` are pdfTeX-only primitives; guard
    any use with ``\\ifdefined\\pdfgentounicode`` or XeTeX dies on an undefined
    control sequence.

Education entries are emitted with ``\\resumeEducationSubheading`` instead of
``\\resumeSubheading`` so a shell can style them separately (``compact`` keeps
education on two lines while collapsing experience to one). A shell that wants
them identical aliases the two — but every shell MUST define it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent / "resume_templates"

# The slug stored on a ResumeProfile when none was ever chosen, and the fallback
# for any slug we don't recognise. This is the original (and only) look the
# builder shipped with, so pre-existing resumes render exactly as before.
DEFAULT_TEMPLATE = "classic"


@dataclass(frozen=True)
class ResumeTemplate:
    slug: str
    name: str
    description: str
    filename: str

    @property
    def path(self) -> Path:
        return TEMPLATES_DIR / self.filename

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8")


# Order here is the order the builder's picker shows them in.
TEMPLATES: dict[str, ResumeTemplate] = {
    t.slug: t
    for t in (
        ResumeTemplate(
            slug="classic",
            name="Classic",
            description="The original sb2nov look — small caps headings with a full-width rule.",
            filename="classic.tex",
        ),
        ResumeTemplate(
            slug="compact",
            name="Compact",
            description="Classic, with experience entries on one line instead of two.",
            filename="compact.tex",
        ),
        ResumeTemplate(
            slug="modern",
            name="Modern",
            description="Sans-serif headings and employer names in a navy accent, over a thin rule.",
            filename="modern.tex",
        ),
        ResumeTemplate(
            slug="serif",
            name="Serif",
            description="Book-style roman text with centred small-caps headings. No rules.",
            filename="serif.tex",
        ),
    )
}


def get_template(slug: object) -> ResumeTemplate:
    """Resolve a stored slug to a template, falling back to the default.

    Deliberately total: an unknown slug, a non-string, ``None``, or a template
    whose file has gone missing all yield the default rather than raising, so a
    bad/legacy value can never make a resume unrenderable.
    """
    if isinstance(slug, str):
        tpl = TEMPLATES.get(slug.strip())
        if tpl is not None and tpl.path.is_file():
            return tpl
    return TEMPLATES[DEFAULT_TEMPLATE]


def list_templates() -> list[dict[str, str]]:
    """Serialisable template list for the builder's picker."""
    return [
        {"slug": t.slug, "name": t.name, "description": t.description}
        for t in TEMPLATES.values()
    ]
