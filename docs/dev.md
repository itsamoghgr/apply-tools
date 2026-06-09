# Lead-Generation Agent Service — Technical Developer Guide

A standalone service that discovers startups by reading the open web, researches
each one's funding and founder, verifies the data, and pushes only clean verified
leads to the platform backend. It stops after 50 verified leads.

This is a **separate FastAPI app** (`agent_server/`, uvicorn on **port 8001**),
independent of the existing platform backend (`backend/`, ~port 8000). They talk
over HTTP only.

> The authoritative interface spec is **`agent_server/CONTRACTS.md`**. This guide
> explains how the pieces fit and how to run/operate them. When the two disagree,
> CONTRACTS.md wins.

---

## 1. Architecture

```
                          ┌─────────────────────────────────────────────┐
  POST /api/v1/hunt ─202─▶ │ AGENT SERVER (agent_server/, uvicorn :8001) │
  GET  /api/v1/hunt/{id} ─▶ │                                            │
                          │  api/app.py ── BackgroundTasks ──┐           │
                          └──────────────────────────────────┼──────────┘
                                                             ▼
                       ORCHESTRATOR (orchestrator/loop.py — deterministic code)
                          discover ─▶ dedup ─▶ [ research ─▶ verify ─▶ deliver ]*
                            (agent)   (det.)    (agent)    (det.)    (det., outbox)
                                                  └── loop until 50 verified OR exhausted
                                                      counter++, random short sleep
                            │                                          │
        ┌───────────────────┘                                          │ HTTP upsert
        ▼ operational                                                  ▼
  ┌──────────────┐                                          ┌────────────────────────┐
  │ apply_agent  │  jobs, checkpoints, seen_cache,          │ PLATFORM (backend/:8000)│
  │ (Postgres)   │  outbox, audit_traces                    │  POST /leads/exists      │
  └──────────────┘  NEVER a copy of clean leads             │  POST /leads/upsert      │
                                                            │  → apply_tools (truth)   │
                                                            └────────────────────────┘
```

**Agency lives in the leaves, determinism in the trunk.** Only two stages are
agentic LLM agents (discovery, research). Everything else — the orchestrator loop,
dedup, verification, delivery — is plain deterministic Python. The orchestrator is
**not** an LLM; it's a `while` loop with a counter and stop conditions.

### Two databases, logically separate
- **Platform DB (`apply_tools`)** — single source of truth for verified leads.
  The agent server never touches it directly; only via the two HTTP endpoints.
- **Agent DB (`apply_agent`)** — operational only: `jobs`, `checkpoints`,
  `seen_cache` (dedup memory), `outbox` (reliable delivery), `audit_traces`. It is
  never the source of truth and never stores a permanent second copy of leads.

---

## 2. Module map

| Path | Responsibility |
|---|---|
| `agent_server/CONTRACTS.md` | Frozen interface spec (records, web tools, DB, API). |
| `contracts/records.py` | `CandidateCompany`, `ResearchResult`, `VerifiedLead`, `PlatformUpsertRequest` (Pydantic v2). |
| `config.py` | All env-driven config in one frozen `Config` (`CONFIG`). |
| `log.py` | structlog setup (mirrors `backend/log.py`). Named `log.py` to avoid shadowing stdlib `logging`. |
| `db/agent_db.py` | Typed helpers over the agent DB (jobs/checkpoints/seen/outbox/audit). SQLAlchemy + psycopg v3. |
| `migrations/` | Agent-DB SQL migrations + a tiny `run.py` runner (tracks applied files in `_migrations`). |
| `db/platform_migration.sql` + `PLATFORM_MIGRATION.md` | The additive `Lead`-table restructure applied to the platform DB. |
| `web/__init__.py` | Frozen `search()` / `fetch_page()` interface + `SearchResult` / `FetchedPage`. |
| `web/search.py` | ddgs key-less search with backoff-retry. Never raises; `[]` on persistent failure. |
| `web/fetch.py` | httpx + readability extraction; headless (Playwright) only for JS-heavy content; **hard-refuses LinkedIn**. |
| `web/verifier.py` | `EmailVerdict` + `Verifier` interface for the verification waterfall. |
| `stages/normalize.py` | `normalize_domain()` + `NON_COMPANY_HOSTS` blocklist. One implementation, used everywhere. |
| `stages/dedup.py` | Normalize → self-dedup → seen-cache filter → platform-exists filter. Deterministic. |
| `stages/platform_client.py` | The one httpx client for the platform API (`leads_exists`, `leads_upsert`). |
| `stages/verify.py` | LinkedIn plausibility + email waterfall (Hunter → Abstract → SMTP) → continuous confidence score. |
| `stages/deliver.py` | Outbox write → platform upsert → mark sent / mark failed; `retry_pending()`. |
| `agents/deps.py` | `AgentDeps` — the dependency bundle injected into the runtime agents. |
| `agents/llm.py` | Thin Messages-API wrapper. Bedrock (Claude Sonnet) **or** direct Anthropic, by `AGENT_LLM_PROVIDER`. |
| `agents/discovery.py` | Discovery agent: open-web tool loop (core) + structured floor (YC/PH/RSS). |
| `agents/sources/{yc,producthunt,rss}.py` | Structured-floor connectors. |
| `agents/research.py` | Research agent: funding + founder + LinkedIn-from-snippets; shortcut on structured funding. |
| `api/app.py`, `api/main.py` | FastAPI app + uvicorn entrypoint. |
| `orchestrator/loop.py` | `run_pipeline()` — the deterministic loop + `Stages` bundle. |
| `orchestrator/runner.py` | `build_stages(job_id)` (real wiring) + `build_stub_stages()` + `launch_pipeline()`. |

