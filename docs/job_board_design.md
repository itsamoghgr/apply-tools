# Job Board — Implementation Design

> Status: **DESIGN — not yet implemented.** Written 2026-08-18.
>
> A watchlist-driven career-page monitor. You supply the companies and their
> career-page URLs; every 3 hours the monitor re-scrapes each board, keeps only
> postings matching your target roles, and stores the ones it has never seen
> before. Every 6 hours an alert email summarises what's new.

---

## 0. Scope

**In scope**

- A user-managed watchlist: company name + career-page URL (+ optional logo).
- A 3-hourly monitor cycle that scrapes each watched company and persists new,
  role-matching postings.
- A 6-hourly alert email: total new roles, then one block per role showing the
  company name and logo.
- A `/job-board` page to manage the watchlist and browse new postings, with a
  one-click push of a posting into the existing applications tracker.

**Explicitly out of scope**

- Company *discovery*. The existing lead-gen pipeline (`agent_server/orchestrator/`)
  hunts for companies; the Job Board does not. It only ever visits URLs you gave it.
- Auto-applying. The Job Board surfaces roles; applying stays manual.

**Target roles** (default set, configurable per-watchlist and globally):
Data Scientist · AI Engineer · Founding Engineer · Forward Deployed Engineer

---

## 1. Where this lives, and why

The monitor is **a second, independent pipeline inside `agent_server/`** — a new
`agent_server/jobboard/` package. It reuses that service's infrastructure
(`web/page_fetch.py`, `agents/llm.py`, `log.py`, `config.py`, the migration runner)
without touching the lead-gen orchestrator, whose `Stages` contract is frozen and
whose loop is shaped around discovery→research→verify→deliver — a poor fit for
"re-scrape a known URL on a timer."

Data is split the way the existing services already split it:

| Store | Holds | Why |
| --- | --- | --- |
| `apply_tools` (platform, Prisma) | `WatchedCompany`, `JobPosting`, `AlertRun` | Durable product data. Lets `/job-board` read it directly via Prisma and lets a posting become a `JobApplication` row. |
| `apply_agent` (agent, raw SQL) | `jobboard_runs`, per-run audit | Operational bookkeeping only — same rule as CONTRACTS.md §0: the agent DB is never a second permanent copy of clean data. |

Consistent with the platform-migration precedent, the new Prisma models are
applied to Postgres via **targeted SQL** and mirrored into `schema.prisma`, never
via `prisma migrate dev` (see the *Prisma migration drift* constraint — the live DB
has drifted from migration history, so `migrate dev` / `db push --accept-data-loss`
are off-limits).

---

## 2. Scraping: ATS-first, LLM fallback

### 2.1 The key finding

I probed the four major ATS platforms against live boards. **Every one returns the
company's entire job board in a single unauthenticated JSON call, including a real
posted-date.** Verified 2026-08-18:

| ATS | Endpoint | Posted-date field | Verified against |
| --- | --- | --- | --- |
| Greenhouse | `boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=false` | `first_published` (present on 85/85 jobs) | `vercel` — 85 jobs |
| Lever | `api.lever.co/v0/postings/<slug>?mode=json` | `createdAt` (epoch ms) | `palantir` — 309 jobs |
| Ashby | `api.ashbyhq.com/posting-api/job-board/<slug>` | `publishedAt` (ISO) | `linear` |
| SmartRecruiters | `api.smartrecruiters.com/v1/companies/<slug>/postings` | `releasedDate` (ISO) | `Visa` |

This collapses a large part of the problem. Your "scrape that day only, else walk
10 pages" rule exists to bound the work when a page won't tell you what's recent —
but on an ATS board there are no pages to walk and no filter to apply. One request
returns everything with exact timestamps, so the monitor can filter precisely and
cheaply, with no LLM in the loop.

The 10-page walk therefore becomes the **fallback path only**, for genuinely custom
career pages.

### 2.2 Adapter selection

`jobboard/adapters/` resolves the career URL you supply to an adapter:

```
detect(career_url) -> (adapter, slug)
  boards.greenhouse.io/<slug>       -> greenhouse
  job-boards.greenhouse.io/<slug>   -> greenhouse
  jobs.lever.co/<slug>              -> lever
  jobs.ashbyhq.com/<slug>           -> ashby
  careers.smartrecruiters.com/<slug>-> smartrecruiters
  <anything else>                   -> generic
```

Each adapter implements one function, returning a normalized shape:

```python
def fetch(slug_or_url: str) -> list[RawPosting]
# RawPosting: external_id, title, location, url, posted_at: datetime|None, source
```

