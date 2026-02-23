#!/usr/bin/env bash
set -euo pipefail

# One-command local dev:
# 1) start the brain server
# 2) expose it with a temporary public WSS URL (cloudflared)
# 3) switch the B2B agent to point at that brain
#
# This is intended for fast dogfooding. For production, use a stable domain.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${RETELL_ENV_FILE:-$ROOT_DIR/.env.retell.local}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

PORT="${PORT:-8080}"
LOCAL_URL="http://127.0.0.1:${PORT}"

PYTHON_BIN="python3"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

cleanup() {
  if [ -n "${TUNNEL_PID:-}" ]; then
    kill "$TUNNEL_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "${SERVER_PID:-}" ]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# Start server.
"$PYTHON_BIN" -m uvicorn app.server:app --host 0.0.0.0 --port "$PORT" >/dev/null 2>&1 &
SERVER_PID=$!

# Start tunnel.
TUNNEL_LOG="$(mktemp)"
cloudflared tunnel --url "$LOCAL_URL" --no-autoupdate --loglevel info --logfile "$TUNNEL_LOG" >/dev/null 2>&1 &
TUNNEL_PID=$!

# Wait for the public URL.
PUBLIC_HTTPS=""
for _ in $(seq 1 200); do
  PUBLIC_HTTPS="$(grep -Eo 'https://[A-Za-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -n 1 || true)"
  if [ -n "$PUBLIC_HTTPS" ]; then
    break
  fi
  sleep 0.05
done

if [ -z "$PUBLIC_HTTPS" ]; then
  echo "Failed to get public URL from cloudflared" >&2
  exit 1
fi

PUBLIC_WSS="wss://${PUBLIC_HTTPS#https://}"
export BRAIN_WSS_BASE_URL="$PUBLIC_WSS/llm-websocket"

echo "Public brain base URL: $BRAIN_WSS_BASE_URL"

# Persist the base URL for future commands (local env file, not committed).
if [ -f "$ENV_FILE" ]; then
  tmp_env="$(mktemp)"
  # Remove any prior value then append the new one.
  grep -v '^BRAIN_WSS_BASE_URL=' "$ENV_FILE" >"$tmp_env" || true
  echo "BRAIN_WSS_BASE_URL=$BRAIN_WSS_BASE_URL" >>"$tmp_env"
  mv "$tmp_env" "$ENV_FILE"
fi

# Switch B2B agent to websocket brain.
./scripts/b2b_switch_to_ws_brain.sh >/dev/null

echo "B2B agent switched to brain."
echo "Dashboard: http://127.0.0.1:${PORT}/dashboard/"
echo "Next: make call"

# Keep processes running.
wait
