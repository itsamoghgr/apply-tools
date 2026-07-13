# Apply Tools

A personal job-application toolkit with three surfaces — a Next.js web app, a FastAPI backend, and a Chrome extension — for the four things I actually do when applying to jobs:

1. **Cover Letter** — turn a JD + company into a tailored, LaTeX-compiled PDF cover letter.
2. **Email** — draft a job-application email (subject + body) for a specific role.
3. **Outreach** — generate a LinkedIn invitation note (≤300 char), a LinkedIn DM/InMail, or a cold email to a specific person.
4. **Score** — rank a JD against every resume variant using a rubric-based fit score (0-10) with a per-category breakdown (skill / experience / impact / education).

Generation runs through the Claude API. Cover letters are compiled locally with [Tectonic](https://tectonic-typesetting.github.io/). Resumes and application history live in a local Postgres database (`apply_tools`), with the schema owned by Prisma and accessed by the FastAPI backend via SQLAlchemy. The web app and extension only talk to `127.0.0.1`; nothing is hosted.

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
# Edit backend/.env and paste:
#   ANTHROPIC_API_KEY  - https://console.anthropic.com (cover letter / email / outreach / score)
#   GROQ_API_KEY       - https://console.groq.com (popup's Auto-detect button; free tier is fine)
```

### 3. Drop in your real cover letter template

- [`backend/template.tex`](backend/template.tex) — replace with your real LaTeX cover letter, keeping these four placeholders intact:
  - `{{COMPANY_NAME}}`
  - `{{ROLE_TITLE}}`
  - `{{HIRING_MANAGER_OR_TEAM}}`
  - `{{BODY_PARAGRAPHS}}`

### 4. Start Postgres and create the database

The app uses Postgres. Install and start a local server, then create the
`apply` role and `apply_tools` database both apps connect to:

```bash
# macOS (Homebrew)
brew install postgresql@17
brew services start postgresql@17

# Create the role + database (one time)
psql -d postgres -c "CREATE ROLE apply LOGIN PASSWORD 'apply' CREATEDB;"
createdb -O apply apply_tools
```

The default connection string used by both `frontend/.env` and `backend/.env`
is `postgresql://apply:apply@localhost:5432/apply_tools`. (`CREATEDB` on the
role lets Prisma Migrate create its shadow database.)

### 5. Set up the frontend + DB schema

```bash
cd frontend
npm install
npx prisma migrate deploy        # applies the schema to Postgres
npx prisma generate
```

> Migrating data from an older SQLite install? After the schema is in place,
> run `cd backend && DATABASE_URL=postgresql://apply:apply@localhost:5432/apply_tools python migrate_sqlite_to_postgres.py`
> to copy `data/apply-tools.db` into Postgres.

