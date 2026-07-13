# FROZEN CONTRACTS — Lead-Generation Agent Service

> **This file is the single source of truth.** Every sub-agent codes against the
> shapes and signatures here. Do not change a frozen contract without routing the
> change through the lead engineer (orchestrator), because multiple agents depend
> on each one. If you find a contract is wrong, STOP and flag it — don't silently
> diverge.
>
> Layout note: this is a SEPARATE FastAPI app. The package is `agent_server/`
> (importable, sibling to `backend/`). Tests live in top-level `tests/`, docs in
> top-level `docs/`. It runs as its own uvicorn process on **port 8002** and
> talks to the existing `backend/` (port ~8001) over HTTP only.

Status legend: 🧊 FROZEN (do not change) · 🔧 owned by one agent (internal).

---

## 0. Big picture

```
POST /api/v1/hunt ──202+job_id──> background runner ──┐
                                                      │
   ┌──────────────────────────────────────────────────┘
   ▼
ORCHESTRATOR (deterministic code loop — NOT an LLM)
   1. discovery agent (agentic)  -> List[CandidateCompany]
   2. dedup (deterministic)      -> survivors
   3. for each survivor, until count==50 or list exhausted:
        fit gate (cheap, 1 LLM)  -> FitVerdict; FAIL → seen_add(skipped)+continue
        research agent (agentic) -> ResearchResult (+brief, fit_score, …)
        verification (det.)      -> VerifiedLead (with confidence score)
        delivery (det., outbox)  -> upsert to PLATFORM via API
        count++, sleep(random short)
```

Two processes: **platform backend** (existing, `backend/`, port ~8001) and
**agent server** (new, `agent_server/`, uvicorn on **port 8002**). Two Postgres
DBs, logically separate:
- **platform DB** (`apply_tools`) = source of truth for verified leads.
- **agent DB** (`apply_agent`) = operational only: jobs, checkpoints, seen-cache,
  outbox, audit. Never a second permanent copy of clean leads.

Logging: **structlog** everywhere (`from agent_server.log import get_logger`).
NOTE: the module is `log.py` (NOT `logging.py`) to avoid shadowing stdlib `logging`.

---

## 1. 🧊 Internal stage record shapes

Defined in `agent_server/contracts/records.py` (Pydantic v2). Authoritative.

### 1.1 CandidateCompany  (output of discovery, input to dedup)
`name, domain (NORMALIZED root), source ("open_web"|"yc_oss"|"product_hunt"|"rss"),
source_url?, funding_stage?, funding_amount?, description?, discovered_at`.
Emitted keyed by `domain`. Noisy extraction expected.

### 1.2 ResearchResult  (output of research, input to verification)
`domain, name, funding_stage?, funding_amount?, founder_name?,
founder_linkedin_url? (PUBLIC SNIPPETS ONLY),
employee_count?, revenue?, location?, industry?, last_round_date?,
brief?, founding_year?, total_raised?, investors[], competitors[], key_people[],
fit_score?, fit_reason?, sources[], used_shortcut`.

### 1.3 VerifiedLead  (output of verification, input to delivery)
`domain, name, funding_stage?, funding_amount?, founder_name?,
founder_linkedin_url?, founder_email?, employee_count?, revenue?, location?,
industry?, last_round_date?, brief?, founding_year?, total_raised?, investors[],
competitors[], key_people[], fit_score?, fit_reason?,
confidence (0–1 SCORE, never bool), verification_detail{}, sources[]`.
Delivery sends regardless; platform stores score.

### 1.4 FitVerdict  (cheap fit-gate decision, produced BEFORE research)
`passed: bool, score: float (0–1), reason: str`. Produced by
`agent_server.stages.fit_gate.run_fit_gate`. `passed=False` → the orchestrator
SKIPS the company: records `seen_add(domain,"skipped",reason="fit:<score>")` +
audits `fit/skipped`, bumps `jobs.skipped_count`, and NEVER saves it. The gate
NEVER raises — on any error it fails OPEN (`passed=True`). With empty
`fit_criteria` it returns pass-through (`passed=True, reason="no_criteria"`, no
LLM call).

**Deep-research fields** (added on ResearchResult / VerifiedLead /
PlatformUpsertRequest, ALL optional so partial research never breaks the
pipeline): `brief: str|None`, `founding_year: str|None`, `total_raised: str|None`,
`investors: list[str]=[]`, `competitors: list[str]=[]`, `key_people: list[str]=[]`,
`fit_score: float|None`, `fit_reason: str|None`. On the platform `Lead` table
these map to `brief, foundingYear, totalRaised, investorsJson, competitorsJson,
keyPeopleJson, fitScore, fitReason` (applied OUT-OF-BAND via
`agent_server/db/deep_research_migration.sql`).

