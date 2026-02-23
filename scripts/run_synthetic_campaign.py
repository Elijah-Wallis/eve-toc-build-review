#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"calls": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("calls"), dict):
            return data
    except Exception:
        pass
    return {"calls": {}}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _send_call(api_key: str, payload: dict[str, Any], timeout_s: int = 25) -> tuple[bool, dict[str, Any], str]:
    req = Request(
        "https://api.retellai.com/v2/create-phone-call",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout_s) as r:
            raw = r.read().decode("utf-8", errors="ignore")
            response: dict[str, Any]
            if raw:
                response = json.loads(raw)
            else:
                response = {}
            return True, response, ""
    except Exception as e:
        return False, {}, str(e)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run synthetic outbound calls from generated campaign queue.")
    parser.add_argument("--queue-file", default="data/retell_calls/synthetic_campaign_call_queue.jsonl")
    parser.add_argument("--out-dir", default="data/retell_calls")
    parser.add_argument("--state-file", default="data/retell_calls/.synthetic_campaign_state.json")
    parser.add_argument("--max-calls", type=int, default=0, help="Upper bound for calls in this run")
    parser.add_argument(
        "--limit-call-rate",
        nargs="?",
        default=0.0,
        type=float,
        const=1.0,
        help="Sleep seconds between calls (flag only defaults to 1.0)",
    )
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--resume", action="store_true", default=False)
    parser.add_argument("--tenant", default="synthetic_medspa")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    api_key = os.getenv("RETELL_API_KEY", "").strip()
    from_number = os.getenv("RETELL_FROM_NUMBER", "").strip()
    agent_id = os.getenv("B2B_AGENT_ID", "").strip()

    if not args.dry_run and (not api_key or not from_number or not agent_id):
        print("Missing RETELL_API_KEY, RETELL_FROM_NUMBER, or B2B_AGENT_ID")
        return 2

    queue_file = Path(args.queue_file)
    out_dir = Path(args.out_dir)
    state_file = Path(args.state_file)
    out_dir.mkdir(parents=True, exist_ok=True)

    queue = _iter_jsonl(queue_file)
    if not queue:
        print(f"No queue records found: {queue_file}")
        return 2

    state = _load_state(state_file)
    seen = state.get("calls", {}) if isinstance(state.get("calls"), dict) else {}

    dispatched = 0
    attempts = 0
    log_path = out_dir / "synthetic_campaign_dispatch_log.jsonl"

    with log_path.open("a", encoding="utf-8") as log:
        for rec in queue:
            if args.max_calls and dispatched >= args.max_calls:
                break

            lead_id = str(rec.get("lead_id") or "").strip()
            to_number = str(rec.get("to_number") or "").strip()
            campaign_id = str(rec.get("campaign_id") or "").strip() or os.getenv("SYNTHETIC_CAMPAIGN_ID", "")

            if not to_number or not lead_id:
                continue

            if args.resume and lead_id in seen:
                continue

            attempts += 1
            metadata = dict(rec.get("metadata") or {})
            metadata.setdefault("tenant", args.tenant)
            metadata.setdefault("campaign_id", campaign_id)
            metadata.setdefault("lead_id", lead_id)
            metadata.setdefault("clinic_id", str(rec.get("clinic_id") or ""))
            metadata.setdefault("clinic_phone", to_number)
            metadata.setdefault("clinic_name", str(rec.get("clinic_name") or ""))

            payload = {
                "from_number": from_number,
                "to_number": to_number,
                "override_agent_id": agent_id,
                "metadata": metadata,
            }

            if args.dry_run:
                call_id = f"dry-run-{lead_id}"
                result = {
                    "status": "queued",
                    "call_id": call_id,
                    "call_status": "dry-run",
                    "reason": "dry_run_mode",
                }
            else:
                ok, result, err = _send_call(api_key, payload)
                if not ok:
                    result = {"status": "failed", "reason": err}
                if ok:
                    call_id = str(result.get("call_id") or "").strip()
                    if not call_id:
                        call_id = f"unknown-{lead_id}"
                else:
                    call_id = f"failed-{lead_id}"

            log.write(
                json.dumps(
                    {
                        "lead_id": lead_id,
                        "campaign_id": campaign_id,
                        "call_id": str(result.get("call_id") or call_id),
                        "to_number": to_number,
                        "status": str(result.get("status") or "unknown"),
                        "payload": {
                            "from_number": from_number,
                            "to_number": to_number,
                            "override_agent_id": agent_id,
                            "metadata": metadata,
                        },
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            )

            if args.resume and isinstance(seen, dict):
                seen[lead_id] = {
                    "call_id": str(result.get("call_id") or call_id),
                    "lead_id": lead_id,
                    "to_number": to_number,
                    "campaign_id": campaign_id,
                    "status": str(result.get("status") or "unknown"),
                    "timestamp_ms": int(time.time() * 1000),
                }

            if result.get("status") == "failed":
                continue
            dispatched += 1

            if args.limit_call_rate and args.limit_call_rate > 0:
                time.sleep(max(0.0, float(args.limit_call_rate)))

    state["campaign_id"] = os.getenv("SYNTHETIC_CAMPAIGN_ID", "") or str(args.tenant)
    state["out_dir"] = str(out_dir)
    state["calls"] = seen
    state["attempted"] = attempts
    state["dispatched"] = dispatched
    state["last_run_utc"] = int(time.time())
    _save_state(state_file, state)

    print(
        json.dumps(
            {
                "status": "ok",
                "attempts": attempts,
                "dispatched": dispatched,
                "dry_run": bool(args.dry_run),
                "out_dir": str(out_dir),
                "state_file": str(state_file),
                "log_file": str(log_path),
            },
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
