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
        # Job Board (WatchedCompany / JobPosting / DigestRun).
        "seededAt",
        "lastCheckedAt",
        "postedAt",
        "firstSeenAt",
        "archivedAt",
        "startedAt",
        "finishedAt",
        "windowStart",
        "windowEnd",
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
    "coverLetter",
)

# JSONB columns on JobApplication — written with an explicit ::jsonb cast and
# json.dumps'd, so a Python dict round-trips correctly. Kept separate from the
# plain-text JOB_APP_COLUMNS above.
JOB_APP_JSON_COLUMNS = ("coverLetterMeta",)


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

    # JSONB columns: serialize dicts; the value gets an explicit ::jsonb cast.
    json_cols = [c for c in JOB_APP_JSON_COLUMNS if c in fields]
    for col in json_cols:
        v = fields[col]
        cleaned[col] = None if v is None else json.dumps(v)

    cols = list(cleaned.keys())
    col_sql = ", ".join(f'"{c}"' for c in cols)
    bind_sql = ", ".join(
        f"CAST(:{c} AS jsonb)" if c in json_cols else f":{c}" for c in cols
    )

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

    # JSONB columns: serialize dicts and cast in the SET clause below.
    json_cols = [c for c in JOB_APP_JSON_COLUMNS if c in fields]
    for col in json_cols:
        v = fields[col]
        updates[col] = None if v is None else json.dumps(v)

    if not updates:
        return False

    def _assign(c: str) -> str:
        return f'"{c}" = CAST(:{c} AS jsonb)' if c in json_cols else f'"{c}" = :{c}'

    set_sql = (
        ", ".join(_assign(c) for c in updates)
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


# -----------------------------------------------------------------------------
# Lead CRUD. Leads are people attached to applications.
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
    "source",
)

# `source` is set at create time only (e.g. 'roster'); it is not user-patchable.
LEAD_PATCH_COLUMNS = tuple(c for c in LEAD_INSERT_COLUMNS if c != "source")


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
    """Return every Lead, newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            text('SELECT * FROM "Lead" ORDER BY "createdAt" DESC')
        ).fetchall()
        return _rows_to_dicts(rows)


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
# Setting key/value store.
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
# The agent server (agent_server/, port 8002) discovers + verifies startups and
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
        # Deep-research fields.
        "brief": payload.get("brief"),
        "foundingYear": payload.get("founding_year"),
        "totalRaised": payload.get("total_raised"),
        "investorsJson": json.dumps(payload.get("investors") or []),
        "competitorsJson": json.dumps(payload.get("competitors") or []),
        "keyPeopleJson": json.dumps(payload.get("key_people") or []),
        "fitScore": payload.get("fit_score"),
        "fitReason": payload.get("fit_reason"),
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
            "revenue", "location", "industry", "lastRoundDate",
            "brief", "foundingYear", "totalRaised", "investorsJson",
            "competitorsJson", "keyPeopleJson", "fitScore", "fitReason",
            "confidence", "source", "sourcesJson", "updatedAt"
        ) VALUES (
            :id, :name, :email, :linkedinUrl, :domain, :companyName,
            :fundingStage, :fundingAmount, :founderName, :employeeCount,
            :revenue, :location, :industry, :lastRoundDate,
            :brief, :foundingYear, :totalRaised, CAST(:investorsJson AS jsonb),
            CAST(:competitorsJson AS jsonb), CAST(:keyPeopleJson AS jsonb),
            :fitScore, :fitReason,
            :confidence, :source, CAST(:sourcesJson AS jsonb), CURRENT_TIMESTAMP
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
            "foundingYear"  = COALESCE(EXCLUDED."foundingYear", "Lead"."foundingYear"),
            "totalRaised"   = COALESCE(EXCLUDED."totalRaised", "Lead"."totalRaised"),
            "investorsJson"   = COALESCE(EXCLUDED."investorsJson", "Lead"."investorsJson"),
            "competitorsJson" = COALESCE(EXCLUDED."competitorsJson", "Lead"."competitorsJson"),
            "keyPeopleJson"   = COALESCE(EXCLUDED."keyPeopleJson", "Lead"."keyPeopleJson"),
            "brief"         = EXCLUDED."brief",
            "fitScore"      = EXCLUDED."fitScore",
            "fitReason"     = EXCLUDED."fitReason",
            "confidence"    = EXCLUDED."confidence",
            "source"        = EXCLUDED."source",
            "sourcesJson"   = EXCLUDED."sourcesJson",
            "updatedAt"     = CURRENT_TIMESTAMP
        RETURNING "id", (xmax = 0) AS created
        """
    )
    # Company upsert commits on its own. The founder person-lead is maintained
    # in a SEPARATE transaction below — critically, NOT in this one: the company
    # row carries email=founder_email, so inserting a founder person-lead with
    # the same email would hit the UNIQUE(email) constraint and, once a statement
    # errors in Postgres, the whole transaction aborts — silently rolling back
    # the company upsert too (it reported success via RETURNING but never
    # persisted). Isolating them keeps the company lead safe.
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    result = {"lead_id": row.id, "created": bool(row.created)}

    # Surface the founder as an Outreach person-lead. Best-effort, isolated: a
    # failure here must never undo the company lead. Skip the founder email when
    # it already lives on the company row (would collide on UNIQUE(email)).
    company_email = payload.get("founder_email")
    founder_email_for_person = None  # the company row already holds this address
    try:
        with get_conn() as conn2:
            _upsert_founder_person_lead(
                conn2,
                founder_name=founder_name,
                founder_email=founder_email_for_person,
                founder_linkedin_url=payload.get("founder_linkedin_url"),
                company_name=company_name,
            )
    except Exception as exc:
        logger.warning(
            "founder_person_lead_failed", domain=domain, error=str(exc)
        )
    return result


