# Apply Tools

A personal browser extension + local FastAPI backend for the four things I actually do when applying to jobs:

1. **Cover Letter** — turn a JD + company into a tailored, LaTeX-compiled PDF cover letter.
2. **Email** — draft a job-application email (subject + body) for a specific role.
3. **Outreach** — generate a LinkedIn invitation note (≤300 char), a LinkedIn DM/InMail, or a cold email to a specific person.
4. **Score** — rank a JD against every resume variant on disk using a rubric-based fit score (0-10) with a per-category breakdown (skill / experience / impact / education).

Generation runs through the Claude API. Cover letters are compiled locally with [Tectonic](https://tectonic-typesetting.github.io/). The extension only talks to `127.0.0.1:8000`; nothing is hosted.

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

### 2. Add your API key

```bash
cp backend/.env.example backend/.env
# Edit backend/.env and paste your real key from https://console.anthropic.com
```

### 3. Drop in your real template and resume(s)

The repo ships with **placeholder** versions that work but are not personalized:

- [`backend/template.tex`](backend/template.tex) — replace with your real LaTeX cover letter, keeping these four placeholders intact:
  - `{{COMPANY_NAME}}`
  - `{{ROLE_TITLE}}`
  - `{{HIRING_MANAGER_OR_TEAM}}`
  - `{{BODY_PARAGRAPHS}}`
- [`backend/resumes/`](backend/resumes) — drop one or more `.txt` files here. Each file is a resume variant Claude treats as ground truth (it is instructed to never invent beyond what's in this file). The filename stem becomes the id, so `data-science.txt` becomes the option `data-science`. Add as many siblings as you want (`swe.txt`, `pm.txt`, etc.).
  - **Optional friendly name:** if the file's first line is `# Label: Data Science (long form)`, that becomes the dropdown label. Otherwise the title-cased filename stem is used. The label line is stripped before sending to Claude.

### 4. Load the browser extension

1. Open `chrome://extensions` (or `brave://extensions`, `edge://extensions`).
2. Toggle **Developer mode** on.
3. Click **Load unpacked** and select the [`extension/`](extension) folder.
4. Pin the extension so its icon is visible.

## Daily use

```bash
./start.sh            # boots FastAPI on http://127.0.0.1:8000
```

Click the extension icon. Pick a resume from the global **Resume** picker at the top — that selection applies to all four tabs and persists across sessions. Then switch tabs as needed:

- **Cover Letter** — paste company + JD, hit Generate. PDF lands in `Downloads/` as `CoverLetter_<Company>.pdf`. An inline JD-fit score appears alongside the PDF.
- **Email** — paste company + JD + optional intent note ("ask for referral", etc.). Subject and body each have copy-to-clipboard buttons.
- **Outreach** — pick channel (LinkedIn invitation / LinkedIn message / email), paste their LinkedIn profile + optional context, hit Generate. The LinkedIn-invitation char counter goes amber near the limit and red over 290.
- **Score** — paste JD, hit Score. Get a ranked leaderboard of every resume on disk. Click any row to switch the global active resume to that one.

The popup shows a small dot indicating whether the backend is reachable (green = ok, red = offline).

## Configuration

- `ANTHROPIC_API_KEY` (**required**) — in `backend/.env`.
- `MODEL` (optional) — override the Claude model, e.g. `MODEL=claude-sonnet-4-5`. Defaults to `claude-opus-4-5`.

## Troubleshooting

- **"tectonic not found on PATH"** — `brew install tectonic` (macOS) or grab a binary from <https://tectonic-typesetting.github.io/>.
- **"ANTHROPIC_API_KEY not set"** — did you `cp backend/.env.example backend/.env` and fill it in?
- **First request is very slow** — Tectonic is downloading packages. Subsequent requests are fast.
- **LaTeX compile fails** — check the error detail in the popup or the uvicorn log. Common cause: your edited `template.tex` has a syntax error, or a stray special char (`&`, `%`, `_`) leaked into a non-escaped slot.
- **`Unknown resume_id`** — the picker is asking for a file that's not on disk. Drop the `.txt` into `backend/resumes/` or pick a different resume.
- **Popup says "offline"** — is `./start.sh` running? Curl `http://127.0.0.1:8000/` directly to confirm.

## Privacy

- The API key lives only in `backend/.env`, which is gitignored. The extension never sees it.
- The backend listens on `127.0.0.1` only by default; do not expose it without adding auth.
- `*.pdf`, your `.env`, and Python venvs are gitignored. **`backend/resumes/*.txt` and `backend/template.tex` are NOT gitignored** — replace them with placeholders before committing if you don't want your real resume / template in git.
- Resumes are sent to Anthropic as part of every request body. Don't put anything in `backend/resumes/` you wouldn't want to send to an LLM API.
