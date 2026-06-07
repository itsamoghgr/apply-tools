"""One-shot import of the historical job-tracker CSV into the JobApplication table.

DEPRECATED (SQLite era): written against the old SQLite backend; uses raw
sqlite3-style SQL (``?`` placeholders, manual ``.commit()``, ``isActive = 1``)
that will NOT run now that db.py is on SQLAlchemy + Postgres. The data it
produced has already been migrated. Kept for reference; port its queries to
``sqlalchemy.text`` named binds before any reuse.

Usage:
    python import_csv.py <path-to-csv> [--dry-run]

The CSV header (Sl No., Company Name, Job Role, Location, Interview Status,
Status, Applied Date, Resume, Company Career Page, Decision Date, Decision Time,
Notes, HR Name, Linkedin, HR Email, Referral, Referral LinkedIn) maps 1:1 to
the JobApplication schema. Resume labels (DS-1, DA-2, ...) get auto-created as
empty Resume stubs so the FK resolves; you fill in real content later via
/resumes.
"""

from __future__ import annotations

import csv
import re
import sys
from datetime import datetime
from pathlib import Path

from db import JOB_APP_COLUMNS, get_conn, insert_job_application

# Mirrors backend/server.py — kept inline here to avoid importing FastAPI for
# a CLI script. Keep these in sync if server.py changes.
ALLOWED_STATUSES = (
    "Applied",
    "In-Progress",
    "Offer",
    "Rejected",
    "Withdrawn",
    "Ghosted",
)
ALLOWED_INTERVIEW_STATUSES = ("Assessment", "Interviewing", "Offer", "Rejected")

RESUME_ID_RE = re.compile(r"^[a-z0-9_-]+$")

DATE_FMT_IN = "%d %b %Y"  # "28 Apr 2026"
DATE_FMT_OUT = "%Y-%m-%d %H:%M:%S"


# CSV column → JobApplication field
COLUMN_MAP = {
    "Company Name": "companyName",
    "Job Role": "jobRole",
    "Location": "location",
    "Interview Status": "interviewStatus",
    "Status": "status",
    "Applied Date": "appliedDate",
    "Resume": "_resume_label",  # transformed below into resumeId
    "Company Career Page": "companyCareerPage",
    "Decision Date": "decisionDate",
    "Decision Time": "decisionTime",
    "Notes": "notes",
    "HR Name": "hrName",
    "Linkedin": "hrLinkedin",
    "HR Email": "hrEmail",
    # The CSV's header has a literal trailing space in "Referral " — handled
    # in get_csv_value() by trying both with and without the space.
    "Referral": "referral",
    "Referral LinkedIn": "referralLinkedin",
}


def get_csv_value(row: dict, csv_col: str) -> str:
    """Look up a CSV column tolerantly (handles trailing whitespace in header).

    The export from Google Sheets has 'Referral ' with a trailing space. Rather
    than hardcode that, try the exact key first, then the stripped variants.
    """
    if csv_col in row:
        return (row[csv_col] or "").strip()
    for k in row.keys():
        if k.strip() == csv_col.strip():
            return (row[k] or "").strip()
    return ""


def parse_date(s: str) -> str | None:
    """Parse '28 Apr 2026' → 'YYYY-MM-DD HH:MM:SS' (UTC midnight)."""
    if not s:
        return None
    try:
        dt = datetime.strptime(s, DATE_FMT_IN)
    except ValueError as e:
        raise ValueError(f"Bad date {s!r}: {e}")
    return dt.strftime(DATE_FMT_OUT)


def slugify_resume_label(label: str) -> str:
    """'DS-1' → 'ds-1'. Asserts the result matches RESUME_ID_RE."""
    slug = label.strip().lower().replace(" ", "-")
    if not RESUME_ID_RE.match(slug):
        raise ValueError(
            f"Resume label {label!r} (slug={slug!r}) doesn't fit the resume id "
            f"regex (^[a-z0-9_-]+$). Edit the CSV or rename the resume."
        )
    return slug


def ensure_resume_stubs(
    conn, resume_labels: set[str], dry_run: bool
) -> tuple[list[str], list[str]]:
    """Create empty Resume rows for any labels not already in the table.

    Returns (created_labels, skipped_existing_labels), both sorted.
    """
    if not resume_labels:
        return [], []

    label_to_slug = {lbl: slugify_resume_label(lbl) for lbl in resume_labels}

    existing = {
        row["id"]
        for row in conn.execute(
            "SELECT id FROM Resume WHERE id IN ("
            + ",".join("?" * len(label_to_slug))
            + ")",
            list(label_to_slug.values()),
        ).fetchall()
    }

    to_create = sorted(
        (lbl, slug) for lbl, slug in label_to_slug.items() if slug not in existing
    )
    already = sorted(lbl for lbl, slug in label_to_slug.items() if slug in existing)

    if not dry_run and to_create:
        conn.executemany(
            'INSERT INTO "Resume" (id, label, content, isActive, createdAt, updatedAt) '
            "VALUES (?, ?, '', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            [(slug, lbl) for lbl, slug in to_create],
        )

    return [lbl for lbl, _ in to_create], already