Detection is stored on the `WatchedCompany` row at creation time (`ats`, `atsSlug`),
so it's visible and correctable in the UI rather than re-guessed every cycle. If a
company migrates ATS, the URL changes and detection re-runs.

### 2.3 The generic fallback

For a custom page, in order:

1. `fetch_page(url)` (existing, `agent_server/web/page_fetch.py`), retrying with
   `render_js=True` when the first pass yields almost no text — many bespoke boards
   are client-rendered.
2. **Date-filter probe.** If the page or its query string exposes a recency control
   (`?posted=today`, `sort=posted_date`, a visible "Last 24 hours" facet), use it and
   take only that day's postings. This is the cheap path when available.
3. **Otherwise, paginate up to 10 pages** (`JOBBOARD_MAX_PAGES`, default 10),
   following `?page=N` / next-link patterns, stopping early when a page yields no new
   links or repeats the previous page's content.
4. Extract postings from the collected text with **one bounded LLM call** per company
   per cycle (`agents/llm.py`, the same client the other agents use), returning strict
   JSON: `[{title, location, url, posted_at?}]`.

The LLM only ever runs on this fallback path. A watchlist of ATS-hosted companies
costs zero LLM calls per cycle.

### 2.4 What "new" means

Most custom pages publish no reliable posted-date, so **the DB is the source of
truth for novelty**, not the page:

> A posting is new iff no `JobPosting` row exists for that `(watchedCompanyId, dedupKey)`.

`dedupKey` = the ATS `external_id` when present, else `sha1(normalized_title | normalized_location | url_path)`. Normalization lowercases, collapses whitespace, and
strips req-id suffixes so a re-listed role doesn't read as new.

`postedAt` is stored when the source provides it and drives the "posted today"
display and the day-filter optimization — but it never gates insertion. This means
a board that silently backfills or omits dates still can't cause a missed role.

**First-cycle seeding.** The first time a company is monitored, its entire current
board is inserted with `isNew = false` and `seededAt` set. Without this, adding a
company to the watchlist would dump its whole back catalogue into your next alert.
Only postings appearing *after* the seed cycle count as new.

### 2.5 Role matching

Exact string equality would miss most real titles, so matching is alias-based
substring matching against a normalized title:

```
Data Scientist            : data scientist, applied scientist, research scientist,
                            ds intern? (excluded by seniority rules below)
AI Engineer               : ai engineer, ml engineer, machine learning engineer,
                            applied ai, applied ml, genai engineer, llm engineer
Founding Engineer         : founding engineer, founding software engineer,
                            member of technical staff
Forward Deployed Engineer : forward deployed, fde, solutions engineer (ai),
                            deployment engineer, forward-deployed
```

Stored as JSON on `Setting` (key `jobboard.roleMatchers`) so you can edit the alias
lists without a deploy, with the above as the seeded default. A per-company override
on `WatchedCompany.roleFilter` lets you narrow a specific board.

Non-matching postings are **not stored** — they're counted into
`jobboard_runs.filtered_count` for observability and dropped. This keeps
`JobPosting` a table of roles you actually care about.

---

## 3. Data model

### 3.1 Platform DB (`apply_tools`) — Prisma

```prisma
model WatchedCompany {
  id            String   @id
  name          String
  careerUrl     String
  domain        String?          // for logo resolution + display
  logoUrl       String?          // explicit override; else derived from domain
  ats           String?          // greenhouse|lever|ashby|smartrecruiters|generic
  atsSlug       String?
  roleFilter    Json?            // null = use the global matcher set
  active        Boolean  @default(true)
  seededAt      DateTime?        // first-cycle seed completed
  lastCheckedAt DateTime?
  lastStatus    String?          // ok | error
  lastError     String?
  createdAt     DateTime @default(now())
  updatedAt     DateTime @updatedAt
  postings      JobPosting[]

  @@unique([careerUrl])
  @@index([active])
}

model JobPosting {
  id               String   @id
  watchedCompanyId String
  watchedCompany   WatchedCompany @relation(fields: [watchedCompanyId], references: [id], onDelete: Cascade)
  dedupKey         String
  externalId       String?
  title            String
  matchedRole      String          // which target role it matched
  location         String?
  url              String
  postedAt         DateTime?       // from source when available
  firstSeenAt      DateTime @default(now())
  isNew            Boolean  @default(true)   // false for seeded backlog
  alertRunId      String?         // set once reported, so it's never re-reported
  archivedAt       DateTime?       // gone from the board on a later cycle
  jobApplicationId String?         // set when pushed into the tracker

  @@unique([watchedCompanyId, dedupKey])
  @@index([firstSeenAt])
  @@index([alertRunId])
  @@index([isNew])
}

model AlertRun {
  id           String    @id
  startedAt    DateTime  @default(now())
  finishedAt   DateTime?
  windowStart  DateTime          // watermark: end of the previous successful alert
  windowEnd    DateTime
  newCount     Int       @default(0)
  companyCount Int       @default(0)
  status       String    @default("pending")  // pending|sent|skipped|failed
  error        String?

  @@index([startedAt])
}
```

