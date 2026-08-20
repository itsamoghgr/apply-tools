# Job Board — Backend Structure & DB Design

> Companion to [`job_board_design.md`](job_board_design.md), which covers the
> product shape and scraping strategy. This document is the build-level spec:
> exact files, exact DDL, exact function signatures, exact endpoints.
>
> Every idiom here is lifted from existing code rather than invented — id
> generation, column allowlists, JSONB casting, the `ON CONFLICT` upsert, and the
> `x-agent-token` auth all mirror `backend/db.py` / `backend/server.py` as they
> stand today.

---

## 1. Process & ownership map

Nothing new is deployed. The Job Board slots into the two services that already run.

```
┌─ backend/  (FastAPI :8001) ──────────── owns apply_tools ─────────────┐
│  db.py       + WatchedCompany / JobPosting / AlertRun row access     │
│  server.py   + /api/v1/jobboard/* endpoints (agent-facing, tokened)   │
└───────────────────────────▲───────────────────────────────────────────┘
                            │  HTTP only (never a direct DB connection)
┌─ agent_server/  (FastAPI :8002) ─────── owns apply_agent ─────────────┐
│  jobboard/   NEW package: adapters, matcher, monitor, alert, mailer  │
│  api/jobboard.py   manual triggers + run history                      │
│  scheduler   APScheduler: 3h monitor, 6h alert                       │
└───────────────────────────────────────────────────────────────────────┘
┌─ frontend/  (Next.js :3001) ─────────────────────────────────────────┐
│  Prisma direct → apply_tools  (watchlist CRUD, feed rendering)        │
│  /api/agent/[...path] proxy → :8002  (manual triggers only)           │
└───────────────────────────────────────────────────────────────────────┘
```

**The load-bearing rule:** the agent service never opens a connection to
`apply_tools`. It reads and writes platform data strictly over HTTP through
`:8001`, exactly as the lead-gen delivery stage already does. That is what keeps
the two databases from silently coupling.

**Why the split falls this way.** Watchlist and postings are product data you'll
browse, edit, and turn into applications — so they live in `apply_tools` where
Prisma and the UI can reach them directly. Run bookkeeping is operational exhaust —
so it lives in `apply_agent`, matching CONTRACTS.md §0's rule that the agent DB is
never a second permanent copy of clean data.

---

## 2. New files

```
agent_server/jobboard/
├── __init__.py
├── adapters/
│   ├── __init__.py          detect(career_url) -> (ats, slug); ADAPTERS registry
│   ├── base.py              RawPosting dataclass; shared HTTP client + helpers
│   ├── greenhouse.py        boards-api.greenhouse.io  → first_published
│   ├── lever.py             api.lever.co             → createdAt (epoch ms)
│   ├── ashby.py             api.ashbyhq.com          → publishedAt
│   ├── smartrecruiters.py   api.smartrecruiters.com  → releasedDate
│   └── generic.py           fetch_page + ≤10-page walk + bounded LLM extract
├── matcher.py               normalize_title, role_match, dedup_key
├── monitor.py               run_monitor_cycle()
├── alert.py                send_alert()
├── mailer.py                send_mail() — smtplib, ~60 lines
├── templates.py             render_alert_html() / render_alert_text()
├── platform_client.py       HTTP client → :8001 /api/v1/jobboard/*
└── db.py                    apply_agent: jobboard_runs bookkeeping

agent_server/api/jobboard.py         router: triggers + run history
agent_server/migrations/0003_jobboard.sql
```

**Modified:** `agent_server/config.py` (new knobs), `agent_server/api/app.py`
(mount router + scheduler lifespan), `agent_server/pyproject.toml` (`apscheduler`),
`backend/db.py` (+~6 functions), `backend/server.py` (+6 endpoints),
`frontend/prisma/schema.prisma` (3 models).

---

## 3. Database design — platform (`apply_tools`)

### 3.1 Entity relationships

```
WatchedCompany 1───┐
   (what you       │  cascade delete
    monitor)       ▼
              JobPosting ──────► JobApplication   (nullable; set on "Track")
                   │  N───1
                   └──────────► AlertRun         (nullable; stamped when reported)
```

`JobPosting` is the join point between the monitor and the rest of the app: it
points *back* at the company being watched, *forward* at a tracked application once
you act on it, and *sideways* at the alert that reported it.