---

## 2. 🧊 Shared web-tooling interfaces

Defined in `agent_server/web/__init__.py`; impls in `web/search.py`, `web/fetch.py`.

```python
@dataclass
class SearchResult: title: str; url: str; snippet: str

def search(query: str, *, max_results: int = 10) -> list[SearchResult]:
    "Free, key-less (ddgs). Backoff-retry. Returns [] on persistent failure; never raises. Never JS-rendered."

@dataclass
class FetchedPage: url: str; final_url: str; title: str|None; text: str; ok: bool; status: int|None

def fetch_page(url: str, *, render_js: bool = False) -> FetchedPage:
    "HTTP GET + readability. Headless ONLY when render_js=True and only for content sites. HARD-REFUSES linkedin.com/in/ (ok=False)."
```

---

## 3. 🧊 Domain normalization (shared, deterministic)

`agent_server/stages/normalize.py`:
```python
def normalize_domain(raw: str) -> str | None:
    "Lowercase; strip scheme/www/path/query/port; return registrable root domain.
     Return None for non-company hosts (linkedin/twitter/x/facebook/medium/github/
     youtube/crunchbase/...) via a shared NON_COMPANY_HOSTS blocklist."
```
ONE implementation, used by discovery, dedup, research, delivery.

---

## 4. 🧊 Agent DB schema (Postgres, DB `apply_agent`)

Owner: schema & infra agent. SQL migrations in `agent_server/migrations/`.
Operational ONLY. All timestamps `timestamptz`, UTC.

- **jobs**: id(PK,text), status(pending|running|succeeded|failed|stopped),
  target_count, verified_count, candidates_total, candidates_processed,
  stop_reason(target_reached|exhausted|error)?, error?, created_at, updated_at,
  finished_at?.
- **checkpoints**: id(bigserial), job_id(FK), stage(discovery|dedup|research|
  verify|deliver|loop), cursor(int)?, state(jsonb), created_at.
- **seen_cache**: domain(PK,text), outcome(verified|dropped), reason?, job_id?,
  seen_at. Seeded/refreshed from platform exists endpoint + updated as we go.
- **outbox**: id(bigserial), job_id(FK), domain, payload(jsonb =
  PlatformUpsertRequest), status(pending|sent|failed), attempts, last_error?,
  created_at, sent_at?.
- **audit_traces**: id(bigserial), job_id(FK), domain?, stage, event, data(jsonb),
  created_at.

`agent_server/db/agent_db.py` exposes typed helpers (create_job, update_job,
get_job, add_checkpoint, seen_has/seen_add/seen_bulk_add, outbox_add/
outbox_pending/outbox_mark_sent/outbox_mark_failed, audit_add). SQLAlchemy +
psycopg v3, mirroring `backend/db.py` connection style but pointing at
`AGENT_DATABASE_URL`.

---

## 5. 🧊 HTTP API of the agent server (port 8002)

Owner: API & orchestrator agent. FastAPI (`agent_server/api/app.py`).

- **POST /api/v1/hunt** body `{target_count?, query_hint?, fit_criteria?, dry_run?}`
  → **202** `{job_id, status:"pending"}` IMMEDIATELY; pipeline runs as background
  task. `dry_run=true` → outbox only, no platform push. `fit_criteria` is the ICP
  for the cheap fit gate (defaults to `query_hint`; both empty → pass-through, no
  skipping).
- **GET /api/v1/hunt/{job_id}** → `{job_id,status,verified_count,skipped_count,
  target_count,candidates_total,candidates_processed,stop_reason,created_at,
  updated_at,finished_at}`. 404 if unknown.
- **GET /health** → `{"status":"ok"}` (also accept HEAD).
- **POST /api/v1/companies/roster** body `{domain?, company?, roles?:[...]}` → `{domain, company, people:[{name,title,email,score,method}], count}`. Role-filtered roster (Hunter enumerate, name-free) + open-web-first per-person email; never 500s — empty roster `{...,"people":[],"count":0}` on no domain/no people. *(Added post-freeze, same precedent as verify/email & seen/drop below.)*

---

## 6. 🧊 Platform API contract (TWO new endpoints on EXISTING backend)

Agent server is the CLIENT. Base `PLATFORM_API_BASE`. Optional `X-Agent-Token`
(`PLATFORM_API_TOKEN`); if unset, no auth. Client lives in
`agent_server/stages/deliver.py` (delivery owner).

- **POST /api/v1/leads/exists** `{domains:[...]}` → `{known:[...]}` (verified OR
  previously seen). Seeds seen-cache.
