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
4. Set the three secrets when prompted:
   - `DATABASE_URL` — Neon connection string from step 1
   - `TRACKING_FERNET_KEY` — value from step 2
   - `TRACKING_API_TOKEN` — value from step 2
5. First deploy takes ~3 minutes (Docker build + cold start). The sidecar
   reads `RENDER_EXTERNAL_URL` automatically to build its own tracking
   URLs, so there's nothing to set after the deploy finishes.

If you later bind a custom domain, set `PUBLIC_BASE_URL` to that hostname
(otherwise emails would still embed the `*.onrender.com` URL). Without a
custom domain, leave it unset.

The service auto-creates the `tracking_events` table on first boot via `_ensure_schema()` in [`main.py`](./main.py).

### 4. Wire up the local backend

In [`backend/.env`](../backend/.env) add:

```env
TRACKING_BASE_URL=https://apply-tools-tracker.onrender.com
TRACKING_FERNET_KEY=<same value as Render>
TRACKING_API_TOKEN=<same value as Render>
```

Restart `./start.sh`. The Reach Out page header should flip to "Tracking active via apply-tools-tracker.onrender.com".

## Keeping the sidecar awake (free tier)

Render free instances sleep after **15 min of inactivity** and take **30–60 sec** to cold-boot. That breaks tracking in two ways:

- **Open tracking is silently dropped.** Gmail's image proxy times out before the sidecar boots, so the open is never recorded. The first email opened after a quiet period — the one you most want to know about — is the one most likely to be missed.
- **Click tracking still works but stalls.** The recipient's browser waits for the redirect, but they stare at a blank "Connecting…" tab for ~60 sec. Some will close it before the redirect lands.

Fix it with a free uptime monitor that pings `/healthz` every few minutes:

### Option A — UptimeRobot (recommended)

1. Sign up at <https://uptimerobot.com> (free, no card).
2. **+ New monitor** → type `HTTP(s)` → URL: `https://<your-sidecar>.onrender.com/healthz` → interval: 5 min.
3. Save. UptimeRobot's free plan allows 50 monitors at 5-min intervals — well under any limit.

This keeps the sidecar warm 24/7. With 5-min checks against a 15-min sleep timer, the service never gets a chance to hibernate.

### Option B — cron-job.org

Same idea, slightly simpler UI. Create an account, add a job at the same `/healthz` URL with a 5–10 min interval.

### What `/healthz` does

It's a stripped-down health probe that returns `{"ok": true}` without touching Postgres, so keep-alive pings don't burn through Neon's connection budget. The endpoint also exists at `/` for monitors that probe the root.

### Should I just upgrade?

If you'll send more than a handful of emails per week, Render's **Starter** plan ($7/mo) eliminates hibernation entirely and removes any need for an external pinger. For occasional use, UptimeRobot is genuinely fine — I've run it this way for months without missing an open.

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
- **Open events stop after Render service sleeps** — Gmail's image proxy times out before the sidecar cold-boots. See [Keeping the sidecar awake](#keeping-the-sidecar-awake-free-tier) above; setting up UptimeRobot against `/healthz` fixes this in ~2 minutes.
- **`/events` returns 401** — bearer token mismatch. Confirm `TRACKING_API_TOKEN` is identical in `backend/.env` and Render's env settings.
- **Clicks redirect to `/` instead of the original URL** — token decode failed. Almost always means `TRACKING_FERNET_KEY` differs between the local backend that encoded the URL and the sidecar that's decoding it.
