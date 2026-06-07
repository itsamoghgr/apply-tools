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

trap 'kill 0' EXIT INT TERM

(cd "$ROOT_DIR/backend" && source venv/bin/activate && exec uvicorn server:app --host 127.0.0.1 --port 8000 --reload) &
(cd "$ROOT_DIR/frontend" && exec npm run dev) &

wait
