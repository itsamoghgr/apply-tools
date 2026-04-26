#!/bin/bash
set -e
cd "$(dirname "$0")/backend"

if [ ! -d "venv" ]; then
  echo "venv/ not found in backend/. Run setup first:"
  echo "  cd backend && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

source venv/bin/activate

if [ ! -f ".env" ]; then
  echo "WARNING: backend/.env not found. Copy .env.example to .env and add your ANTHROPIC_API_KEY."
fi

exec uvicorn server:app --host 127.0.0.1 --port 8000 --reload
