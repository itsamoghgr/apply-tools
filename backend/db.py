"""SQLite access for the resumes + applications tables managed by Prisma.

Prisma (in the Next.js frontend) owns the schema and migrations. This module
only reads resume content and inserts Application rows; it never issues DDL.
"""

from __future__ import annotations

import logging
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("coverletter")

BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = (BACKEND_DIR / ".." / "data").resolve()
DB_PATH = DATA_DIR / "apply-tools.db"
PDF_DIR = DATA_DIR / "pdfs"


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection in WAL mode with Row factory."""
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"SQLite DB not found at {DB_PATH}. Run `cd frontend && npx prisma migrate dev`."
        )
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
    finally:
        conn.close()


def fetch_resume(resume_id: str | None) -> tuple[str, str] | None:
    """Return (id, content) for the given resume id, or the first active resume
    when id is None. Returns None if no match.
    """
    with get_conn() as conn:
        if resume_id:
            row = conn.execute(
                "SELECT id, content FROM Resume WHERE id = ?", (resume_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, content FROM Resume WHERE isActive = 1 ORDER BY id LIMIT 1"
            ).fetchone()
        return (row["id"], row["content"]) if row else None


def list_resume_rows() -> list[dict[str, str]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, label FROM Resume WHERE isActive = 1 ORDER BY id"
        ).fetchall()
        return [{"id": r["id"], "label": r["label"]} for r in rows]


def save_pdf(company: str, pdf_bytes: bytes) -> str:
    """Persist generated PDF and return its absolute path."""
    import re
    from datetime import datetime, timezone

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", company.strip()).strip("._-") or "Company"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = PDF_DIR / f"CoverLetter_{safe}_{ts}.pdf"
    out.write_bytes(pdf_bytes)
    return str(out)


# -----------------------------------------------------------------------------
# JobApplication CRUD (the user's tracker spreadsheet, separate from the
# Application audit log above).
# -----------------------------------------------------------------------------

JOB_APP_COLUMNS = (
    "companyName",
    "jobRole",
    "jobUrl",
    "location",
    "interviewStatus",
    "status",
    "appliedDate",
    "resumeId",
    "companyCareerPage",
    "decisionDate",
    "decisionTime",
    "notes",
    "hrName",
    "hrLinkedin",
    "hrEmail",
    "referral",
    "referralLinkedin",
    "jobDescription",
)


def insert_job_application(fields: dict) -> str:
    """Insert a JobApplication row. `fields` must contain at minimum companyName.

    Returns the new row's id. Unknown keys are ignored. Empty strings on
    nullable columns are converted to NULL so SQLite stores them consistently.
    """
    if not fields.get("companyName"):
        raise ValueError("companyName is required")

    app_id = secrets.token_urlsafe(12)
    cleaned = {"id": app_id}
    for col in JOB_APP_COLUMNS:
        if col not in fields:
            continue
        v = fields[col]
        if isinstance(v, str):
            v = v.strip()
            if v == "" and col != "companyName":
                v = None
        cleaned[col] = v

    cols = list(cleaned.keys())
    placeholders = ", ".join("?" for _ in cols)
    col_sql = ", ".join(f'"{c}"' for c in cols)
    values = [cleaned[c] for c in cols]

    with get_conn() as conn:
        conn.execute(
            f'INSERT INTO "JobApplication" ({col_sql}, "updatedAt") '
            f"VALUES ({placeholders}, CURRENT_TIMESTAMP)",
            values,
        )
        conn.commit()
    return app_id


def update_job_application(app_id: str, fields: dict) -> bool:
    """Patch a JobApplication row. Only known columns are written.

    Returns True if a row was updated, False if no such id (or no fields).
    """
    updates = {}
    for col in JOB_APP_COLUMNS:
        if col not in fields:
            continue
        v = fields[col]
        if isinstance(v, str):
            v = v.strip()
            if v == "":
                v = None
        updates[col] = v
    if not updates:
        return False

    set_sql = ", ".join(f'"{c}" = ?' for c in updates) + ', "updatedAt" = CURRENT_TIMESTAMP'
    values = list(updates.values()) + [app_id]
    with get_conn() as conn:
        cur = conn.execute(
            f'UPDATE "JobApplication" SET {set_sql} WHERE "id" = ?', values
        )
        conn.commit()
        return cur.rowcount > 0


def delete_job_application(app_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute('DELETE FROM "JobApplication" WHERE "id" = ?', (app_id,))
        conn.commit()
        return cur.rowcount > 0


def list_job_applications() -> list[dict]:
    """Return every JobApplication row as plain dicts, newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT * FROM "JobApplication" ORDER BY "createdAt" DESC'
        ).fetchall()
        return [dict(r) for r in rows]


# -----------------------------------------------------------------------------
# ReachOut CRUD (LinkedIn-driven outreach emails: draft, edit, send).
# -----------------------------------------------------------------------------

REACH_OUT_INSERT_COLUMNS = (
    "recipientName",
    "recipientEmail",
    "linkedinProfile",
    "contextNote",
    "resumeId",
    "subject",
    "body",
)

REACH_OUT_PATCH_COLUMNS = (
    "recipientName",
    "recipientEmail",
    "linkedinProfile",
    "contextNote",
    "resumeId",
    "subject",
    "body",
    "htmlBody",
    "status",
    "sentAt",
    "errorMessage",
)


