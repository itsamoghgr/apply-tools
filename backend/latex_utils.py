"""LaTeX helpers: escape user-supplied strings and compile a .tex source via tectonic."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path


_LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape_latex(text: str) -> str:
    """Escape characters that have special meaning in LaTeX.

    Apply to short strings substituted into the template (company, role, hiring
    manager). Do NOT apply to body content where the LLM is allowed to emit a
    whitelist of LaTeX commands.
    """
    if text is None:
        return ""
    # Two-pass: handle backslash first to avoid double-escaping the replacement.
    out = []
    for ch in text:
        out.append(_LATEX_SPECIALS.get(ch, ch))
    return "".join(out)


class LatexCompileError(RuntimeError):
    """Raised when tectonic exits non-zero. Includes stderr for debugging."""


def compile_latex(tex_source: str, jobname: str = "cover_letter") -> bytes:
    """Compile a complete .tex source string to PDF bytes via tectonic.

    Runs in an isolated temporary directory and cleans up automatically.
    Raises LatexCompileError with tectonic's stderr if compilation fails.
    Raises FileNotFoundError if tectonic is not on PATH.
    """
    if shutil.which("tectonic") is None:
        raise FileNotFoundError(
            "tectonic not found on PATH. Install with `brew install tectonic` "
            "(macOS) or download a binary from https://tectonic-typesetting.github.io/"
        )

    with tempfile.TemporaryDirectory(prefix="coverletter_") as tmp:
        tmp_path = Path(tmp)
        tex_path = tmp_path / f"{jobname}.tex"
        tex_path.write_text(tex_source, encoding="utf-8")

        proc = subprocess.run(
            [
                "tectonic",
                "--keep-logs",
                "--outdir",
                str(tmp_path),
                str(tex_path),
            ],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )

        if proc.returncode != 0:
            raise LatexCompileError(
                f"tectonic exited with code {proc.returncode}.\n"
                f"--- stdout ---\n{proc.stdout}\n"
                f"--- stderr ---\n{proc.stderr}"
            )

        pdf_path = tmp_path / f"{jobname}.pdf"
        if not pdf_path.exists():
            raise LatexCompileError(
                f"tectonic reported success but no PDF was produced.\n"
                f"--- stdout ---\n{proc.stdout}\n"
                f"--- stderr ---\n{proc.stderr}"
            )

        return pdf_path.read_bytes()


# Fallback page-object scan for the rare uncompressed PDF. Tectonic compresses
# everything into object streams, so this regex finds nothing there — pypdf is
# the real path. Careful NOT to match `/Type /Pages` (the tree node).
_PAGE_OBJ_RE = re.compile(rb"/Type\s*/Page(?![s])")


def count_pdf_pages(pdf_bytes: bytes) -> int:
    """Return the number of pages in a PDF.

    Uses pypdf (handles Tectonic's compressed object/xref streams). Falls back
    to a plaintext page-object scan, then returns 0 if both fail — callers
    treat 0 as "unknown" and skip the page-limit check rather than wrongly
    blocking the user.
    """
    try:
        import io

        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(pdf_bytes)).pages)
    except Exception:
        # pypdf missing or parse error — try the plaintext scan as a last resort.
        return len(_PAGE_OBJ_RE.findall(pdf_bytes))
