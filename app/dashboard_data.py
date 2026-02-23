from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any


_TYPE_RE = re.compile(r"^#\s*TYPE\s+([a-zA-Z_:][a-zA-Z0-9_:]*)\s+(counter|gauge|histogram)\s*$")
_SAMPLE_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([-+]?[0-9]+(?:\.[0-9]+)?)$")
_LE_RE = re.compile(r'le="([^"]+)"')


def parse_prometheus_text(text: str) -> tuple[dict[str, float], dict[str, float], dict[str, dict[str, float]]]:
    types: dict[str, str] = {}
    counters: dict[str, float] = {}
    gauges: dict[str, float] = {}
    hist_buckets: dict[str, dict[str, float]] = {}

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m_type = _TYPE_RE.match(line)
        if m_type:
            types[m_type.group(1)] = m_type.group(2)
            continue
        if line.startswith("#"):
            continue

        m_sample = _SAMPLE_RE.match(line)
        if not m_sample:
            continue
        name = m_sample.group(1)
        labels = m_sample.group(2) or ""
        value = float(m_sample.group(3))

        if name.endswith("_bucket"):
            base = name[: -len("_bucket")]
            m_le = _LE_RE.search(labels)
            if m_le is None:
                continue
            le = m_le.group(1)
            hist_buckets.setdefault(base, {})[le] = value
            continue

        t = types.get(name, "")
        if t == "counter":
            counters[name] = value
        elif t == "gauge":
            gauges[name] = value

    return counters, gauges, hist_buckets


def histogram_quantile_from_buckets(buckets: dict[str, float], q: float) -> float | None:
    if not buckets:
        return None
    items: list[tuple[float, float]] = []
    inf_count: float | None = None
    for le_str, count in buckets.items():
        if le_str == "+Inf":
            inf_count = float(count)
            continue
        try:
            items.append((float(le_str), float(count)))
        except Exception:
            continue
    items.sort(key=lambda x: x[0])
    if inf_count is None:
        if not items:
            return None
        inf_count = items[-1][1]
    if inf_count <= 0:
        return None

    target = max(1.0, math.ceil(float(q) * float(inf_count)))
    for le, cumulative in items:
        if cumulative >= target:
            return le
    if items:
        return items[-1][0]
    return None


def _state_for_threshold(value: float | None, *, target: float, op: str) -> str:
    if value is None:
        return "unknown"
    if op == "lte":
        return "pass" if value <= target else "fail"
    if op == "eq":
        return "pass" if value == target else "fail"
    return "unknown"