# -----------------------------------------------------------------------------
# Job Board (career-page monitor).
#
# Written by the agent service (agent_server/, port 8002) over HTTP via the
# /api/v1/jobboard/* endpoints; read directly by the Next.js UI through Prisma.
# Schema lives in frontend/prisma/sql/jobboard_migration.sql.
#
# The load-bearing invariant is the UNIQUE index on
# ("watchedCompanyId", "dedupKey"): every posting write is an ON CONFLICT DO
# UPDATE against it, so re-running a monitor cycle — or crashing halfway through
# one — can never duplicate a posting.
#
# Nothing here deletes: postings that vanish from a board are stamped archivedAt.
# -----------------------------------------------------------------------------

WATCHED_COMPANY_COLUMNS = (
    "name",
    "careerUrl",
    "domain",
    "logoUrl",
    "ats",
    "atsSlug",
    "active",
    "maxAgeDays",
    "seededAt",
    "lastCheckedAt",
    "lastStatus",
    "lastError",
)

# JSONB columns on WatchedCompany — written with an explicit ::jsonb cast and
# json.dumps'd, mirroring JOB_APP_JSON_COLUMNS above.
WATCHED_COMPANY_JSON_COLUMNS = ("roleFilter", "countryFilter")


def list_watched_companies(active_only: bool = True) -> list[dict]:
    """Return watched companies (the monitor's work list), newest first."""
    sql = 'SELECT * FROM "WatchedCompany"'
    if active_only:
        sql += ' WHERE "active" = true'
    sql += ' ORDER BY "createdAt" DESC'
    with get_conn() as conn:
        return _rows_to_dicts(conn.execute(text(sql)).fetchall())


def get_watched_company(company_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            text('SELECT * FROM "WatchedCompany" WHERE "id" = :id'),
            {"id": company_id},
        ).fetchone()
        return _row_to_dict(row) if row else None


