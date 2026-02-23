#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SUPERVISOR_SCRIPT="${RETELL_WS_SUPERVISOR_SCRIPT:-$ROOT_DIR/scripts/ws_brain_8099_supervisor.sh}"
PORT="${WS_BRAIN_PORT:-8099}"
HOST="${WS_BRAIN_HOST:-127.0.0.1}"

usage() {
  cat <<'EOF'
Usage: ws_brain_8099_prod.sh [--start|--stop|--status|--restart]

This is the production command wrapper for the LLM websocket brain on 8099.
It forces/reinforces the continuous watcher mode and waits for readiness.
EOF
}

is_listening() {
  python3 - <<'PY' "$1"
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket()
sock.settimeout(0.25)
try:
    sock.connect(("127.0.0.1", port))
    sock.close()
    print("1")
except Exception:
    print("0")
PY
}

wait_for_listener() {
  local max_checks="$1"
  local attempt=0

  while (( attempt < max_checks )); do
    if [[ "$(is_listening "$PORT")" == "1" ]]; then
      return 0
    fi
    sleep 0.25
    attempt=$((attempt + 1))
  done
  return 1
}

action="start"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --start|--status|--stop|--restart)
      action="${1#--}"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 2
      ;;
  esac
done

case "$action" in
  start)
    "$SUPERVISOR_SCRIPT" --daemon --port "$PORT" --host "$HOST" >/dev/null 2>&1
    if ! wait_for_listener 40; then
      echo "ERROR: brain did not become reachable on ${HOST}:${PORT} after startup checks." >&2
      echo "Inspect log: $ROOT_DIR/logs/ws_brain_${PORT}.log" >&2
      "$SUPERVISOR_SCRIPT" --status --port "$PORT" --host "$HOST" || true
      exit 2
    fi
    echo "LLM websocket brain watcher running and listening on ${HOST}:${PORT}"
    ;;
  status)
    "$SUPERVISOR_SCRIPT" --status --port "$PORT" --host "$HOST"
    ;;
  stop)
    "$SUPERVISOR_SCRIPT" --stop --port "$PORT" --host "$HOST"
    ;;
  restart)
    "$SUPERVISOR_SCRIPT" --stop --port "$PORT" --host "$HOST"
    "$SUPERVISOR_SCRIPT" --daemon --port "$PORT" --host "$HOST" >/dev/null 2>&1
    if ! wait_for_listener 40; then
      echo "ERROR: brain did not become reachable on ${HOST}:${PORT} after restart." >&2
      echo "Inspect log: $ROOT_DIR/logs/ws_brain_${PORT}.log" >&2
      exit 2
    fi
    echo "LLM websocket brain restarted and listening on ${HOST}:${PORT}"
    ;;
esac

