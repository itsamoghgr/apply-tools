"""Postgres access for the resumes + applications tables managed by Prisma.

Prisma (in the Next.js frontend) owns the schema and migrations. This module
only reads/writes rows; it never issues DDL.

Connection comes from DATABASE_URL (the same variable the frontend uses), e.g.
    postgresql://apply:apply@localhost:5432/apply_tools
We normalise that to the psycopg (v3) driver and pool connections via a single
module-level SQLAlchemy engine.

Datetime contract: Prisma stores DateTime as native Postgres timestamp(3).
psycopg returns those as Python ``datetime`` objects and accepts ``datetime``
(or ISO strings) on the way in. Callers in server.py pass timezone-aware
datetimes; on read we serialise datetimes to ISO-8601 strings so the API
responses stay JSON-friendly and stable for the extension/UI.
"""

from __future__ import annotations

import os
import re
import json
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, Row
from sqlalchemy.exc import IntegrityError

from log import get_logger

# Load backend/.env so DATABASE_URL is visible even when db is imported before
# any other module calls load_dotenv() (server.py imports db first).
load_dotenv()

logger = get_logger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = (BACKEND_DIR / ".." / "data").resolve()
# PDFs still live on disk next to the (now retired) data dir.
PDF_DIR = DATA_DIR / "pdfs"


class UniqueViolation(Exception):
    """Raised when an INSERT/UPDATE violates a unique constraint.

    server.py catches this to turn a duplicate Lead.email into a clean 409.
    Carries the offending column name when we can parse it from the driver
    error, so callers can craft a precise message.
    """

    def __init__(self, message: str, column: str | None = None):
        super().__init__(message)
        self.column = column


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Point it at Postgres, e.g. "
            "postgresql://apply:apply@localhost:5432/apply_tools"
        )
    # Prisma-style URLs use the bare postgres:// scheme. Pin the psycopg v3
    # driver so SQLAlchemy doesn't reach for psycopg2.
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    # Strip Prisma-only query params (e.g. ?schema=public, connection_limit)
    # that the psycopg driver doesn't understand. We keep the default search
    # path, which resolves the public schema Prisma created.
    url = re.sub(r"\?.*$", "", url)
    return url


# Single pooled engine for the process, created lazily on first use so that
# importing this module never fails just because DATABASE_URL isn't set yet.
# pool_pre_ping recycles connections dropped by the server so a long-idle
# backend doesn't 500 on the first query.
_engine_singleton: Engine | None = None


def get_engine() -> Engine:
    global _engine_singleton
    if _engine_singleton is None:
        _engine_singleton = create_engine(
            _database_url(),
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            future=True,
        )
    return _engine_singleton


@contextmanager
def get_conn() -> Iterator[Connection]:
    """Yield a transactional SQLAlchemy connection.

    The block commits on success and rolls back on exception, mirroring the
    explicit conn.commit()/close() the old sqlite3 code did by hand. Callers
    no longer call .commit() themselves.
    """
    with get_engine().begin() as conn:
        yield conn


# Datetime columns across all tables — used to coerce psycopg datetime objects
# back into ISO-8601 strings on read, preserving the wire format the UI and
# extension already consume.
_DATETIME_COLUMNS = frozenset(
    {
        "appliedDate",
        "decisionDate",
        "createdAt",
        "updatedAt",
        "repliedAt",
        "sentAt",
        "linkedAt",
        "lastSentAt",
    }
)


def _row_to_dict(row: Row) -> dict[str, Any]:
    """Convert a SQLAlchemy Row to a plain dict, ISO-stringifying datetimes."""
    d = dict(row._mapping)
    for key, value in d.items():
        if isinstance(value, datetime) and key in _DATETIME_COLUMNS:
            d[key] = value.isoformat()
    return d


def _rows_to_dicts(rows) -> list[dict[str, Any]]:
    return [_row_to_dict(r) for r in rows]


def fetch_resume(resume_id: str | None) -> tuple[str, str] | None:
    """Return (id, content) for the given resume id, or the first active resume
    when id is None. Returns None if no match.
    """
    with get_conn() as conn:
        if resume_id:
            row = conn.execute(
                text('SELECT id, content FROM "Resume" WHERE id = :id'),
                {"id": resume_id},
            ).fetchone()
        else:
            row = conn.execute(
                text(
                    'SELECT id, content FROM "Resume" '
                    'WHERE "isActive" = true ORDER BY id LIMIT 1'
                )
            ).fetchone()
        return (row.id, row.content) if row else None


