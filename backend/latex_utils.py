"""LaTeX helpers: escape user-supplied strings and compile a .tex source via tectonic."""

from __future__ import annotations

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
