"""Import job tracker data from the Google-Sheets-exported HTML file (USA.html).

DEPRECATED (SQLite era): opens its own sqlite3 connection to
data/apply-tools.db and will NOT work now that the app runs on Postgres. The
data it produced has already been migrated. Kept for reference; port to
SQLAlchemy + Postgres before any reuse.

Usage:
    python import_html.py <path-to-html> [--dry-run]

Parses the HTML table, maps each row to the JobApplication schema columns,
creates resume stubs as needed, and inserts records into the SQLite database.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
import sys
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from bs4 import BeautifulSoup

# ── paths ──────────────────────────────────────────────────────────────────────
BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = (BACKEND_DIR / ".." / "data").resolve()
DB_PATH = DATA_DIR / "apply-tools.db"

# ── allowed values (mirror server.py) ──────────────────────────────────────────
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

# Google Sheets HTML dates come as "January 20, 2026" or "February 6, 2026"
DATE_FORMATS = [
    "%B %d, %Y",   # January 20, 2026
    "%b %d, %Y",   # Jan 20, 2026
    "%d %b %Y",    # 20 Jan 2026 (CSV format)
    "%m/%d/%Y",    # 1/20/2026
]


def get_conn():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB not found at {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def parse_date(s: str) -> str | None:
    """Try multiple date formats and return YYYY-MM-DD HH:MM:SS or None."""
    s = s.strip()
    if not s:
        return None
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    print(f"  WARNING: could not parse date '{s}', skipping", file=sys.stderr)
    return None


def slugify_resume_label(label: str) -> str:
    """'DS-1' → 'ds-1', 'Custom - Tesla-2' → 'custom---tesla-2'."""
    slug = label.strip().lower().replace(" ", "-")
    if not RESUME_ID_RE.match(slug):
        raise ValueError(
            f"Resume label {label!r} (slug={slug!r}) doesn't fit resume id regex"
        )
    return slug


def extract_text(td) -> str:
    """Get visible text from a <td>, stripping whitespace."""
    return (td.get_text(strip=True) or "").strip()


def extract_link(td) -> str:
    """Get the first href from a <td> (for career page / job role links)."""
    a = td.find("a")
    if a and a.get("href"):
        return a["href"].strip()
    return ""


def parse_html_table(html_path: Path) -> list[dict]:
    """Parse the Google Sheets HTML table into a list of row dicts."""
    with open(html_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    table = soup.find("table")
    if not table:
        raise RuntimeError("No <table> found in the HTML file")

    all_rows = table.find_all("tr")

    # Find the header row — it has <td> elements with class "s0" or text "Sl No."
    header_cells = None
    header_row_idx = None
    for idx, tr in enumerate(all_rows):
        tds = tr.find_all("td")
        texts = [extract_text(td) for td in tds]
        if any("Sl No" in t for t in texts) or any("Company Name" in t for t in texts):
            header_cells = texts
            header_row_idx = idx
            break

    if header_cells is None:
        raise RuntimeError("Could not find header row with 'Sl No.' in HTML table")

    print(f"Header found at row {header_row_idx}: {header_cells}")

    # Column name → index mapping
    col_idx = {}
    for i, name in enumerate(header_cells):
        col_idx[name.strip()] = i

    # Required columns
    required = ["Company Name"]
    for r in required:
        if r not in col_idx:
            # Try with trailing space
            found = False
            for k in col_idx:
                if k.strip() == r:
                    col_idx[r] = col_idx[k]
                    found = True
                    break
            if not found:
                raise RuntimeError(f"Required column '{r}' not found. Found: {list(col_idx.keys())}")

    def get_cell(tds, col_name):
        """Get the <td> element for a column by name."""
        idx = col_idx.get(col_name)
        if idx is None:
            # Try with trailing space
            for k in col_idx:
                if k.strip() == col_name.strip():
                    idx = col_idx[k]
                    break
        if idx is None or idx >= len(tds):
            return None
        return tds[idx]

    def get_text_val(tds, col_name):
        td = get_cell(tds, col_name)
        return extract_text(td) if td else ""

    def get_link_val(tds, col_name):
        td = get_cell(tds, col_name)
        return extract_link(td) if td else ""

    rows = []
    for tr in all_rows[header_row_idx + 1:]:
        # Skip freezebar rows (they have class "freezebar-cell")
        if tr.find("td", class_="freezebar-cell"):
            continue

        tds = tr.find_all("td")
        if len(tds) < 3:
            continue

        company = get_text_val(tds, "Company Name")
        if not company:
            continue

        # Job Role — get text (which is the visible title), the link is in the <a>
        job_role = get_text_val(tds, "Job Role")
        location = get_text_val(tds, "Location")

        interview_status = get_text_val(tds, "Interview Status")
        status = get_text_val(tds, "Status") or "Applied"

        applied_date = get_text_val(tds, "Applied Date")
        resume_label = get_text_val(tds, "Resume")

        # Career page — prefer the link href over the visible text
        career_page = get_link_val(tds, "Company Career Page") or get_text_val(tds, "Company Career Page")

        decision_date = get_text_val(tds, "Decision Date")
        decision_time = get_text_val(tds, "Decision Time")
        notes = get_text_val(tds, "Notes")
        hr_name = get_text_val(tds, "HR Name")

        # LinkedIn — prefer href
        hr_linkedin = get_link_val(tds, "Linkedin") or get_text_val(tds, "Linkedin")

        hr_email = get_text_val(tds, "HR Email")

        # Referral column has trailing space in header: "Referral "
        referral = ""
        referral_linkedin = ""
        for col_name_try in ["Referral", "Referral "]:
            if col_name_try in col_idx:
                referral = get_text_val(tds, col_name_try)
                break

        referral_linkedin = get_text_val(tds, "Referral LinkedIn") or get_link_val(tds, "Referral LinkedIn")

        rows.append({
            "companyName": company,
            "jobRole": job_role,
            "location": location,
            "interviewStatus": interview_status,
            "status": status,
            "appliedDate": applied_date,
            "resumeLabel": resume_label,
            "companyCareerPage": career_page,
            "decisionDate": decision_date,
            "decisionTime": decision_time,
            "notes": notes,
            "hrName": hr_name,
            "hrLinkedin": hr_linkedin,
            "hrEmail": hr_email,
            "referral": referral,
            "referralLinkedin": referral_linkedin,
        })

    return rows


def ensure_resume_stubs(conn, resume_labels: set[str], dry_run: bool):
    """Create Resume stubs for any labels not in the DB."""
    if not resume_labels:
        return [], []

    label_to_slug = {}
    for lbl in resume_labels:
        try:
            label_to_slug[lbl] = slugify_resume_label(lbl)
        except ValueError as e:
            print(f"  WARNING: skipping resume label: {e}", file=sys.stderr)

    if not label_to_slug:
        return [], []

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


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return 2

    html_path = Path(argv[1])
    dry_run = "--dry-run" in argv[2:]
    if not html_path.exists():
        print(f"HTML file not found: {html_path}", file=sys.stderr)
        return 1

    print(f"Parsing {html_path} ...")
    rows = parse_html_table(html_path)
    print(f"Extracted {len(rows)} data rows from HTML table")

    if not rows:
        print("No rows to import.", file=sys.stderr)
        return 1

    # ── validate ───────────────────────────────────────────────────────────────
    errors = 0
    resume_labels = set()

    for i, row in enumerate(rows, start=1):
        # Validate status
        if row["status"] not in ALLOWED_STATUSES:
            print(f"  ERROR row {i}: status '{row['status']}' not in {ALLOWED_STATUSES}", file=sys.stderr)
            errors += 1

        # Validate interview status (can be empty)
        if row["interviewStatus"] and row["interviewStatus"] not in ALLOWED_INTERVIEW_STATUSES:
            print(f"  ERROR row {i}: interviewStatus '{row['interviewStatus']}' not in {ALLOWED_INTERVIEW_STATUSES}", file=sys.stderr)
            errors += 1

        # Collect resume labels
        if row["resumeLabel"]:
            resume_labels.add(row["resumeLabel"])

    if errors > 0:
        print(f"\n{errors} validation error(s). Fix the data and retry.", file=sys.stderr)
        return 1

    print(f"Validation passed. {len(resume_labels)} unique resume labels found.")

    # ── resume stubs ───────────────────────────────────────────────────────────
    conn = get_conn()
    created, existing = ensure_resume_stubs(conn, resume_labels, dry_run)
    if created:
        print(f"{'Would create' if dry_run else 'Created'} {len(created)} resume stubs: {', '.join(created)}")
    if existing:
        print(f"{len(existing)} resume(s) already in DB: {', '.join(existing)}")
    conn.commit()

    if dry_run:
        print(f"\nDRY-RUN: would insert {len(rows)} JobApplication rows")
        conn.close()
        return 0

    # ── insert ─────────────────────────────────────────────────────────────────
    imported = 0
    for i, row in enumerate(rows, start=1):
        app_id = secrets.token_urlsafe(12)

        applied = parse_date(row["appliedDate"])
        if applied is None:
            applied = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        decision = parse_date(row["decisionDate"])

        resume_id = None
        if row["resumeLabel"]:
            try:
                resume_id = slugify_resume_label(row["resumeLabel"])
            except ValueError:
                pass

        # Convert empty strings to None
        def none_if_empty(v):
            return v.strip() if v and v.strip() else None

        values = {
            "id": app_id,
            "companyName": row["companyName"],
            "jobRole": none_if_empty(row["jobRole"]),
            "location": none_if_empty(row["location"]),
            "interviewStatus": none_if_empty(row["interviewStatus"]),
            "status": row["status"],
            "appliedDate": applied,
            "resumeId": resume_id,
            "companyCareerPage": none_if_empty(row["companyCareerPage"]),
            "decisionDate": decision,
            "decisionTime": none_if_empty(row["decisionTime"]),
            "notes": none_if_empty(row["notes"]),
            "hrName": none_if_empty(row["hrName"]),
            "hrLinkedin": none_if_empty(row["hrLinkedin"]),
            "hrEmail": none_if_empty(row["hrEmail"]),
            "referral": none_if_empty(row["referral"]),
            "referralLinkedin": none_if_empty(row["referralLinkedin"]),
        }

        cols = list(values.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_sql = ", ".join(f'"{c}"' for c in cols)
        vals = [values[c] for c in cols]

        try:
            conn.execute(
                f'INSERT INTO "JobApplication" ({col_sql}, "createdAt", "updatedAt") '
                f"VALUES ({placeholders}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                vals,
            )
            imported += 1
        except sqlite3.Error as e:
            print(f"  ERROR inserting row {i} ({row['companyName']}): {e}", file=sys.stderr)

        if imported % 100 == 0:
            print(f"  ... {imported} rows inserted")
            conn.commit()

    conn.commit()
    conn.close()

    print(f"\nDONE: imported {imported} / {len(rows)} rows into JobApplication")
    print(f"  New resume stubs: {len(created)}")
    print(f"  Reused existing resumes: {len(existing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