def list_resume_rows() -> list[dict[str, str]]:
    with get_conn() as conn:
        rows = conn.execute(
            text(
                'SELECT id, label FROM "Resume" '
                'WHERE "isActive" = true ORDER BY id'
            )
        ).fetchall()
        return [{"id": r.id, "label": r.label} for r in rows]


def save_pdf(company: str, pdf_bytes: bytes) -> str:
    """Persist generated PDF and return its absolute path."""
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
    nullable columns are converted to NULL so storage stays consistent.
    """
    if not fields.get("companyName"):
        raise ValueError("companyName is required")

    app_id = secrets.token_urlsafe(12)
    cleaned: dict[str, Any] = {"id": app_id}
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
    col_sql = ", ".join(f'"{c}"' for c in cols)
    bind_sql = ", ".join(f":{c}" for c in cols)

    with get_conn() as conn:
        conn.execute(
            text(
                f'INSERT INTO "JobApplication" ({col_sql}, "updatedAt") '
                f"VALUES ({bind_sql}, CURRENT_TIMESTAMP)"
            ),
            cleaned,
        )
    return app_id


def update_job_application(app_id: str, fields: dict) -> bool:
    """Patch a JobApplication row. Only known columns are written.

    Returns True if a row was updated, False if no such id (or no fields).
    """
    updates: dict[str, Any] = {}
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

    set_sql = (
        ", ".join(f'"{c}" = :{c}' for c in updates)
        + ', "updatedAt" = CURRENT_TIMESTAMP'
    )
    params = dict(updates, _id=app_id)
    with get_conn() as conn:
        cur = conn.execute(
            text(f'UPDATE "JobApplication" SET {set_sql} WHERE "id" = :_id'),
            params,
        )
        return cur.rowcount > 0


def delete_job_application(app_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            text('DELETE FROM "JobApplication" WHERE "id" = :id'), {"id": app_id}
        )
        return cur.rowcount > 0


def list_job_applications() -> list[dict]:
    """Return every JobApplication row as plain dicts, newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            text('SELECT * FROM "JobApplication" ORDER BY "createdAt" DESC')
        ).fetchall()
        return _rows_to_dicts(rows)


# -----------------------------------------------------------------------------
# JobApplication ↔ Lead links (many-to-many via JobApplicationLead).
# -----------------------------------------------------------------------------


def add_job_application_lead(
    app_id: str, lead_id: str, role: str | None = None
) -> bool:
    """Link a Lead to a JobApplication. Idempotent on the (app, lead) pair."""
    role_v = role.strip() if isinstance(role, str) and role.strip() else None
    with get_conn() as conn:
        cur = conn.execute(
            text(
                'INSERT INTO "JobApplicationLead" '
                '("jobApplicationId", "leadId", "role") '
                "VALUES (:app, :lead, :role) "
                'ON CONFLICT ("jobApplicationId", "leadId") DO NOTHING'
            ),
            {"app": app_id, "lead": lead_id, "role": role_v},
        )
        return cur.rowcount > 0


def remove_job_application_lead(app_id: str, lead_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            text(
                'DELETE FROM "JobApplicationLead" '
                'WHERE "jobApplicationId" = :app AND "leadId" = :lead'
            ),
            {"app": app_id, "lead": lead_id},
        )
        return cur.rowcount > 0


def list_leads_for_application(app_id: str) -> list[dict]:
    """Return all leads linked to an application, with the join's `role` tag."""
    with get_conn() as conn:
        rows = conn.execute(
            text(
                'SELECT l.*, jal."role" AS "linkRole", '
                'jal."createdAt" AS "linkedAt" '
                'FROM "JobApplicationLead" jal '
                'JOIN "Lead" l ON l."id" = jal."leadId" '
                'WHERE jal."jobApplicationId" = :app '
                'ORDER BY jal."createdAt" ASC'
            ),
            {"app": app_id},
        ).fetchall()
        return _rows_to_dicts(rows)


