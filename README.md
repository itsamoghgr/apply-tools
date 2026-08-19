# Apply Tools

A personal job-application toolkit with three surfaces — a Next.js web app, a FastAPI backend, and a Chrome extension — built around what I actually do when applying to jobs:

- **Cover letters** — turn a JD + company into a tailored, LaTeX-compiled PDF.
- **Resume builder** — edit a resume as structured data, export an ATS-friendly PDF, with AI assists for bullets, summaries, and JD tailoring.
- **Scoring** — rank a JD against every resume variant with a rubric-based fit score and a per-category breakdown (skill / experience / impact / education).
- **Tracking** — log every application, its status, linked contacts, and an audit trail of everything generated.

Generation runs through a pluggable provider chain (Anthropic, Bedrock, Groq, NVIDIA NIM). Cover letters and resumes are compiled locally with [Tectonic](https://tectonic-typesetting.github.io/). Everything lives in a local Postgres database (`apply_tools`) — schema owned by Prisma, read by the FastAPI backend via SQLAlchemy. The web app and extension only talk to `127.0.0.1`; nothing is hosted.

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

### 6. Load the browser extension

1. Open `chrome://extensions` (or `brave://extensions`, `edge://extensions`).
2. Toggle **Developer mode** on.
3. Click **Load unpacked** and select the [`extension/`](extension) folder.
4. Pin the extension so its icon is visible.

## Daily use

```bash
./start.sh            # FastAPI on :8001 + Next.js on :3001
```

Open <http://localhost:3001> for the web app, or click the extension icon. Both read and write the same Postgres database. `start.sh` frees the ports from any stale run, verifies Postgres is reachable on `localhost:5432`, and also boots the optional lead-generation agent service on `:8002` if `agent_server/venv` exists. `./stop.sh` shuts everything down.

### Web app

| Page | What it does |
| --- | --- |
| `/applications` | The tracker. Every application with status, dates, notes, linked leads, and a generated cover letter per row. |
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

**Provider routing** — each task picks its own backend, so you can run cheap models where quality matters least:

- `AI_PROVIDER` (default `anthropic`) — cover letters, questions, resume AI.
- `SCORE_PROVIDER` (defaults to `AI_PROVIDER`) — JD scoring.
- `EXTRACT_PROVIDER` (default `bedrock`) — Auto-detect JD extraction.

Valid values are `anthropic`, `bedrock`, `groq`, and `nvidia`. Each provider has its own model override (`MODEL`, `BEDROCK_MODEL`, `GROQ_MODEL`, `NIM_MODEL`, …) plus timeouts (`LLM_TIMEOUT_SECS`, `EXTRACT_TIMEOUT_SECS`) and logging knobs (`LOG_FORMAT`, `LOG_LEVEL`, `ENV`).

`DATABASE_URL` is read by both the backend and the frontend and must match between `backend/.env` and `frontend/.env`.

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

## Privacy

- API keys live only in `backend/.env`, which is gitignored. Neither the extension nor the web app sees them.
- The backend and Next.js dev server listen on `127.0.0.1` only by default; do not expose them without adding auth.
- Data lives in your local Postgres server. `data/` (saved PDFs, plus any legacy SQLite file), `*.pdf`, `.env`, `node_modules/`, and Python venvs are gitignored. **`backend/template.tex` is NOT gitignored** — replace it with a placeholder before committing if you don't want your real template in git.
- Resumes are sent to your configured LLM provider as part of every request body. Don't keep anything in a resume you wouldn't want to send to an LLM API.
- When you click **Auto-detect** on a page that doesn't match a known job board (LinkedIn, Greenhouse, Lever, Ashby, Workday, Indeed), the page's visible text is sent to the extraction provider for company + JD extraction. Groq's free tier may use prompts for service improvement — check their data policy if that matters to you.