---

## 3. The data contract between stages

```
discovery → CandidateCompany(name, domain, source, funding_stage?, funding_amount?, …)
   dedup  → list[CandidateCompany]  (normalized, novel)
 research → ResearchResult(domain, name, funding_*, founder_name?, founder_linkedin_url?, sources, used_shortcut)
   verify → VerifiedLead(…, founder_email?, confidence: float 0–1, verification_detail, sources)
  deliver → PlatformUpsertRequest → POST /api/v1/leads/upsert  (ON CONFLICT (domain) DO UPDATE)
```

`confidence` is a **continuous score, never a boolean**. Delivery sends regardless;
the platform records the score so a human can triage.

---

## 4. The two agentic leaves

Both are LLM agents using the **same shared toolset** (`search`, `fetch_page`) via
`AgentDeps`, with a **bounded tool-use loop** (discovery ≤ 12 tool calls, research
≤ 10). They emit Anthropic-shaped tools and parse a final JSON answer tolerantly.

- **Discovery** (`run_discovery`): open-web search + reading is the **core**
  differentiator (fresh, long-tail leads). The structured floor (YC OSS API,
  Product Hunt API, funding RSS) is the cheap reliable baseline, merged in. On LLM
  failure it degrades to the floor — the pipeline never crashes.
- **Research** (`run_research`): narrower goal — funding stage/amount, founder name,
  founder LinkedIn URL. **Shortcut rule:** if the candidate already carries
  structured funding, don't re-derive it (`used_shortcut=True`). **LinkedIn rule:**
  the founder's LinkedIn URL is extracted from **public search snippets only** —
  the profile page is never fetched (and `fetch_page` hard-refuses LinkedIn as a
  safety net).

### Tool-use message threading (important)
The Messages API (and Bedrock) require: after an assistant turn with `tool_use`
blocks, the next user turn must contain a `tool_result` for **every** `tool_use`
id, and the assistant turn must carry the actual `tool_use` blocks. Both loops
enforce this — the per-batch cap is applied at the *turn* level, never mid-batch,
so no `tool_use` is ever left without a `tool_result`. Regression tests use a
protocol-enforcing fake LLM (`TestToolUseMessageThreading`).

---

## 5. Verification waterfall

`verify()` produces a continuous confidence in `[0,1]`:

```
confidence = 0.55·email_score + 0.20·linkedin_plausibility
           + 0.15·has_founder_name + 0.10·has_funding
```

- **Email** (primary): tries free-tier APIs in `VERIFY_PROVIDERS` order
  (`hunter`, `abstract`), each behind its key. A provider with no key is skipped.
  **SMTP is the last, weak fallback** — it assumes residential port 25 is blocked
  and most targets are accept-all, so even a 250 yields only a low score.
- **LinkedIn**: structural plausibility of the `/in/<slug>` URL — treated as
  plausibility, not proof. Never fetched.

With no API keys configured the gate still returns a `VerifiedLead` scored from
structure alone, so the pipeline runs end-to-end keyless (lower confidence).

---

## 6. Reliable delivery (outbox pattern)