def list_reach_outs_for_application(app_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            text(
                'SELECT * FROM "ReachOut" WHERE "jobApplicationId" = :app '
                'ORDER BY "createdAt" DESC'
            ),
            {"app": app_id},
        ).fetchall()
        return _rows_to_dicts(rows)


# -----------------------------------------------------------------------------
# ReachOut CRUD (LinkedIn-driven outreach emails: draft, edit, send).
# -----------------------------------------------------------------------------

REACH_OUT_INSERT_COLUMNS = (
    "recipientName",
    "recipientEmail",
    "linkedinProfile",
    "contextNote",
    "resumeId",
    "leadId",
    "jobApplicationId",
    "channel",
    "subject",
    "body",
)

REACH_OUT_PATCH_COLUMNS = (
    "recipientName",
    "recipientEmail",
    "linkedinProfile",
    "contextNote",
    "resumeId",
    "channel",
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
            "leadId",
            "jobApplicationId",
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
    channel = (fields.get("channel") or "email").strip() or "email"
    # Email channel needs an address; LinkedIn channels don't (the user
    # pastes the message into LinkedIn manually).
    base_required = (
        ("recipientName", "recipientEmail") if channel == "email" else ("recipientName",)
    )
    # LinkedIn invitations have no subject (just a 300-char note).
    if require_content:
        if channel == "linkedin_invitation":
            content_required = ("linkedinProfile", "body")
        else:
            content_required = ("linkedinProfile", "subject", "body")
    else:
        content_required = ()
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
    # caller skipped them (blank manual draft, or LinkedIn channels where
    # subject/recipientEmail don't apply).
    for col in ("recipientEmail", "linkedinProfile", "subject", "body"):
        cleaned.setdefault(col, "")

    cols = list(cleaned.keys())
    col_sql = ", ".join(f'"{c}"' for c in cols)
    bind_sql = ", ".join(f":{c}" for c in cols)
    with get_conn() as conn:
        conn.execute(
            text(
                f'INSERT INTO "ReachOut" ({col_sql}, "status", "updatedAt") '
                f"VALUES ({bind_sql}, 'draft', CURRENT_TIMESTAMP)"
            ),
            cleaned,
        )
    return row_id


def get_reach_out(row_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            text('SELECT * FROM "ReachOut" WHERE "id" = :id'), {"id": row_id}
        ).fetchone()
        return _row_to_dict(row) if row else None


def update_reach_out(row_id: str, fields: dict) -> bool:
    """Patch a ReachOut row. Only known columns are written. Returns True on hit."""
    updates: dict = {}
    for col in REACH_OUT_PATCH_COLUMNS:
        if col in fields:
            updates[col] = _clean_reach_out_value(col, fields[col])
    if not updates:
        return False

    set_sql = (
        ", ".join(f'"{c}" = :{c}' for c in updates)
        + ', "updatedAt" = CURRENT_TIMESTAMP'
    )
    params = dict(updates, _id=row_id)
    with get_conn() as conn:
        cur = conn.execute(
            text(f'UPDATE "ReachOut" SET {set_sql} WHERE "id" = :_id'), params
        )
        return cur.rowcount > 0


def delete_reach_out(row_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            text('DELETE FROM "ReachOut" WHERE "id" = :id'), {"id": row_id}
        )
        return cur.rowcount > 0


def list_reach_outs() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            text('SELECT * FROM "ReachOut" ORDER BY "createdAt" DESC')
        ).fetchall()
        return _rows_to_dicts(rows)


# Event recording moved off-box: see tracking-sidecar/main.py. The local
# backend no longer holds a per-event row or aggregate counters; both are
# fetched on demand from the sidecar via `/reach-out/{id}/events` and
# `/reach-out/aggregates`.


# -----------------------------------------------------------------------------
# Lead CRUD (the people you might reach out to). A Lead is the master
# record; ReachOut rows can reference one via ReachOut.leadId so each
# Lead's profile shows how many emails were sent to them.
# -----------------------------------------------------------------------------

LEAD_INSERT_COLUMNS = (
    "name",
    "email",
    "linkedinUrl",
    "linkedinProfile",
    "currentCompany",
    "role",
    "replied",
    "repliedAt",
    "notes",
)

LEAD_PATCH_COLUMNS = LEAD_INSERT_COLUMNS