def build_dashboard_summary(metrics_text: str) -> dict[str, Any]:
    counters, gauges, hists = parse_prometheus_text(metrics_text)

    ack_p95 = histogram_quantile_from_buckets(hists.get("vic_turn_final_to_ack_segment_ms", {}), 0.95)
    first_p95 = histogram_quantile_from_buckets(hists.get("vic_turn_final_to_first_segment_ms", {}), 0.95)
    cancel_p95 = histogram_quantile_from_buckets(hists.get("vic_barge_in_cancel_latency_ms", {}), 0.95)

    checks = [
        {
            "id": "ack_p95",
            "title": "ACK latency p95",
            "target": "<=300ms",
            "value": ack_p95,
            "state": _state_for_threshold(ack_p95, target=300, op="lte"),
            "laymen": "How fast Eve acknowledges users.",
            "technical": "vic_turn_final_to_ack_segment_ms p95",
            "fix": "Inspect queue pressure and writer backpressure timeout metrics.",
        },
        {
            "id": "first_content_p95",
            "title": "First response p95",
            "target": "<=700ms",
            "value": first_p95,
            "state": _state_for_threshold(first_p95, target=700, op="lte"),
            "laymen": "How fast Eve starts giving real content.",
            "technical": "vic_turn_final_to_first_segment_ms p95",
            "fix": "Reduce tool latency and model timeout/filler thresholds.",
        },
        {
            "id": "barge_cancel_p95",
            "title": "Barge-in cancel p95",
            "target": "<=250ms",
            "value": cancel_p95,
            "state": _state_for_threshold(cancel_p95, target=250, op="lte"),
            "laymen": "How fast Eve stops talking when user interrupts.",
            "technical": "vic_barge_in_cancel_latency_ms p95",
            "fix": "Tune interruption sensitivity and cancel path latency.",
        },
        {
            "id": "reasoning_leak",
            "title": "Reasoning leakage",
            "target": "==0",
            "value": int(counters.get("voice_reasoning_leak_total", 0)),
            "state": _state_for_threshold(float(counters.get("voice_reasoning_leak_total", 0)), target=0, op="eq"),
            "laymen": "Internal chain-of-thought is not exposed to users.",
            "technical": "voice_reasoning_leak_total",
            "fix": "Keep plain-language policy and guardrail transforms enabled.",
        },
        {
            "id": "jargon_violation",
            "title": "Jargon violations",
            "target": "==0",
            "value": int(counters.get("voice_jargon_violation_total", 0)),
            "state": _state_for_threshold(float(counters.get("voice_jargon_violation_total", 0)), target=0, op="eq"),
            "laymen": "Eve responses stay understandable.",
            "technical": "voice_jargon_violation_total",
            "fix": "Adjust readability filters and phrasing templates.",
        },
    ]

    failing = sum(1 for c in checks if c["state"] == "fail")
    passing = sum(1 for c in checks if c["state"] == "pass")
    unknown = sum(1 for c in checks if c["state"] == "unknown")

    status = "green"
    if failing > 0:
        status = "red"
    elif passing == 0:
        status = "gray"

    skills_inv = int(counters.get("skills_invocations_total", 0))
    skills_hit = int(counters.get("skills_hit_total", 0))
    skills_hit_rate_pct = round((skills_hit / skills_inv) * 100.0, 1) if skills_inv > 0 else None

    return {
        "status": status,
        "checks": checks,
        "totals": {
            "passing": passing,
            "failing": failing,
            "unknown": unknown,
        },
        "memory": {
            "transcript_chars_current": int(gauges.get("memory_transcript_chars_current", 0)),
            "transcript_utterances_current": int(gauges.get("memory_transcript_utterances_current", 0)),
        },
        "skills": {
            "invocations_total": skills_inv,
            "hit_total": skills_hit,
            "hit_rate_pct": skills_hit_rate_pct,
            "error_total": int(counters.get("skills_error_total", 0)),
        },
        "shell": {
            "exec_total": int(counters.get("shell_exec_total", 0)),
            "exec_denied_total": int(counters.get("shell_exec_denied_total", 0)),
            "exec_timeout_total": int(counters.get("shell_exec_timeout_total", 0)),
        },
        "self_improve": {
            "cycles_total": int(counters.get("self_improve_cycles_total", 0)),
            "proposals_total": int(counters.get("self_improve_proposals_total", 0)),
            "applies_total": int(counters.get("self_improve_applies_total", 0)),
            "blocked_on_gates_total": int(counters.get("self_improve_blocked_on_gates_total", 0)),
        },
        "context": {
            "compactions_total": int(counters.get("context_compactions_total", 0)),
            "compaction_tokens_saved_total": int(counters.get("context_compaction_tokens_saved_total", 0)),
        },
    }


