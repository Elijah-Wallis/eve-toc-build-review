#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${RETELL_ENV_FILE:-$ROOT_DIR/.env.retell.local}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${RETELL_API_KEY:?RETELL_API_KEY is required}"
: "${B2B_AGENT_ID:?B2B_AGENT_ID is required}"

# Base URL Retell should connect to. Retell will append /{call_id}.
# Example: wss://YOUR_DOMAIN/llm-websocket
BRAIN_WSS_BASE_URL="${BRAIN_WSS_BASE_URL:-${RETELL_LLM_WEBSOCKET_BASE_URL:-}}"
: "${BRAIN_WSS_BASE_URL:?Set BRAIN_WSS_BASE_URL (e.g. wss://YOUR_DOMAIN/llm-websocket)}"

python3 - <<'PY'
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

api = os.environ["RETELL_API_KEY"].strip()
agent_id = os.environ["B2B_AGENT_ID"].strip()
base_url = os.environ["BRAIN_WSS_BASE_URL"].strip().rstrip("/")

root = Path(__file__).resolve().parents[1]
backup_dir = root / "data" / "retell_agent_backups"
backup_dir.mkdir(parents=True, exist_ok=True)

def curl_json(args: list[str]) -> dict:
    out = subprocess.check_output(args, text=True)
    return json.loads(out)

def try_patch(payload: dict) -> tuple[bool, dict | None, str]:
    data = json.dumps(payload)
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
        data,
    ]
    p = subprocess.run(cmd, text=True, capture_output=True)
    if p.returncode != 0:
        return False, None, (p.stderr.strip() or p.stdout.strip() or "curl_failed")
    try:
        return True, json.loads(p.stdout), ""
    except Exception:
        return False, None, (p.stdout.strip() or "bad_json_response")

agent = curl_json(
    [
        "curl",
        "-sS",
        "-H",
        f"Authorization: Bearer {api}",
        f"https://api.retellai.com/get-agent/{agent_id}",
    ]
)

stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
backup_path = backup_dir / f"{agent_id}_{stamp}.json"
backup_path.write_text(json.dumps(agent, indent=2), encoding="utf-8")

# Best-effort: set response engine to custom LLM websocket.
# Retell docs have evolved; try a small set of candidate payloads.
# We do NOT assume unknown fields exist; we probe and require the response to reflect the requested URL.

candidates: list[tuple[str, dict]] = [
    (
        "response_engine.type=llm-websocket websocket_url",
        {"response_engine": {"type": "llm-websocket", "websocket_url": base_url}},
    ),
    (
        "response_engine.type=llm-websocket llm_websocket_url",
        {"response_engine": {"type": "llm-websocket", "llm_websocket_url": base_url}},
    ),
    (
        "response_engine.type=custom-llm llm_websocket_url",
        {"response_engine": {"type": "custom-llm", "llm_websocket_url": base_url}},
    ),
    (
        "legacy llm_websocket_url",
        {"llm_websocket_url": base_url},
    ),
]

errors: list[str] = []
for label, payload in candidates:
    ok, resp, err = try_patch(payload)
    if not ok or resp is None:
        errors.append(f"{label}: {err}")
        continue

    engine = resp.get("response_engine") or {}
    # Some APIs may echo under response_engine; some may echo a legacy field.
    reflected = (
        (engine.get("websocket_url") == base_url)
        or (engine.get("llm_websocket_url") == base_url)
        or (resp.get("llm_websocket_url") == base_url)
    )
    if not reflected:
        errors.append(f"{label}: patch_ok_but_url_not_reflected")
        continue

    result_path = backup_dir / f"{agent_id}_{stamp}.switched.json"
    result_path.write_text(json.dumps(resp, indent=2), encoding="utf-8")

    # Print minimal non-sensitive confirmation.
    print(
        json.dumps(
            {
                "status": "ok",
                "agent_id": agent_id,
                "brain_ws_base_url": base_url,
                "response_engine": resp.get("response_engine"),
            },
            indent=2,
        )
    )
    sys.exit(0)

# If we get here, switching failed. Leave agent unchanged (only attempted PATCH calls).
# Surface candidate errors and point to backup for manual rollback if needed.
print(
    json.dumps(
        {
            "status": "error",
            "agent_id": agent_id,
            "brain_ws_base_url": base_url,
            "backup_path": str(backup_path.relative_to(root)),
            "attempt_errors": errors,
        },
        indent=2,
    )
)
sys.exit(1)
PY