def _clean_lead_value(col: str, value):
    """Empty strings on nullable columns become NULL. `name` is NOT NULL."""
    if isinstance(value, str):
        v = value.strip()
        if v == "" and col != "name":
            return None
        return v
    return value


def insert_lead(fields: dict) -> str:
    """Insert a Lead row. `name` is required.

    Returns the new id. Raises ValueError if name is missing or empty,
    or UniqueViolation if `email` collides with an existing Lead.email
    (the column is UNIQUE).
    """
    name = fields.get("name")
    if not (isinstance(name, str) and name.strip()):
        raise ValueError("name is required")

    lead_id = secrets.token_urlsafe(12)
    cleaned: dict = {"id": lead_id}
    for col in LEAD_INSERT_COLUMNS:
        if col not in fields:
            continue
        cleaned[col] = _clean_lead_value(col, fields[col])

    # Auto-stamp repliedAt when the caller flips replied=true without
    # supplying their own timestamp, mirroring the UI's expectation.
    if cleaned.get("replied") and not cleaned.get("repliedAt"):
        cleaned["repliedAt"] = datetime.now(timezone.utc)

    cols = list(cleaned.keys())
    col_sql = ", ".join(f'"{c}"' for c in cols)
    bind_sql = ", ".join(f":{c}" for c in cols)
    with get_conn() as conn:
        try:
            conn.execute(
                text(
                    f'INSERT INTO "Lead" ({col_sql}, "updatedAt") '
                    f"VALUES ({bind_sql}, CURRENT_TIMESTAMP)"
                ),
                cleaned,
            )
        except IntegrityError as exc:
            raise _as_unique_violation(exc) from exc
    return lead_id


def update_lead(lead_id: str, fields: dict) -> bool:
    """Patch a Lead row. Returns True on hit, False if no such id or no
    known fields were sent.

    Side effect: setting `replied` true without `repliedAt` stamps the
    timestamp; setting `replied` false clears `repliedAt` unless the
    caller also passed an explicit value.
    """
    updates: dict = {}
    for col in LEAD_PATCH_COLUMNS:
        if col in fields:
            updates[col] = _clean_lead_value(col, fields[col])
    if not updates:
        return False

    if "replied" in updates:
        if updates["replied"] and "repliedAt" not in updates:
            updates["repliedAt"] = datetime.now(timezone.utc)
        elif not updates["replied"] and "repliedAt" not in updates:
            updates["repliedAt"] = None

    set_sql = (
        ", ".join(f'"{c}" = :{c}' for c in updates)
        + ', "updatedAt" = CURRENT_TIMESTAMP'
    )
    params = dict(updates, _id=lead_id)
    with get_conn() as conn:
        try:
            cur = conn.execute(
                text(f'UPDATE "Lead" SET {set_sql} WHERE "id" = :_id'), params
            )
        except IntegrityError as exc:
            raise _as_unique_violation(exc) from exc
        return cur.rowcount > 0


def delete_lead(lead_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            text('DELETE FROM "Lead" WHERE "id" = :id'), {"id": lead_id}
        )
        return cur.rowcount > 0


def get_lead(lead_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            text('SELECT * FROM "Lead" WHERE "id" = :id'), {"id": lead_id}
        ).fetchone()
        return _row_to_dict(row) if row else None


