#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${RETELL_ENV_FILE:-$ROOT_DIR/.env.retell.local}"
SUPERVISOR_SCRIPT="${RETELL_WS_SUPERVISOR_SCRIPT:-$ROOT_DIR/scripts/ws_brain_8099_supervisor.sh}"
PROD_BRAIN_SCRIPT="${RETELL_WS_PROD_SCRIPT:-$ROOT_DIR/scripts/ws_brain_8099_prod.sh}"
REQUIRED_B2B_AGENT_ID="${B2B_OUTBOUND_AGENT_ID:-agent_7a0abb6b0df0e6352fbd236f3b}"
REQUIRED_FROM_NUMBER="+14695998571"
REQUIRED_WS_BASE="wss://ws.evesystems.org/llm-websocket"

is_remote_non_local_ws_host() {
  local ws_url="${BRAIN_WSS_BASE_URL:-}"
  if [[ -z "$ws_url" ]]; then
    return 1
  fi
  if [[ "$ws_url" == *"localhost"* || "$ws_url" == *"127.0.0.1"* ]]; then
    return 1
  fi
  return 0
}

validate_b2b_ws_base_url() {
  local base_url="$1"
  if [[ -z "$base_url" ]]; then
    return 0
  fi

  if [[ "${RETELL_ENFORCE_B2B_WS_URL:-true}" != "true" ]]; then
    return 0
  fi

  python3 - <<'PY' "$base_url" "$REQUIRED_WS_BASE"
import sys
import urllib.parse

base = sys.argv[1].strip()
required = sys.argv[2].strip()

parsed = urllib.parse.urlparse(base)
if not parsed.scheme:
    print("invalid:missing_scheme")
    sys.exit(2)
if parsed.scheme not in {"ws", "wss"}:
    print(f"invalid:scheme:{parsed.scheme}")
    sys.exit(2)

required = urllib.parse.urlparse(required)
if parsed.hostname != required.hostname:
    print(f"invalid:host:{parsed.hostname}")
    sys.exit(3)

base_path = (parsed.path or "").rstrip("/")
req_path = (required.path or "").rstrip("/")
if base_path != req_path:
    print(f"invalid:path:{base_path}")
    sys.exit(3)
print("ok")
sys.exit(0)
PY
}

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  echo "Create it with RETELL_API_KEY, B2B_AGENT_ID, RETELL_FROM_NUMBER, DOGFOOD_TO_NUMBER." >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${RETELL_API_KEY:?RETELL_API_KEY is required}"
: "${B2B_AGENT_ID:?B2B_AGENT_ID is required}"
: "${RETELL_FROM_NUMBER:?RETELL_FROM_NUMBER is required}"

TO_NUMBER="${1:-${DOGFOOD_TO_NUMBER:-}}"
if [[ -z "${TO_NUMBER:-}" ]]; then
  echo "Usage: scripts/call_b2b.sh [to_number]" >&2
  echo "Set DOGFOOD_TO_NUMBER in $ENV_FILE or pass a number like +19859914360" >&2
  exit 2
fi

# Optional fast-fail/fallback tuning.
CALL_AGENT_ID="${B2B_AGENT_ID}"
if [[ "${RETELL_ENFORCE_B2B_AGENT_ID:-true}" == "true" && -n "$REQUIRED_B2B_AGENT_ID" && "$CALL_AGENT_ID" != "$REQUIRED_B2B_AGENT_ID" ]]; then
  echo "Error: B2B_AGENT_ID must match configured outbound agent '$REQUIRED_B2B_AGENT_ID' for this rollout." >&2
  echo "Current B2B_AGENT_ID='$CALL_AGENT_ID'." >&2
  if [[ -z "${B2B_OUTBOUND_AGENT_ID:-}" ]]; then
    echo "Tip: set B2B_OUTBOUND_AGENT_ID in your env file to the canonical outbound agent if you need a different rollout target." >&2
  fi
  exit 2
fi