def upsert_watched_company(fields: dict) -> dict:
    """Insert or update a watched company, keyed on careerUrl.

    Returns ``{"id": str, "created": bool}``. Adding the same career page twice
    edits the existing row rather than raising, mirroring platform_upsert_lead.
    On update, only the columns actually supplied are overwritten — COALESCE
    keeps the stored value when an incoming one is NULL, so a partial patch from
    the monitor can't blank out user-entered fields like `name` or `logoUrl`.
    """
    career_url = (fields.get("careerUrl") or "").strip()
    if not career_url:
        raise ValueError("careerUrl is required")
    if not (fields.get("name") or "").strip():
        raise ValueError("name is required")

    params: dict[str, Any] = {
        "id": secrets.token_urlsafe(12),
        "careerUrl": career_url,
    }
    for col in WATCHED_COMPANY_COLUMNS:
        if col == "careerUrl":
            continue
        v = fields.get(col)
        if isinstance(v, str):
            v = v.strip() or None
        params[col] = v
    for col in WATCHED_COMPANY_JSON_COLUMNS:
        v = fields.get(col)
        params[col] = None if v is None else json.dumps(v)

    # `active` is NOT NULL with a default — never let an omitted key write NULL.
    if params.get("active") is None:
        params["active"] = True

    sql = text(
        """
        INSERT INTO "WatchedCompany" (
            "id", "name", "careerUrl", "domain", "logoUrl", "ats", "atsSlug",
            "roleFilter", "countryFilter", "maxAgeDays", "active", "seededAt",
            "lastCheckedAt", "lastStatus", "lastError", "updatedAt"
        ) VALUES (
            :id, :name, :careerUrl, :domain, :logoUrl, :ats, :atsSlug,
            CAST(:roleFilter AS jsonb), CAST(:countryFilter AS jsonb), :maxAgeDays,
            :active, :seededAt, :lastCheckedAt, :lastStatus, :lastError,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT ("careerUrl") DO UPDATE SET
            "name"       = COALESCE(EXCLUDED."name",       "WatchedCompany"."name"),
            "domain"     = COALESCE(EXCLUDED."domain",     "WatchedCompany"."domain"),
            "logoUrl"    = COALESCE(EXCLUDED."logoUrl",    "WatchedCompany"."logoUrl"),
            "ats"        = COALESCE(EXCLUDED."ats",        "WatchedCompany"."ats"),
            "atsSlug"    = COALESCE(EXCLUDED."atsSlug",    "WatchedCompany"."atsSlug"),
            "roleFilter" = COALESCE(EXCLUDED."roleFilter", "WatchedCompany"."roleFilter"),
            "countryFilter" = COALESCE(EXCLUDED."countryFilter", "WatchedCompany"."countryFilter"),
            "maxAgeDays" = COALESCE(EXCLUDED."maxAgeDays", "WatchedCompany"."maxAgeDays"),
            "active"     = EXCLUDED."active",
            "updatedAt"  = CURRENT_TIMESTAMP
        RETURNING "id", (xmax = 0) AS created
        """
    )
    try:
        with get_conn() as conn:
            row = conn.execute(sql, params).fetchone()
    except IntegrityError as exc:
        raise _as_unique_violation(exc) from exc
    return {"id": row.id, "created": bool(row.created)}


def update_watched_company(company_id: str, fields: dict) -> bool:
    """Patch a WatchedCompany. Only known columns are written.

    Used by the monitor to stamp lastCheckedAt / lastStatus / lastError /
    seededAt after each company, and by the UI to toggle `active`. Returns True
    if a row was updated.

    Unlike upsert_watched_company, an explicit None here DOES clear the column —
    that is how lastError is reset after a company recovers.
    """
    updates: dict[str, Any] = {}
    for col in WATCHED_COMPANY_COLUMNS:
        if col not in fields:
            continue
        v = fields[col]
        if isinstance(v, str):
            v = v.strip() or None
        updates[col] = v

    json_cols = [c for c in WATCHED_COMPANY_JSON_COLUMNS if c in fields]
    for col in json_cols:
        v = fields[col]
        updates[col] = None if v is None else json.dumps(v)

    if not updates:
        return False

    def _assign(c: str) -> str:
        return f'"{c}" = CAST(:{c} AS jsonb)' if c in json_cols else f'"{c}" = :{c}'

    set_sql = ", ".join(_assign(c) for c in updates) + ', "updatedAt" = CURRENT_TIMESTAMP'
    params = dict(updates, _id=company_id)
    with get_conn() as conn:
        cur = conn.execute(
            text(f'UPDATE "WatchedCompany" SET {set_sql} WHERE "id" = :_id'), params
        )
        return cur.rowcount > 0


