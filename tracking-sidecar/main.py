"""Public-internet tracking sidecar.

This is a deliberately tiny FastAPI app whose sole job is to:
  1. Decode pytracking URLs that the local backend embedded in outgoing email.
  2. Record the open/click event in Postgres.
  3. Return a 1x1 pixel (opens) or 302 to the original URL (clicks).
  4. Expose authenticated endpoints so the local dashboard can read events
     and aggregate counters back out.

It deploys to a real public host (Render) so Gmail's image proxy can hit it
without ngrok-style abuse interstitials. It shares two secrets with the
local backend via env vars: `TRACKING_FERNET_KEY` (so we can decode URLs
the local backend encoded) and `TRACKING_API_TOKEN` (auth on /events and
/aggregates).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Iterable

import psycopg
import psycopg_pool
import pytracking
from fastapi import Depends, FastAPI, HTTPException, Header, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from pytracking import Configuration

logger = logging.getLogger("tracking-sidecar")
logging.basicConfig(level=logging.INFO)


# ---------------------------------------------------------------------------
# Configuration. All required values come from env so the same image deploys
# anywhere. Failing fast on import means a misconfigured deploy never serves
# bad data.
# ---------------------------------------------------------------------------


def _env(key: str) -> str | None:
    v = os.getenv(key)
    return v.strip() if v else None


def _require_env(key: str) -> str:
    v = _env(key)
    if not v:
        raise RuntimeError(f"{key} env var is required")
    return v


DATABASE_URL = _require_env("DATABASE_URL")
TRACKING_FERNET_KEY = _require_env("TRACKING_FERNET_KEY").encode("utf-8")
TRACKING_API_TOKEN = _require_env("TRACKING_API_TOKEN")

# Render injects RENDER_EXTERNAL_URL automatically (e.g.
# https://apply-tools-tracker.onrender.com) on every deploy, including the
# first one. We prefer an explicit PUBLIC_BASE_URL when set so users can
# point at a custom domain or run off-Render — otherwise we self-discover.
_public_base = _env("PUBLIC_BASE_URL") or _env("RENDER_EXTERNAL_URL")
if not _public_base:
    raise RuntimeError(
        "PUBLIC_BASE_URL env var is required (or RENDER_EXTERNAL_URL when "
        "deployed on Render)"
    )
PUBLIC_BASE_URL = _public_base.rstrip("/") + "/"

OPEN_PATH = "track/open/"
CLICK_PATH = "track/click/"

CONFIG = Configuration(
    base_open_tracking_url=PUBLIC_BASE_URL + OPEN_PATH,
    base_click_tracking_url=PUBLIC_BASE_URL + CLICK_PATH,
    encryption_bytestring_key=TRACKING_FERNET_KEY,
    append_slash=False,
)


# ---------------------------------------------------------------------------
# Postgres connection pool. Lazy-created via lifespan so we don't open a
# socket at import time (helps with `flyctl deploy` and `render deploy`
# health checks that spin the container up briefly).
# ---------------------------------------------------------------------------


pool: psycopg_pool.ConnectionPool | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global pool
    pool = psycopg_pool.ConnectionPool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        kwargs={"autocommit": True},
    )
    pool.wait()
    _ensure_schema()
    try:
        yield
    finally:
        if pool is not None:
            pool.close()


def _ensure_schema() -> None:
    """Create the events table if it doesn't exist.

    A 1-table schema doesn't justify Alembic. The `id` is the path token from
    pytracking, which dedupes accidental replays (Gmail proxy + real open).
    """
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tracking_events (
                id            TEXT PRIMARY KEY,
                reach_out_id  TEXT NOT NULL,
                event_type    TEXT NOT NULL CHECK (event_type IN ('open', 'click')),
                tracked_url   TEXT,
                user_agent    TEXT,
                user_ip       TEXT,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS tracking_events_reach_out_idx "
            "ON tracking_events (reach_out_id, created_at)"
        )


# ---------------------------------------------------------------------------
# Auth dependency for the dashboard-facing endpoints. Open/click endpoints
# are unauthenticated by necessity (mail clients can't bring credentials).
# ---------------------------------------------------------------------------


def require_token(authorization: str = Header(default="")) -> None:
    expected = f"Bearer {TRACKING_API_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid bearer token")


# ---------------------------------------------------------------------------
# App.
# ---------------------------------------------------------------------------


app = FastAPI(title="Apply Tools tracking sidecar", lifespan=lifespan)


@app.get("/")
def health() -> dict:
    return {"ok": True, "service": "tracking-sidecar"}


# ---------------------------------------------------------------------------
# Tracking endpoints. Both intentionally swallow decode errors and respond
# with the benign "expected" payload so a tampered URL doesn't reveal that
# the link is a tracker.
# ---------------------------------------------------------------------------


_PIXEL_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


def _record_event(
    *,
    request: Request,
    tracking_result: Any,
    event_type: str,
) -> None:
    metadata = getattr(tracking_result, "metadata", None) or {}
    reach_out_id = metadata.get("reach_out_id")
    if not reach_out_id:
        logger.warning("Tracking event missing reach_out_id metadata; ignoring.")
        return

    user_agent = request.headers.get("user-agent")
    user_ip = request.client.host if request.client else None
    tracked_url = getattr(tracking_result, "tracked_url", None)

    # Bucket the dedupe id at ~10-minute resolution so repeated proxy fetches
    # within the same window collapse to one row, but a true open hours later
    # records again. Click events also include the URL so two clicks on
    # different links count separately.
    import re
    from datetime import datetime, timezone

    bucket = int(datetime.now(timezone.utc).timestamp() // 600)
    parts = [reach_out_id, event_type, tracked_url or "", str(bucket)]
    event_id = "evt_" + re.sub(r"\W+", "_", "::".join(parts))[:120]

    assert pool is not None
    try:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tracking_events
                  (id, reach_out_id, event_type, tracked_url, user_agent, user_ip)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (event_id, reach_out_id, event_type, tracked_url, user_agent, user_ip),
            )
    except psycopg.Error as exc:
        logger.warning("Failed to record %s event: %s", event_type, exc)


@app.get("/track/open/{path:path}")
def track_open(path: str, request: Request) -> Response:
    try:
        full_url = CONFIG.base_open_tracking_url + path
        result = pytracking.get_open_tracking_result(full_url, configuration=CONFIG)
        _record_event(request=request, tracking_result=result, event_type="open")
    except Exception as exc:
        logger.info("Open tracking decode failed: %s", exc)

    pixel_bytes, mime_type = pytracking.get_open_tracking_pixel()
    return Response(content=pixel_bytes, media_type=mime_type, headers=_PIXEL_HEADERS)


@app.get("/track/click/{path:path}")
def track_click(path: str, request: Request) -> Response:
    try:
        full_url = CONFIG.base_click_tracking_url + path
        result = pytracking.get_click_tracking_result(full_url, configuration=CONFIG)
    except Exception as exc:
        logger.info("Click tracking decode failed: %s", exc)
        return RedirectResponse(url="/", status_code=302)

    _record_event(request=request, tracking_result=result, event_type="click")
    target = getattr(result, "tracked_url", None) or "/"
    return RedirectResponse(url=target, status_code=302)


# ---------------------------------------------------------------------------
# Dashboard read APIs. Bearer-token-protected; the local backend proxies them
# through to the user's UI.
# ---------------------------------------------------------------------------


@app.get("/events/{reach_out_id}", dependencies=[Depends(require_token)])
def list_events(reach_out_id: str) -> dict[str, Any]:
    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, reach_out_id, event_type, tracked_url,
                   user_agent, user_ip, created_at
            FROM tracking_events
            WHERE reach_out_id = %s
            ORDER BY created_at ASC
            """,
            (reach_out_id,),
        )
        rows = cur.fetchall()
    return {
        "events": [
            {
                "id": r[0],
                "reachOutId": r[1],
                "eventType": r[2],
                "trackedUrl": r[3],
                "userAgent": r[4],
                "userIp": r[5],
                "createdAt": r[6].isoformat(),
            }
            for r in rows
        ]
    }