### 3.2 DDL

Applied as **targeted SQL**, then mirrored into `schema.prisma` and followed by
`prisma generate`. Never `prisma migrate dev` and never `db push --accept-data-loss`
— the live DB has drifted from migration history, so those commands are unsafe here.
The file lives at `frontend/prisma/sql/jobboard_migration.sql`, matching how
`platform_migration.sql` and `deep_research_migration.sql` were handled.

```sql
-- jobboard_migration.sql — additive only. No DROP, no data deletion.

CREATE TABLE IF NOT EXISTS "WatchedCompany" (
    "id"            text        PRIMARY KEY,
    "name"          text        NOT NULL,
    "careerUrl"     text        NOT NULL,
    "domain"        text,
    "logoUrl"       text,
    "ats"           text,          -- greenhouse|lever|ashby|smartrecruiters|generic
    "atsSlug"       text,
    "roleFilter"    jsonb,         -- NULL = inherit the global matcher set
    "active"        boolean     NOT NULL DEFAULT true,
    "seededAt"      timestamp(3),  -- first-cycle backlog seed completed
    "lastCheckedAt" timestamp(3),
    "lastStatus"    text,          -- ok | error
    "lastError"     text,
    "createdAt"     timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"     timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- One row per career page. Makes the add-company path an idempotent upsert.
CREATE UNIQUE INDEX IF NOT EXISTS "WatchedCompany_careerUrl_key"
    ON "WatchedCompany" ("careerUrl");
CREATE INDEX IF NOT EXISTS "WatchedCompany_active_idx"
    ON "WatchedCompany" ("active");

CREATE TABLE IF NOT EXISTS "JobPosting" (
    "id"               text        PRIMARY KEY,
    "watchedCompanyId" text        NOT NULL
        REFERENCES "WatchedCompany"("id") ON DELETE CASCADE,
    "dedupKey"         text        NOT NULL,
    "externalId"       text,
    "title"            text        NOT NULL,
    "matchedRole"      text        NOT NULL,
    "location"         text,
    "url"              text        NOT NULL,
    "postedAt"         timestamp(3),   -- source-provided; NULL when unknown
    "firstSeenAt"      timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "isNew"            boolean     NOT NULL DEFAULT true,
    "alertRunId"      text,           -- stamped once reported
    "archivedAt"       timestamp(3),   -- vanished from the board
    "jobApplicationId" text
        REFERENCES "JobApplication"("id") ON DELETE SET NULL,
    "createdAt"        timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt"        timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- THE central invariant: one row per (company, posting). This unique index is
-- what makes the monitor idempotent — re-running a cycle can never duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS "JobPosting_company_dedup_key"
    ON "JobPosting" ("watchedCompanyId", "dedupKey");

-- Alert query: unreported new postings since a watermark.
CREATE INDEX IF NOT EXISTS "JobPosting_unalerted_idx"
    ON "JobPosting" ("firstSeenAt") WHERE "alertRunId" IS NULL AND "isNew";
CREATE INDEX IF NOT EXISTS "JobPosting_alertRunId_idx"
    ON "JobPosting" ("alertRunId");
-- Feed query: live postings for a company.
CREATE INDEX IF NOT EXISTS "JobPosting_company_live_idx"
    ON "JobPosting" ("watchedCompanyId", "firstSeenAt" DESC)
    WHERE "archivedAt" IS NULL;

CREATE TABLE IF NOT EXISTS "AlertRun" (
    "id"           text        PRIMARY KEY,
    "startedAt"    timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "finishedAt"   timestamp(3),
    "windowStart"  timestamp(3) NOT NULL,
    "windowEnd"    timestamp(3) NOT NULL,
    "newCount"     integer     NOT NULL DEFAULT 0,
    "companyCount" integer     NOT NULL DEFAULT 0,
    "status"       text        NOT NULL DEFAULT 'pending',  -- pending|sent|skipped|failed
    "error"        text
);

CREATE INDEX IF NOT EXISTS "AlertRun_startedAt_idx" ON "AlertRun" ("startedAt" DESC);
-- Watermark lookup: the last alert that actually mailed.
CREATE INDEX IF NOT EXISTS "AlertRun_sent_idx"
    ON "AlertRun" ("finishedAt" DESC) WHERE "status" = 'sent';
```