- **POST /api/v1/leads/upsert** body = PlatformUpsertRequest:
  `{domain, company_name, funding_stage?, funding_amount?, founder_name?,
  founder_linkedin_url?, founder_email?, confidence, source, sources[]}` →
  `{ok, lead_id, created}`. Keyed `ON CONFLICT (domain) DO UPDATE`; idempotent.

---

## 7. 🧊 Platform `Lead` table — RESTRUCTURED (owner-approved)

Owner decision: restructure the platform leads table as required. The platform
backend (`backend/`) gets a migration adding domain-keyed company/funding/founder/
confidence fields + a UNIQUE index on `domain`. Existing columns preserved.

New columns on `"Lead"`: `domain text UNIQUE`, `companyName text`,
`fundingStage text`, `fundingAmount text`, `founderName text`,
`confidence double precision`, `source text`, `sourcesJson jsonb`.
Mapping on upsert: founder_email→email, founder_linkedin_url→linkedinUrl,
founder_name→name (and founderName), company_name→companyName.

Owner: schema & infra agent writes the platform migration + specifies the two
endpoints; lead engineer integrates them into `backend/server.py` + `backend/db.py`.

---

## 8. 🧊 Runtime-agent contract (discovery & research)

LLM agents (Anthropic `claude-*`) with the shared web tools as their toolset, in
`agent_server/agents/`.

```python
def run_discovery(job_id, *, query_hint, target, deps: AgentDeps) -> list[CandidateCompany]
def run_research(job_id, candidate: CandidateCompany, deps: AgentDeps, fit_criteria: str = "") -> ResearchResult
# Cheap fit gate (stages/fit_gate.py) — runs BEFORE research; never raises (fails open):
def run_fit_gate(job_id, candidate: CandidateCompany, fit_criteria: str, *, deps: AgentDeps) -> FitVerdict
```

Deep research = the existing extraction PLUS, in the SAME run (no double-search),
a qualitative `brief`, structured `founding_year/total_raised/investors/
competitors/key_people`, and an authoritative `fit_score/fit_reason` scored
against `fit_criteria` over the already-accumulated evidence (~1–2 extra LLM
calls). `MAX_TOOL_CALLS = CONFIG.deep_research_tool_budget` (default 20).

`AgentDeps` (frozen, `agents/deps.py`): `search, fetch_page, llm (LLMClient),
audit(stage,event,data), normalize_domain`.

- Discovery: open-web search+read is the CORE; structured floor (YC OSS API,
  Product Hunt API, funding RSS) is the cheap baseline, merged in.
- Research: shortcut on structured stage/funding; LinkedIn URL from snippets ONLY.
- Orchestrator (trunk) owns loop/counter/sleep/stop/dedup/verify/deliver. Agents
  NEVER do those.

---

## 9. Module ownership map (no two agents edit the same file)

| path | owner |
|---|---|
| `contracts/*`, `log.py`, `config.py`, `agents/deps.py`, `web/__init__.py`, `web/verifier.py` | lead engineer — FROZEN/skeleton |
| `db/agent_db.py`, `migrations/*`, platform migration spec | schema & infra agent |
| `web/search.py`, `web/fetch.py` | web-tooling agent |
| `api/app.py`, `api/main.py`, `orchestrator/runner.py`, `orchestrator/loop.py` | API & orchestrator agent |
| `agents/discovery.py`, `agents/sources/*`, `agents/llm.py` | discovery-agent builder |
| `agents/research.py` | research-agent builder |
| `stages/normalize.py`, `stages/dedup.py` | dedup builder |
| `stages/verify.py`, `stages/deliver.py` | verification & delivery builder |
| `backend/server.py`, `backend/db.py` (2 endpoints + migration) | lead engineer integrates |
| `tests/*`, `docs/dev.md`, `docs/dev_new.md` | QA & docs agent |

Shared touch-points are READ-ONLY for everyone except the lead engineer. Need a
new field? Ask the lead engineer.

---

## 10. Build order (phases)

- **Phase 0**: frozen contracts (this file + the skeleton modules) + repo skeleton
  + DB schema/migrations. ← we are here
- **Phase 1**: API + orchestrator skeleton with STUBBED stages — whole loop runs
  end to end with fakes, returns 202, loops, writes DB, stops correctly.
- **Phase 2**: shared web tooling + deterministic core (dedup, verification, outbox).
- **Phase 3**: agentic leaves — discovery (open-web + structured floor) + research.
- **Phase 4**: integration, a real small end-to-end run, docs (dev.md + dev_new.md).

Every sub-agent delivers WITH TESTS and a one-paragraph summary of what it built
and any assumptions.
