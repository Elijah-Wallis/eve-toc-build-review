#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${RETELL_ENV_FILE:-$ROOT_DIR/.env.retell.local}"
BACKUP_JSON="${1:-}"

if [[ -z "$BACKUP_JSON" ]]; then
  echo "Usage: $0 data/retell_agent_backups/<backup>.json" >&2
  exit 2
fi
if [[ ! -f "$BACKUP_JSON" ]]; then
  echo "Missing backup file: $BACKUP_JSON" >&2
  exit 2
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${RETELL_API_KEY:?RETELL_API_KEY is required}"

python3 - <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

api = os.environ["RETELL_API_KEY"].strip()
backup_path = Path(sys.argv[1]).resolve()
backup = json.loads(backup_path.read_text(encoding="utf-8"))
agent_id = str(backup.get("agent_id") or backup.get("id") or "").strip()
if not agent_id:
    raise SystemExit("Backup JSON missing agent_id")

payload = {}
# Restore only the fields we know are safe to patch.
if "response_engine" in backup and backup["response_engine"] is not None:
    payload["response_engine"] = backup["response_engine"]
if "llm_websocket_url" in backup and backup["llm_websocket_url"] is not None:
    payload["llm_websocket_url"] = backup["llm_websocket_url"]

if not payload:
    raise SystemExit("Backup JSON had no response_engine/llm_websocket_url to restore")

cmd = [
    "curl",
    "-sS",
    "-X",
    "PATCH",
    f"https://api.retellai.com/update-agent/{agent_id}",
    "-H",
    f"Authorization: Bearer {api}",
    "-H",
    "Content-Type: application/json",
    "--data",
    json.dumps(payload),
]

out = subprocess.check_output(cmd, text=True)
resp = json.loads(out)
print(json.dumps({"status": "ok", "agent_id": agent_id, "response_engine": resp.get("response_engine")}, indent=2))
PY
"$BACKUP_JSON"