BRAIN_WS_FAILOVER_TO_BACKUP="${RETELL_WS_FAILOVER_TO_BACKUP:-true}"
B2B_AGENT_ID_BACKUP="${B2B_AGENT_ID_BACKUP:-}"
AUTO_RESOLVE_CF_WS="${RETELL_AUTO_RESOLVE_CF_WS:-true}"
RETELL_ENSURE_BRAIN="${RETELL_ENSURE_BRAIN_RUNNING:-true}"
RETELL_WS_TARGET_PORT="${RETELL_WS_TARGET_PORT:-8099}"
RETELL_WS_START_TIMEOUT_SEC="${RETELL_WS_START_TIMEOUT_SEC:-20}"
FORCE_BRAIN_PORT_8099="${RETELL_FORCE_BRAIN_PORT_8099:-auto}"

PRODUCTION_BRAIN_TOPOLOGY=0

is_local_listener() {
  local port="$1"
  python3 - <<'PY' "$port"
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect(("127.0.0.1", port))
    print("local_listener_ok=1")
    sys.exit(0)
except Exception:
    print("local_listener_ok=0")
    sys.exit(1)
finally:
    sock.close()
PY
}

ensure_local_brain() {
  local port="$1"
  local tries="$2"
  if is_local_listener "$port" >/dev/null; then
    return 0
  fi

  if [[ "$RETELL_ENSURE_BRAIN" != "true" && "$RETELL_ENSURE_BRAIN" != "1" ]]; then
    echo "Local brain on 127.0.0.1:${port} is not active and RETELL_ENSURE_BRAIN_RUNNING is disabled." >&2
    return 2
  fi

  if [[ ! -x "$SUPERVISOR_SCRIPT" ]]; then
    echo "Brain supervisor not executable: $SUPERVISOR_SCRIPT" >&2
    echo "Set RETELL_WS_SUPERVISOR_SCRIPT to a valid script." >&2
    return 2
  fi

  if [[ "$PRODUCTION_BRAIN_TOPOLOGY" == "1" ]]; then
    if [[ ! -x "$PROD_BRAIN_SCRIPT" ]]; then
      echo "Production brain launcher not executable: $PROD_BRAIN_SCRIPT" >&2
      echo "Set RETELL_WS_PROD_SCRIPT to a valid script." >&2
      return 2
    fi
    echo "Starting production brain watcher on 127.0.0.1:8099..." >&2
    "$PROD_BRAIN_SCRIPT" --start >/dev/null 2>&1 || {
      echo "Failed to launch production ws_brain watcher: $PROD_BRAIN_SCRIPT" >&2
      return 2
    }
  else
    echo "Starting supervised brain on 127.0.0.1:${port}..." >&2
    "$SUPERVISOR_SCRIPT" --daemon --port "$port" --host "127.0.0.1" >/dev/null 2>&1 || {
      echo "Failed to launch ws_brain supervisor: $SUPERVISOR_SCRIPT" >&2
      return 2
    }
  fi

  for _ in $(seq 1 "$tries"); do
    if is_local_listener "$port" >/dev/null 2>&1; then
      echo "Brain process is listening on 127.0.0.1:${port}." >&2
      return 0
    fi
    sleep 0.5
  done
  echo "Local brain did not come up on port ${port} after ${tries} checks." >&2
  return 3
}

check_ws_handshake() {
  local base_url="$1"
  local msg
  msg="$(python3 - <<'PY' "$base_url"
import asyncio
import sys
import json

uri = sys.argv[1].strip().rstrip("/")
call_id = "dogfood-health-check"
endpoint = f"{uri}/{call_id}"

try:
    import websockets  # type: ignore
except Exception:
    sys.exit(0)

async def _probe() -> int:
    try:
        async with websockets.connect(endpoint, open_timeout=5, close_timeout=2) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            try:
                msg = json.loads(raw)
            except Exception:
                msg = raw
            if isinstance(msg, dict) and msg.get("response_type") in {"response", "pong"}:
                return 0
            return 0
    except Exception as exc:
        print(f"websocket_handshake_check_failed: {exc}")
        return 2

sys.exit(asyncio.run(_probe()))
PY
  )"
  echo "$msg"
}

# E.164 validation for dial strings.
if [[ ! "$TO_NUMBER" =~ ^\+[0-9]{7,15}$ ]]; then
  echo "Warning: TO_NUMBER '$TO_NUMBER' is not in E.164 format (expected +<country code><number>)." >&2
  echo "Call may fail if formatting is incorrect." >&2
fi