def list_leads() -> list[dict]:
    """Return every Lead with reach-out aggregates joined in.

    A single LEFT JOIN keeps this O(N) instead of N+1; we surface
    `reachOutCount`, `lastSentAt`, and `lastStatus` so the dashboard can
    show "3 emails, last sent 2d ago" without a follow-up query.
    """
    with get_conn() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    l.*,
                    COALESCE(agg."reachOutCount", 0) AS "reachOutCount",
                    agg."lastSentAt" AS "lastSentAt",
                    agg."lastStatus" AS "lastStatus"
                FROM "Lead" l
                LEFT JOIN (
                    SELECT
                        ro."leadId" AS "leadId",
                        COUNT(*) AS "reachOutCount",
                        MAX(ro."sentAt") AS "lastSentAt",
                        -- pick the status of the most recent reach-out
                        (SELECT r2."status"
                           FROM "ReachOut" r2
                          WHERE r2."leadId" = ro."leadId"
                          ORDER BY r2."createdAt" DESC, r2."id" DESC
                          LIMIT 1) AS "lastStatus"
                    FROM "ReachOut" ro
                    WHERE ro."leadId" IS NOT NULL
                    GROUP BY ro."leadId"
                ) agg ON agg."leadId" = l."id"
                ORDER BY l."createdAt" DESC
                """
            )
        ).fetchall()
        return _rows_to_dicts(rows)


def find_or_create_lead_by_email(
    name: str,
    email: str | None,
    *,
    linkedin_profile: str | None = None,
    linkedin_url: str | None = None,
    current_company: str | None = None,
    role: str | None = None,
) -> str | None:
    """Look up a Lead by email; create one if missing. Returns the lead id,
    or None when no email was provided (so we don't accidentally create
    nameless duplicate rows for every blank email).

    Used by the ReachOut create paths to keep the Lead → ReachOut graph
    populated automatically. Existing Lead rows are NOT mutated here —
    callers can edit them on the Leads page if their data drifted.
    """
    if not (isinstance(email, str) and email.strip()):
        return None
    email_clean = email.strip()
    with get_conn() as conn:
        row = conn.execute(
            text('SELECT "id" FROM "Lead" WHERE "email" = :email'),
            {"email": email_clean},
        ).fetchone()
        if row:
            return row.id
    fields = {
        "name": name.strip() if isinstance(name, str) and name.strip() else email_clean,
        "email": email_clean,
        "linkedinProfile": linkedin_profile,
        "linkedinUrl": linkedin_url,
        "currentCompany": current_company,
        "role": role,
    }
    return insert_lead(fields)


def _as_unique_violation(exc: IntegrityError) -> Exception:
    """Map a SQLAlchemy IntegrityError to UniqueViolation when it's a unique
    constraint breach, else return the original error unchanged.

    Postgres reports unique breaches with SQLSTATE 23505. We try to pull the
    column name out of the constraint/detail text (e.g. "Lead_email_key" or
    "Key (email)=(...) already exists") for a precise caller message.
    """
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate != "23505":
        return exc
    msg = str(orig) if orig else str(exc)
    col = None
    m = re.search(r"Key \((?P<col>[^)]+)\)=", msg)
    if m:
        col = m.group("col")
    else:
        m = re.search(r'"\w+_(?P<col>\w+)_key"', msg)
        if m:
            col = m.group("col")
    return UniqueViolation(msg, column=col)


# -----------------------------------------------------------------------------
# Setting key/value store (used for Gmail credentials).
# -----------------------------------------------------------------------------


def get_setting(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            text('SELECT "value" FROM "Setting" WHERE "key" = :key'),
            {"key": key},
        ).fetchone()
        return row.value if row else None


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            text(
                'INSERT INTO "Setting" ("key", "value", "updatedAt") '
                "VALUES (:key, :value, CURRENT_TIMESTAMP) "
                'ON CONFLICT ("key") DO UPDATE SET "value" = EXCLUDED."value", '
                '"updatedAt" = CURRENT_TIMESTAMP'
            ),
            {"key": key, "value": value},
        )


def delete_setting(key: str) -> None:
    with get_conn() as conn:
        conn.execute(
            text('DELETE FROM "Setting" WHERE "key" = :key'), {"key": key}
        )


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
                text(
                    """
                    INSERT INTO "Application" (
                        id, mode, company, "jobDescription", "resumeId",
                        output, "scoreData", "pdfPath", "createdAt"
                    ) VALUES (
                        :id, :mode, :company, :job_description, :resume_id,
                        :output, :score_data, :pdf_path, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "id": app_id,
                    "mode": mode,
                    "company": company,
                    "job_description": job_description,
                    "resume_id": resume_id,
                    "output": output,
                    "score_data": score_data,
                    "pdf_path": pdf_path,
                },
            )
    except Exception as exc:
        # Logging-only failure — generations should not fail because the audit
        # log is unavailable.
        logger.warning("application_log_failed", mode=mode, error=str(exc))
    return app_id


