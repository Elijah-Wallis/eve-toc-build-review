#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: ws_brain_8099_supervisor.sh [--port PORT] [--host HOST] [--daemon|--status|--stop|--start-once]

Modes:
  --watch      : run process watcher loop (default).
  --daemon     : start watcher in background and return.
  --status     : print current supervisor status.
  --stop       : stop running supervisor.
  --start-once : start uvicorn once (no watch/restart).
EOF
}

PORT="${WS_BRAIN_PORT:-8099}"
HOST="${WS_BRAIN_HOST:-127.0.0.1}"
MODE="watch"
PYTHON_BIN="python3"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT="$2"
      shift 2
      ;;
    --host)
      HOST="$2"
      shift 2
      ;;
    --daemon|--status|--stop|--start-once|--watch)
      MODE="${1#--}"
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

PID_DIR="$ROOT_DIR/.run"
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$PID_DIR" "$LOG_DIR"
SUPERVISOR_PID_FILE="$PID_DIR/ws_brain_supervisor_${PORT}.pid"
SERVER_PID_FILE="$PID_DIR/ws_brain_server_${PORT}.pid"
LOG_FILE="$LOG_DIR/ws_brain_${PORT}.log"

_port_from_pid_is_listening() {
  python3 - <<'PY' "$1"
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect(("127.0.0.1", port))
    sock.close()
    print("1")
except Exception:
    print("0")
PY
}

_is_listening() {
  [ "$(_port_from_pid_is_listening "$PORT")" = "1" ]
}

_is_running() {
  local pid_file="$1"
  if [[ -s "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file")"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  return 1
}

_stop_pid_file() {
  local pid_file="$1"
  if [[ -s "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file")"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
    rm -f "$pid_file"
  fi
}

_start_server_once() {
  if _is_listening; then
    echo "WARN: refusing to start new server because ${HOST}:${PORT} is already bound." >&2
    if [[ -s "$SERVER_PID_FILE" ]]; then
      return 0
    fi
    # If listener exists but pid file is stale/missing, avoid respawn storm until explicit stop.
    return 0
  fi
  "$PYTHON_BIN" -m uvicorn app.server:app --host "$HOST" --port "$PORT" \
    --proxy-headers --forwarded-allow-ips '*' \
    >>"$LOG_FILE" 2>&1 &
  local server_pid=$!
  echo "$server_pid" >"$SERVER_PID_FILE"
  return 0
}

_wait_for_listener() {
  local max_checks="$1"
  local wait_ms="${2:-0.5}"
  local attempt=0

  while (( attempt < max_checks )); do
    if _is_listening; then
      return 0
    fi
    sleep "$wait_ms"
    attempt=$(( attempt + 1 ))
  done
  return 1
}

_watch() {
  while true; do
    if ! _is_running "$SERVER_PID_FILE" || ! _is_listening; then
      _stop_pid_file "$SERVER_PID_FILE"
      _start_server_once
      if ! _wait_for_listener 20 0.25; then
        echo "WARN: server did not bind within startup grace, restarting in 1s" >&2
        _stop_pid_file "$SERVER_PID_FILE"
        sleep 1
        continue
      fi
    fi
    # Wait for server to exit (normal crash or manual stop), then loop and restart.
    local server_pid=""
    if [[ -f "$SERVER_PID_FILE" ]]; then
      read -r server_pid < "$SERVER_PID_FILE"
    fi
    if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
      wait "$server_pid" || true
    fi
  done
}

case "$MODE" in
  daemon)
    if _is_running "$SUPERVISOR_PID_FILE"; then
      echo "Supervisor already running (pid=$(<$SUPERVISOR_PID_FILE))" >&2
      exit 0
    fi
    nohup "$0" --watch --port "$PORT" --host "$HOST" >>"$LOG_FILE" 2>&1 &
    local_pid=$!
    echo "$local_pid" >"$SUPERVISOR_PID_FILE"
    echo "Started ws brain supervisor pid=$local_pid on ${HOST}:${PORT}"
    ;;
  stop)
    _stop_pid_file "$SUPERVISOR_PID_FILE"
    _stop_pid_file "$SERVER_PID_FILE"
    echo "Stopped supervisor and server for ${HOST}:${PORT} (if present)."
    ;;
  status)
    printf 'supervisor=%s\n' "$(_is_running "$SUPERVISOR_PID_FILE" && echo yes || echo no)"
    printf 'server=%s\n' "$(_is_running "$SERVER_PID_FILE" && echo yes || echo no)"
    printf 'listening=%s\n' "$(_is_listening && echo yes || echo no)"
    printf 'supervisor_pid_file=%s\n' "$SUPERVISOR_PID_FILE"
    printf 'server_pid_file=%s\n' "$SERVER_PID_FILE"
    printf 'log_file=%s\n' "$LOG_FILE"
    ;;
  start-once)
    _start_server_once
    if ! _wait_for_listener 120 0.1; then
      echo "WARN: ws brain failed to bind within 12s on startup." >&2
      if [[ -s "$SERVER_PID_FILE" ]]; then
        tail -n 80 "$LOG_FILE" 2>/dev/null | sed 's/^/[ws-brain log] /'
      fi
      exit 2
    fi
    echo "Started single-instance ws brain pid=$(<"$SERVER_PID_FILE") on ${HOST}:${PORT}"
    ;;
  watch|*)
    echo "$$" >"$SUPERVISOR_PID_FILE"
    trap '_stop_pid_file "$SERVER_PID_FILE"; _stop_pid_file "$SUPERVISOR_PID_FILE"' EXIT INT TERM
    _watch
    ;;
esac