def upsert_job_postings(company_id: str, postings: list[dict]) -> dict:
    """Bulk-upsert postings for one company, in ONE transaction.

    Returns ``{"inserted": int, "updated": int, "new_ids": [...]}`` where
    `new_ids` are the ids of rows that did not previously exist — that is
    precisely the set of genuinely-new postings the digest should report.

    ON CONFLICT ("watchedCompanyId", "dedupKey") DO UPDATE refreshes the mutable
    display fields (title / location / url / postedAt / externalId / matchedRole)
    and clears archivedAt, since a role that reappeared on the board is live
    again.

    It deliberately NEVER touches "isNew", "firstSeenAt", or "digestRunId": a
    board re-listing an old role must not push it back into your inbox.

    `isNew` is per-posting and supplied by the caller — the monitor passes False
    for every row during a company's first (seed) cycle so the pre-existing
    backlog is recorded without ever being reported.

    Postgres's `xmax = 0` on the RETURNING row distinguishes a fresh INSERT from
    a conflict-UPDATE, so the caller learns what was new without a second query.
    """
    if not postings:
        return {"inserted": 0, "updated": 0, "new_ids": []}

    sql = text(
        """
        INSERT INTO "JobPosting" (
            "id", "watchedCompanyId", "dedupKey", "externalId", "title",
            "matchedRole", "location", "url", "postedAt", "minYears", "country",
            "isNew", "updatedAt"
        ) VALUES (
            :id, :watchedCompanyId, :dedupKey, :externalId, :title,
            :matchedRole, :location, :url, :postedAt, :minYears, :country,
            :isNew, CURRENT_TIMESTAMP
        )
        ON CONFLICT ("watchedCompanyId", "dedupKey") DO UPDATE SET
            "title"       = EXCLUDED."title",
            "matchedRole" = EXCLUDED."matchedRole",
            "location"    = COALESCE(EXCLUDED."location",   "JobPosting"."location"),
            "url"         = EXCLUDED."url",
            "postedAt"    = COALESCE(EXCLUDED."postedAt",   "JobPosting"."postedAt"),
            "minYears"    = COALESCE(EXCLUDED."minYears",   "JobPosting"."minYears"),
            "country"     = COALESCE(EXCLUDED."country",    "JobPosting"."country"),
            "externalId"  = COALESCE(EXCLUDED."externalId", "JobPosting"."externalId"),
            "archivedAt"  = NULL,
            "updatedAt"   = CURRENT_TIMESTAMP
        RETURNING "id", (xmax = 0) AS created
        """
    )

    inserted = 0
    updated = 0
    new_ids: list[str] = []

    # One connection => one transaction for the whole batch: a mid-batch failure
    # rolls the entire company's postings back rather than leaving them half
    # written. The next cycle re-upserts them harmlessly.
    with get_conn() as conn:
        for p in postings:
            dedup_key = (p.get("dedupKey") or "").strip()
            title = (p.get("title") or "").strip()
            url = (p.get("url") or "").strip()
            if not (dedup_key and title and url):
                raise ValueError("dedupKey, title and url are required on every posting")

            params = {
                "id": secrets.token_urlsafe(12),
                "watchedCompanyId": company_id,
                "dedupKey": dedup_key,
                "externalId": p.get("externalId"),
                "title": title,
                "matchedRole": (p.get("matchedRole") or "").strip() or "unknown",
                "location": p.get("location"),
                "url": url,
                "postedAt": p.get("postedAt"),
                "minYears": p.get("minYears"),
                "country": p.get("country"),
                "isNew": bool(p.get("isNew", True)),
            }
            row = conn.execute(sql, params).fetchone()
            if row.created:
                inserted += 1
                new_ids.append(row.id)
            else:
                updated += 1

    return {"inserted": inserted, "updated": updated, "new_ids": new_ids}


def archive_missing_postings(company_id: str, live_dedup_keys: list[str]) -> int:
    """Stamp archivedAt on postings absent from the latest fetch. Never deletes.

    Returns the number archived.

    An EMPTY key list is treated as a no-op rather than "archive everything".
    That guard matters: a career page that 500s or returns an empty body would
    otherwise wipe the whole board's live status in one cycle.
    """
    keys = [k for k in (live_dedup_keys or []) if isinstance(k, str) and k.strip()]
    if not keys:
        return 0
    with get_conn() as conn:
        cur = conn.execute(
            text(
                'UPDATE "JobPosting" SET "archivedAt" = CURRENT_TIMESTAMP, '
                '"updatedAt" = CURRENT_TIMESTAMP '
                'WHERE "watchedCompanyId" = :cid AND "archivedAt" IS NULL '
                'AND NOT ("dedupKey" = ANY(:keys))'
            ),
            {"cid": company_id, "keys": keys},
        )
        return cur.rowcount