# -----------------------------------------------------------------------------
# Domain-keyed lead intake (used by the lead-generation agent service).
#
# The agent server (agent_server/, port 8001) discovers + verifies startups and
# pushes clean leads here over HTTP. These two helpers back the two new
# endpoints (POST /api/v1/leads/exists, POST /api/v1/leads/upsert). They key on
# the normalised root `domain` column added by platform_migration.sql, with a
# partial UNIQUE index (domain WHERE domain IS NOT NULL).
# -----------------------------------------------------------------------------


def platform_leads_known_domains(domains: list[str]) -> list[str]:
    """Return the subset of `domains` already present on some Lead row.

    Used by the agent's dedup stage to avoid re-researching anything the
    platform already has. Case-insensitive on the stored domain.
    """
    wanted = [d.strip().lower() for d in domains if isinstance(d, str) and d.strip()]
    if not wanted:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            text(
                'SELECT DISTINCT "domain" FROM "Lead" '
                'WHERE "domain" = ANY(:domains) AND "domain" IS NOT NULL'
            ),
            {"domains": wanted},
        ).fetchall()
    return [r.domain for r in rows]


def _upsert_founder_person_lead(
    conn,
    *,
    founder_name: str | None,
    founder_email: str | None,
    founder_linkedin_url: str | None,
    company_name: str | None,
) -> None:
    """Maintain a person-lead (domain NULL) for a discovered company's founder,
    so founders flow into the Outreach tab automatically.

    Idempotent across hunt re-runs: matches an existing agent-founder row by
    LinkedIn URL first, else by (name + company), and updates it; otherwise
    inserts. Tagged ``source='agent-founder'`` and ``role='Founder'`` so it is
    distinct from the domain-keyed company row and from hand-added leads. Runs
    in the SAME transaction/connection as the company upsert.
    """
    if not (isinstance(founder_name, str) and founder_name.strip()):
        return  # nothing to create without at least a name
    founder_name = founder_name.strip()
    li = (founder_linkedin_url or "").strip() or None
    company = (company_name or "").strip() or None

    # Find an existing agent-founder row to update (avoid duplicates on re-run).
    row = None
    if li:
        row = conn.execute(
            text(
                "SELECT \"id\" FROM \"Lead\" WHERE \"source\" = 'agent-founder' "
                'AND "linkedinUrl" = :li LIMIT 1'
            ),
            {"li": li},
        ).fetchone()
    if row is None:
        row = conn.execute(
            text(
                "SELECT \"id\" FROM \"Lead\" WHERE \"source\" = 'agent-founder' "
                'AND "name" = :n AND "currentCompany" IS NOT DISTINCT FROM :c '
                "LIMIT 1"
            ),
            {"n": founder_name, "c": company},
        ).fetchone()

    if row is not None:
        # Refresh contact details without clobbering existing values with nulls.
        conn.execute(
            text(
                'UPDATE "Lead" SET '
                '"email" = COALESCE(:email, "email"), '
                '"linkedinUrl" = COALESCE(:li, "linkedinUrl"), '
                '"currentCompany" = COALESCE(:c, "currentCompany"), '
                '"updatedAt" = CURRENT_TIMESTAMP '
                'WHERE "id" = :id'
            ),
            {"email": founder_email, "li": li, "c": company, "id": row.id},
        )
        return

    # Insert a fresh person-lead. domain stays NULL so it shows in Outreach.
    # Email may collide with an existing person-lead (email is UNIQUE); on
    # conflict we simply skip rather than fail the whole delivery.
    try:
        conn.execute(
            text(
                'INSERT INTO "Lead" ("id", "name", "email", "linkedinUrl", '
                '"currentCompany", "role", "source", "updatedAt") VALUES '
                "(:id, :n, :email, :li, :c, 'Founder', 'agent-founder', "
                "CURRENT_TIMESTAMP)"
            ),
            {
                "id": secrets.token_urlsafe(12),
                "n": founder_name,
                "email": founder_email or None,
                "li": li,
                "c": company,
            },
        )
    except IntegrityError:
        # email already belongs to another lead — leave that one as the truth.
        pass