`deliver()`:
1. Write the lead to the agent-DB `outbox` as `pending` (durable first).
2. If `dry_run`, stop (outbox only).
3. Else upsert to the platform; on success mark `sent` + record the domain
   `verified` in `seen_cache`; on `PlatformUnreachable` mark `failed` (still in the
   outbox, retryable).

`retry_pending()` re-attempts `pending`+`failed` rows. The platform upsert is
idempotent (`ON CONFLICT (domain)`), so re-sending is harmless. **No verified lead
is ever lost** even if the platform is down mid-run.

---

## 7. The platform side (two new endpoints)

Applied to the **existing** backend (`backend/db.py` + `backend/server.py`) plus an
additive `Lead`-table migration (`agent_server/db/platform_migration.sql`):

- `POST /api/v1/leads/exists` `{domains:[…]}` → `{known:[…]}` — seeds the seen-cache.
- `POST /api/v1/leads/upsert` (PlatformUpsertRequest) → `{ok, lead_id, created}` —
  idempotent, keyed on the new `domain` column (partial unique index).

Optional shared secret via `X-Agent-Token` (`PLATFORM_API_TOKEN`); unset = open
(dev default). New `Lead` columns: `domain` (unique), `companyName`, `fundingStage`,
`fundingAmount`, `founderName`, `confidence`, `source`, `sourcesJson`.

---

## 8. Running it locally

### Prereqs
- Postgres on `localhost:5432` with role `apply` and both DBs `apply_tools`
  (platform) and `apply_agent` (agent).
- The platform backend running on `:8000` with the two new endpoints + migration.

### Setup
```bash
# 1. Agent DB
createdb -h localhost -U apply apply_agent
AGENT_DATABASE_URL=postgresql://apply:apply@localhost:5432/apply_agent \
  python -m agent_server.migrations.run

# 2. Platform migration (against apply_tools)
psql "$DATABASE_URL" -f agent_server/db/platform_migration.sql

# 3. Agent server venv
python -m venv agent_server/venv
agent_server/venv/bin/pip install -e ./agent_server

# 4. Config
cp agent_server/.env.example agent_server/.env   # then edit
```

### Configure the LLM (`agent_server/.env`)
- **AWS Bedrock (recommended, mirrors the platform):**
  ```
  AGENT_LLM_PROVIDER=bedrock
  AWS_ACCESS_KEY_ID=…
  AWS_SECRET_ACCESS_KEY=…
  BEDROCK_REGION=us-east-1
  BEDROCK_MODEL=us.anthropic.claude-sonnet-4-5-20250929-v1:0
  ```
- **Direct Anthropic:** `AGENT_LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY=…`.

(With neither, discovery/research degrade gracefully to the structured floor.)

### Run + drive a hunt
```bash
agent_server/venv/bin/python -m agent_server.api.main        # serves :8001

curl -s -XPOST localhost:8001/api/v1/hunt \
  -H 'content-type: application/json' \
  -d '{"target_count":3,"query_hint":"recently funded AI dev tools startups"}'
# → {"job_id":"…","status":"pending"}

curl -s localhost:8001/api/v1/hunt/<job_id>   # live progress
```
Add `"dry_run": true` to run the full pipeline but write only to the outbox (no
platform push).

---

## 9. Tests

```bash
cd <repo root>
agent_server/venv/bin/python -m pytest tests/ -q     # 212 tests
```
- Pure-logic tests (records, normalize, dedup, verify, deliver, web, agents) run
  fully offline with mocks/respx — no DB, no network, no LLM.
- DB-touching tests request the `live_db` fixture, which **skips cleanly** if
  `apply_agent` is unreachable. `conftest.py` truncates operational tables before
  each live-DB test for isolation.

---

## 10. Operational notes

- **Stop conditions:** the loop stops on `verified_count >= target_count` (50 by
  default) **or** when the candidate list is exhausted — it never indexes past the
  end. `stop_reason` is `target_reached` / `exhausted` / `error`.
- **Checkpoints:** a `loop` checkpoint with the cursor is written each iteration,
  so a run is resumable in principle.
- **Resilience:** a single bad candidate (LLM error, tool failure, unreachable
  platform) is logged + audited and never aborts the run. The trunk catches
  per-candidate exceptions; only a top-level failure marks the job `failed`.
- **Logging:** structlog, console by default, JSON when `ENV=production` or
  `LOG_FORMAT=json`. Every stage emits `audit_traces` rows you can query for a full
  reasoning/tool trace per job.