### 3.3 Design notes on the schema

**The partial unique index is the whole correctness story.** `("watchedCompanyId",
"dedupKey")` means the monitor can be run twice, crash halfway, or be triggered
manually mid-cycle without ever producing a duplicate posting. Every write is an
`ON CONFLICT DO UPDATE`, so re-running is free.

**`isNew` vs `alertRunId` are different questions.** `isNew=false` means "existed
before we started watching" (the seed backlog). `alertRunId IS NULL` means "not yet
reported by email". A posting must satisfy both to enter an alert. Keeping them
separate is what makes seeding safe: seeded rows are permanently excluded from
alerts without needing a fake `alertRunId`.

**Nothing is ever deleted.** `archivedAt` marks a posting that disappeared from the
board; the row stays. This satisfies the project's absolute no-deletion rule and
preserves the record of roles that were once open.

**`jobApplicationId` uses `ON DELETE SET NULL`**, matching how `Resume` is
referenced from `JobApplication`. Deleting a tracked application returns the posting
to untracked rather than cascading a delete into the Job Board.

**Timestamps are `timestamp(3)`** to match every other Prisma-owned table, and are
written from timezone-aware UTC datetimes. Not negotiable: the SQLite→Postgres
migration previously shifted `appliedDate` and broke dashboard date grouping.

### 3.4 Prisma mirror

Added to `schema.prisma` with a comment marking them as out-of-band, in the same
style as the existing `Lead` agent-field comments:

```prisma
// ── Job Board ─────────────────────────────────────────────────────────────────
// Career-page monitor. Rows are written by the agent service (agent_server/,
// port 8002) over HTTP via backend /api/v1/jobboard/*; the UI reads them
// directly through Prisma. Schema applied OUT-OF-BAND via
// frontend/prisma/sql/jobboard_migration.sql (NOT via prisma migrate).
model WatchedCompany {
  id            String    @id
  name          String
  careerUrl     String    @unique
  domain        String?
  logoUrl       String?
  ats           String?
  atsSlug       String?
  roleFilter    Json?
  active        Boolean   @default(true)
  seededAt      DateTime?
  lastCheckedAt DateTime?
  lastStatus    String?
  lastError     String?
  createdAt     DateTime  @default(now())
  updatedAt     DateTime  @updatedAt
  postings      JobPosting[]

  @@index([active])
}

model JobPosting {
  id               String    @id
  watchedCompanyId String
  watchedCompany   WatchedCompany @relation(fields: [watchedCompanyId], references: [id], onDelete: Cascade)
  dedupKey         String
  externalId       String?
  title            String
  matchedRole      String
  location         String?
  url              String
  postedAt         DateTime?
  firstSeenAt      DateTime  @default(now())
  isNew            Boolean   @default(true)
  alertRunId      String?
  archivedAt       DateTime?
  jobApplicationId String?
  jobApplication   JobApplication? @relation(fields: [jobApplicationId], references: [id], onDelete: SetNull)
  createdAt        DateTime  @default(now())
  updatedAt        DateTime  @updatedAt

  @@unique([watchedCompanyId, dedupKey])
  @@index([alertRunId])
  // NOTE: the partial indexes (unalerted, company-live) are DB-level only —
  // Prisma can't express `WHERE`, so they're not redeclared here.
}

model AlertRun {
  id           String    @id
  startedAt    DateTime  @default(now())
  finishedAt   DateTime?
  windowStart  DateTime
  windowEnd    DateTime
  newCount     Int       @default(0)
  companyCount Int       @default(0)
  status       String    @default("pending")
  error        String?

  @@index([startedAt])
}
```

`JobApplication` gains one back-relation line: `jobPostings JobPosting[]`.

---

## 4. Database design — agent (`apply_agent`)

`agent_server/migrations/0003_jobboard.sql`, applied by the existing runner
(`python -m agent_server.migrations.run`), which tracks applied files in
`_migrations` and runs each in one transaction.

