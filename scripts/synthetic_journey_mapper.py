#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b")
GENERIC_EMAIL_PREFIX = {"info", "admin", "frontdesk", "contact", "hello", "office"}


def _to_ms(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        had_z = value.endswith("Z")
        try:
            return int(value)
        except ValueError:
            pass
        if had_z:
            value = value[:-1]
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None and had_z:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:
            return None
    return None


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _normalize_phone(v: Any) -> str:
    return re.sub(r"\D", "", str(v or ""))


def _jsonl_records(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _load_calls(calls_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    if not calls_dir.exists():
        return rows
    for p in sorted(calls_dir.glob("*/call.json")):
        try:
            call = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(call, dict):
            rows.append((str(p.parent.name), call))
    return rows


def _load_leads_map(path: Path | None) -> dict[str, dict[str, Any]]:
    leads: dict[str, dict[str, Any]] = {}
    if path is None or not path.exists():
        return leads

    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        for rec in _jsonl_records(path):
            lead_id = str(rec.get("lead_id") or rec.get("id") or "").strip()
            if not lead_id:
                continue
            leads[lead_id] = rec
        return leads

    # CSV/TSV
    with path.open("r", encoding="utf-8", newline="") as f:
        delim = "\t" if path.suffix.lower() == ".tsv" else ","
        reader = csv.DictReader(f, delimiter=delim)
        for row in reader:
            lead_id = str((row.get("lead_id") or row.get("id") or "")).strip()
            if lead_id:
                leads[lead_id] = {k: str(v or "").strip() for k, v in row.items()}
    return leads


def _build_lead_index_by_phone(leads: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    for row in leads.values():
        phone = _normalize_phone(row.get("clinic_phone") or row.get("phone") or row.get("to_number"))
        if phone:
            idx[phone] = row
        clinic_id = str(row.get("clinic_id") or "").strip()
        if clinic_id:
            idx[f"cid:{clinic_id}"] = row
    return idx


def _normalize_tool_name(value: Any) -> str:
    return str(value or "").strip().lower()


def _parse_tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _extract_tool_calls(call: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for name in [event["name"] for event in _extract_tool_events(call)]:
        if name not in names:
            names.append(name)
    return names


def _extract_tool_events(call: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    raw = call.get("tool_calls")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                name = _normalize_tool_name(item.get("name") or item.get("tool_name"))
                if name:
                    event = {"name": name}
                    event["arguments"] = _parse_tool_arguments(item.get("arguments"))
                    events.append(event)
    twt = call.get("transcript_with_tool_calls")
    if isinstance(twt, list):
        for item in twt:
            if not isinstance(item, dict):
                continue
            name = _normalize_tool_name(item.get("name") or item.get("tool_name"))
            if not name:
                continue
            if any(e.get("name") == name for e in events):
                continue
            event = {"name": name}
            event["arguments"] = _parse_tool_arguments(item.get("arguments"))
            events.append(event)
    return events


def _extract_recording_followup_requests(tool_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in tool_events:
        if _normalize_tool_name(event.get("name")) != "send_call_recording_followup":
            continue
        args = event.get("arguments") if isinstance(event.get("arguments"), dict) else {}
        out.append(
            {
                "tool": "send_call_recording_followup",
                "campaign_id": str(args.get("campaign_id", "")).strip(),
                "clinic_id": str(args.get("clinic_id", "")).strip(),
                "lead_id": str(args.get("lead_id", "")).strip(),
                "call_id": str(args.get("call_id", "")).strip(),
                "to_number": str(args.get("to_number", "")).strip(),
                "recording_url": str(args.get("recording_url", args.get("call_recording_url", ""))).strip(),
                "recipient_email": str(args.get("recipient_email", "")).strip(),
                "recipient_phone": str(args.get("recipient_phone", "")).strip(),
                "channel": _normalize_tool_name(args.get("channel", args.get("channels", "twilio_sms"))),
                "reason": str(args.get("reason", "queued")).strip().lower(),
                "next_step": str(args.get("next_step", "")).strip(),
                "timestamp_ms": _to_int(args.get("timestamp_ms"), int(time.time() * 1000)),
            }
        )
    return out


def _extract_recording_followup_reasons(requests: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    for item in requests:
        reason = str(item.get("reason", "")).strip().lower()
        if reason and reason not in reasons:
            reasons.append(reason)
    return reasons


def _normalize_transcript(call: dict[str, Any]) -> str:
    raw = str(call.get("transcript") or "").strip()
    if raw:
        return raw.lower()
    tw = call.get("transcript_object")
    if isinstance(tw, list):
        lines: list[str] = []
        for item in tw:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").lower()
            content = str(item.get("content") or "").strip()
            if role and content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines).lower()
    return ""


def _call_outcome_from_analysis(call: dict[str, Any]) -> str:
    analysis = call.get("call_analysis") if isinstance(call.get("call_analysis"), dict) else {}
    if not analysis:
        return ""
    custom = analysis.get("custom_analysis_data")
    if isinstance(custom, dict):
        outcome = str(custom.get("call_outcome") or "").strip().lower()
        if outcome:
            return outcome
    outcome = str(analysis.get("call_outcome") or "").strip().lower()
    return outcome


def _sentiment_from_analysis(call: dict[str, Any]) -> str:
    analysis = call.get("call_analysis") if isinstance(call.get("call_analysis"), dict) else {}
    raw = str(analysis.get("user_sentiment") or "").strip().lower()
    if raw:
        return raw

    score = analysis.get("sentiment_score")
    if isinstance(score, (int, float)):
        if score >= 6:
            return "positive"
        if score <= 4:
            return "negative"
        return "neutral"
    return "unknown"


def _extract_captured_email(
    call: dict[str, Any], transcript: str, tool_names: list[str], tool_events: list[dict[str, Any]] | None = None
) -> str:
    if "send_evidence_package" in [n.lower() for n in tool_names]:
        raw = call.get("tool_calls")
        if isinstance(raw, list):
            for item in raw:
                args = item.get("arguments") if isinstance(item, dict) else None
                if isinstance(args, dict):
                    email = str(args.get("recipient_email") or "").strip()
                    if email:
                        return email
    if tool_events:
        for item in tool_events:
            name = _normalize_tool_name(item.get("name"))
            if name != "send_call_recording_followup":
                continue
            args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            email = str((args.get("recipient_email") or "")).strip()
            if email:
                return email
    for line in EMAIL_RE.findall(transcript):
        email = line.strip().lower()
        local = email.split("@", 1)[0] if "@" in email else ""
        if local and local not in GENERIC_EMAIL_PREFIX:
            return email
    return ""


def _fallback_stage(transcript: str, tool_names: list[str]) -> str:
    if any(_normalize_tool_name(n) == "send_call_recording_followup" for n in tool_names):
        return "voicemail"
    if any(n in {"send_evidence_package"} for n in tool_names):
        return "email_captured"
    if any(n in {"mark_dnc_compliant"} for n in tool_names):
        return "dnc"

    text = transcript.lower()
    if "voicemail" in text or "left voicemail" in text or "didn't answer" in text or "no answer" in text:
        return "voicemail"
    if "not interested" in text or "not a fit" in text or "do not" in text and "call" in text:
        return "rejected"
    if "book" in text or "schedule" in text or "demo" in text or "appointment" in text:
        return "booked_demo"
    if "email" in text:
        return "email_captured"
    return "unknown"


def _normalize_call_outcome(outcome: str, transcript: str, tool_names: list[str]) -> tuple[str, str]:
    raw = str(outcome or "").strip().lower()
    if raw:
        if raw in {"not_interested", "unqualified", "no_show", "bad_fit"}:
            return raw, "rejected"
        if raw in {"booked", "appointment_booked", "booked_demo", "scheduled", "schedule"}:
            return raw, "booked_demo"
        if raw in {"send_evidence", "evidence_sent", "email_sent", "email_captured", "lead_qualified"}:
            return raw, "email_captured"
        if raw in {"dnc", "do_not_call", "compliant_dnc", "dnc_marked"}:
            return raw, "dnc"
    resolved = _fallback_stage(transcript, tool_names)
    return raw or resolved, resolved


def _post_json(url: str, payload: dict[str, Any], timeout_s: int = 15) -> None:
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout_s) as r:
        _ = r.read()


def _load_call_dir_name(call: dict[str, Any], call_dir: str) -> str:
    return str(call.get("call_id") or call_dir or "").strip()


def _transcript_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize synthetic Retell call artifacts into ontology journey rows.")
    parser.add_argument("--calls-dir", default="data/retell_calls")
    parser.add_argument("--lead-file", default="", help="Optional medspa_leads.csv or synthetic call queue jsonl")
    parser.add_argument("--campaign-id", default="", help="Filter/match campaign only (optional)")
    parser.add_argument("--tenant", default="synthetic_medspa")
    parser.add_argument("--out", default="data/retell_calls/synthetic_customer_journeys.jsonl")
    parser.add_argument("--push-webhook", default=os.getenv("N8N_OUTCOME_WEBHOOK_URL", ""))
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    calls = _load_calls(Path(args.calls_dir))
    if not calls:
        print(f"No call artifacts found in: {args.calls_dir}")
        return 2

    lead_rows = _load_leads_map(Path(args.lead_file) if args.lead_file else None)
    lead_by_phone = _build_lead_index_by_phone(lead_rows)

    out_file = Path(args.out)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    out_records: list[dict[str, Any]] = []

    for call_dir, call in calls:
        if not isinstance(call, dict):
            continue

        metadata = call.get("metadata") if isinstance(call.get("metadata"), dict) else {}
        if args.campaign_id:
            meta_campaign = str(metadata.get("campaign_id") or "").strip()
            if meta_campaign and meta_campaign != str(args.campaign_id):
                continue

        to_number = str(call.get("to_number") or call.get("to") or metadata.get("to_number") or "").strip()
        clinic_id = str(metadata.get("clinic_id") or "").strip()
        lead_id = str(metadata.get("lead_id") or "").strip()
        recording_url = str(call.get("recording_url") or "").strip()

        lead = None
        if not lead_id and to_number:
            lead = lead_by_phone.get(_normalize_phone(to_number))
            if lead:
                lead_id = str(lead.get("lead_id") or "").strip()
                clinic_id = clinic_id or str(lead.get("clinic_id") or "")
        if not lead_id and clinic_id:
            lead = lead_by_phone.get(f"cid:{clinic_id}")
            if lead:
                lead_id = str(lead.get("lead_id") or "").strip()
        if lead is None and lead_id:
            lead = lead_by_phone.get(lead_id) or lead_by_phone.get(f"cid:{clinic_id}")

        campaign_id = str(metadata.get("campaign_id") or args.campaign_id or "").strip()
        attempt_number = _to_int(metadata.get("attempt_number") or metadata.get("attempt"), 0)
        attempt_warning_threshold = _to_int(
            metadata.get("attempt_warning_threshold"),
            0,
        )
        if lead and not attempt_number:
            attempt_number = _to_int(lead.get("attempts"), 0)
        if lead and not attempt_warning_threshold:
            attempt_warning_threshold = _to_int(lead.get("attempt_warning_threshold"), 0)
        attempts_exceeded_200 = bool(
            metadata.get("attempts_exceeded_200")
            or (lead and str(lead.get("attempts_exceeded_200", "")).strip().lower() in {"true", "1", "yes"})
            or (attempt_warning_threshold and attempt_number > attempt_warning_threshold)
        )

        transcript = _normalize_transcript(call)
        tool_events = _extract_tool_events(call)
        tool_names = [event["name"] for event in tool_events]
        recording_followup_requests = _extract_recording_followup_requests(tool_events)
        recording_followup_reasons = _extract_recording_followup_reasons(recording_followup_requests)
        outcome = _call_outcome_from_analysis(call)
        raw_outcome = outcome

        if not raw_outcome:
            outcome = _fallback_stage(transcript, tool_names)
            call_outcome = outcome
            conversion_stage = outcome
        else:
            outcome_key, conversion_stage = _normalize_call_outcome(raw_outcome, transcript, tool_names)
            call_outcome = outcome_key

        captured_email = _extract_captured_email(call, transcript, tool_names, tool_events)
        call_status = str(call.get("call_status") or call.get("status") or "").strip().lower() or "unknown"
        sentiment = _sentiment_from_analysis(call)
        duration = call.get("duration_ms")
        call_id = _load_call_dir_name(call, call_dir)
        outcome_ts = _to_ms(call.get("end_timestamp")) or _to_ms(call.get("start_timestamp")) or int(time.time() * 1000)

        rec = {
            "tenant": args.tenant,
            "campaign_id": campaign_id,
            "lead_id": lead_id or "unknown",
            "clinic_id": clinic_id or "unknown",
            "call_id": call_id,
            "to_number": to_number,
            "call_outcome": call_outcome,
            "conversion_stage": conversion_stage,
            "tool_calls": tool_names,
            "tool_call_events": tool_events,
            "captured_email": captured_email,
            "recording_url": recording_url,
            "recording_followup_requested": bool(recording_followup_requests),
            "recording_followup_requests": recording_followup_requests,
            "recording_followup_reasons": recording_followup_reasons,
            "recording_followup_reason": recording_followup_reasons[0] if recording_followup_reasons else "",
            "attempt_number": attempt_number,
            "attempt_warning_threshold": attempt_warning_threshold,
            "attempts_exceeded_200": attempts_exceeded_200,
            "call_status": call_status,
            "sentiment": sentiment,
            "call_duration_ms": int(duration) if isinstance(duration, (int, float)) else None,
            "transcript_hash": _transcript_hash(transcript),
            "outcome_ts": outcome_ts,
        }
        out_records.append(rec)

    with out_file.open("w", encoding="utf-8") as f:
        for rec in out_records:
            f.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")

    payload = {
        "tenant": args.tenant,
        "campaign_id": args.campaign_id or (out_records[0]["campaign_id"] if out_records else ""),
        "count": len(out_records),
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "records": out_records,
    }

    if args.push_webhook:
        try:
            _post_json(args.push_webhook, payload)
            payload["webhook_pushed"] = True
        except Exception as e:
            payload["webhook_pushed"] = False
            payload["webhook_error"] = str(e)

    print(json.dumps({"status": "ok", "out": str(out_file), "count": len(out_records)}, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
