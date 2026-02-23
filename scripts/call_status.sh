#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${RETELL_ENV_FILE:-$ROOT_DIR/.env.retell.local}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  echo "Create it with RETELL_API_KEY." >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${RETELL_API_KEY:?RETELL_API_KEY is required}"

CALL_ID="${1:-}"
if [[ -z "$CALL_ID" ]]; then
  echo "Usage: scripts/call_status.sh <call_id>" >&2
  exit 2
fi

curl -sS \
  -H "Authorization: Bearer $RETELL_API_KEY" \
  "https://api.retellai.com/v2/get-call/$CALL_ID" \
  | python3 - <<'PY'
import json
import sys

raw = sys.stdin.read()
try:
    data = json.loads(raw)
except Exception:
    print(raw)
    raise SystemExit(1)

def get(path, default=""):
    cur = data
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default

call_id = get(("call_id",), "")
status = get(("call_status",), "")
disconn = get(("disconnection_reason",), "")
disconn_msg = get(("disconnection_reason_message",), "")
ended_reason = get(("ended_reason",), "")
duration = get(("call_duration_ms",), "")
transcript = get(("transcript",), [])
last_event = ""
if isinstance(transcript, list) and transcript:
    last = transcript[-1]
    if isinstance(last, dict):
        last_event = (last.get("content") or "").strip()

print(f"call_id={call_id}")
print(f"status={status}")
if disconn:
    print(f"disconnection_reason={disconn}")
if disconn_msg:
    print(f"disconnection_reason_message={disconn_msg}")
if ended_reason:
    print(f"ended_reason={ended_reason}")
if duration != "":
    print(f"duration_ms={duration}")
if last_event:
    print(f"last_transcript={last_event}")
print("---")
print(json.dumps(data, indent=2))
PY
