# Apply Tools — tracking sidecar

A tiny FastAPI service that handles email open + click tracking on the public internet so the local Apply Tools backend can stay on `localhost`. Deploys to Render's free tier with Neon Postgres for storage.

## Endpoints

| Path | Auth | Purpose |
|---|---|---|
| `GET /track/open/{path}` | none (mail-client GET) | Records an open and returns a 1×1 PNG. |
| `GET /track/click/{path}` | none (mail-client GET) | Records a click and 302-redirects to the real URL. |
| `GET /events/{reach_out_id}` | `Authorization: Bearer <token>` | List of events for one reach-out. |
| `POST /aggregates` | `Authorization: Bearer <token>` | Batched counts + last-seen for a list of reach-out ids. |

The local backend's [proxy route](../frontend/src/app/api/proxy/[...path]/route.ts) forwards dashboard requests through the local FastAPI server, which adds the bearer token and calls this sidecar. Mail clients hit `/track/*` directly.

## One-time deployment

### 1. Create a Neon Postgres project

1. Sign up at <https://neon.tech> (free, no credit card).
2. Create a project. Region close to your Render region (Oregon → AWS US-West).
3. Copy the **pooled** connection string (Neon's dashboard labels it "Pooled connection"). It looks like:
   ```
   postgresql://user:pass@ep-xxx-pooler.us-west-2.aws.neon.tech/neondb?sslmode=require
   ```

### 2. Generate the shared secrets

Run on your laptop:

```bash
# Fernet key (must match the one in backend/.env so URLs decode)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# API token (any random string — used for /events and /aggregates auth)
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Save both. You'll paste them into Render and `backend/.env` next.

### 3. Deploy to Render

1. Push this repo to GitHub.
2. Open <https://dashboard.render.com/select-repo?type=blueprint> → pick the repo.
3. Render reads [`tracking-sidecar/render.yaml`](./render.yaml) and offers to create the service.
4. Set the four secrets when prompted:
   - `DATABASE_URL` — Neon connection string from step 1
   - `TRACKING_FERNET_KEY` — value from step 2
   - `TRACKING_API_TOKEN` — value from step 2
   - `PUBLIC_BASE_URL` — Render generates a `*.onrender.com` URL on first deploy; set this to that URL (e.g. `https://apply-tools-tracker.onrender.com`). You'll need to redeploy once after setting it.
5. First deploy takes ~3 minutes (Docker build + cold start).

The service auto-creates the `tracking_events` table on first boot via `_ensure_schema()` in [`main.py`](./main.py).

### 4. Wire up the local backend

In [`backend/.env`](../backend/.env) add:

```env
TRACKING_BASE_URL=https://apply-tools-tracker.onrender.com
TRACKING_FERNET_KEY=<same value as Render>
TRACKING_API_TOKEN=<same value as Render>
```

Restart `./start.sh`. The Reach Out page header should flip to "Tracking active via apply-tools-tracker.onrender.com".

## Local dev (run the sidecar against a local Postgres)

```bash
cd tracking-sidecar
docker run --rm -d --name tracker-pg -p 5432:5432 -e POSTGRES_PASSWORD=local postgres:16
export DATABASE_URL=postgresql://postgres:local@127.0.0.1:5432/postgres
export TRACKING_FERNET_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export TRACKING_API_TOKEN=local-dev-token
export PUBLIC_BASE_URL=http://127.0.0.1:9000
pip install -r requirements.txt
uvicorn main:app --port 9000
```

You can pair this with a tunnel for end-to-end testing without re-deploying — but for the prod path, deploying to Render is what eliminates the ngrok interstitial problem entirely.

## Troubleshooting

- **Render service stays at "deploying" forever** — check the service logs. Most common cause is `DATABASE_URL` missing the `?sslmode=require` suffix; psycopg refuses to connect to Neon over plain TCP.
- **Open events stop after Render service sleeps** — by design on the free plan. Upgrade to Starter ($7/mo) for an always-on instance, or accept the ~5% miss rate on first opens after long quiet periods.
- **`/events` returns 401** — bearer token mismatch. Confirm `TRACKING_API_TOKEN` is identical in `backend/.env` and Render's env settings.
- **Clicks redirect to `/` instead of the original URL** — token decode failed. Almost always means `TRACKING_FERNET_KEY` differs between the local backend that encoded the URL and the sidecar that's decoding it.