All timestamps are stored **UTC** (`timestamptz`), matching the existing
applied-date convention — the SQLite→Postgres migration previously shifted dates
and broke dashboard date grouping, so this is load-bearing.

### 3.2 Agent DB (`apply_agent`) — migration `0003_jobboard.sql`

```sql
CREATE TABLE IF NOT EXISTS jobboard_runs (
    id             text PRIMARY KEY,
    kind           text NOT NULL CHECK (kind IN ('monitor','alert')),
    status         text NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running','succeeded','failed')),
    companies_total     int NOT NULL DEFAULT 0,
    companies_ok        int NOT NULL DEFAULT 0,
    companies_failed    int NOT NULL DEFAULT 0,
    postings_seen       int NOT NULL DEFAULT 0,
    postings_matched    int NOT NULL DEFAULT 0,
    postings_new        int NOT NULL DEFAULT 0,
    error          text,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz
);
CREATE INDEX IF NOT EXISTS jobboard_runs_kind_idx ON jobboard_runs (kind, started_at DESC);
```

Applied through the existing `python -m agent_server.migrations.run`.

---

## 4. The monitor cycle (every 3h)

`jobboard/monitor.py :: run_monitor_cycle() -> MonitorSummary`

```
1. open jobboard_runs row (kind='monitor')
2. companies = platform GET /jobboard/companies?active=true
3. for each company (sequential, jittered sleep between — same courtesy
   pacing as the lead-gen loop):
     a. adapter = detect(company.ats or company.careerUrl)
     b. raw = adapter.fetch(...)            # one API call, or the 10-page walk
     c. if company.postedAt data exists and company.seededAt is set:
          prefer today's postings; else consider all returned
     d. matched = [p for p in raw if role_match(p.title, filters)]
     e. upsert each matched posting keyed (companyId, dedupKey)
          - seeding cycle  -> isNew=false, set seededAt
          - otherwise      -> insert with isNew=true when unseen
     f. archive rows absent from this fetch (archivedAt=now) — not deleted
     g. patch company.lastCheckedAt / lastStatus / lastError
4. close the run row with counts
```

**Failure isolation.** A per-company exception is caught, recorded on that company
(`lastStatus='error'`), counted, and the loop continues — mirroring the lead-gen
loop's per-candidate `try/except`. One dead career page never aborts the cycle.

**Never deletes.** Postings that vanish from a board are marked `archivedAt`, never
removed. This respects the project's absolute no-data-deletion rule and keeps the
history of what was once open.

---

## 5. The alert email (every 6h)

`jobboard/alert.py :: send_alert(force=False) -> AlertResult`

### 5.1 Window

Not a fixed "last 6 hours". The window is a **watermark**:

```
windowStart = max(finishedAt of last AlertRun WHERE status='sent')  (else epoch)
windowEnd   = now()
postings    = JobPosting WHERE isNew AND alertRunId IS NULL
                          AND firstSeenAt > windowStart
```

Each alert covers exactly two monitor cycles in the healthy case, but if a cycle
is slow, fails, or the machine sleeps, nothing is dropped — the unreported rows are
simply carried into the next alert. `alertRunId` is stamped on every posting
included, so a role can never be reported twice.

**Zero-new behaviour:** status `skipped`, no mail sent. `force=true` (or
`JOBBOARD_ALERT_HEARTBEAT=true`) sends a "nothing new" heartbeat instead.

### 5.2 Content

Per your spec — the total first, then a block per role beneath it:

```
Subject: 7 new roles — Anthropic, Ramp, Linear +2

  ┌─────────────────────────────────────────┐
  │            7 new roles                  │
  │      across 5 companies · last 6h       │
  └─────────────────────────────────────────┘

  [logo]  Anthropic
          Forward Deployed Engineer
          San Francisco · posted today          [View →]

  [logo]  Ramp
          AI Engineer
          New York · posted today               [View →]
  …
```

Grouped by company, ordered by newest `firstSeenAt`. Each row links to the posting
URL; the footer links to `/job-board`.