```sql
-- 0003_jobboard.sql — Job Board operational bookkeeping.
-- Mirrors the `jobs` table's shape/conventions: text PK, timestamptz, CHECKed status.

CREATE TABLE IF NOT EXISTS jobboard_runs (
    id                text        PRIMARY KEY,
    kind              text        NOT NULL CHECK (kind IN ('monitor','alert')),
    status            text        NOT NULL DEFAULT 'running'
                                  CHECK (status IN ('running','succeeded','failed')),
    trigger           text        NOT NULL DEFAULT 'schedule'
                                  CHECK (trigger IN ('schedule','manual')),
    companies_total   int         NOT NULL DEFAULT 0,
    companies_ok      int         NOT NULL DEFAULT 0,
    companies_failed  int         NOT NULL DEFAULT 0,
    postings_seen     int         NOT NULL DEFAULT 0,   -- returned by sources
    postings_matched  int         NOT NULL DEFAULT 0,   -- passed the role filter
    postings_new      int         NOT NULL DEFAULT 0,   -- actually inserted
    alert_run_id     text,                              -- platform AlertRun.id
    error             text,
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz
);

CREATE INDEX IF NOT EXISTS jobboard_runs_kind_idx
    ON jobboard_runs (kind, started_at DESC);

-- Per-company outcome within a run. Makes "which board broke, and why" answerable
-- without grepping logs, and powers the red status badge in the UI.
CREATE TABLE IF NOT EXISTS jobboard_run_companies (
    id                 bigserial   PRIMARY KEY,
    run_id             text        NOT NULL REFERENCES jobboard_runs (id) ON DELETE CASCADE,
    watched_company_id text        NOT NULL,   -- platform id; no FK across DBs
    company_name       text,
    ats                text,
    ok                 boolean     NOT NULL,
    postings_seen      int         NOT NULL DEFAULT 0,
    postings_matched   int         NOT NULL DEFAULT 0,
    postings_new       int         NOT NULL DEFAULT 0,
    pages_fetched      int         NOT NULL DEFAULT 1,   -- >1 only on the generic walk
    used_llm           boolean     NOT NULL DEFAULT false,
    error              text,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS jobboard_run_companies_run_idx
    ON jobboard_run_companies (run_id);
```

`used_llm` and `pages_fetched` exist so you can see at a glance which companies are
costing LLM calls — i.e. which ones should be repointed at an ATS URL.

---

## 5. `backend/db.py` additions

Appended as a new section, following the file's existing section-comment style and
its column-allowlist pattern. Ids via `secrets.token_urlsafe(12)`, exactly as
`insert_job_application` and `platform_upsert_lead` do.

