#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


PHONE_RE = re.compile(r"[^0-9+]")
MAX_CALLS_CLAMP = 2000
MAX_CONCURRENCY_CLAMP = 100


def _normalize_phone(v: Any) -> str:
    s = PHONE_RE.sub("", str(v or ""))
    if not s:
        return ""
    if s.startswith("++"):
        s = s[1:]
    if s.startswith("+"):
        return s
    if len(s) >= 10:
        return f"+{s}"
    return s


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except Exception:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _parse_call_window(window: Any) -> tuple[int, int] | None:
    raw = str(window or "").strip()
    if not raw:
        return None
    if "-" not in raw:
        return None
    start_raw, end_raw = raw.split("-", 1)
    try:
        start_h, start_m = [int(x.strip()) for x in start_raw.split(":")]
        end_h, end_m = [int(x.strip()) for x in end_raw.split(":")]
        if not (0 <= start_h < 24 and 0 <= start_m < 60 and 0 <= end_h < 24 and 0 <= end_m < 60):
            return None
        return start_h * 60 + start_m, end_h * 60 + end_m
    except Exception:
        return None


def _is_within_call_window(local_now: datetime, window: Any) -> bool:
    parsed = _parse_call_window(window)
    if parsed is None:
        return True
    start_min, end_min = parsed
    current_minute = local_now.hour * 60 + local_now.minute
    if start_min <= end_min:
        return start_min <= current_minute <= end_min
    return current_minute >= start_min or current_minute <= end_min


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "campaigns": {},
            "calls": {},
            "created_utc": int(time.time()),
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return {
        "campaigns": {},
        "calls": {},
        "created_utc": int(time.time()),
    }


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _coerce_int(value: Any, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        coerced = int(value)
    except Exception:
        return default
    if min_value is not None and coerced < min_value:
        coerced = min_value
    if max_value is not None and coerced > max_value:
        coerced = max_value
    return coerced


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    value_str = str(value).strip().lower()
    if value_str in {"1", "true", "yes", "on", "y"}:
        return True
    if value_str in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _load_dispatch_controls(
    path: Path,
    *,
    fallback_max_calls: int,
    fallback_concurrency: int,
) -> dict[str, Any]:
    default_controls = {
        "max_calls": _coerce_int(fallback_max_calls, 0, min_value=0, max_value=MAX_CALLS_CLAMP),
        "concurrency": _coerce_int(fallback_concurrency, 20, min_value=1, max_value=MAX_CONCURRENCY_CLAMP),
        "stop_requested": False,
        "source": "live-campaign-runner",
    }
    if not path.exists():
        return default_controls
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_controls
    if not isinstance(raw, dict):
        return default_controls
    max_calls = _coerce_int(raw.get("max_calls"), fallback_max_calls, min_value=0, max_value=MAX_CALLS_CLAMP)
    concurrency = _coerce_int(raw.get("concurrency"), fallback_concurrency, min_value=1, max_value=MAX_CONCURRENCY_CLAMP)
    stop_requested = _coerce_bool(raw.get("stop_requested"), _coerce_bool(raw.get("stop"), False))
    return {
        **default_controls,
        "max_calls": max_calls,
        "concurrency": concurrency,
        "stop_requested": stop_requested,
    }


def _persist_dispatch_controls(
    path: Path,
    controls: dict[str, Any],
) -> None:
    payload = {
        "max_calls": int(controls.get("max_calls", 0)),
        "concurrency": int(controls.get("concurrency", 20)),
        "stop_requested": bool(controls.get("stop_requested", False)),
        "source": str(controls.get("source", "live-campaign-runner")),
        "updated_utc": int(time.time()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _send_call(
    *,
    api_key: str,
    from_number: str,
    to_number: str,
    agent_id: str,
    metadata: dict[str, Any],
    dry_run: bool,
) -> tuple[bool, dict[str, Any], str]:
    if dry_run:
        return True, {
            "status": "queued",
            "call_id": f"dry-run-{metadata.get('lead_id')}",
        }, ""

    payload = {
        "from_number": from_number,
        "to_number": to_number,
        "override_agent_id": agent_id,
        "metadata": metadata,
    }
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
        with urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", errors="ignore")
            if raw:
                response = json.loads(raw)
            else:
                response = {}
        return True, response, ""
    except Exception as e:
        return False, {}, str(e)


def _thread_safe_load_default_state(state: dict[str, Any], campaign_id: str) -> dict[str, Any]:
    campaigns = state.get("campaigns")
    if not isinstance(campaigns, dict):
        campaigns = {}
        state["campaigns"] = campaigns
    campaign = campaigns.get(campaign_id)
    if not isinstance(campaign, dict):
        campaign = {"daily_count": 0, "daily_date": _today_utc()}
        campaigns[campaign_id] = campaign
    return campaign


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Run live campaign calls from lead queue JSONL.")
    ap.add_argument("--queue-file", default="data/leads/live_call_queue.jsonl")
    ap.add_argument("--state-file", default="data/leads/.live_campaign_state.json")
    ap.add_argument("--out-dir", default="data/retell_calls")
    ap.add_argument("--campaign-id", default=os.getenv("CAMPAIGN_ID", "ont-live-001"))
    ap.add_argument("--tenant", default="live_medspa")
    ap.add_argument("--max-calls", type=int, default=0, help="Upper bound for calls this run (0=unlimited by caps).")
    ap.add_argument("--daily-call-cap", type=int, default=int(os.getenv("CAMPAIGN_DAILY_CALL_CAP", "3")))
    ap.add_argument("--max-attempts", type=int, default=int(os.getenv("CAMPAIGN_MAX_ATTEMPTS", "500")))
    ap.add_argument(
        "--attempt-warning-threshold",
        type=int,
        default=int(os.getenv("CAMPAIGN_ATTEMPT_WARNING_THRESHOLD", "200")),
    )
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--limit-call-rate", nargs="?", default=0.0, type=float, const=1.0)
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--resume", action="store_true", default=False)
    ap.add_argument("--allow-after-hours-calls", dest="allow_after_hours_calls", action="store_true", default=True)
    ap.add_argument("--no-after-hours-calls", dest="allow_after_hours_calls", action="store_false", help="Disable outside-hours calling.")
    ap.add_argument(
        "--controls-file",
        default=os.getenv("LIVE_DISPATCH_CONTROL_FILE", "data/leads/.live_dispatch_controls.json"),
        help="JSON control file for max_calls / concurrency / stop flag.",
    )
    ap.add_argument(
        "--stop-reasons",
        default="dnc,closed,invalid,contacted,booked",
        help="Comma-separated terminal outcomes.",
    )
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.getenv("RETELL_API_KEY", "").strip()
    from_number = os.getenv("RETELL_FROM_NUMBER", "").strip()
    agent_id = os.getenv("B2B_AGENT_ID", "").strip()
    if not args.dry_run and (not api_key or not from_number or not agent_id):
        print("Missing RETELL_API_KEY, RETELL_FROM_NUMBER, or B2B_AGENT_ID")
        return 2

    control_path = Path(args.controls_file)
    controls = _load_dispatch_controls(
        control_path,
        fallback_max_calls=args.max_calls,
        fallback_concurrency=args.concurrency,
    )
    if controls.get("stop_requested"):
        print(json.dumps(
            {
                "status": "stopped",
                "reason": "dashboard_stop_flag",
                "max_calls": controls.get("max_calls", args.max_calls),
                "concurrency": controls.get("concurrency", args.concurrency),
                "campaign_id": args.campaign_id,
            },
            sort_keys=True,
            indent=2,
        ))
        return 0
    args.max_calls = _coerce_int(controls.get("max_calls"), args.max_calls, min_value=0, max_value=MAX_CALLS_CLAMP)
    args.concurrency = _coerce_int(controls.get("concurrency"), args.concurrency, min_value=1, max_value=MAX_CONCURRENCY_CLAMP)

    queue = _iter_jsonl(Path(args.queue_file))
    if not queue:
        print(f"No queue records found: {args.queue_file}")
        return 2

    stop_reasons = {x.strip().lower() for x in args.stop_reasons.split(",") if x.strip()}
    state = _load_state(Path(args.state_file))
    calls = state.get("calls")
    if not isinstance(calls, dict):
        calls = {}
        state["calls"] = calls

    campaign_state = _thread_safe_load_default_state(state, args.campaign_id)
    today = _today_utc()
    if campaign_state.get("daily_date") != today:
        campaign_state["daily_date"] = today
        campaign_state["daily_count"] = 0
    daily_count = int(campaign_state.get("daily_count", 0))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "live_campaign_dispatch_log.jsonl"

    selected: list[dict[str, Any]] = []
    local_now = datetime.now().astimezone()
    for rec in queue:
        lead_id = str(rec.get("lead_id") or "").strip()
        if not lead_id:
            continue
        to_number = _normalize_phone(rec.get("to_number") or rec.get("clinic_phone") or rec.get("phone"))
        if not to_number:
            continue

        if args.resume and str(lead_id) in calls:
            continue
        state_entry = calls.get(str(lead_id), {})
        if not isinstance(state_entry, dict):
            state_entry = {}
        if _normalize_phone(state_entry.get("to_number") or "") != to_number:
            state_entry["to_number"] = to_number
        reason = str(
            state_entry.get("lead_status")
            or state_entry.get("status")
            or state_entry.get("last_status")
            or rec.get("lead_status")
            or ""
        ).strip().lower()
        if reason and reason in stop_reasons:
            continue
        attempts = int(state_entry.get("attempts", 0) or 0)
        warning_threshold = int(args.attempt_warning_threshold)
        state_entry["attempt_warning_threshold"] = warning_threshold
        state_entry["attempts_exceeded_200"] = attempts > warning_threshold and warning_threshold > 0
        in_business_hours = _is_within_call_window(local_now, rec.get("call_hours"))
        after_hours = not in_business_hours
        if not args.allow_after_hours_calls and after_hours:
            continue
        if after_hours and state_entry.get("after_hours_call_once_done", False):
            continue
        if attempts >= int(args.max_attempts):
            continue
        if int(daily_count) >= int(args.daily_call_cap):
            continue
        selected.append(rec)

    selected.sort(
        key=lambda r: (
            int(
                r.get("segment_score", 0)
                if isinstance(r.get("segment_score"), (int, float))
                else _to_int(r.get("segment_score"))
            ),
            int(r.get("last_action_ts", 0) or 0),
        ),
        reverse=True,
    )

    if args.max_calls and args.max_calls > 0:
        selected = selected[: args.max_calls]

    dispatched = 0
    attempts = 0

    with ThreadPoolExecutor(max_workers=max(1, int(args.concurrency))) as pool:
        futures = {}
        to_process = 0

        for rec in selected:
            if not args.max_calls or to_process < int(args.max_calls):
                lead_id = str(rec.get("lead_id") or "").strip()
                call_state = calls.get(lead_id, {})
                if not isinstance(call_state, dict):
                    call_state = {}
                to_number = _normalize_phone(rec.get("to_number") or rec.get("clinic_phone") or rec.get("phone"))
                attempt_number = int(call_state.get("attempts", 0)) + 1
                warning_threshold = int(args.attempt_warning_threshold or 0)
                call_window = str(rec.get("call_hours") or "09:00-18:00").strip() or "09:00-18:00"
                in_business_hours = _is_within_call_window(datetime.now().astimezone(), call_window)
                after_hours = not in_business_hours
                call_state["lead_id"] = lead_id
                call_state["campaign_id"] = str(rec.get("campaign_id") or args.campaign_id).strip()
                future = pool.submit(
                    _send_call,
                    api_key=api_key,
                    from_number=from_number,
                    to_number=to_number,
                    agent_id=agent_id,
                    metadata={
                        "tenant": args.tenant,
                        "campaign_id": str(rec.get("campaign_id") or args.campaign_id).strip(),
                        "campaign_name": str(rec.get("campaign_name") or "").strip(),
                        "clinic_id": str(rec.get("clinic_id") or "").strip(),
                        "lead_id": lead_id,
                        "clinic_name": str(
                            rec.get("clinic_name")
                            or rec.get("business_name")
                            or rec.get("name")
                            or rec.get("practice_name")
                            or rec.get("practice")
                            or ""
                        ).strip(),
                        "business_name": str(
                            rec.get("business_name")
                            or rec.get("clinic_name")
                            or rec.get("name")
                            or rec.get("practice_name")
                            or rec.get("practice")
                            or ""
                        ).strip(),
                        "clinic_phone": to_number,
                        "call_window": call_window,
                        "call_window_type": "after_hours" if after_hours else "business_hours",
                        "call_segment": str(rec.get("lead_segment") or "").strip(),
                        "segment_score": str(rec.get("segment_score") or 0),
                        "attempt_number": attempt_number,
                        "attempt_warning_threshold": warning_threshold,
                        "attempts_exceeded_200": attempt_number > warning_threshold and warning_threshold > 0,
                    },
                    dry_run=bool(args.dry_run),
                )
                futures[future] = (lead_id, rec, to_number, after_hours, call_window)
                to_process += 1
            if args.max_calls and to_process >= int(args.max_calls):
                break

        with log_path.open("a", encoding="utf-8") as log:
            for future in as_completed(futures):
                lead_id, rec, to_number, after_hours, call_window = futures[future]
                ok, result, err = future.result()
                attempts += 1
                call_id = str(result.get("call_id") or "").strip() if isinstance(result, dict) else ""
                if not call_id:
                    call_id = f"failed-{lead_id}"

                if not ok:
                    status = "failed"
                    reason = err
                else:
                    status = str(result.get("status") or "queued")
                    reason = "ok"

                record_state = calls.get(lead_id)
                if not isinstance(record_state, dict):
                    record_state = {"lead_id": lead_id}
                record_state.update(
                    {
                        "lead_id": lead_id,
                        "campaign_id": str(rec.get("campaign_id") or args.campaign_id),
                        "call_id": call_id,
                        "to_number": to_number,
                        "attempts": int(record_state.get("attempts", 0)) + 1,
                        "attempt_warning_threshold": int(args.attempt_warning_threshold),
                        "attempts_exceeded_200": (
                            int(record_state.get("attempts", 0)) + 1 > int(args.attempt_warning_threshold)
                            if int(args.attempt_warning_threshold) > 0
                            else False
                        ),
                        "lead_status": status,
                        "status": status,
                        "call_window": call_window,
                        "call_window_type": "after_hours" if after_hours else "business_hours",
                        "after_hours_call_once_done": bool(after_hours),
                        "last_status": status,
                        "reason": reason,
                        "timestamp_utc": int(time.time()),
                        "campaign_id_filter": str(rec.get("campaign_id") or args.campaign_id),
                    }
                )
                calls[lead_id] = record_state
                if status != "failed":
                    dispatched += 1
                    campaign_state["daily_count"] = int(campaign_state.get("daily_count", 0)) + 1
                elif status == "failed":
                    pass
                if args.dry_run:
                    reason = "dry_run_mode"
                    status = "dry_run"

                log.write(
                    json.dumps(
                        {
                            "lead_id": lead_id,
                            "campaign_id": str(rec.get("campaign_id") or args.campaign_id),
                            "call_id": call_id,
                            "to_number": to_number,
                            "call_window": call_window,
                            "call_window_type": "after_hours" if after_hours else "business_hours",
                            "status": status,
                            "reason": reason,
                            "attempt": int(record_state.get("attempts", 0)),
                            "attempts_exceeded_200": bool(record_state.get("attempts_exceeded_200", False)),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )

                if args.limit_call_rate and args.limit_call_rate > 0:
                    time.sleep(float(args.limit_call_rate))

    state["campaigns"] = state.get("campaigns", {})
    state.setdefault("campaigns", {})
    state["campaigns"][str(args.campaign_id)] = campaign_state
    state["calls"] = calls
    state["last_run_utc"] = int(time.time())
    _save_state(Path(args.state_file), state)

    print(
        json.dumps(
            {
                "status": "ok",
                "dispatched": dispatched,
                "attempts": attempts,
                "queue_size": len(queue),
                "selected": len(selected),
                "daily_count": int(campaign_state.get("daily_count", 0)),
                "dry_run": bool(args.dry_run),
                "max_calls": int(args.max_calls),
                "concurrency": int(args.concurrency),
                "state_file": str(args.state_file),
                "log_file": str(log_path),
            },
            sort_keys=True,
            indent=2,
        )
    )
    controls.update({"stop_requested": False})
    _persist_dispatch_controls(control_path, controls)
    return 0


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