def build_repo_map(repo_root: Path) -> dict[str, Any]:
    components = [
        {
            "id": "runtime_core",
            "title": "Runtime Core",
            "path": "app/",
            "laymen": "The live brain that takes calls and responds.",
            "technical": "FastAPI server, orchestrator, policy, tool routing, metrics.",
        },
        {
            "id": "automation_scripts",
            "title": "Automation Scripts",
            "path": "scripts/",
            "laymen": "Operational commands that keep Eve healthy.",
            "technical": "Acceptance runners, scorecards, self-improve cycle, metrics tools.",
        },
        {
            "id": "tests_contracts",
            "title": "Tests and Contracts",
            "path": "tests/",
            "laymen": "Proof that behavior is stable and safe.",
            "technical": "Unit, contract, replay, latency, policy, and regression tests.",
        },
        {
            "id": "skills_library",
            "title": "Skills Library",
            "path": "skills/",
            "laymen": "Reusable methods Eve can apply to solve tasks faster.",
            "technical": "Markdown skill artifacts loaded and injected by retriever.",
        },
        {
            "id": "knowledge_docs",
            "title": "Knowledge and SOP",
            "path": "docs/",
            "laymen": "How the system is operated and improved safely.",
            "technical": "Runbooks, self-improve SOP, and operational references.",
        },
    ]

    for c in components:
        p = repo_root / c["path"]
        c["exists"] = p.exists()
        c["files"] = sum(1 for _ in p.rglob("*") if _.is_file()) if p.exists() else 0

    top_level = []
    for p in sorted(repo_root.iterdir(), key=lambda x: x.name.lower()):
        if p.name.startswith("."):
            continue
        if p.name in {".venv", "retell_ws_brain.egg-info", "__pycache__"}:
            continue
        top_level.append({
            "name": p.name,
            "type": "dir" if p.is_dir() else "file",
        })

    sop_docs = [
        "docs/self_improve_sop.md",
        "README.md",
        "soul.md",
    ]

    return {
        "repo_root": str(repo_root),
        "components": components,
        "top_level": top_level,
        "sop_docs": sop_docs,
    }


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
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
                rows.append(rec)
    return rows


def _read_live_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(raw, dict):
        return raw
    return {}


def _normalize_tool_name(name: Any) -> str:
    return str(name or "").strip().replace(" ", "_").lower()