```python
# -----------------------------------------------------------------------------
# Job Board (career-page monitor).
#
# Written by the agent service (agent_server/, port 8002) over HTTP via the
# /api/v1/jobboard/* endpoints; read directly by the Next.js UI through Prisma.
# Postings are keyed (watchedCompanyId, dedupKey) with a UNIQUE index, so every
# write here is an idempotent upsert — re-running a monitor cycle is free.
# -----------------------------------------------------------------------------

WATCHED_COMPANY_COLUMNS = (
    "name", "careerUrl", "domain", "logoUrl", "ats", "atsSlug",
    "active", "seededAt", "lastCheckedAt", "lastStatus", "lastError",
)
WATCHED_COMPANY_JSON_COLUMNS = ("roleFilter",)


def list_watched_companies(active_only: bool = True) -> list[dict]:
    """Return watched companies, newest first. The monitor's work list."""


def upsert_watched_company(fields: dict) -> dict:
    """Insert or update keyed on careerUrl. Returns {"id", "created"}.

    ON CONFLICT ("careerUrl") DO UPDATE — adding the same career page twice
    edits the existing row instead of erroring, mirroring platform_upsert_lead.
    """


def update_watched_company(company_id: str, fields: dict) -> bool:
    """Patch a WatchedCompany. Used by the monitor to stamp lastCheckedAt /
    lastStatus / lastError / seededAt after each company."""


def upsert_job_postings(company_id: str, postings: list[dict]) -> dict:
    """Bulk-upsert postings for one company in ONE transaction.

    Returns {"inserted": int, "updated": int, "new_ids": [...]}.

    ON CONFLICT ("watchedCompanyId", "dedupKey") DO UPDATE refreshes title /
    location / url / postedAt and clears archivedAt (a role that reappeared is
    live again) — but NEVER touches isNew, firstSeenAt, or alertRunId, so a
    re-listed posting can't re-enter an alert.

    `inserted` is computed from the `xmax = 0` trick so the caller learns which
    rows were genuinely new without a second query.
    """


def archive_missing_postings(company_id: str, live_dedup_keys: list[str]) -> int:
    """Mark postings absent from the latest fetch as archived. Never deletes.

    Sets archivedAt = now() WHERE archivedAt IS NULL AND dedupKey <> ALL(:keys).
    Returns the count archived. An empty key list is treated as a no-op, so a
    failed fetch can never archive an entire board.
    """


def list_unalerted_postings(window_start: datetime | None) -> list[dict]:
    """Postings eligible for the next alert, joined to their company.

    WHERE "isNew" AND "alertRunId" IS NULL AND "archivedAt" IS NULL
      AND ("firstSeenAt" > :window_start OR :window_start IS NULL)
    Ordered by company name, then firstSeenAt DESC. Includes company name,
    domain, and logoUrl so the mailer needs no second query.
    """


def create_alert_run(window_start: datetime, window_end: datetime) -> str:
    """Open a AlertRun row (status 'pending'). Returns its id."""


def close_alert_run(run_id: str, *, status: str, new_count: int,
                     company_count: int, posting_ids: list[str],
                     error: str | None = None) -> None:
    """Close a AlertRun and stamp alertRunId on the reported postings —
    in ONE transaction, so a crash can never mark a run sent while leaving
    postings unstamped (which would double-report them next cycle)."""


def last_sent_alert_at() -> datetime | None:
    """finishedAt of the most recent status='sent' AlertRun — the watermark.
    None on first ever run, which makes the first alert unbounded-backwards
    (but seeded rows are isNew=false, so it still won't dump a backlog)."""
```

**The transactional pair in `close_alert_run` is the subtle one.** Marking the run
`sent` and stamping `alertRunId` on its postings must be atomic. Split them, and a
crash between the two leaves postings unstamped — they'd be reported again in the
next alert. Both statements run on one `get_conn()` connection, which the existing
context manager already wraps in a transaction.

---

## 6. `backend/server.py` endpoints

Agent-facing, so they sit under `/api/v1/` and reuse `_require_agent_token`, which
enforces the shared secret only when `PLATFORM_API_TOKEN` is set (unauthenticated
local dev keeps working unchanged).

| Method | Path | Body → Response |
| --- | --- | --- |
| `GET` | `/api/v1/jobboard/companies?active=true` | → `{companies: [...]}` |
| `POST` | `/api/v1/jobboard/companies/upsert` | `WatchedCompanyUpsert` → `{ok, id, created}` |
| `PATCH` | `/api/v1/jobboard/companies/{id}` | status/seed patch → `{ok}` |
| `POST` | `/api/v1/jobboard/postings/upsert` | `{company_id, postings[]}` → `{ok, inserted, updated, new_ids}` |
| `POST` | `/api/v1/jobboard/postings/archive` | `{company_id, live_dedup_keys[]}` → `{ok, archived}` |
| `GET` | `/api/v1/jobboard/postings/unalerted?since=` | → `{postings: [...]}` |
| `POST` | `/api/v1/jobboard/alert-runs` | `{window_start, window_end}` → `{ok, id}` |
| `POST` | `/api/v1/jobboard/alert-runs/{id}/close` | `{status, new_count, company_count, posting_ids[], error?}` → `{ok}` |

Pydantic models follow the file's existing `Field(..., max_length=N)` discipline:

```python
class JobPostingIn(BaseModel):
    dedup_key:   str = Field(..., min_length=1, max_length=200)
    external_id: str | None = Field(default=None, max_length=200)
    title:       str = Field(..., min_length=1, max_length=300)
    matched_role: str = Field(..., min_length=1, max_length=100)
    location:    str | None = Field(default=None, max_length=200)
    url:         str = Field(..., min_length=1, max_length=2000)
    posted_at:   datetime | None = None

class JobPostingsUpsertRequest(BaseModel):
    company_id: str = Field(..., min_length=1, max_length=64)
    postings: list[JobPostingIn] = Field(default_factory=list, max_length=500)
```

