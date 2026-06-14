# Cover Letter Generator — Development Plan

A personal browser extension that generates tailored, LaTeX-compiled PDF cover letters from a company name and job description.

---

## Stack at a glance

- **Frontend:** Browser extension (Manifest V3, vanilla JS)
- **Backend:** Local Python (FastAPI) on `localhost:8001`
- **LLM:** Claude API (Anthropic SDK)
- **LaTeX:** Tectonic (single-binary compiler)
- **Source of truth:** Your `.tex` template + your resume text

---

## Phase 0 — Prerequisites (30 minutes)

Before writing any code, get the environment ready.

**Install:**
- Python 3.10+
- Node.js (only needed if you want to lint/test the extension; not strictly required)
- Tectonic — `brew install tectonic` (Mac), or download the binary for Linux/Windows
- A Chrome/Brave/Edge browser for loading the unpacked extension

**Get:**
- An Anthropic API key from console.anthropic.com
- Your `.tex` cover letter template
- Your resume as text (extract it from PDF once, save as `resume.txt`)

**Verify:**
```bash
tectonic --version
python3 --version
```

If both work, you're done with Phase 0.

---

## Phase 1 — Project scaffold (15 minutes)

Set up the directory structure and empty files. No logic yet.

```
cover-letter-gen/
├── backend/
│   ├── server.py
│   ├── generate.py
│   ├── latex_utils.py
│   ├── template.tex          # your template, with placeholders
│   ├── resume.txt            # your background, plain text
│   ├── requirements.txt
│   └── .env                  # ANTHROPIC_API_KEY=sk-...
├── extension/
│   ├── manifest.json
│   ├── popup.html
│   ├── popup.js
│   ├── popup.css
│   └── icon.png
├── start.sh
├── .gitignore                # ignore .env, __pycache__, *.pdf, *.aux, etc.
└── README.md
```

**Create the venv:**
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn anthropic python-dotenv
pip freeze > requirements.txt
```

**Done when:** the structure exists and the venv is active.

---

## Phase 2 — Template surgery (30–60 minutes)

This is the most important manual step. You're converting your static `.tex` into a fillable template.

**Decide what's static vs. dynamic.** Static = your name, address, email, signature, date logic, formatting. Dynamic = anything that should change per application.

**Replace dynamic parts with placeholders.** Use clear, unambiguous tokens:

```latex
\textbf{{{COMPANY_NAME}}}
\\
{{COMPANY_ADDRESS_OR_BLANK}}

Dear {{HIRING_MANAGER_OR_TEAM}},

{{BODY_PARAGRAPHS}}

Sincerely,\\
Your Name
```

**Recommended placeholders for v1:**
- `{{COMPANY_NAME}}` — for the address block and any in-body mentions
- `{{ROLE_TITLE}}` — extracted from the JD
- `{{HIRING_MANAGER_OR_TEAM}}` — defaults to "Hiring Team" if unknown
- `{{BODY_PARAGRAPHS}}` — the entire tailored body, 3 paragraphs of LaTeX-safe prose

**Why one big `{{BODY_PARAGRAPHS}}` instead of `{{BODY_1}}`, `{{BODY_2}}`, `{{BODY_3}}`?**
You said you want Claude to decide structure based on the JD. A single body block lets it write 2 paragraphs for a short JD or 4 for a senior role, instead of being forced into a fixed shape.

**Done when:** `template.tex` compiles cleanly with `tectonic` after you manually substitute test values for every placeholder. **Test this before moving on.**

---

## Phase 3 — LaTeX utilities (30 minutes)

`backend/latex_utils.py` — small, boring, critical.

**Two functions:**

1. `escape_latex(text: str) -> str` — escapes `& % $ # _ { } ~ ^ \` so user-supplied or LLM-supplied strings can't break compilation. Apply to `COMPANY_NAME`, `ROLE_TITLE`, `HIRING_MANAGER_OR_TEAM`. **Do not** apply to `BODY_PARAGRAPHS` if you want Claude to use `\emph{}` etc. — instead, instruct Claude in the prompt to only emit a whitelist of LaTeX commands.

2. `compile_latex(tex_source: str, workdir: Path) -> bytes` — writes the `.tex`, runs `tectonic` as a subprocess, reads the resulting PDF, returns the bytes. Cleans up aux files. Raises a clear error with `tectonic`'s stderr if compilation fails.

**Done when:** unit-test by feeding it a substituted template and getting back PDF bytes you can write to disk and open.

---

## Phase 4 — Claude integration (1–2 hours)

`backend/generate.py` is the brain. This is where most of the quality work happens.

**The core function:**
```
generate_cover_letter(company: str, jd: str) -> bytes  # returns PDF
```

**Steps inside it:**

1. Load `resume.txt` and `template.tex` from disk.
2. Build a structured prompt (see below).
3. Call Claude with `claude-opus-4-5` or `claude-sonnet-4-5`.
4. Parse the JSON response.
5. Substitute into the template.
6. Compile via `latex_utils.compile_latex`.
7. Return PDF bytes.

**The prompt — the part that matters most.** It should:
- Give Claude the resume text as context ("here's everything true about the candidate")
- Give it the JD and company name
- Ask it to **first analyze** what the JD emphasizes (skills? culture? specific tech?)
- Ask it to **decide** how many body paragraphs make sense (2–4)
- Ask it to **select** real items from the resume that match
- Ask it to return strict JSON: `{role_title, hiring_manager, body_paragraphs}`
- Forbid invention — only frame existing resume content
- Restrict LaTeX to a whitelist: `\emph{}`, `\textbf{}`, `\\`, blank lines for paragraph breaks. Nothing else.