def _normalize_phone(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _extract_transcript_text(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        lines = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            content = str(row.get("content", "")).strip()
            if not content:
                continue
            speaker = str(row.get("role", "")).strip()
            if speaker:
                lines.append(f"{speaker}: {content}")
            else:
                lines.append(content)
        return "\n".join(lines).strip()
    return ""


def _extract_transcript_text_with_fallback(raw: Any, transcript_file: Path | None = None) -> str:
    text = _extract_transcript_text(raw)
    if text:
        return text
    if transcript_file is None or not transcript_file.exists():
        return ""
    try:
        return transcript_file.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _snippet(text: str, max_len: int = 180) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _build_lead_index(queue_rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for row in queue_rows:
        phone = _normalize_phone(row.get("phone") or row.get("to") or row.get("to_number") or "")
        lead_id = str(row.get("lead_id") or row.get("id") or "").strip()
        clinic_id = str(row.get("clinic_id") or row.get("practice_id") or "").strip()
        clinic_name = str(
            row.get("business_name")
            or row.get("clinic_name")
            or row.get("practice_name")
            or row.get("name")
            or row.get("practice")
            or ""
        ).strip()
        if not clinic_name:
            continue
        metadata = {
            "clinic_name": clinic_name,
            "clinic_id": clinic_id or lead_id,
            "lead_id": lead_id,
        }
        if phone:
            index[f"phone:{phone}"] = metadata
        if lead_id:
            index[f"lead:{lead_id}"] = metadata
        if clinic_id:
            index[f"clinic:{clinic_id}"] = metadata
    return index


def _resolve_business_info(
    lead_index: dict[str, dict[str, str]],
    *,
    to_number: str,
    clinic_id: str = "",
    lead_id: str = "",
) -> dict[str, str]:
    normalized_phone = _normalize_phone(to_number)
    if normalized_phone:
        lead = lead_index.get(f"phone:{normalized_phone}")
        if lead:
            return lead
    if lead_id:
        lead = lead_index.get(f"lead:{lead_id}")
        if lead:
            return lead
    if clinic_id:
        lead = lead_index.get(f"clinic:{clinic_id}")
        if lead:
            return lead
    return {}


def build_outbound_pipeline_status(repo_root: Path, *, campaign_id: str | None = None, tenant: str | None = None) -> dict[str, Any]:
    queue_file = repo_root / "data" / "leads" / "live_call_queue.jsonl"
    backup_queue = repo_root / "data" / "leads" / "call_queue.jsonl"
    state_file = repo_root / "data" / "leads" / ".live_campaign_state.json"
    live_leads_file = repo_root / "data" / "leads" / "live_leads.csv"
    queue_rows = _read_jsonl_records(queue_file)
    active_queue_file = queue_file
    if not queue_rows and backup_queue.exists():
        queue_rows = _read_jsonl_records(backup_queue)
        active_queue_file = backup_queue
    state = _read_live_state(state_file)
    lead_index = _build_lead_index(queue_rows)

    campaigns = state.get("campaigns")
    if not isinstance(campaigns, dict):
        campaigns = {}

    selected_campaign_id = campaign_id
    if not selected_campaign_id:
        selected_campaign_id = next(iter(campaigns.keys()), "ont-live-001")

    campaign_state = campaigns.get(selected_campaign_id)
    if not isinstance(campaign_state, dict):
        campaign_state = {}

    calls_state = state.get("calls")
    if not isinstance(calls_state, dict):
        calls_state = {}

    # Intake summary
    queue_size = len(queue_rows)
    lead_status_counts: dict[str, int] = {}
    for rec in queue_rows:
        status = str(rec.get("lead_status") or rec.get("status") or "").strip().lower() or "new"
        lead_status_counts[status] = lead_status_counts.get(status, 0) + 1

    # Dispatch summary from state
    dispatched = sum(1 for c in calls_state.values() if isinstance(c, dict))
    in_progress = sum(
        1 for c in calls_state.values()
        if isinstance(c, dict) and str(c.get("lead_status", "")).strip().lower() not in {"failed", "completed", "booked", "dnc", "closed", "invalid", "contacted"}
    )
    terminal = sum(
        1 for c in calls_state.values()
        if isinstance(c, dict) and str(c.get("lead_status", "")).strip().lower() in {"dnc", "closed", "invalid", "contacted", "booked", "completed", "failed"}
    )

    calls_dir = repo_root / "data" / "retell_calls"
    call_rows: list[dict[str, Any]] = []
    for p in sorted(calls_dir.glob("*/call.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(rec, dict):
                rec["__call_path"] = str(p)
                call_rows.append(rec)
        except Exception:
            continue

    call_status_counts: dict[str, int] = {}
    latest_calls: list[dict[str, Any]] = []
    for rec in call_rows:
        raw_status = str(rec.get("call_status") or rec.get("status") or rec.get("state") or "unknown")
        status = raw_status.strip().lower() or "unknown"
        metadata = rec.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        to_number = str(rec.get("to_number") or rec.get("to") or metadata.get("to_number") or "").strip()
        metadata_clinic_id = str(metadata.get("clinic_id") or rec.get("clinic_id") or "").strip()
        metadata_lead_id = str(metadata.get("lead_id") or rec.get("lead_id") or "").strip()
        lead_info = _resolve_business_info(
            lead_index,
            to_number=to_number,
            clinic_id=metadata_clinic_id,
            lead_id=metadata_lead_id,
        )
        clinic_name = str(
            metadata.get("business_name")
            or metadata.get("clinic_name")
            or metadata.get("name")
            or metadata.get("practice")
            or metadata.get("practice_name")
            or rec.get("clinic_name")
            or rec.get("clinic")
            or lead_info.get("clinic_name")
            or ""
        ).strip()
        clinic_id = str(
            metadata.get("clinic_id")
            or rec.get("clinic_id")
            or lead_info.get("clinic_id")
            or ""
        ).strip()
        transcript_text = _extract_transcript_text_with_fallback(
            rec.get("transcript")
            if rec.get("transcript") is not None
            else rec.get("transcript_with_tool_calls"),
            transcript_file=Path(str(rec.get("__call_path") or "")).parent / "transcript.txt",
        )
        call_status_counts[status] = call_status_counts.get(status, 0) + 1
        latest_calls.append(
            {
                "call_id": str(rec.get("call_id") or "").strip(),
                "clinic_name": clinic_name or "Unknown business",
                "clinic_id": clinic_id,
                "to_number": to_number,
                "lead_id": str(metadata.get("lead_id") or lead_info.get("lead_id") or rec.get("lead_id") or "").strip(),
                "transcript_snippet": _snippet(transcript_text, max_len=190),
                "status": status,
                "call_json_path": str(rec.get("__call_path") or ""),
            }
        )

    journey_path = repo_root / "data" / "retell_calls" / "live_customer_journeys.jsonl"
    journeys = _read_jsonl_records(journey_path)
    journey_counts: dict[str, int] = {}
    nurture_flags = {
        "recording_followup": 0,
        "send_evidence": 0,
        "set_follow_up_plan": 0,
        "log_call_outcome": 0,
    }
    for row in journeys:
        stage = str(row.get("conversion_stage") or "unknown").strip().lower()
        if not stage:
            stage = "unknown"
        journey_counts[stage] = journey_counts.get(stage, 0) + 1
        tools = row.get("tool_calls")
        if isinstance(tools, list):
            for name in tools:
                key = _normalize_tool_name(name)
                if key in nurture_flags:
                    nurture_flags[key] += 1

        for key in ("recording_followup_requested",):
            if str(row.get(key)).strip().lower() in {"1", "true", "yes"}:
                nurture_flags["recording_followup"] = nurture_flags["recording_followup"] + 1

    # Lead file sanity + segmentation visibility
    segments: dict[str, int] = {}
    if live_leads_file.exists():
        try:
            import csv

            with live_leads_file.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    segment = str(row.get("lead_segment") or row.get("segment") or "unsegmented").strip().lower()
                    segments[segment] = segments.get(segment, 0) + 1
        except Exception:
            pass

    # Step lighting
    step_status = {
        "intake": "ok" if queue_size > 0 else "idle",
        "dispatch": "ok" if campaign_state.get("daily_count", 0) > 0 or dispatched > 0 else ("running" if queue_size > 0 else "idle"),
        "mapping": "ok" if journey_path.exists() else ("running" if call_rows else "idle"),
        "persistence": "ok" if bool(journeys) else ("running" if journey_counts else "idle"),
        "nurture": "ok" if any(v > 0 for v in nurture_flags.values()) else ("running" if call_rows else "idle"),
    }

    return {
        "campaign_id": selected_campaign_id,
        "tenant": tenant or "live_medspa",
        "files": {
            "queue_file": str(active_queue_file),
            "active_queue_file": str(active_queue_file),
            "state_file": str(state_file),
            "journey_file": str(journey_path),
            "live_leads_file": str(live_leads_file),
        },
        "intake": {
            "queue_file": str(active_queue_file),
            "queue_file_exists": active_queue_file.exists(),
            "queue_size": queue_size,
            "lead_status_counts": lead_status_counts,
            "segments": segments,
        },
        "dispatch": {
            "attempted_calls": dispatched,
            "in_progress": in_progress,
            "terminal": terminal,
            "daily_count": int(campaign_state.get("daily_count", 0) or 0),
            "last_run_utc": int(state.get("last_run_utc", 0) or 0),
            "call_window_caps": {
                "daily_call_cap": int(campaign_state.get("daily_call_cap", os.getenv("CAMPAIGN_DAILY_CALL_CAP", "3")) or 3),
                "max_attempts": int(campaign_state.get("max_attempts", os.getenv("CAMPAIGN_MAX_ATTEMPTS", "500")) or 500),
                "attempt_warning_threshold": int(
                    campaign_state.get("attempt_warning_threshold", os.getenv("CAMPAIGN_ATTEMPT_WARNING_THRESHOLD", "200")) or 200
                ),
            },
        },
        "dispatch_state": {
            "calls_map_size": len(calls_state),
            "status_counts": lead_status_counts,
        },
        "transcripts": {
            "call_artifact_count": len(call_rows),
            "call_status_counts": call_status_counts,
            "latest_calls": latest_calls[-20:][::-1],
        },
        "journeys": {
            "count": len(journeys),
            "conversion_counts": journey_counts,
            "nurture_tool_hits": nurture_flags,
        },
        "step_status": step_status,
        "pipeline_health": {
            "stage": "active" if any(v == "running" for v in step_status.values()) else "idle",
        },
    }


def load_call_detail(
    calls_dir: Path,
    *,
    call_id: str | None = None,
    clinic_id: str | None = None,
    to_number: str | None = None,
) -> dict[str, Any] | None:
    if not calls_dir.exists():
        return None

    target_call_id = str(call_id or "").strip()
    target_clinic_id = str(clinic_id or "").strip()
    target_to = _normalize_phone(to_number) if to_number else ""

    def _lineify_lines(raw: Any) -> list[str]:
        if raw is None:
            return []
        lines: list[str] = []
        if isinstance(raw, str):
            return [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if isinstance(raw, list):
            for row in raw:
                if not isinstance(row, dict):
                    continue
                role = str(row.get("role", "")).strip()
                content = str(row.get("content", "")).strip()
                if not content:
                    continue
                if role:
                    lines.append(f"{role}: {content}")
                else:
                    lines.append(content)
        return lines

    def _coalesce_business_name(rec: dict[str, Any], row_to: str) -> str:
        metadata = rec.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        return str(
            metadata.get("business_name")
            or metadata.get("clinic_name")
            or metadata.get("name")
            or metadata.get("practice")
            or metadata.get("practice_name")
            or rec.get("clinic_name")
            or rec.get("clinic")
            or metadata.get("clinic")
            or "Unknown business"
        ).strip()

    def _build_row(rec: dict[str, Any], path: Path) -> dict[str, Any]:
        metadata = rec.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        row_to = str(rec.get("to_number") or rec.get("to") or metadata.get("to_number") or "").strip()
        transcript_text = _extract_transcript_text_with_fallback(
            rec.get("transcript")
            if rec.get("transcript") is not None
            else rec.get("transcript_with_tool_calls"),
            transcript_file=path.parent / "transcript.txt",
        )
        return {
            "call_id": str(rec.get("call_id") or "").strip(),
            "clinic_name": _coalesce_business_name(rec, row_to),
            "clinic_id": str(
                metadata.get("clinic_id")
                or rec.get("clinic_id")
                or rec.get("practice_id")
                or ""
            ).strip(),
            "lead_id": str(metadata.get("lead_id") or rec.get("lead_id") or "").strip(),
            "to_number": row_to,
            "from_number": str(rec.get("from_number") or metadata.get("from_number") or "").strip(),
            "status": str(rec.get("call_status") or rec.get("status") or rec.get("state") or "unknown").strip().lower(),
            "duration_ms": rec.get("duration_ms"),
            "call_analysis": rec.get("call_analysis") if isinstance(rec.get("call_analysis"), dict) else {},
            "tool_calls": rec.get("tool_calls") if isinstance(rec.get("tool_calls"), list) else [],
            "transcript": transcript_text,
            "transcript_lines": _lineify_lines(rec.get("transcript_object") or rec.get("transcript_with_tool_calls")),
            "recording_url": str(rec.get("recording_url") or rec.get("recording_multi_channel_url") or "").strip(),
            "call_json_path": str(path),
            "call_started_at": rec.get("start_timestamp"),
            "call_ended_at": rec.get("end_timestamp"),
        }

    by_id_candidates: list[tuple[dict[str, Any], Path]] = []
    by_rest_candidates: list[tuple[dict[str, Any], Path]] = []

    for p in sorted(calls_dir.glob("*/call.json")):
        try:
            payload_raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload_raw, dict):
            continue

        payload = payload_raw
        call_candidate_id = str(payload.get("call_id") or "").strip()
        payload_to = _normalize_phone(
            payload.get("to_number")
            or payload.get("to")
            or (payload.get("metadata", {}) or {}).get("to_number")
        )

        if target_call_id and call_candidate_id == target_call_id:
            return _build_row(payload, p)

        if (target_clinic_id or target_to):
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            candidate_clinic = str(
                metadata.get("clinic_id")
                or payload.get("clinic_id")
                or payload.get("practice_id")
                or ""
            ).strip()
            if target_clinic_id and target_clinic_id == candidate_clinic:
                by_rest_candidates.append((payload, p))
            elif target_to and target_to == payload_to:
                by_rest_candidates.append((payload, p))

    if by_rest_candidates:
        latest = sorted(by_rest_candidates, key=lambda item: item[1].parent.stat().st_mtime, reverse=True)[0]
        return _build_row(latest[0], latest[1])
    return None