def row_to_fields(row: dict, line_no: int) -> dict | None:
    """Translate a CSV row into a dict ready for insert_job_application.

    Returns None if the row should be skipped (blank Company Name).
    Raises ValueError on validation failures (bad status, bad date) so the
    importer aborts loudly rather than silently dropping data.
    """
    company = get_csv_value(row, "Company Name")
    if not company:
        return None

    status = get_csv_value(row, "Status") or "Applied"
    if status not in ALLOWED_STATUSES:
        raise ValueError(
            f"line {line_no}: status {status!r} not in {ALLOWED_STATUSES}"
        )

    interview = get_csv_value(row, "Interview Status")
    if interview and interview not in ALLOWED_INTERVIEW_STATUSES:
        raise ValueError(
            f"line {line_no}: interviewStatus {interview!r} not in "
            f"{ALLOWED_INTERVIEW_STATUSES} (or empty)"
        )

    applied = parse_date(get_csv_value(row, "Applied Date"))
    if applied is None:
        # JobApplication.appliedDate is non-null with a default; mirror the
        # FastAPI track endpoint which fills with "now" when blank.
        applied = datetime.utcnow().strftime(DATE_FMT_OUT)

    decision = parse_date(get_csv_value(row, "Decision Date"))

    resume_label = get_csv_value(row, "Resume")
    resume_id = slugify_resume_label(resume_label) if resume_label else None

    fields = {
        "companyName": company,
        "jobRole": get_csv_value(row, "Job Role"),
        "location": get_csv_value(row, "Location"),
        "interviewStatus": interview,
        "status": status,
        "appliedDate": applied,
        "resumeId": resume_id,
        "companyCareerPage": get_csv_value(row, "Company Career Page"),
        "decisionDate": decision,
        "decisionTime": get_csv_value(row, "Decision Time"),
        "notes": get_csv_value(row, "Notes"),
        "hrName": get_csv_value(row, "HR Name"),
        "hrLinkedin": get_csv_value(row, "Linkedin"),
        "hrEmail": get_csv_value(row, "HR Email"),
        "referral": get_csv_value(row, "Referral"),
        "referralLinkedin": get_csv_value(row, "Referral LinkedIn"),
    }
    # Sanity: every field name we wrote must be a known JobApplication column.
    unknown = set(fields) - set(JOB_APP_COLUMNS)
    if unknown:
        raise RuntimeError(f"unknown JobApplication columns: {unknown}")
    return fields


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return 2

    csv_path = Path(argv[1])
    dry_run = "--dry-run" in argv[2:]
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        return 1

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"read {len(rows)} CSV rows from {csv_path}")

    # First pass: collect every unique resume label.
    resume_labels = {get_csv_value(r, "Resume") for r in rows}
    resume_labels.discard("")

    # First pass: validate every row and build the field dicts. Aborting
    # before any insert keeps the DB unchanged on bad data.
    parsed: list[dict] = []
    skipped = 0
    for i, row in enumerate(rows, start=2):  # line 1 is header
        try:
            fields = row_to_fields(row, line_no=i)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        if fields is None:
            skipped += 1
            continue
        parsed.append(fields)

    print(
        f"parsed: {len(parsed)} valid rows | {skipped} skipped (blank company)"
    )

    # Second pass: write to the DB (or dry-run preview).
    with get_conn() as conn:
        created, existing = ensure_resume_stubs(conn, resume_labels, dry_run)
        if created:
            print(
                f"{'would create' if dry_run else 'created'} "
                f"{len(created)} resume stubs: {', '.join(created)}"
            )
        if existing:
            print(
                f"{len(existing)} resume(s) already in DB, reused: "
                f"{', '.join(existing)}"
            )
        if dry_run:
            conn.commit()  # commits nothing since ensure_resume_stubs short-circuited
            print(f"DRY-RUN: would insert {len(parsed)} JobApplication rows")
            return 0
        conn.commit()

    if dry_run:
        return 0

    # Inserts happen one row at a time via the existing helper (which opens
    # its own connection). 622 inserts is small enough that we don't need
    # to batch.
    imported = 0
    for fields in parsed:
        insert_job_application(fields)
        imported += 1
        if imported % 100 == 0:
            print(f"  ... {imported} rows inserted")

    print(
        f"DONE: imported {imported} rows | "
        f"new resume stubs: {len(created)} | "
        f"reused existing resumes: {len(existing)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