class AggregatesRequest(BaseModel):
    ids: list[str] = Field(..., max_length=500)


@app.post("/aggregates", dependencies=[Depends(require_token)])
def aggregate_counts(req: AggregatesRequest) -> dict[str, Any]:
    """Batch endpoint for the list view: counts + last-seen per reach_out_id."""
    if not req.ids:
        return {"aggregates": {}}

    assert pool is not None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT reach_out_id, event_type,
                   COUNT(*) AS n, MAX(created_at) AS last_at
            FROM tracking_events
            WHERE reach_out_id = ANY(%s)
            GROUP BY reach_out_id, event_type
            """,
            (list(req.ids),),
        )
        rows = cur.fetchall()

    aggregates: dict[str, dict[str, Any]] = {
        rid: {
            "openCount": 0,
            "clickCount": 0,
            "lastOpenedAt": None,
            "lastClickedAt": None,
        }
        for rid in req.ids
    }
    for reach_out_id, event_type, n, last_at in rows:
        bucket = aggregates.setdefault(
            reach_out_id,
            {
                "openCount": 0,
                "clickCount": 0,
                "lastOpenedAt": None,
                "lastClickedAt": None,
            },
        )
        if event_type == "open":
            bucket["openCount"] = n
            bucket["lastOpenedAt"] = last_at.isoformat() if last_at else None
        elif event_type == "click":
            bucket["clickCount"] = n
            bucket["lastClickedAt"] = last_at.isoformat() if last_at else None

    return {"aggregates": aggregates}
