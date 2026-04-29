# Apply Tools

A personal job-application toolkit with three surfaces — a Next.js web app, a FastAPI backend, and a Chrome extension — for the four things I actually do when applying to jobs:

1. **Cover Letter** — turn a JD + company into a tailored, LaTeX-compiled PDF cover letter.
2. **Email** — draft a job-application email (subject + body) for a specific role.
3. **Outreach** — generate a LinkedIn invitation note (≤300 char), a LinkedIn DM/InMail, or a cold email to a specific person.
4. **Score** — rank a JD against every resume variant using a rubric-based fit score (0-10) with a per-category breakdown (skill / experience / impact / education).

Generation runs through the Claude API. Cover letters are compiled locally with [Tectonic](https://tectonic-typesetting.github.io/). Resumes and application history live in a local SQLite DB at `data/apply-tools.db`, owned by Prisma. The web app and extension only talk to `127.0.0.1`; nothing is hosted.

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

### 4. Set up the frontend + DB

```bash
cd frontend
npm install
npx prisma migrate dev          # creates data/apply-tools.db and seeds existing backend/resumes/*.txt
```

Resumes are now managed in the web app at `/resumes`. The Prisma seed imports any existing `.txt` files in `backend/resumes/` on first run — after that, edit them in the UI. (Old `.txt` files can be deleted; they're no longer read at request time.)

### 5. Load the browser extension

1. Open `chrome://extensions` (or `brave://extensions`, `edge://extensions`).
2. Toggle **Developer mode** on.
3. Click **Load unpacked** and select the [`extension/`](extension) folder.
4. Pin the extension so its icon is visible.

## Daily use

```bash
./start.sh            # boots FastAPI on :8000 + Next.js on :3000
```

Open <http://localhost:3000> for the web app, or click the Chrome extension icon. Both read/write the same SQLite DB.

Web app pages: `/resumes` (manage), `/generate` (4 tabs), `/score` (leaderboard), `/history` (audit trail of every successful generation, including PDFs).

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
- **`SQLite DB not found`** — run `cd frontend && npx prisma migrate dev` to create `data/apply-tools.db`.
- **Popup says "offline"** — is `./start.sh` running? Curl `http://127.0.0.1:8000/` directly to confirm.

## Privacy

- API keys live only in `backend/.env`, which is gitignored. Neither the extension nor the web app sees them.
- The backend and Next.js dev server listen on `127.0.0.1` only by default; do not expose them without adding auth.
- `data/` (SQLite DB + saved PDFs), `*.pdf`, `.env`, `node_modules/`, and Python venvs are gitignored. **`backend/template.tex` is NOT gitignored** — replace it with a placeholder before committing if you don't want your real template in git. (`backend/resumes/*.txt` is only used as a one-time seed source on first migrate; the DB is the source of truth afterwards.)
- Resumes are sent to Anthropic as part of every request body. Don't store anything in your resumes you wouldn't want to send to an LLM API.
- When you click **Auto-detect** on a page that doesn't match a known job board (LinkedIn, Greenhouse, Lever, Ashby, Workday, Indeed), the page's visible text is sent to Groq for company + JD extraction. Groq's free tier may use prompts for service improvement — check their data policy if that matters to you.
