#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${RETELL_ENV_FILE:-$ROOT_DIR/.env.retell.local}"
PROMPT_FILE="${RETELL_PROMPT_FILE:-$ROOT_DIR/scripts/prompts/b2b_fast_plain.prompt.txt}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  exit 2
fi
if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "Missing prompt file: $PROMPT_FILE" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${RETELL_API_KEY:?RETELL_API_KEY is required}"
: "${B2B_AGENT_ID:?B2B_AGENT_ID is required}"

BEGIN_AFTER_USER_SILENCE_MS="${BEGIN_AFTER_USER_SILENCE_MS:-10}"
VOICE_SPEED="${VOICE_SPEED:-1.05}"
MODEL_TEMPERATURE="${MODEL_TEMPERATURE:-0.03}"
STT_ENDPOINTING_MS="${STT_ENDPOINTING_MS:-5}"
MODEL_HIGH_PRIORITY="${MODEL_HIGH_PRIORITY:-true}"
LLM_MODEL="${LLM_MODEL:-gemini-2.5-flash-lite}"
START_SPEAKER="${START_SPEAKER:-user}"
TRIM_TOOLS="${TRIM_TOOLS:-true}"
export ROOT_DIR
export RETELL_PROMPT_FILE="$PROMPT_FILE"
export BEGIN_AFTER_USER_SILENCE_MS
export VOICE_SPEED
export MODEL_TEMPERATURE
export STT_ENDPOINTING_MS
export MODEL_HIGH_PRIORITY
export LLM_MODEL
export START_SPEAKER
export TRIM_TOOLS

python3 - <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

api = os.environ["RETELL_API_KEY"]
agent_id = os.environ["B2B_AGENT_ID"]
prompt_file = Path(os.environ.get("RETELL_PROMPT_FILE") or "")
if not prompt_file:
    root = Path(os.environ["ROOT_DIR"])
    prompt_file = root / "scripts" / "prompts" / "b2b_fast_plain.prompt.txt"
prompt = prompt_file.read_text(encoding="utf-8")

def curl_json(args: list[str]) -> dict:
    out = subprocess.check_output(args, text=True)
    return json.loads(out)

agent = curl_json(
    [
        "curl",
        "-sS",
        "-H",
        f"Authorization: Bearer {api}",
        f"https://api.retellai.com/get-agent/{agent_id}",
    ]
)
llm_id = agent.get("response_engine", {}).get("llm_id")
if not llm_id:
    raise SystemExit("Could not resolve llm_id from get-agent response")

llm_payload = {
    "model": os.environ["LLM_MODEL"],
    "start_speaker": os.environ["START_SPEAKER"],
    "general_prompt": prompt,
    "begin_after_user_silence_ms": int(os.environ["BEGIN_AFTER_USER_SILENCE_MS"]),
    "model_temperature": float(os.environ["MODEL_TEMPERATURE"]),
    "model_high_priority": os.environ.get("MODEL_HIGH_PRIORITY", "true").lower() in {"1", "true", "yes", "on"},
}

if os.environ.get("TRIM_TOOLS", "true").lower() in {"1", "true", "yes", "on"}:
    current_llm = curl_json(
        [
            "curl",
            "-sS",
            "-H",
            f"Authorization: Bearer {api}",
            f"https://api.retellai.com/get-retell-llm/{llm_id}",
        ]
    )
    keep = {
        "end_call",
        "send_evidence_package",
        "mark_dnc_compliant",
        "log_call_outcome",
        "set_follow_up_plan",
        "set_followup",
        "send_call_recording_followup",
    }
    tools = current_llm.get("general_tools") or []
    trimmed = [t for t in tools if (t.get("name") or "") in keep]
    if trimmed:
        llm_payload["general_tools"] = trimmed
llm = curl_json(
    [
        "curl",
        "-sS",
        "-X",
        "PATCH",
        f"https://api.retellai.com/update-retell-llm/{llm_id}",
        "-H",
        f"Authorization: Bearer {api}",
        "-H",
        "Content-Type: application/json",
        "--data",
        json.dumps(llm_payload),
    ]
)

agent_payload = {
    "responsiveness": 1.0,
    "interruption_sensitivity": 1.0,
    "begin_message_delay_ms": 0,
    "enable_backchannel": False,
    "normalize_for_speech": False,
    "voice_speed": float(os.environ["VOICE_SPEED"]),
    "custom_stt_config": {"provider": "deepgram", "endpointing_ms": int(os.environ["STT_ENDPOINTING_MS"])},
}
updated_agent = curl_json(
    [
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
        json.dumps(agent_payload),
    ]
)

print(
    json.dumps(
        {
            "status": "ok",
            "agent_id": agent_id,
            "llm_id": llm_id,
            "llm_begin_after_user_silence_ms": llm.get("begin_after_user_silence_ms"),
            "llm_start_speaker": llm.get("start_speaker"),
            "llm_model": llm.get("model"),
            "llm_model_temperature": llm.get("model_temperature"),
            "agent_voice_speed": updated_agent.get("voice_speed"),
            "agent_responsiveness": updated_agent.get("responsiveness"),
            "agent_interruption_sensitivity": updated_agent.get("interruption_sensitivity"),
            "agent_stt_endpointing_ms": (updated_agent.get("custom_stt_config") or {}).get("endpointing_ms"),
        },
        indent=2,
    )
)
PY