if [[ "${RETELL_ENFORCE_B2B_FROM:-true}" == "true" && "$RETELL_FROM_NUMBER" != "$REQUIRED_FROM_NUMBER" ]]; then
  echo "Error: RETELL_FROM_NUMBER must be '$REQUIRED_FROM_NUMBER' for this rollout." >&2
  echo "Current RETELL_FROM_NUMBER='$RETELL_FROM_NUMBER'." >&2
  exit 2
fi

if [[ ! "$RETELL_FROM_NUMBER" =~ ^\+[0-9]{7,15}$ ]]; then
  echo "Warning: RETELL_FROM_NUMBER '$RETELL_FROM_NUMBER' is not in E.164 format (expected +<country code><number>)." >&2
  echo "Call may fail if formatting is incorrect." >&2
fi

if [[ -n "${BRAIN_WSS_BASE_URL:-}" ]]; then
  if ! validate_b2b_ws_base_url "$BRAIN_WSS_BASE_URL"; then
    echo "Error: BRAIN_WSS_BASE_URL must use '$REQUIRED_WS_BASE/{call_id}' for this rollout." >&2
    echo "Current value: '$BRAIN_WSS_BASE_URL'." >&2
    echo "Set RETELL_ENFORCE_B2B_WS_URL=false to bypass this check." >&2
    exit 2
  fi
fi

CLOUDFLARE_ENV_FILE="${CLOUDFLARE_ENV_FILE:-$ROOT_DIR/.env.cloudflare.local}"

# If websocket URL is missing or stale, resolve a valid Cloudflare production websocket host automatically.
resolve_cf_ws_url() {
  CLOUDFLARE_ENV_FILE="$CLOUDFLARE_ENV_FILE" \
  python3 - <<'PY'
import json
import os
import socket
import urllib.request
from pathlib import Path

env_cf = Path(os.environ["CLOUDFLARE_ENV_FILE"])
if not env_cf.exists():
    raise SystemExit(1)

env = {}
for line in env_cf.read_text(encoding="utf-8").splitlines():
    if not line or line.lstrip().startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    env[k.strip()] = v.strip()

account = env.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
token = env.get("CLOUDFLARE_EVE_TOC_BUILD_API_TOKEN", "").strip()
if not account or not token:
    raise SystemExit(1)

hdr = {"Authorization": f"Bearer {token}"}
tunnels_url = f"https://api.cloudflare.com/client/v4/accounts/{account}/cfd_tunnel"
req = urllib.request.Request(tunnels_url, headers=hdr)
with urllib.request.urlopen(req, timeout=20) as resp:
    payload = json.loads(resp.read().decode("utf-8"))

if not payload.get("success"):
    raise SystemExit(1)

best = []
preferred = []
for tunnel in payload.get("result", []) or []:
    tid = tunnel.get("id")
    if not tid:
        continue
    cfg_url = f"https://api.cloudflare.com/client/v4/accounts/{account}/cfd_tunnel/{tid}/configurations"
    req_cfg = urllib.request.Request(cfg_url, headers=hdr)
    with urllib.request.urlopen(req_cfg, timeout=20) as cfg_resp:
        cfg = json.loads(cfg_resp.read().decode("utf-8"))
    if not cfg.get("success"):
        continue
    ingress = (cfg.get("result") or {}).get("config", {}).get("ingress", [])
    for rule in ingress:
        host = rule.get("hostname")
        service = rule.get("service", "")
        if not service or service.startswith("http_status"):
            continue
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except Exception:
            continue
        if host.endswith(".evesystems.org"):
            preferred.append((host, service))
        else:
            best.append((host, service))

if preferred:
    host, service = sorted(preferred, key=lambda x: x[0])[0]
elif best:
    host, service = sorted(best, key=lambda x: x[0])[0]
else:
    raise SystemExit(1)
_ = service
print(f"wss://{host}/llm-websocket")
PY
}

