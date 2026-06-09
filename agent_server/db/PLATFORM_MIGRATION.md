# Platform Lead Table — Migration Spec

**File:** `agent_server/db/platform_migration.sql`  
**Target DB:** `apply_tools` (the platform Postgres database)  
**Authored by:** schema & infra agent  
**Applied by:** lead engineer (touches `backend/db.py` and `backend/server.py`)

---

## What this migration does

Adds eight columns to the existing `"Lead"` table and creates a partial UNIQUE
index on `domain`.  All existing columns and rows are preserved; the migration
is purely additive.

| New column | Type | Description |
|---|---|---|
| `domain` | `text` | Normalised registrable root domain (e.g. `acme.io`). Agent pipeline always sets this. |
| `companyName` | `text` | Canonical company name from the agent pipeline. |
| `fundingStage` | `text` | Funding stage label (`seed`, `series_a`, etc.). |
| `fundingAmount` | `text` | Raw string as extracted (`$2M`). |
| `founderName` | `text` | Primary founder full name. |
| `confidence` | `double precision` | Verification confidence score, 0–1. |
| `source` | `text` | Discovery source (`open_web`, `yc_oss`, `product_hunt`, `rss`). |
| `sourcesJson` | `jsonb` | Full `sources[]` array from `ResearchResult` / `VerifiedLead`. |

A **partial UNIQUE index** (`WHERE domain IS NOT NULL`) is created so that
pre-existing rows with `domain = NULL` do not conflict with each other, while
every new agent-sourced lead is deduplicated by domain.

---

## How to apply

```bash
# From the repo root, against the platform DB:
psql "$DATABASE_URL" -f agent_server/db/platform_migration.sql
```

The SQL is idempotent (`ADD COLUMN IF NOT EXISTS`, `CREATE UNIQUE INDEX IF NOT EXISTS`),
so re-running it is safe.

---

## Two new backend endpoints (lead engineer implements)

These are specified in CONTRACTS §6.  The migration is a prerequisite for both.

### POST /api/v1/leads/exists

Request: `{ "domains": ["acme.io", "stripe.com", ...] }`  
Response: `{ "known": ["stripe.com"] }`  — domains already in `"Lead"` (any row,
regardless of how the lead was created).

Implementation hint:
```sql
SELECT domain FROM "Lead"
WHERE domain = ANY(:domains) AND domain IS NOT NULL
```

### POST /api/v1/leads/upsert

Request body = `PlatformUpsertRequest`:
```json
{
  "domain": "acme.io",
  "company_name": "Acme Inc",
  "funding_stage": "seed",
  "funding_amount": "$2M",
  "founder_name": "Jane Doe",
  "founder_linkedin_url": "https://linkedin.com/in/...",
  "founder_email": "jane@acme.io",
  "confidence": 0.87,
  "source": "open_web",
  "sources": ["https://techcrunch.com/...", "https://acme.io/about"]
}
```

Response: `{ "ok": true, "lead_id": "<id>", "created": true|false }`

Column mapping on upsert:

| Request field | Lead column |
|---|---|
| `domain` | `domain` |
| `company_name` | `companyName` |
| `funding_stage` | `fundingStage` |
| `funding_amount` | `fundingAmount` |
| `founder_name` | `founderName` (also `name` when the row is new and `name` is otherwise blank) |
| `founder_email` | `email` |
| `founder_linkedin_url` | `linkedinUrl` |
| `confidence` | `confidence` |
| `source` | `source` |
| `sources` (list) | `sourcesJson` (jsonb) |

ON CONFLICT target: `(domain) WHERE domain IS NOT NULL`.  
`updatedAt` must be bumped on every update.  
The endpoint is idempotent — repeated delivery of the same domain is safe.

---

## Auth

Optional `X-Agent-Token` header (value = `PLATFORM_API_TOKEN` env var).  If the
env var is unset, the endpoint accepts unauthenticated calls (dev-friendly
default).
