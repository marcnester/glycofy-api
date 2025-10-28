#!/bin/zsh
# Glycofy API — resilient restart helper for macOS (zsh)
# - Activates venv
# - Loads .env (API_HOST, API_PORT)
# - Frees busy port
# - Tries fallback ports automatically
# - Starts uvicorn with --reload in the foreground

set -euo pipefail

# --- cd to repo root ---
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPT_DIR"

# --- venv ---
if [[ ! -d ".venv" ]]; then
  echo "❌ No virtual environment found at .venv"
  echo "   Create one with:"
  echo "   python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi
source .venv/bin/activate 2>/dev/null || { echo "❌ Failed to activate .venv"; exit 1; }

# --- load .env (safe for simple KEY=VALUE lines) ---
if [[ -f .env ]]; then
  set -o allexport
  source .env
  set +o allexport
  echo "✅ Loaded .env"
else
  echo "⚠️  No .env found; using defaults."
fi

# --- defaults ---
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8090}"

# --- utility: is port in use? ---
port_in_use() {
  local p="$1"
  # show all listeners on this port (sudo allows seeing processes not owned by current user)
  if command -v lsof >/dev/null 2>&1; then
    sudo lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  else
    # fallback to netstat if lsof isn't available
    netstat -anv 2>/dev/null | grep -q "[\.\:]$p .*LISTEN"
    return $?
  fi
}

# --- utility: free a port if possible ---
free_port() {
  local p="$1"
  if command -v lsof >/dev/null 2>&1; then
    local pids
    pids="$(sudo lsof -ti :"$p" || true)"
    if [[ -n "$pids" ]]; then
      echo "🧹 Killing PIDs on port $p: $pids"
      sudo kill -9 $pids || true
    fi
  fi
}

# --- extra cleanup: kill stray uvicorn/watchers ---
cleanup_uvicorn() {
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  pkill -f "uvicorn" 2>/dev/null || true
  pkill -f "watchfiles" 2>/dev/null || true
}

# --- try starting on a given port ---
try_start() {
  local host="$1"
  local port="$2"
  echo "────────────────────────────────────────────────────────"
  echo "🔎 Attempting start on http://$host:$port ..."
  cleanup_uvicorn
  free_port "$port"
  sleep 0.3
  echo "🚀 Starting Uvicorn (reload enabled)…"
  # Foreground run; if binding fails, uvicorn exits non-zero and the script continues.
  uvicorn app.main:app --reload --host "$host" --port "$port"
}

# --- build candidate port list ---
# Priority: API_PORT from .env, then +10, then common dev ports
CANDIDATES=()
CANDIDATES+=("$API_PORT")
CANDIDATES+=("$((API_PORT + 10))")
CANDIDATES+=(8090 8100 8110 8080 5000 8888)

# Deduplicate while preserving order
UNIQ=()
for p in "${CANDIDATES[@]}"; do
  skip=false
  for q in "${UNIQ[@]}"; do
    [[ "$p" == "$q" ]] && skip=true && break
  done
  [[ "$skip" == true ]] || UNIQ+=("$p")
done

echo "📦 Host: $API_HOST"
echo "📦 Preferred port: $API_PORT"
echo "📦 Candidate ports: ${UNIQ[*]}"

# --- iterate candidates until one works ---
for PORT in "${UNIQ[@]}"; do
  if port_in_use "$PORT"; then
    echo "⛔️ Port $PORT is currently in use."
    continue
  fi
  # If uvicorn exits due to bind error, continue to next candidate
  if try_start "$API_HOST" "$PORT"; then
    # If uvicorn ever returns 0 (normally doesn't with --reload), exit
    exit 0
  else
    echo "⚠️  Start on $PORT failed (likely bind error). Trying next…"
    sleep 0.5
  fi
done

# --- last resort: pick a random high port ---
RAND=$((20000 + RANDOM % 40000))
echo "🌀 Trying random fallback port: $RAND"
if port_in_use "$RAND"; then
  echo "⛔️ Random port $RAND also in use. Aborting."
  exit 1
fi
try_start "$API_HOST" "$RAND"