Resumes are now managed in the web app at `/resumes`. The Prisma seed imports any existing `.txt` files in `backend/resumes/` on first run — after that, edit them in the UI. (Old `.txt` files can be deleted; they're no longer read at request time.)

### 6. Load the browser extension

1. Open `chrome://extensions` (or `brave://extensions`, `edge://extensions`).
2. Toggle **Developer mode** on.
3. Click **Load unpacked** and select the [`extension/`](extension) folder.
4. Pin the extension so its icon is visible.

## Daily use

```bash
./start.sh            # boots FastAPI on :8001 + Next.js on :3001
```

Open <http://localhost:3001> for the web app, or click the Chrome extension icon. Both read/write the same Postgres database. `start.sh` checks that Postgres is reachable on `localhost:5432` before booting.

Web app pages: `/resumes` (manage), `/resume-builder` (structured resume editor → LaTeX PDF, with AI assists), `/generate` (4 tabs), `/score` (leaderboard), `/history` (audit trail of every successful generation, including PDFs).

### Resume Builder (`/resume-builder`)

Build a resume from structured fields — header/contact, professional summary, education, experience (with per-bullet editing), technical skills, and projects — then export a polished, ATS-friendly PDF compiled from the [`sb2nov`](https://github.com/sb2nov/resume)-style template at [`backend/resume_template.tex`](backend/resume_template.tex). Each saved resume lives in the `ResumeProfile` table (sections stored as JSON).

AI assists (all routed through the same provider/fallback chain as the rest of the app):

- **Improve bullet** (✨ on each bullet) — rewrites a single line into a strong, metric-driven, action-verb bullet. Bold metrics with `\textbf{...}` are preserved; everything else is LaTeX-escaped so a stray `%`/`&`/`$` can't break the compile.
- **Suggest summary + skills** — drafts a professional summary and organised skill categories from the experience you've entered (grounded in your real content; never padded with unrelated buzzwords).
- **Draft from notes** — paste an old resume or a brain-dump; AI extracts structured education/experience/skills/projects to pre-fill the builder.
- **Tailor to JD** — paste a job description; AI reorders and rewrites your *existing* bullets and skills to foreground what the role wants, without inventing experience.

> The template loads `glyphtounicode`/`\pdfgentounicode` only under pdfTeX and omits `fontawesome5` (unused), since Tectonic compiles with XeTeX — both are guarded so the same `.tex` compiles cleanly here.

Extension popup — pick a resume from the global picker; it persists across sessions. Then switch tabs as needed:

- **Cover Letter** — paste company + JD, hit Generate. PDF lands in `Downloads/` as `CoverLetter_<Company>.pdf`. An inline JD-fit score appears alongside the PDF.
- **Email** — paste company + JD + optional intent note ("ask for referral", etc.). Subject and body each have copy-to-clipboard buttons.
- **Outreach** — pick channel (LinkedIn invitation / LinkedIn message / email), paste their LinkedIn profile + optional context, hit Generate. The LinkedIn-invitation char counter goes amber near the limit and red over 290.
- **Score** — paste JD, hit Score. Get a ranked leaderboard of every resume on disk. Click any row to switch the global active resume to that one.

The popup shows a small dot indicating whether the backend is reachable (green = ok, red = offline).

## Configuration

- `ANTHROPIC_API_KEY` (**required**) — in `backend/.env`. Powers cover letter / email / outreach / score.
- `GROQ_API_KEY` (**required for Auto-detect**) — in `backend/.env`. Powers the popup's Auto-detect button when site selectors miss. Free tier is fine.
- `MODEL` (optional) — override the Claude model, e.g. `MODEL=claude-sonnet-4-5`. Defaults to `claude-opus-4-5`.
- `EXTRACT_MODEL` (optional) — override the Groq model used by `/extract-jd`. Defaults to `llama-3.3-70b-versatile`.

## Troubleshooting

- **"tectonic not found on PATH"** — `brew install tectonic` (macOS) or grab a binary from <https://tectonic-typesetting.github.io/>.
- **"ANTHROPIC_API_KEY not set"** — did you `cp backend/.env.example backend/.env` and fill it in?
- **First request is very slow** — Tectonic is downloading packages. Subsequent requests are fast.
- **LaTeX compile fails** — check the error detail in the popup or the uvicorn log. Common cause: your edited `template.tex` has a syntax error, or a stray special char (`&`, `%`, `_`) leaked into a non-escaped slot.
- **`Unknown resume_id`** — the picker is asking for an id that isn't in the DB. Add or rename a resume at `/resumes`.
- **`Postgres not reachable on localhost:5432`** — start it with `brew services start postgresql@17`, then confirm the `apply_tools` database and `apply` role exist (see Setup step 4).
- **`DATABASE_URL is not set`** — the backend reads it from `backend/.env`; make sure that line is present and matches `frontend/.env`.
- **Prisma "could not create the shadow database"** — the `apply` role needs `CREATEDB`: `psql -d postgres -c "ALTER ROLE apply CREATEDB;"`.
- **Popup says "offline"** — is `./start.sh` running? Curl `http://127.0.0.1:8001/` directly to confirm.

## Privacy

- API keys live only in `backend/.env`, which is gitignored. Neither the extension nor the web app sees them.
- The backend and Next.js dev server listen on `127.0.0.1` only by default; do not expose them without adding auth.
- The Postgres data lives in your local Postgres server. `data/` (saved PDFs, plus any legacy SQLite file), `*.pdf`, `.env`, `node_modules/`, and Python venvs are gitignored. **`backend/template.tex` is NOT gitignored** — replace it with a placeholder before committing if you don't want your real template in git. (`backend/resumes/*.txt` is only used as a one-time seed source on first migrate; the DB is the source of truth afterwards.)
- Resumes are sent to Anthropic as part of every request body. Don't store anything in your resumes you wouldn't want to send to an LLM API.
- When you click **Auto-detect** on a page that doesn't match a known job board (LinkedIn, Greenhouse, Lever, Ashby, Workday, Indeed), the page's visible text is sent to Groq for company + JD extraction. Groq's free tier may use prompts for service improvement — check their data policy if that matters to you.