# Verify websocket endpoint connectivity (DNS). If broken, optionally fall back to backup agent.
if [[ -z "${BRAIN_WSS_BASE_URL:-}" && "$AUTO_RESOLVE_CF_WS" == "true" ]]; then
  echo "BRAIN_WSS_BASE_URL is empty; attempting Cloudflare auto-resolution..." >&2
  RESOLVED_WS_URL="$(resolve_cf_ws_url || true)"
  if [[ -n "${RESOLVED_WS_URL:-}" ]]; then
    BRAIN_WSS_BASE_URL="$RESOLVED_WS_URL"
    export BRAIN_WSS_BASE_URL
    echo "Auto-resolved websocket base URL: $BRAIN_WSS_BASE_URL" >&2
  else
    echo "Cloudflare auto-resolution failed; continuing with configured BRAIN_WSS_BASE_URL value." >&2
  fi
fi

if [[ -n "${BRAIN_WSS_BASE_URL:-}" ]]; then
  if ! python3 - <<'PY'
import os, socket, sys, urllib.parse

url = os.environ.get("BRAIN_WSS_BASE_URL", "").strip()
if not url:
    sys.exit(0)
parsed = urllib.parse.urlparse(url)
host = parsed.hostname
if not host:
    print("invalid_b2b_ws_url")
    sys.exit(2)
try:
    socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
except Exception:
    print("dns_lookup_failed")
    sys.exit(3)
sys.exit(0)
PY
  then
    :
  else
    RESOLVE_STATUS=$?
    if [[ "$RESOLVE_STATUS" -eq 3 ]]; then
      echo "Error: BRAIN_WSS_BASE_URL='$BRAIN_WSS_BASE_URL' is not resolvable from this host." >&2
      if [[ "$BRAIN_WS_FAILOVER_TO_BACKUP" == "true" && -n "$B2B_AGENT_ID_BACKUP" ]]; then
        echo "Falling back to backup agent id from B2B_AGENT_ID_BACKUP." >&2
        CALL_AGENT_ID="$B2B_AGENT_ID_BACKUP"
      else
        echo "Fix: start ws_brain_dev_on.sh or set a valid BRAIN_WSS_BASE_URL." >&2
        exit 2
      fi
    elif [[ "$RESOLVE_STATUS" -eq 2 ]]; then
      echo "Error: BRAIN_WSS_BASE_URL='${BRAIN_WSS_BASE_URL}' is invalid." >&2
      exit 2
    fi
  fi
else
  echo "Warning: BRAIN_WSS_BASE_URL is not set; skipping websocket pre-check." >&2
fi

# Optionally verify the selected agent is wired to this websocket URL.
if [[ "${RETELL_VERIFY_AGENT_WS_URL:-true}" == "true" && -n "${BRAIN_WSS_BASE_URL:-}" ]]; then
  AGENT_WS_URL="$(
    python3 - <<'PY' "$CALL_AGENT_ID" "$BRAIN_WSS_BASE_URL" "$RETELL_API_KEY"
import json
import os
import sys
import urllib.error
import urllib.request

agent_id = sys.argv[1].strip()
api_key = sys.argv[3].strip()

url = f"https://api.retellai.com/get-agent/{agent_id}"
req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", errors="ignore")
    print(f"__retell_http_error__{exc.code}::{body}")
    raise SystemExit(1)
except Exception as exc:
    print(f"__retell_request_failed__{exc}")
    raise SystemExit(1)

engine = payload.get("response_engine") or {}
agent_url = (
    engine.get("websocket_url")
    or engine.get("llm_websocket_url")
    or payload.get("llm_websocket_url")
)
if agent_url:
    print(str(agent_url).strip())
else:
    print("")