def _clean_reach_out_value(col: str, value):
    if isinstance(value, str):
        v = value.strip()
        # recipientName / recipientEmail / linkedinProfile / subject / body
        # are NOT NULL — keep empty strings out of UPDATEs by callers.
        if v == "" and col in {
            "contextNote",
            "resumeId",
            "errorMessage",
            "sentAt",
            "htmlBody",
        }:
            return None
        return v
    return value


def insert_reach_out(fields: dict, *, require_content: bool = True) -> str:
    """Insert a ReachOut row in 'draft' status. Returns the new id.

    When `require_content` is True (default, used by the AI-generated path),
    `linkedinProfile`, `subject`, and `body` must each be non-empty strings.
    Set False for blank manual drafts where the user will fill in subject
    and body inside the editor before sending — we still write empty
    strings into those NOT NULL columns to keep the schema simple.
    """
    base_required = ("recipientName", "recipientEmail")
    content_required = (
        ("linkedinProfile", "subject", "body") if require_content else ()
    )
    for col in base_required + content_required:
        v = fields.get(col)
        if not (isinstance(v, str) and v.strip()):
            raise ValueError(f"{col} is required")

    row_id = secrets.token_urlsafe(12)
    cleaned: dict = {"id": row_id}
    for col in REACH_OUT_INSERT_COLUMNS:
        if col in fields:
            cleaned[col] = _clean_reach_out_value(col, fields[col])
    # Backfill the NOT NULL content columns with empty strings when the
    # caller is creating a blank manual draft.
    for col in ("linkedinProfile", "subject", "body"):
        cleaned.setdefault(col, "")

    cols = list(cleaned.keys())
    placeholders = ", ".join("?" for _ in cols)
    col_sql = ", ".join(f'"{c}"' for c in cols)
    values = [cleaned[c] for c in cols]
    with get_conn() as conn:
        conn.execute(
            f'INSERT INTO "ReachOut" ({col_sql}, "status", "updatedAt") '
            f"VALUES ({placeholders}, 'draft', CURRENT_TIMESTAMP)",
            values,
        )
        conn.commit()
    return row_id


def get_reach_out(row_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            'SELECT * FROM "ReachOut" WHERE "id" = ?', (row_id,)
        ).fetchone()
        return dict(row) if row else None


def update_reach_out(row_id: str, fields: dict) -> bool:
    """Patch a ReachOut row. Only known columns are written. Returns True on hit."""
    updates: dict = {}
    for col in REACH_OUT_PATCH_COLUMNS:
        if col in fields:
            updates[col] = _clean_reach_out_value(col, fields[col])
    if not updates:
        return False

    set_sql = ", ".join(f'"{c}" = ?' for c in updates) + ', "updatedAt" = CURRENT_TIMESTAMP'
    values = list(updates.values()) + [row_id]
    with get_conn() as conn:
        cur = conn.execute(
            f'UPDATE "ReachOut" SET {set_sql} WHERE "id" = ?', values
        )
        conn.commit()
        return cur.rowcount > 0


def delete_reach_out(row_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute('DELETE FROM "ReachOut" WHERE "id" = ?', (row_id,))
        conn.commit()
        return cur.rowcount > 0


def list_reach_outs() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT * FROM "ReachOut" ORDER BY "createdAt" DESC'
        ).fetchall()
        return [dict(r) for r in rows]


# Event recording moved off-box: see tracking-sidecar/main.py. The local
# backend no longer holds a per-event row or aggregate counters; both are
# fetched on demand from the sidecar via `/reach-out/{id}/events` and
# `/reach-out/aggregates`.


# -----------------------------------------------------------------------------
# Setting key/value store (used for Gmail credentials).
# -----------------------------------------------------------------------------


def get_setting(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            'SELECT "value" FROM "Setting" WHERE "key" = ?', (key,)
        ).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            'INSERT INTO "Setting" ("key", "value", "updatedAt") '
            "VALUES (?, ?, CURRENT_TIMESTAMP) "
            'ON CONFLICT("key") DO UPDATE SET "value" = excluded."value", '
            '"updatedAt" = CURRENT_TIMESTAMP',
            (key, value),
        )
        conn.commit()


def delete_setting(key: str) -> None:
    with get_conn() as conn:
        conn.execute('DELETE FROM "Setting" WHERE "key" = ?', (key,))
        conn.commit()


# -----------------------------------------------------------------------------
# Application audit-log insert (covers cover letters, emails, scoring, etc).
# -----------------------------------------------------------------------------


def insert_application(
    *,
    mode: str,
    company: str | None = None,
    job_description: str | None = None,
    resume_id: str | None = None,
    output: str | None = None,
    score_data: str | None = None,
    pdf_path: str | None = None,
) -> str:
    """Insert an Application row and return its id."""
    app_id = secrets.token_urlsafe(12)
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO Application (
                    id, mode, company, jobDescription, resumeId,
                    output, scoreData, pdfPath, createdAt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    app_id,
                    mode,
                    company,
                    job_description,
                    resume_id,
                    output,
                    score_data,
                    pdf_path,
                ),
            )
            conn.commit()
    except sqlite3.Error as exc:
        # Logging-only failure — generations should not fail because the audit
        # log is unavailable.
        logger.warning("Failed to log application (%s): %s", mode, exc)
    return app_id
