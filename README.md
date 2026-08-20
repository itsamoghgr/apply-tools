# Apply Tools

A personal job-application toolkit with three surfaces — a Next.js web app, a FastAPI backend, and a Chrome extension — built around what I actually do when applying to jobs:

- **Cover letters** — turn a JD + company into a tailored, LaTeX-compiled PDF.
- **Resume builder** — edit a resume as structured data, export an ATS-friendly PDF, with AI assists for bullets, summaries, and JD tailoring.
- **Scoring** — rank a JD against every resume variant with a rubric-based fit score and a per-category breakdown (skill / experience / impact / education).
- **Tracking** — log every application, its status, linked contacts, and an audit trail of everything generated.
- **Job Board** — watch companies' career pages, get new matching roles collected on a schedule you set and emailed as an alert at the times you choose.

Generation runs through a pluggable provider chain (Anthropic, Bedrock, Groq, Google Gemini / Vertex AI, NVIDIA NIM). Cover letters and resumes are compiled locally with [Tectonic](https://tectonic-typesetting.github.io/). Everything lives in a local Postgres database (`apply_tools`) — schema owned by Prisma, read by the FastAPI backend via SQLAlchemy. The web app and extension only talk to `127.0.0.1`; nothing is hosted.

## Setup

### 1. Install Tectonic and Python deps

```bash
# macOS
brew install tectonic
tectonic --version   # sanity check

cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

> The first `tectonic` compile downloads LaTeX packages and can take 30s+. Subsequent compiles are ~2s.

### 2. Add your API keys

```bash
cp backend/.env.example backend/.env
```

At minimum set `ANTHROPIC_API_KEY` (cover letters, resume AI, scoring, chat) and `GROQ_API_KEY` (the popup's Auto-detect button; free tier is fine). See [Configuration](#configuration) for the full set and how to switch providers.

### 3. Drop in your real cover letter template

[`backend/template.tex`](backend/template.tex) — replace with your real LaTeX cover letter, keeping these four placeholders intact:

- `{{COMPANY_NAME}}`
- `{{ROLE_TITLE}}`
- `{{HIRING_MANAGER_OR_TEAM}}`
- `{{BODY_PARAGRAPHS}}`

### 4. Start Postgres and create the database

```bash
# macOS (Homebrew)
brew install postgresql@17
brew services start postgresql@17

# Create the role + database (one time)
psql -d postgres -c "CREATE ROLE apply LOGIN PASSWORD 'apply' CREATEDB;"
createdb -O apply apply_tools
```

The default connection string used by both `frontend/.env` and `backend/.env` is `postgresql://apply:apply@localhost:5432/apply_tools`. (`CREATEDB` on the role lets Prisma Migrate create its shadow database.)

### 5. Set up the frontend + DB schema

```bash
cd frontend
npm install
npx prisma migrate deploy        # applies the schema to Postgres
npx prisma generate
```

Resumes are managed in the web app at `/resumes`. The Prisma seed imports any existing `.txt` files in `backend/resumes/` on first run — after that, edit them in the UI. (Those `.txt` files can then be deleted; they're not read at request time.)

### 6. (Optional) Enable the Job Board

The Job Board's tables are applied out-of-band rather than through Prisma Migrate,
because this database has drifted from its migration history:

```bash
psql "$DATABASE_URL" -f frontend/prisma/sql/jobboard_migration.sql
cd frontend && npx prisma generate

# Agent-side bookkeeping tables + deps
cd agent_server && python3 -m venv venv && source venv/bin/activate
pip install -e .
cd .. && agent_server/venv/bin/python -m agent_server.migrations.run
```

For the alert email, add these to `agent_server/.env` (a Gmail **app password**,
not your account password — Google Account → Security → 2-Step Verification →
App passwords):

```bash
JOBBOARD_SMTP_USER=you@gmail.com
JOBBOARD_SMTP_APP_PASSWORD=xxxxxxxxxxxxxxxx
JOBBOARD_ALERT_TO=you@gmail.com
```

Without them the monitor still runs and stores roles; only the alert is skipped
(and the postings it would have reported stay queued for the next successful send).

### 7. Load the browser extension

1. Open `chrome://extensions` (or `brave://extensions`, `edge://extensions`).
2. Toggle **Developer mode** on.
3. Click **Load unpacked** and select the [`extension/`](extension) folder.
4. Pin the extension so its icon is visible.

## Daily use

```bash
./start.sh            # FastAPI on :8001 + Next.js on :3001
```

Open <http://localhost:3001> for the web app, or click the extension icon. Both read and write the same Postgres database. `start.sh` frees the ports from any stale run, verifies Postgres is reachable on `localhost:5432`, and also boots the agent service on `:8002` if `agent_server/venv` exists. That service runs both the lead-generation pipeline and the Job Board's schedules, so it needs to be up for career pages to be monitored. `./stop.sh` shuts everything down.

### Web app

| Page | What it does |
| --- | --- |
| `/applications` | The tracker. Every application with status, dates, notes, linked leads, and a generated cover letter per row. |
| `/job-board` | Watched career pages and the new roles found on them. One click sends a posting to the tracker. |
| `/resume-builder` | Structured resume editor → LaTeX PDF, with AI assists (see below). |
| `/resumes` | Manage raw resume text used by the cover-letter and scoring flows. |
| `/leads` | People attached to applications, plus companies surfaced by the agent service. |
| `/profile` | Master career data that resumes and generation draw from. |
| `/history` | Audit trail of every successful generation, including PDFs. |

### Resume Builder (`/resume-builder`)

Build a resume from structured fields — header/contact, professional summary, education, experience (with per-bullet editing), technical skills, and projects — then export a polished, ATS-friendly PDF compiled from the [`sb2nov`](https://github.com/sb2nov/resume)-style template at [`backend/resume_template.tex`](backend/resume_template.tex). Each saved resume lives in the `ResumeProfile` table with its sections stored as JSON.

AI assists (all routed through the same provider/fallback chain as the rest of the app):

- **Improve bullet** (✨ on each bullet) — rewrites a single line into a strong, metric-driven, action-verb bullet. Bold metrics with `\textbf{...}` are preserved; everything else is LaTeX-escaped so a stray `%`/`&`/`$` can't break the compile.
- **Suggest summary + skills** — drafts a professional summary and organised skill categories from the experience you've entered, grounded in your real content.
- **Draft from notes** — paste an old resume or a brain-dump; AI extracts structured education/experience/skills/projects to pre-fill the builder.
- **Tailor to JD** — paste a job description; AI reorders and rewrites your *existing* bullets and skills to foreground what the role wants, without inventing experience.

> The template loads `glyphtounicode`/`\pdfgentounicode` only under pdfTeX and omits `fontawesome5` (unused), since Tectonic compiles with XeTeX — both are guarded so the same `.tex` compiles cleanly here.

### Job Board (`/job-board`)

Add a company's career page and it is re-scraped every 3 hours; anything new that
matches your target roles is stored, and an alert email lands at your chosen times with
the count and one block per role.

**Target roles** — Data Scientist, Data Analyst, AI Engineer, Founding Engineer,
Forward Deployed Engineer. Matching is alias-based and anchored on the *head* of
the title (the part before the first comma or dash), so "Software Development
Engineer, Open Data Analytics" is correctly *not* a Data Analyst role — the team
name is not the job.

**How pages are read.** Greenhouse, Lever, Ashby, SmartRecruiters and
amazon.jobs each expose a public JSON endpoint that returns a whole board in one
call, with a real posted-date and the job description. Those are read exactly
and for free — no LLM, no pagination. Any other career page falls back to
fetching the HTML (with a headless render when needed), walking up to
`JOBBOARD_MAX_PAGES` pages, and extracting postings with a single bounded LLM
call per company per cycle. **Point a company at its ATS board URL when it has
one** — it is cheaper and more accurate than its marketing careers page.

Two fields are parsed out of the job description while scraping, with no extra
requests: **minimum years of experience** (from phrases like "3+ years"; the
lowest figure stated wins) and **country** (ISO alpha-2, from location strings
that arrive in five incompatible formats across sources). Both are `null` when
they genuinely cannot be determined, and `null` is always treated as *keep* —
a parser miss must never hide a real job.

**Filtering** happens in two places, and the distinction matters:

- *Scrape settings* run during the scrape and postings that fail them are never
  stored. Per company: which roles and which countries. Globally (the **Scrape
  settings** button): retention window, maximum experience, and a default
  country list. These are coarse and user-controlled by design.
- *Feed filters* are display-only: search, role, company, location, experience
  band, age, sort, "new only", "hide applied".

Nothing is ever deleted. Postings outside a retention window or above an
experience ceiling are **archived** — they leave the feed and alerts but the
row survives, so widening either setting brings them straight back. Anything
already sent to the tracker is exempt from every sweep.

The first scan of a new company records its entire current board as *not new*,
so adding a company never floods your next alert with its back catalogue.

### Extension popup

Pick a resume from the global picker; it persists across sessions. Then switch tabs:

- **Cover** — paste company + JD, hit Generate. PDF lands in `Downloads/` as `CoverLetter_<Company>.pdf`, with an inline JD-fit score alongside it.
- **Score** — paste a JD for a ranked leaderboard of every resume. Click any row to make it the global active resume.
- **Question** — answer a free-text application question in your own voice, grounded in the active resume.
- **Track** — log the current posting to the applications tracker without leaving the page.
- **Lead** — save a contact (name, email, LinkedIn, company) straight to `/leads`.
- **Chat** — free-form assistant for cover letters, interview prep, and career questions.

Most tabs read a shared company + JD box at the top; **Auto-detect** fills it from the current page. A small dot shows whether the backend is reachable (green = ok, red = offline).

## Configuration

All of these live in `backend/.env`. See [`backend/.env.example`](backend/.env.example) for the annotated list.

**Keys**

- `ANTHROPIC_API_KEY` — powers cover letters, resume AI, scoring, and chat under the default provider.
- `GROQ_API_KEY` — required for Auto-detect (JD extraction). Free tier is fine.
- `NVIDIA_API_KEY`, `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` — only if you point a provider at NVIDIA NIM or Bedrock.
- `GEMINI_API_KEY` — Google **AI Studio** key (<https://aistudio.google.com/apikey>), used when `AI_PROVIDER=gemini`.
- `VERTEX_PROJECT` / `VERTEX_LOCATION` — Google **Vertex AI**, used when `AI_PROVIDER=vertex`. Same Gemini models, but billed to a GCP project (so Google Cloud credits apply) and authenticated with Application Default Credentials rather than a key: `gcloud auth application-default login`. Required if an org policy disallows API keys, which also blocks the AI Studio route.

**Provider routing** — each task picks its own backend, so you can run cheap models where quality matters least:

- `AI_PROVIDER` (default `anthropic`) — cover letters, questions, resume AI.
- `SCORE_PROVIDER` (defaults to `AI_PROVIDER`) — JD scoring.
- `EXTRACT_PROVIDER` (default `bedrock`) — Auto-detect JD extraction.

Valid values are `anthropic`, `bedrock`, `groq`, `gemini`, `vertex`, and `nvidia`. A provider with no credentials configured is skipped in the fallback chain rather than failing it, so adding one can't break a working setup. Each provider has its own model override (`MODEL`, `BEDROCK_MODEL`, `GROQ_MODEL`, `NIM_MODEL`, …) plus timeouts (`LLM_TIMEOUT_SECS`, `EXTRACT_TIMEOUT_SECS`) and logging knobs (`LOG_FORMAT`, `LOG_LEVEL`, `ENV`).

`DATABASE_URL` is read by both the backend and the frontend and must match between `backend/.env` and `frontend/.env`.

**Job Board** — all in `agent_server/.env`.

*When to scrape, and when to alert.* Every time below is read in
`JOBBOARD_TIMEZONE`, so it keeps meaning the same wall-clock hour across a DST
shift. Narrowing any of these never loses roles: the alert window is a
watermark, so anything found outside alert hours rolls into the next alert.

| Var | Default | Meaning |
| --- | --- | --- |
| `JOBBOARD_ENABLED` | `true` | Master switch for both schedules |
| `JOBBOARD_TIMEZONE` | `UTC` | IANA zone all schedule times are read in |
| `JOBBOARD_MONITOR_INTERVAL_H` | `3` | How often career pages are re-scraped |
| `JOBBOARD_MONITOR_ACTIVE_START` | `07:00` | Earliest hour a scrape may run |
| `JOBBOARD_MONITOR_ACTIVE_END` | `23:00` | Latest hour a scrape may run (set equal to start for 24/7) |
| `JOBBOARD_MONITOR_DAYS` | `*` | Days scraping runs — `*`, `mon-fri`, or `mon,wed,fri` |
| `JOBBOARD_ALERT_AT` | `09:00,18:00` | Times of day the alert email is sent. **Wins over the interval below** |
| `JOBBOARD_ALERT_DAYS` | `*` | Days the alert is sent |
| `JOBBOARD_ALERT_INTERVAL_H` | `6` | Fallback only, used when `JOBBOARD_ALERT_AT` is blank |
| `JOBBOARD_TODAY_ONLY` | `true` in code, **set to `false`** in `.env.example` | When `true`, only same-day postings match — so a scan run in the evening reports nothing. Dedup already prevents repeats, so `false` is the useful setting |
| `JOBBOARD_MAX_PAGES` | `10` | Page cap for the non-ATS fallback |
| `JOBBOARD_SMTP_*`, `JOBBOARD_ALERT_TO` | — | Alert mail transport |

Retention, maximum experience, and the default country list are set in the UI
(the **Scrape settings** button) rather than by env var, since they are things
you change while browsing.

## Troubleshooting

- **"tectonic not found on PATH"** — `brew install tectonic` (macOS) or grab a binary from <https://tectonic-typesetting.github.io/>.
- **"ANTHROPIC_API_KEY not set"** — did you `cp backend/.env.example backend/.env` and fill it in?
- **First request is very slow** — Tectonic is downloading packages. Subsequent requests are fast.
- **LaTeX compile fails** — check the error detail in the popup or the uvicorn log. Common cause: your edited `template.tex` has a syntax error, or a stray special char (`&`, `%`, `_`) leaked into a non-escaped slot.
- **`Unknown resume_id`** — the picker is asking for an id that isn't in the DB. Add or rename a resume at `/resumes`.
- **`Postgres not reachable on localhost:5432`** — start it with `brew services start postgresql@17`, then confirm the `apply_tools` database and `apply` role exist (Setup step 4).
- **`DATABASE_URL is not set`** — the backend reads it from `backend/.env`; make sure that line is present and matches `frontend/.env`.
- **Prisma "could not create the shadow database"** — the `apply` role needs `CREATEDB`: `psql -d postgres -c "ALTER ROLE apply CREATEDB;"`.
- **Popup says "offline"** — is `./start.sh` running? Curl `http://127.0.0.1:8001/` directly to confirm.
- **Job Board says "Agent service offline"** — the agent service on `:8002` isn't up. It needs `agent_server/venv` to exist (see setup step 6); without it, schedules don't run and the Scan/Alert buttons fail.
- **A scan finds 0 roles across every company** — check `JOBBOARD_TODAY_ONLY`. When `true`, only roles posted *that same day* match, so an evening scan legitimately returns nothing.
- **A company shows "no matching roles"** — it's being watched, the scrape succeeded, and nothing matched your five target roles. A red "scrape failed" row means the opposite: the board couldn't be read, and the error is shown inline.
- **Alert never arrives** — `JOBBOARD_SMTP_APP_PASSWORD` must be a Gmail *app password*. Postings aren't lost when a send fails; they roll into the next successful alert.

## Privacy

- API keys live only in `backend/.env`, which is gitignored. Neither the extension nor the web app sees them.
- The backend and Next.js dev server listen on `127.0.0.1` only by default; do not expose them without adding auth.
- The Job Board fetches public career pages and their JSON endpoints directly; nothing about you is sent to them. Company logos in the web app and alert email are loaded from a public favicon service by domain.
- Data lives in your local Postgres server. `data/` (saved PDFs, plus any legacy SQLite file), `*.pdf`, `.env`, `node_modules/`, and Python venvs are gitignored. **`backend/template.tex` is NOT gitignored** — replace it with a placeholder before committing if you don't want your real template in git.
- Resumes are sent to your configured LLM provider as part of every request body. Don't keep anything in a resume you wouldn't want to send to an LLM API.
- When you click **Auto-detect** on a page that doesn't match a known job board (LinkedIn, Greenhouse, Lever, Ashby, Workday, Indeed), the page's visible text is sent to the extraction provider for company + JD extraction. Groq's free tier may use prompts for service improvement — check their data policy if that matters to you.