PY
  )"
  if [[ "$AGENT_WS_URL" == __retell_http_error__* || "$AGENT_WS_URL" == __retell_request_failed__* ]]; then
    echo "Warning: could not read selected agent $CALL_AGENT_ID websocket config. Proceeding with manual override ${CALL_AGENT_ID}." >&2
  elif [[ -n "$AGENT_WS_URL" && "$AGENT_WS_URL" != *"${BRAIN_WSS_BASE_URL}"* ]]; then
    echo "Warning: selected agent $CALL_AGENT_ID is configured for websocket '$AGENT_WS_URL'." >&2
    echo "Expected base: '$BRAIN_WSS_BASE_URL' from env." >&2
    if [[ "$BRAIN_WS_FAILOVER_TO_BACKUP" == "true" && "$CALL_AGENT_ID" != "$B2B_AGENT_ID" && -n "$B2B_AGENT_ID" ]]; then
      # already on backup; avoid looping
      echo "Proceeding with current selected agent; review agent config in Retell dashboard if needed." >&2
    elif [[ "$BRAIN_WS_FAILOVER_TO_BACKUP" == "true" && -n "$B2B_AGENT_ID_BACKUP" && "$CALL_AGENT_ID" == "$B2B_AGENT_ID" ]]; then
      echo "Falling back to backup agent id from B2B_AGENT_ID_BACKUP due endpoint mismatch." >&2
      CALL_AGENT_ID="$B2B_AGENT_ID_BACKUP"
    fi
  elif [[ -n "$AGENT_WS_URL" && "$AGENT_WS_URL" != *"llm-websocket"* ]]; then
    echo "Warning: selected agent $CALL_AGENT_ID websocket URL '$AGENT_WS_URL' is not using the llm-websocket route." >&2
  fi
fi

# Select production topology and ensure local brain early (when enabled).
# - Remote/non-local BRAIN_WSS_BASE_URL implies production path (8099 + production watcher).
# - Explicit override via RETELL_FORCE_BRAIN_PORT_8099 can force/disable this behavior.
if [[ "$FORCE_BRAIN_PORT_8099" == "1" || "$FORCE_BRAIN_PORT_8099" == "true" ]]; then
  PRODUCTION_BRAIN_TOPOLOGY=1
elif [[ "$FORCE_BRAIN_PORT_8099" == "0" || "$FORCE_BRAIN_PORT_8099" == "false" ]]; then
  PRODUCTION_BRAIN_TOPOLOGY=0
elif is_remote_non_local_ws_host; then
  PRODUCTION_BRAIN_TOPOLOGY=1
fi

if [[ "$PRODUCTION_BRAIN_TOPOLOGY" == "1" ]]; then
  RETELL_WS_TARGET_PORT="8099"
fi

if [[ "$RETELL_ENSURE_BRAIN" == "true" || "$RETELL_ENSURE_BRAIN" == "1" ]]; then
  WS_HANDSHAKE_LOCAL_PORT="${RETELL_WS_TARGET_PORT}"
  if ! ensure_local_brain "$WS_HANDSHAKE_LOCAL_PORT" "$RETELL_WS_START_TIMEOUT_SEC"; then
    echo "Error: local brain on 127.0.0.1:${WS_HANDSHAKE_LOCAL_PORT} is not available." >&2
    if [[ "$PRODUCTION_BRAIN_TOPOLOGY" == "1" ]]; then
      echo "Fix: start a persistent production brain instance using ${PROD_BRAIN_SCRIPT}." >&2
      echo "Example: ${PROD_BRAIN_SCRIPT} --start" >&2
    else
      echo "Fix: start a persistent instance using ${SUPERVISOR_SCRIPT}." >&2
      echo "Example: ${SUPERVISOR_SCRIPT} --daemon --port ${WS_HANDSHAKE_LOCAL_PORT} --host 127.0.0.1" >&2
    fi
    exit 2
  fi
fi

# Optional local websocket handshake guard.
# If enabled, verify Retell can connect to this public endpoint directly before creating the call.
if [[ "${RETELL_VERIFY_WS_HANDSHAKE:-true}" == "true" && -n "${BRAIN_WSS_BASE_URL:-}" ]]; then
  WS_HANDSHAKE_LOCAL_PORT="${RETELL_WS_TARGET_PORT:-8099}"

  set +e
  hand_msg="$(check_ws_handshake "$BRAIN_WSS_BASE_URL")"
  hand_rc=$?
  set -e
  if [[ "$hand_rc" -ne 0 ]]; then
    echo "Error: websocket endpoint handshake check failed for BRAIN_WSS_BASE_URL='$BRAIN_WSS_BASE_URL'." >&2
    echo "Detail: $hand_msg" >&2
    echo "Retell will usually return error_llm_websocket_open if this is not fixed." >&2
    if [[ "${hand_msg}" == *"HTTP 502"* || "${hand_msg}" == *"handshake failed"* ]]; then
      if ! is_local_listener "$WS_HANDSHAKE_LOCAL_PORT" >/dev/null; then
        echo "Local listener is not active on 127.0.0.1:${WS_HANDSHAKE_LOCAL_PORT}." >&2
        echo "Retrying brain startup before failing..." >&2
        if ! ensure_local_brain "$WS_HANDSHAKE_LOCAL_PORT" "$RETELL_WS_START_TIMEOUT_SEC"; then
          echo "Start the brain on that port, or set RETELL_WS_TARGET_PORT to your active server port." >&2
          echo "Fix by ensuring Cloudflare route stays mapped to an active backend and points to your running brain." >&2
          echo "Run: bash scripts/cloudflare_verify.sh" >&2
          exit 2
        fi
        hand_msg="$(check_ws_handshake "$BRAIN_WSS_BASE_URL")"
        hand_rc=$?
        if [[ "$hand_rc" -eq 0 ]]; then
          echo "Brain restart succeeded; websocket handshake check recovered." >&2
        else
          echo "Detail: $hand_msg" >&2
          echo "Retell will usually return error_llm_websocket_open if this is not fixed." >&2
          echo "Fix by ensuring Cloudflare route stays mapped to an active backend and points to your running brain." >&2
          echo "Run: bash scripts/cloudflare_verify.sh" >&2
          exit 2
        fi
      fi
    fi
  fi