The `max_length=500` cap on the posting list bounds a single request — Palantir's
Lever board alone returns 309 postings, so the cap is real, not theoretical. The
monitor chunks larger boards.

Watchlist **CRUD from the UI does not use these endpoints.** The Next.js app writes
`WatchedCompany` directly through Prisma server actions, matching how
`/applications` and `/resumes` already work. These endpoints exist for the agent
service, which is forbidden from touching `apply_tools` directly.

---

## 7. `agent_server/jobboard/` internals

### 7.1 `adapters/base.py`

```python
@dataclass(frozen=True)
class RawPosting:
    external_id: str | None
    title: str
    location: str | None
    url: str
    posted_at: datetime | None   # UTC; None when the source doesn't say
    source: str                  # which adapter produced it

class AdapterError(Exception):
    """Raised on an unusable response. Caught per-company by the monitor."""
```

Every adapter is `fetch(slug: str) -> list[RawPosting]` — one signature, so the
monitor never branches on ATS type. `detect()` returns `(ats, slug)` from the career
URL and is called once at add-company time, persisted on the row, and re-run only
when the URL changes.

### 7.2 `matcher.py`

```python
def normalize_title(title: str) -> str:
    """Lowercase, collapse whitespace, strip req-ids/roman numerals/level
    suffixes so 'Senior AI Engineer II (REQ-4821)' and 'Senior AI Engineer'
    don't read as two different roles."""

def role_match(title: str, matchers: dict[str, list[str]]) -> str | None:
    """Return the target role a title matches, or None. Alias substring match
    against the normalized title; longest alias wins so 'forward deployed
    engineer' beats a bare 'engineer'."""

def dedup_key(posting: RawPosting) -> str:
    """external_id when the source gives one (authoritative and stable),
    else sha1(normalized_title | normalized_location | url_path)."""
```

Matchers are read from the platform `Setting` table (key `jobboard.roleMatchers`)
with the four target roles seeded as the default, so alias lists are editable
without a deploy. A `WatchedCompany.roleFilter` overrides per board.

### 7.3 `monitor.py`

```python
def run_monitor_cycle(*, trigger: str = "schedule") -> MonitorSummary:
    """Scrape every active watched company once. Never raises."""
```

Flow, per company, with a jittered sleep between:

1. `adapter.fetch(slug)` → `RawPosting[]`
2. Role filter → matched subset (`postings_seen` vs `postings_matched` recorded)
3. If `seededAt` is NULL → this is the **seed cycle**: upsert all matched with
   `isNew=false`, then set `seededAt`. Nothing counts as new.
4. Else → upsert with `isNew=true`; `new_ids` from the upsert tells us what's
   genuinely new.
5. `archive_missing_postings(company_id, live_keys)`
6. Patch `lastCheckedAt` / `lastStatus` / `lastError`
7. Record the per-company row in `jobboard_run_companies`

**Failure isolation** mirrors the lead-gen loop's per-candidate `try/except`: one
company's exception is caught, recorded on that company and in the run table, and
the loop continues. A dead career page never aborts a cycle.

**Day-scoping.** When `JOBBOARD_TODAY_ONLY` is on *and* the source provides
`posted_at`, only same-day postings are considered — evaluated in your **local**
timezone so the day boundary matches your expectation, not UTC's. When the source
gives no date, the DB dedup decides novelty and the day filter simply doesn't apply.

### 7.4 `alert.py`

```python
def send_alert(*, force: bool = False, trigger: str = "schedule") -> AlertResult:
    """Report everything found since the last successful alert. Never raises."""
```

1. `window_start = last_sent_alert_at()` (None on first run)
2. `create_alert_run(window_start, now)` → `run_id`
3. `list_unalerted_postings(window_start)`
4. Empty and not `force` → close `skipped`, no mail
5. Render HTML + text, `mailer.send_mail(...)`
6. Success → `close_alert_run(status='sent', posting_ids=[...])`
   Failure → `close_alert_run(status='failed', posting_ids=[])`

Step 6's failure branch is deliberate: **postings keep `alertRunId = NULL` on a
send failure**, so they roll into the next successful alert rather than being
silently lost. Combined with the watermark, a laptop asleep through two cycles
produces one catch-up alert containing everything, not a gap.

### 7.5 `mailer.py`