**Logos.** Resolved from the company's `domain` via a favicon service
(`icons.duckduckgo.com/ip3/<domain>.ico`), with `WatchedCompany.logoUrl`
overriding when set, and a lettermark fallback (initial on a coloured block,
rendered as inline HTML/CSS) when neither resolves or the image fails to load.
The web UI additionally retries a second favicon source before the lettermark;
email gets one shot, since it cannot retry a failed image.

> Corrected during implementation: this originally specified
> `logo.clearbit.com`. That host no longer resolves — the free logo API was
> retired — so every logo in both the UI and the alert was a broken image.
> The replacement sources were verified live before switching.

The email is built as **table-based HTML with inline styles** (the only thing that
renders reliably across mail clients), with a plaintext alternative.

### 5.3 Transport

New, self-contained `jobboard/mailer.py` — roughly 60 lines of `smtplib.SMTP_SSL`,
sending a `MIMEMultipart('alternative')`. It deliberately does **not** restore
`backend/mail.py`, which was removed in `8cef324` along with its Gmail IMAP inbox
and open/click tracking sidecar; none of that is wanted here.

```
JOBBOARD_SMTP_HOST=smtp.gmail.com
JOBBOARD_SMTP_PORT=465
JOBBOARD_SMTP_USER=...
JOBBOARD_SMTP_APP_PASSWORD=...      # Gmail app password, not the account password
JOBBOARD_ALERT_TO=itsamoghgr@gmail.com
```

Credentials live in `agent_server/.env` (gitignored), read through `config.py`
alongside the existing keys. If SMTP config is absent the alert logs a clear
warning and marks the run `failed` without crashing the scheduler — and the
postings keep `alertRunId = NULL`, so they roll into the next successful alert
rather than being lost.

---

## 6. Scheduling

APScheduler (`BackgroundScheduler`), started in the FastAPI lifespan of the
existing `:8002` app, so `./start.sh` continues to be the only thing you run.

```python
scheduler.add_job(run_monitor_cycle, "interval", hours=3,
                  id="jobboard_monitor", max_instances=1, coalesce=True,
                  misfire_grace_time=1800)
scheduler.add_job(send_alert, "interval", hours=6,
                  id="jobboard_alert",  max_instances=1, coalesce=True,
                  misfire_grace_time=1800)
```

- `max_instances=1` — a slow cycle can never overlap itself.
- `coalesce=True` + `misfire_grace_time` — after a laptop sleep, one catch-up run
  fires rather than a burst of missed ones.
- Adds `apscheduler>=3.10` to `agent_server/pyproject.toml`. In-memory jobstore is
  sufficient: the watermark lives in `AlertRun`, so restarts lose no state.

Because the two jobs are independent timers, the alert sometimes lands moments
before a monitor cycle finishes — the watermark design makes that harmless.

An offset start (alert first fires 30 min after boot) avoids an alert racing the
very first monitor cycle on a cold start.

---

## 7. HTTP surface