fi

# Final explicit endpoint check if fallback also mismatches.
if [[ "${CALL_AGENT_ID}" != "${B2B_AGENT_ID}" && -n "${BRAIN_WSS_BASE_URL:-}" ]]; then
  echo "Call will use override agent id: $CALL_AGENT_ID" >&2
fi

RESP="$(
  curl -sS -X POST "https://api.retellai.com/v2/create-phone-call" \
    -H "Authorization: Bearer $RETELL_API_KEY" \
    -H "Content-Type: application/json" \
    --data "{\"from_number\":\"$RETELL_FROM_NUMBER\",\"to_number\":\"$TO_NUMBER\",\"override_agent_id\":\"$CALL_AGENT_ID\",\"metadata\":{\"source\":\"dogfood\"}}"
)"

# Default behavior: do NOT print raw JSON (it can include sensitive fields).
# Use PRINT_RAW=1 if you explicitly want to see the full API response.
if [[ "${PRINT_RAW:-0}" == "1" ]]; then
  echo "$RESP"
fi

echo "$RESP" | python3 -c 'import json,sys
try:
  r=json.load(sys.stdin)
except Exception as e:
  print(f"Failed to parse response: {e}", file=sys.stderr)
  raise SystemExit(1)
cid=r.get("call_id","")
status=r.get("call_status","")
if cid:
  print(f"Started call_id={cid} status={status}")
else:
  print("\nRetell response did not include call_id", file=sys.stderr)
  raise SystemExit(1)'

CALL_ID="$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("call_id",""))')"

AUTO_LEARN="${RETELL_AUTO_LEARN_ON_CALL:-true}"
AUTO_LEARN_LOWER="$(printf '%s' "$AUTO_LEARN" | tr '[:upper:]' '[:lower:]')"
if [[ "$AUTO_LEARN_LOWER" == "true" && -n "$CALL_ID" ]]; then
  mkdir -p "$ROOT_DIR/data/retell_calls"
  (
    POLL_SEC="${RETELL_AUTO_LEARN_POLL_SEC:-5}"
    POLL_STEPS="${RETELL_AUTO_LEARN_POLL_STEPS:-180}"
    for _ in $(seq 1 "$POLL_STEPS"); do
      STATUS="$(
        curl -sS -H "Authorization: Bearer $RETELL_API_KEY" \
          "https://api.retellai.com/v2/get-call/$CALL_ID" \
          | python3 -c 'import json,sys; print((json.load(sys.stdin).get("call_status") or "").strip())'
      )"
      if [[ "$STATUS" == "ended" ]]; then
        break
      fi
      sleep "$POLL_SEC"
    done
    python3 "$ROOT_DIR/scripts/retell_learning_loop.py" \
      --limit "${RETELL_LEARN_LIMIT:-100}" \
      --threshold "${RETELL_LEARN_THRESHOLD:-250}" \
      > "$ROOT_DIR/data/retell_calls/_last_auto_learn.log" 2>&1
  ) &
  echo "Auto-learning queued in background for call_id=$CALL_ID"
fi
