#!/bin/bash
set -e
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Backend checks
if [ ! -d "$ROOT_DIR/backend/venv" ]; then
  echo "backend/venv/ not found. Run setup first:"
  echo "  cd backend && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi
if [ ! -f "$ROOT_DIR/backend/.env" ]; then
  echo "WARNING: backend/.env not found. Copy .env.example to .env and add your ANTHROPIC_API_KEY."
fi

# DB / frontend checks
# Postgres replaces the old SQLite file. Confirm the server is reachable on
# 5432 before we boot the apps, since both backend and frontend need it.
if ! nc -z localhost 5432 2>/dev/null; then
  echo "Postgres not reachable on localhost:5432. Start it first, e.g.:"
  echo "  brew services start postgresql@17"
  echo "Then ensure the apply_tools database + role exist (see dev.md)."
  exit 1
fi
if [ ! -d "$ROOT_DIR/frontend/node_modules" ]; then
  echo "frontend/node_modules not found. Run: cd frontend && npm install"
  exit 1
fi

# Free our ports first so a stale process from a previous run doesn't cause
# "address already in use" / "another dev server is already running". Uses the
# companion stop.sh; harmless when nothing is running.
if [ -x "$ROOT_DIR/stop.sh" ]; then
  "$ROOT_DIR/stop.sh" >/dev/null 2>&1 || true
fi

trap 'kill 0' EXIT INT TERM

(cd "$ROOT_DIR/backend" && source venv/bin/activate && exec python -m server) &

# Lead-generation agent service (separate uvicorn process on :8001). Optional —
# only started if its venv exists, so setups that haven't installed it still boot.
# It runs from the repo root so the `agent_server` package imports resolve.
if [ -d "$ROOT_DIR/agent_server/venv" ]; then
  (cd "$ROOT_DIR" && exec "$ROOT_DIR/agent_server/venv/bin/python" -m agent_server.api.main) &
fi

# Pipe the Next.js dev server through the backend's structlog filter so the
# frontend's request lines render in the same format as the backend logs.
# PYTHONPATH lets the filter import `log` from backend/; -u keeps the pipe
# unbuffered so lines aren't held back.
(
  cd "$ROOT_DIR/frontend" \
    && npm run dev 2>&1 \
       | PYTHONPATH="$ROOT_DIR/backend" "$ROOT_DIR/backend/venv/bin/python" -u -m frontend_log_filter
) &

wait