def list_undigested_postings(window_start: datetime | None = None) -> list[dict]:
    """Postings eligible for the next digest, joined to their company.

    Eligibility is the conjunction of three independent conditions:
      isNew            -> not part of a company's seeded backlog
      digestRunId NULL -> not already reported by an earlier digest
      archivedAt NULL  -> still live on the board

    `window_start` is the watermark (finishedAt of the last SENT digest); NULL
    on the first-ever run, which makes that digest unbounded-backwards — safe,
    because seeded rows carry isNew = false.

    Company name / domain / logoUrl are joined in so the mailer renders without
    a second query.
    """
    sql = """
        SELECT p.*,
               c."name"    AS "companyName",
               c."domain"  AS "companyDomain",
               c."logoUrl" AS "companyLogoUrl"
        FROM "JobPosting" p
        JOIN "WatchedCompany" c ON c."id" = p."watchedCompanyId"
        WHERE p."isNew" AND p."digestRunId" IS NULL AND p."archivedAt" IS NULL
    """
    params: dict[str, Any] = {}
    if window_start is not None:
        sql += ' AND p."firstSeenAt" > :window_start'
        params["window_start"] = window_start
    sql += ' ORDER BY c."name" ASC, p."firstSeenAt" DESC'

    with get_conn() as conn:
        return _rows_to_dicts(conn.execute(text(sql), params).fetchall())


def create_digest_run(window_start: datetime | None, window_end: datetime) -> str:
    """Open a DigestRun row (status 'pending'). Returns its id."""
    run_id = secrets.token_urlsafe(12)
    with get_conn() as conn:
        conn.execute(
            text(
                'INSERT INTO "DigestRun" ("id", "windowStart", "windowEnd", "status") '
                "VALUES (:id, :ws, :we, 'pending')"
            ),
            {"id": run_id, "ws": window_start, "we": window_end},
        )
    return run_id


def close_digest_run(
    run_id: str,
    *,
    status: str,
    new_count: int = 0,
    company_count: int = 0,
    posting_ids: list[str] | None = None,
    error: str | None = None,
) -> None:
    """Close a DigestRun and stamp digestRunId on the postings it reported —
    both in ONE transaction.

    The atomicity is the point. If the run were marked 'sent' in one transaction
    and the postings stamped in another, a crash in between would leave those
    postings with digestRunId NULL while the watermark had already advanced past
    them: they would be silently reported a second time (or, with a different
    ordering, dropped entirely). One transaction makes that window impossible.

    Callers pass posting_ids ONLY for status='sent'. On 'failed' the ids are left
    unstamped on purpose, so the postings roll into the next successful digest
    instead of being lost to a transient SMTP error.
    """
    if status not in ("sent", "skipped", "failed"):
        raise ValueError(f"invalid digest status: {status}")

    ids = list(posting_ids or [])
    with get_conn() as conn:
        conn.execute(
            text(
                'UPDATE "DigestRun" SET "status" = :status, "newCount" = :new_count, '
                '"companyCount" = :company_count, "error" = :error, '
                '"finishedAt" = CURRENT_TIMESTAMP WHERE "id" = :id'
            ),
            {
                "id": run_id,
                "status": status,
                "new_count": new_count,
                "company_count": company_count,
                "error": error,
            },
        )
        if ids:
            conn.execute(
                text(
                    'UPDATE "JobPosting" SET "digestRunId" = :run_id, '
                    '"updatedAt" = CURRENT_TIMESTAMP '
                    'WHERE "id" = ANY(:ids) AND "digestRunId" IS NULL'
                ),
                {"run_id": run_id, "ids": ids},
            )


def last_sent_digest_at() -> datetime | None:
    """finishedAt of the most recent successfully-SENT digest — the watermark.

    Deliberately ignores 'skipped' and 'failed' runs: only a digest that
    actually reached your inbox may advance the window. Returns None before the
    first successful send.
    """
    with get_conn() as conn:
        row = conn.execute(
            text(
                'SELECT "finishedAt" FROM "DigestRun" '
                "WHERE \"status\" = 'sent' AND \"finishedAt\" IS NOT NULL "
                'ORDER BY "finishedAt" DESC LIMIT 1'
            )
        ).fetchone()
        return row.finishedAt if row else None