Use Claude's `response_format` or just instruct "respond with JSON only, no markdown fences." Parse defensively — strip ` ```json ` fences if they appear.

**Done when:** running `python -c "from generate import generate_cover_letter; open('test.pdf','wb').write(generate_cover_letter('Anthropic', '...JD here...'))"` produces a sensible, JD-tailored PDF.

---

## Phase 5 — Local server (30 minutes)

`backend/server.py` — FastAPI, one endpoint.

**`POST /generate`**
- Body: `{"company": "...", "job_description": "..."}`
- Calls `generate_cover_letter()`
- Returns the PDF bytes with `Content-Type: application/pdf` and `Content-Disposition: attachment; filename="CoverLetter_<company>.pdf"`

**CORS:** allow `chrome-extension://*` origins, or just `*` since this is localhost-only and personal.

**Health check:** `GET /` returns `{"ok": true}` so the extension can verify the server is up.

**`start.sh`:**
```bash
#!/bin/bash
cd "$(dirname "$0")/backend"
source venv/bin/activate
uvicorn server:app --host 127.0.0.1 --port 8001 --reload
```

**Done when:** `./start.sh` boots the server and `curl -X POST localhost:8001/generate -H 'Content-Type: application/json' -d '{"company":"Anthropic","job_description":"..."}' --output test.pdf` works.

---

## Phase 6 — Browser extension (1–2 hours)

The frontend. Keep it dumb — it's a thin client.

**`manifest.json`** — Manifest V3, permissions: `["storage"]` (just for remembering API endpoint if needed), `host_permissions: ["http://localhost:8001/*"]`.

**`popup.html`** — Two fields and a button:
- Input: company name (one-liner)
- Textarea: job description (large, scrollable)
- Button: "Generate"
- Status area: shows "Generating…", "Done!", or error messages
- Optional: a small status dot showing if `localhost:8001` is reachable

**`popup.js`** — On Generate click:
1. Disable button, show "Generating… (this takes ~15s)"
2. `fetch('http://localhost:8001/generate', {method:'POST', body: JSON.stringify({company, job_description})})`
3. Get the PDF blob
4. Trigger a download via `chrome.downloads.download({url: blobUrl, filename: 'CoverLetter_<company>.pdf', saveAs: false})`
5. Show success, re-enable button

**Loading the extension:** `chrome://extensions` → Developer Mode → Load unpacked → select `extension/` folder.

**Done when:** clicking the icon, pasting a JD, hitting Generate, and getting a PDF in your Downloads folder all works end-to-end.

---

## Phase 7 — Polish (1 hour, ongoing)

Things you'll want once you're using it for real:

- **Filename includes role**, not just company: `CoverLetter_Anthropic_MLEngineer.pdf`. Have Claude return a slug.
- **Preview before download** — let the popup show the generated body text in a `<details>` block before saving the PDF, so you can spot bad outputs without opening the file.
- **Regenerate button** — if the first attempt is mid, hit it again without re-pasting.
- **Edit-then-compile** — a textarea showing the body Claude wrote, with a "Recompile with edits" button. This is the single biggest quality-of-life upgrade and worth building once you've used the basic version a few times.
- **History** — save the last N generations to a local SQLite or just JSON file, so you can reuse phrasing.
- **Cost tracking** — log token counts per request so you know what you're spending.

---

## Phase 8 — Hardening (optional)

Only if/when you start sharing it:

- Move the API key to a backend `.env` that the extension can't see (already true in this design — good)
- Add basic auth or a shared secret on `/generate` if you ever expose the backend beyond localhost
- Containerize with Docker (Tectonic + Python in one image) for easy deploy
- Replace `chrome.downloads` flow with a proper "save dialog" if you want more control

---

## Rough time budget

| Phase | Time |
|---|---|
| 0. Prereqs | 30 min |
| 1. Scaffold | 15 min |
| 2. Template | 30–60 min |
| 3. LaTeX utils | 30 min |
| 4. Claude integration | 1–2 hr |
| 5. Local server | 30 min |
| 6. Extension | 1–2 hr |
| 7. Polish | ongoing |

**Total to working v1:** ~5–7 focused hours.

---

## Risks & gotchas to watch for

- **LaTeX escaping is sneaky.** Test with a company name like `AT&T` and a JD that mentions `100% remote` early — those break naive substitution.
- **Claude sometimes wraps JSON in markdown fences** even when told not to. Strip them defensively.
- **Tectonic downloads packages on first run.** First compilation will be slow (30s+). Subsequent ones are fast (~2s).
- **Extension popups close on focus loss.** If your fetch is in flight when the popup closes, the download still completes but you lose the UI feedback. Consider moving the fetch to a service worker if this annoys you.
- **Token budget.** A long JD + your full resume can run 3–5k input tokens. Cheap, but be aware.
- **Don't over-tailor.** A cover letter that name-drops every JD keyword reads like ChatGPT slop. The prompt should explicitly tell Claude to be selective and human.

---

## What to build first

If you want to validate the riskiest part first, build **Phase 2 + 3 + 4** before touching the extension. Get a Python script that takes a company name and JD string and spits out a PDF. Once that works and the output is good, the extension is just a 90-minute UI wrapper around it.