~60 lines. `smtplib.SMTP_SSL` + `MIMEMultipart("alternative")` with a plaintext
part and an HTML part. It deliberately does **not** restore `backend/mail.py`
(deleted in `8cef324`) — none of that module's Gmail IMAP inbox or open/click
tracking is wanted here. Missing SMTP config logs a clear warning and marks the run
failed without crashing the scheduler.

---

## 8. Config additions

`agent_server/config.py`, following its existing flat-dataclass style:

```python
    # ── Job Board ──────────────────────────────────────────────────────────
    jobboard_enabled: bool = os.environ.get("JOBBOARD_ENABLED", "true").lower() == "true"
    jobboard_monitor_interval_h: int = _int("JOBBOARD_MONITOR_INTERVAL_H", 3)
    jobboard_alert_interval_h: int = _int("JOBBOARD_ALERT_INTERVAL_H", 6)
    jobboard_max_pages: int = _int("JOBBOARD_MAX_PAGES", 10)
    jobboard_today_only: bool = os.environ.get("JOBBOARD_TODAY_ONLY", "true").lower() == "true"
    jobboard_alert_heartbeat: bool = os.environ.get("JOBBOARD_ALERT_HEARTBEAT", "false").lower() == "true"
    jobboard_company_sleep_min_s: float = float(os.environ.get("JOBBOARD_COMPANY_SLEEP_MIN_S", "1.0"))
    jobboard_company_sleep_max_s: float = float(os.environ.get("JOBBOARD_COMPANY_SLEEP_MAX_S", "3.0"))

    smtp_host: str = os.environ.get("JOBBOARD_SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = _int("JOBBOARD_SMTP_PORT", 465)
    smtp_user: str | None = os.environ.get("JOBBOARD_SMTP_USER") or None
    smtp_app_password: str | None = os.environ.get("JOBBOARD_SMTP_APP_PASSWORD") or None
    alert_to: str | None = os.environ.get("JOBBOARD_ALERT_TO") or None
```

---

## 9. Failure modes and what protects against each

| Failure | Protection |
| --- | --- |
| Monitor runs twice / crashes mid-cycle | `UNIQUE (watchedCompanyId, dedupKey)` + `ON CONFLICT DO UPDATE` — every write idempotent |
| One career page 500s or changes layout | Per-company `try/except`; run continues; red badge in UI |
| Fetch returns empty (site down) | Empty `live_dedup_keys` is a no-op — can't archive a whole board |
| Alert email fails to send | Postings keep `alertRunId = NULL`; roll into the next alert |
| Crash between "run sent" and "stamp postings" | Both in one transaction in `close_alert_run` |
| Adding a company floods the alert | Seed cycle writes `isNew=false` for the entire existing board |
| Laptop asleep for hours | APScheduler `coalesce=True` + watermark window → one catch-up alert |
| Board re-lists an old role | Upsert never touches `isNew` / `firstSeenAt` / `alertRunId` |
| Timezone drift on dates | All UTC on ingest, `timestamp(3)`, local-tz only for the "today" boundary |
| Runaway LLM cost | LLM only on the generic path, one bounded call per company per cycle; `used_llm` tracked per run |

---

## 10. Build order (backend slice)

1. `0003_jobboard.sql` + `jobboard_migration.sql` + Prisma mirror + `prisma generate`
2. `backend/db.py` functions, exercised directly from `python -c` against a scratch company
3. `backend/server.py` endpoints + Pydantic models, exercised with `curl`
4. `jobboard/adapters/` + `matcher.py`, unit-tested against checked-in JSON fixtures
5. `jobboard/monitor.py` + `jobboard/db.py` bookkeeping
6. `jobboard/mailer.py` + `alert.py` + `templates.py`
7. `api/jobboard.py` router + APScheduler lifespan

After step 5 the monitor populates real data and you can inspect it with SQL before
any email code exists. Steps 1–3 are pure plumbing with no agent dependency, so they
can be verified in isolation.

**Tests** (`tests/`, matching the existing layout): adapter parsing against
fixtures, and three monitor cases that carry the real risk — seeding marks nothing
new, an unchanged board on cycle two yields zero new, one added posting yields
exactly one new. Plus two alert cases: the watermark never double-reports, and a
failed send leaves postings unalerted.
