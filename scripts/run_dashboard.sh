#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${RETELL_ENV_FILE:-$ROOT_DIR/.env.retell.local}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

# Default to B2B profile for dogfood voice calls unless explicitly overridden.
export CONVERSATION_PROFILE="${CONVERSATION_PROFILE:-b2b}"

PYTHON_BIN="python3"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

PORT="${PORT:-8080}"
URL="http://127.0.0.1:${PORT}/dashboard/"

if command -v open >/dev/null 2>&1; then
  open "$URL" >/dev/null 2>&1 || true
fi

echo "Eve dashboard: $URL"
exec "$PYTHON_BIN" -m uvicorn app.server:app --host 0.0.0.0 --port "$PORT"