def platform_upsert_lead(payload: dict) -> dict:
    """Idempotently upsert a verified lead keyed on `domain`.

    `payload` is the agent's PlatformUpsertRequest shape (snake_case). Returns
    ``{"lead_id": str, "created": bool}``. ON CONFLICT (domain) updates in
    place — never creates a duplicate. `founderName` also seeds `name` when the
    row is new, since `name` is NOT NULL on the table.

    Side effect: when a founder is present, also maintains a separate person-lead
    (domain NULL, source 'agent-founder') so the founder appears in the Outreach
    tab. See :func:`_upsert_founder_person_lead`.
    """
    domain = (payload.get("domain") or "").strip().lower()
    if not domain:
        raise ValueError("domain is required for a domain-keyed upsert")

    company_name = payload.get("company_name")
    founder_name = payload.get("founder_name")
    # `name` is NOT NULL; fall back to founder, then company, then the domain.
    name = founder_name or company_name or domain
    sources = payload.get("sources") or []

    params = {
        "id": secrets.token_urlsafe(12),
        "name": name,
        "email": payload.get("founder_email"),
        "linkedinUrl": payload.get("founder_linkedin_url"),
        "domain": domain,
        "companyName": company_name,
        "fundingStage": payload.get("funding_stage"),
        "fundingAmount": payload.get("funding_amount"),
        "founderName": founder_name,
        "employeeCount": payload.get("employee_count"),
        "revenue": payload.get("revenue"),
        "location": payload.get("location"),
        "industry": payload.get("industry"),
        "lastRoundDate": payload.get("last_round_date"),
        "confidence": payload.get("confidence"),
        "source": payload.get("source") or "agent-server",
        "sourcesJson": json.dumps(sources),
    }

    # The conflict target must repeat the partial index predicate. On update we
    # refresh the agent-sourced columns and bump updatedAt, but we do NOT clobber
    # an existing human-edited `name`/`email` with nulls — COALESCE keeps the old
    # value when the incoming one is null.
    sql = text(
        """
        INSERT INTO "Lead" (
            "id", "name", "email", "linkedinUrl", "domain", "companyName",
            "fundingStage", "fundingAmount", "founderName", "employeeCount",
            "revenue", "location", "industry", "lastRoundDate", "confidence",
            "source", "sourcesJson", "updatedAt"
        ) VALUES (
            :id, :name, :email, :linkedinUrl, :domain, :companyName,
            :fundingStage, :fundingAmount, :founderName, :employeeCount,
            :revenue, :location, :industry, :lastRoundDate, :confidence,
            :source, CAST(:sourcesJson AS jsonb), CURRENT_TIMESTAMP
        )
        ON CONFLICT ("domain") WHERE "domain" IS NOT NULL
        DO UPDATE SET
            "email"         = COALESCE(EXCLUDED."email", "Lead"."email"),
            "linkedinUrl"   = COALESCE(EXCLUDED."linkedinUrl", "Lead"."linkedinUrl"),
            "companyName"   = COALESCE(EXCLUDED."companyName", "Lead"."companyName"),
            "fundingStage"  = COALESCE(EXCLUDED."fundingStage", "Lead"."fundingStage"),
            "fundingAmount" = COALESCE(EXCLUDED."fundingAmount", "Lead"."fundingAmount"),
            "founderName"   = COALESCE(EXCLUDED."founderName", "Lead"."founderName"),
            "employeeCount" = COALESCE(EXCLUDED."employeeCount", "Lead"."employeeCount"),
            "revenue"       = COALESCE(EXCLUDED."revenue", "Lead"."revenue"),
            "location"      = COALESCE(EXCLUDED."location", "Lead"."location"),
            "industry"      = COALESCE(EXCLUDED."industry", "Lead"."industry"),
            "lastRoundDate" = COALESCE(EXCLUDED."lastRoundDate", "Lead"."lastRoundDate"),
            "confidence"    = EXCLUDED."confidence",
            "source"        = EXCLUDED."source",
            "sourcesJson"   = EXCLUDED."sourcesJson",
            "updatedAt"     = CURRENT_TIMESTAMP
        RETURNING "id", (xmax = 0) AS created
        """
    )
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
        # Same transaction: surface the founder as an Outreach person-lead.
        _upsert_founder_person_lead(
            conn,
            founder_name=founder_name,
            founder_email=payload.get("founder_email"),
            founder_linkedin_url=payload.get("founder_linkedin_url"),
            company_name=company_name,
        )
    return {"lead_id": row.id, "created": bool(row.created)}
