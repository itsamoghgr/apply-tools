#!/bin/bash
# Stop every process the application starts: the platform backend (:8000), the
# lead-generation agent server (:8001), and the Next.js frontend (:3000/:3001).
# Safe to run anytime; it only targets this app's ports and process names.
set -u
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

PORTS=(8000 8001 3000 3001)
# Process-name patterns this app launches (covers strays not bound to a port,
# e.g. a reloader child or a frontend started on a bumped port).
PATTERNS=(
  "agent_server.api.main"
  "python -m server"
  "next dev"
  "next-server"
  "frontend_log_filter"
)

killed=0

kill_pids() {
  # $1 = description, $2... = pids
  local desc="$1"; shift
  for pid in "$@"; do
    [ -z "$pid" ] && continue
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null && echo "  stopped $desc (pid $pid)" && killed=$((killed + 1))
    fi
  done
}

echo "Stopping apply-tools processes…"

# 1. By port — the authoritative way to catch whatever is actually serving.
for port in "${PORTS[@]}"; do
  pids=$(lsof -ti :"$port" 2>/dev/null)
  [ -n "$pids" ] && kill_pids ":$port" $pids
done

# 2. By name — catch strays (reloader children, log filter, bumped-port dev server).
for pat in "${PATTERNS[@]}"; do
  pids=$(pgrep -f "$pat" 2>/dev/null)
  [ -n "$pids" ] && kill_pids "$pat" $pids
done

# 3. Give them a moment, then force-kill anything still holding a port.
sleep 1
for port in "${PORTS[@]}"; do
  pids=$(lsof -ti :"$port" 2>/dev/null)
  if [ -n "$pids" ]; then
    echo "  force-killing stragglers on :$port"
    kill -9 $pids 2>/dev/null
    killed=$((killed + 1))
  fi
done

if [ "$killed" -eq 0 ]; then
  echo "Nothing was running."
else
  echo "Done."
fi