New router `agent_server/api/jobboard.py`, mounted on the existing `:8002` app and
reachable from the browser through the existing `/api/agent/[...path]` proxy.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/jobboard/monitor/run` | Trigger a monitor cycle now (background task) |
| `POST` | `/api/v1/jobboard/alert/send` | Send an alert now; `{force:true}` to mail even when empty |
| `GET` | `/api/v1/jobboard/runs?kind=&limit=` | Recent run history + counts |
| `POST` | `/api/v1/jobboard/detect` | `{careerUrl}` → `{ats, slug, sampleCount}` — used by the add-company form to validate a URL before saving |

Watchlist and posting **CRUD is plain Prisma in Next.js server actions**, matching
how `/applications` and `/resumes` already work — no need to route product data
through the agent service. The agent service reads/writes those tables through the
platform backend, extending `backend/db.py` + `backend/server.py` with:

```
GET   /jobboard/companies?active=true
PATCH /jobboard/companies/{id}          # lastCheckedAt / lastStatus / seededAt
POST  /jobboard/postings/upsert         # bulk, idempotent on (companyId, dedupKey)
POST  /jobboard/postings/archive        # bulk archive by dedupKey
GET   /jobboard/postings/unalerted
POST  /jobboard/alert-runs             # create / close a AlertRun
```

This keeps the existing invariant that the agent service touches platform data
**over HTTP only**, never by connecting to `apply_tools` directly.

---

## 8. `/job-board` page

New nav entry (`Radar` icon from lucide, matching the existing icon set), sitting
between Applications and Leads.

**Two sections:**

1. **New roles feed** — postings grouped by company, newest first, each row showing
   logo, company, title, matched role, location, posted/first-seen time, and a link
   out. Filters: *unseen only* · by role · by company. Primary action per row:
   **Track** → creates a `JobApplication` (company, role, URL, career page
   prefilled) and stamps `jobApplicationId` back on the posting, so the Job Board
   shows what you've already actioned.
2. **Watchlist** — table of watched companies with career URL, detected ATS,
   last-checked time, status badge (green ok / red error with the message), and
   posting counts. Add-company form calls `/detect` first so a bad URL is caught
   immediately, and shows a live "found N jobs, M matching" preview before saving.

A header strip shows last monitor run, next scheduled run, last alert, and manual
**Run now** / **Send alert** buttons wired to the trigger endpoints.

---

## 9. Config

Added to `agent_server/config.py`:

| Var | Default | Meaning |
| --- | --- | --- |
| `JOBBOARD_ENABLED` | `true` | Master switch for both scheduled jobs |
| `JOBBOARD_MONITOR_INTERVAL_H` | `3` | Monitor cadence |
| `JOBBOARD_ALERT_INTERVAL_H` | `6` | Alert cadence |
| `JOBBOARD_MAX_PAGES` | `10` | Fallback pagination cap |
| `JOBBOARD_TODAY_ONLY` | `true` | Prefer same-day postings when the source dates them |
| `JOBBOARD_ALERT_HEARTBEAT` | `false` | Mail even when nothing is new |
| `JOBBOARD_COMPANY_SLEEP_S` | `1.0–3.0` | Jitter between companies |
| `JOBBOARD_SMTP_*`, `JOBBOARD_ALERT_TO` | — | Mail transport (§5.3) |

---

## 10. Build order

| # | Step | Deliverable |
| --- | --- | --- |
| 1 | Schema | `0003_jobboard.sql`; Prisma models + targeted SQL DDL against `apply_tools`; `prisma generate` |
| 2 | Adapters | `jobboard/adapters/{greenhouse,lever,ashby,smartrecruiters,generic}.py` + `detect()`; unit-tested against recorded fixtures |
| 3 | Matching | `jobboard/matcher.py` — normalization, alias matching, `dedupKey` |
| 4 | Platform API | `backend/db.py` + `backend/server.py` `/jobboard/*` endpoints |
| 5 | Monitor | `jobboard/monitor.py` + run bookkeeping; verify seeding vs new-detection |
| 6 | Mailer + alert | `jobboard/mailer.py`, `jobboard/alert.py`, HTML template; watermark logic |
| 7 | Scheduler + routes | APScheduler in lifespan; `api/jobboard.py` triggers |
| 8 | UI | `/job-board` page, watchlist CRUD, Track action, sidebar entry |
| 9 | Docs | README section; `.env.example` entries |

Steps 1–5 are independently useful: after step 5 the monitor is populating real
data and you can inspect it with SQL before any email exists.

**Testing.** Adapter tests run against checked-in JSON fixtures (no network).
Monitor tests use a fake adapter + a seeded temp DB to assert the three cases that
actually matter: seeding marks nothing new, an unchanged board on the second cycle
produces zero new, and one added posting produces exactly one new. Alert tests
assert the watermark never double-reports and that a failed send leaves postings
unalerted.

---

## 11. Risks / open points

- **Custom pages are the weak spot.** Bespoke boards (React-rendered, infinite
  scroll, no dates) are where extraction gets unreliable. Mitigation: the ATS path
  covers most real companies exactly; the generic path is bounded, and per-company
  errors surface in the UI as a red status badge rather than failing silently.
  Recommendation: prefer the ATS URL over a marketing `/careers` page when both exist.
- **Clearbit logos** are a third-party dependency and require remote images enabled
  in Gmail. The lettermark fallback means the alert is never broken by it, and
  `logoUrl` lets you pin a logo per company. Say the word if you'd prefer CID-embedded
  images instead.
- **Rate limiting.** One request per company per 3h with jitter is negligible for
  ATS APIs. A watchlist in the hundreds would want concurrency plus per-host
  throttling; the sequential loop is right for a watchlist in the tens.
- **Laptop sleep.** APScheduler only runs while `start.sh` is up. `coalesce` handles
  short sleeps; if you want the monitor to run when the app isn't, a launchd plist
  hitting the trigger endpoint is the follow-up.
- **`postedAt` timezone.** Lever returns epoch ms, Greenhouse an offset ISO string.
  All are converted to UTC on ingest; "posted today" is evaluated in **your local
  timezone**, not UTC, so the day boundary matches your expectation.