def link_posting_to_application(posting_id: str, job_application_id: str) -> bool:
    """Point a posting at the JobApplication created from it ("Track" action)."""
    with get_conn() as conn:
        cur = conn.execute(
            text(
                'UPDATE "JobPosting" SET "jobApplicationId" = :app_id, '
                '"updatedAt" = CURRENT_TIMESTAMP WHERE "id" = :id'
            ),
            {"id": posting_id, "app_id": job_application_id},
        )
        return cur.rowcount > 0


def archive_stale_postings(weeks: int) -> int:
    """Archive live postings older than `weeks`, by posted date.

    ARCHIVES, never deletes: the row stays and only leaves the live feed, so
    widening the retention window later brings the postings straight back. A
    posting with no posted date falls back to when we first saw it.

    Rows already linked to a JobApplication are exempt — you acted on those, and
    a retention sweep must not quietly retire something in your tracker.
    """
    if weeks < 1:
        return 0
    with get_conn() as conn:
        cur = conn.execute(
            text(
                'UPDATE "JobPosting" SET "archivedAt" = CURRENT_TIMESTAMP, '
                '"updatedAt" = CURRENT_TIMESTAMP '
                'WHERE "archivedAt" IS NULL '
                'AND "jobApplicationId" IS NULL '
                'AND COALESCE("postedAt", "firstSeenAt") < '
                "  CURRENT_TIMESTAMP - make_interval(weeks => :weeks)"
            ),
            {"weeks": weeks},
        )
        return cur.rowcount


def archive_over_experience(max_years: int) -> int:
    """Archive live postings that REQUIRE MORE than `max_years` of experience.

    ARCHIVES, never deletes — raising the threshold later brings them straight
    back, so this setting is safe to experiment with.

    Postings with NO stated requirement are KEPT. "Not stated" is not evidence
    of seniority: a third of postings simply don't publish a number, and many
    are junior-friendly. Only roles we KNOW exceed the threshold are removed.

    Rows already linked to a JobApplication are exempt, so a threshold change
    never retires something sitting in your tracker.
    """
    with get_conn() as conn:
        cur = conn.execute(
            text(
                'UPDATE "JobPosting" SET "archivedAt" = CURRENT_TIMESTAMP, '
                '"updatedAt" = CURRENT_TIMESTAMP '
                'WHERE "archivedAt" IS NULL '
                'AND "jobApplicationId" IS NULL '
                'AND "minYears" IS NOT NULL AND "minYears" > :max_years'
            ),
            {"max_years": max_years},
        )
        return cur.rowcount


def restore_within_experience(max_years: int) -> int:
    """Un-archive postings that fit a RAISED experience threshold.

    The counterpart that makes the setting reversible: only rows archived by an
    earlier, stricter threshold come back — anything retired by the retention
    window stays archived, because that is a separate (time-based) decision.
    """
    with get_conn() as conn:
        cur = conn.execute(
            text(
                'UPDATE "JobPosting" SET "archivedAt" = NULL, '
                '"updatedAt" = CURRENT_TIMESTAMP '
                'WHERE "archivedAt" IS NOT NULL '
                'AND "minYears" IS NOT NULL AND "minYears" <= :max_years '
                'AND COALESCE("postedAt", "firstSeenAt") > '
                "  CURRENT_TIMESTAMP - make_interval(weeks => :weeks)"
            ),
            {"max_years": max_years, "weeks": _retention_weeks_setting()},
        )
        return cur.rowcount


def _retention_weeks_setting() -> int:
    """Current retention window, so a restore can't resurrect stale postings."""
    raw = get_setting("jobboard.retentionWeeks")
    try:
        weeks = int(raw) if raw else 4
    except (TypeError, ValueError):
        weeks = 4
    return weeks if 1 <= weeks <= 52 else 4


def count_over_experience(max_years: int) -> int:
    """How many LIVE postings a threshold would archive — for a UI preview."""
    with get_conn() as conn:
        row = conn.execute(
            text(
                'SELECT count(*) AS n FROM "JobPosting" '
                'WHERE "archivedAt" IS NULL AND "jobApplicationId" IS NULL '
                'AND "minYears" IS NOT NULL AND "minYears" > :max_years'
            ),
            {"max_years": max_years},
        ).fetchone()
        return int(row.n)
