#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import random
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import BrainConfig
from app.metrics import VIC
from tests.harness.transport_harness import HarnessSession


EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b")
GENERIC_LOCAL = {"info", "admin", "frontdesk", "contact", "hello", "office"}
CLOSE_REQUEST_RE = re.compile(
    r"\b(close|close this out|close this call|close the call|archive|send it|send this|call me now|hang up|hang up now|end call|end this call)\b",
    re.I,
)
BETA_ALPHA = 2.0
BETA_BETA = 8.0
FR_P95_TRIM_FRACTION = 0.08
LATENCY_CANDIDATES_SAFE_MAX_MS = 5_000.0

# very lightweight fallback matcher for spoken emails like "name at gmail dot com"
SPOKEN_EMAIL_RE = re.compile(
    r"\b([a-z0-9._%+-]{1,64})\s+(?:at|@)\s+([a-z0-9.-]{1,128})\s+(?:dot|\.)\s+([a-z]{2,10})\b",
    re.I,
)

OBJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "is_sales": re.compile(r"\b(is this sales|sales call|are you selling)\b", re.I),
    "busy": re.compile(r"\b(busy|with a patient|in a meeting|call back)\b", re.I),
    "no_email_policy": re.compile(r"\b(don't|do not|cant|can't|won't|will not)\s+(give|share).*(email)\b", re.I),
    "generic_inbox": re.compile(r"\b(info@|admin@|frontdesk@|contact@|hello@)\b", re.I),
    "not_interested": re.compile(r"\b(not interested|not right now|we're good|we are good)\b", re.I),
}


def _looks_like_call_record(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    if "call_id" in obj:
        return True
    if isinstance(obj.get("latency"), dict):
        return True
    if "transcript_object" in obj or "transcript_with_tool_calls" in obj or "transcript" in obj:
        return True
    return False


@dataclass
class CallFeatures:
    call_id: str
    ended: bool
    answered: bool
    first_response_latency_ms: float | None
    email_captured: bool
    direct_email_captured: bool
    close_intent: bool
    close_to_email_success: bool
    time_to_email_capture_sec: float | None
    turns_to_capture: int | None
    objection_hits: dict[str, int]


@dataclass
class RevenueOpsSummary:
    corpus_total_calls: int
    ended_calls: int
    answered_calls: int
    email_captures: int
    direct_email_captures: int
    generic_email_captures: int
    email_capture_rate: float
    direct_email_capture_rate: float
    close_request_count: int
    close_to_email_success_count: int
    close_request_rate: float
    close_to_email_rate: float
    first_response_latency_p50_ms: float | None
    first_response_latency_p95_ms: float | None
    time_to_email_capture_p50_sec: float | None
    time_to_email_capture_p95_sec: float | None
    turns_to_capture_p50: float | None
    turns_to_capture_p95: float | None
    objection_counts: dict[str, int]
    objective_score: float


def _quantile(vals: list[float], q: float, *, trim_fraction: float = 0.0) -> float | None:
    if not vals:
        return None
    arr = sorted(float(v) for v in vals if isinstance(v, (int, float)) and v >= 0.0)
    if not arr:
        return None
    if trim_fraction > 0.0:
        drop = int(len(arr) * trim_fraction)
        if 2 * drop >= len(arr):
            drop = max(0, (len(arr) // 2) - 1)
        if drop > 0:
            arr = arr[drop : len(arr) - drop]
    if len(arr) == 1:
        return float(arr[0])
    idx = (len(arr) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(arr) - 1)
    frac = idx - lo
    return float(arr[lo] + (arr[hi] - arr[lo]) * frac)


def _is_generic_email(email: str) -> bool:
    local = email.split("@", 1)[0].lower().strip()
    return local in GENERIC_LOCAL


def _extract_text_lines(call: dict[str, Any]) -> list[tuple[str, str, float | None]]:
    lines: list[tuple[str, str, float | None]] = []
    tobj = call.get("transcript_object")
    if isinstance(tobj, list) and tobj:
        for item in tobj:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            text = str(item.get("content") or "").strip()
            t_end: float | None = None
            words = item.get("words")
            if isinstance(words, list) and words:
                last = words[-1]
                if isinstance(last, dict) and isinstance(last.get("end"), (int, float)):
                    t_end = float(last["end"])
            if role and text:
                lines.append((role, text, t_end))
        if lines:
            return lines

    raw = str(call.get("transcript") or "")
    for line in raw.splitlines():
        ln = line.strip()
        if not ln or ":" not in ln:
            continue
        role, content = ln.split(":", 1)
        r = role.strip().lower()
        if r in {"agent", "user"}:
            lines.append((r, content.strip(), None))
    return lines


def _find_email_capture(lines: list[tuple[str, str, float | None]]) -> tuple[bool, bool, float | None, int | None]:
    for idx, (_, text, t_end) in enumerate(lines, start=1):
        emails = EMAIL_RE.findall(text)
        if emails:
            direct = any(not _is_generic_email(e) for e in emails)
            return True, direct, t_end, idx
        if SPOKEN_EMAIL_RE.search(text):
            return True, True, t_end, idx
    return False, False, None, None


def _extract_close_progression(lines: list[tuple[str, str, float | None]]) -> tuple[bool, bool, int, int]:
    """
    Detect whether user asks to close and whether any direct email was supplied after that request.

    Returns:
        (close_intent, close_to_email_success, close_turn_idx, close_email_turn_idx)
    """
    close_turn = 0
    close_intent = False
    close_to_email_success = False
    close_success_turn = 0
    for idx, role, text in [(i + 1, r, t) for i, (r, t, _) in enumerate(lines)]:
        if role != "user":
            continue
        has_email, has_direct_email = _email_in_text(text)
        if CLOSE_REQUEST_RE.search(text or "") and not close_intent:
            close_intent = True
            close_turn = idx
            if has_email and has_direct_email:
                close_to_email_success = True
                close_success_turn = idx
            continue
        if not close_intent:
            continue
        if has_email and has_direct_email:
            close_to_email_success = True
            close_success_turn = idx
            break
    if close_turn == 0:
        return False, False, 0, 0
    if not close_to_email_success:
        return True, False, close_turn, close_success_turn
    return True, True, close_turn, close_success_turn


def _email_in_text(text: str) -> tuple[bool, bool]:
    emails = EMAIL_RE.findall(text)
    if emails:
        direct = any(not _is_generic_email(e) for e in emails)
        return True, direct
    if SPOKEN_EMAIL_RE.search(text):
        return True, True
    return False, False


def _first_response_latency_ms(
    call: dict[str, Any],
    lines: list[tuple[str, str, float | None]],
    *,
    replay_ms: float | None = None,
) -> float | None:
    if replay_ms is not None and isinstance(replay_ms, (int, float)):
        replay_ms_f = float(replay_ms)
        if 0.0 <= replay_ms_f <= LATENCY_CANDIDATES_SAFE_MAX_MS:
            return replay_ms_f
    lat = call.get("latency") or {}
    candidates = ["llm", "e2e", "asr", "s2s"]
    for key in candidates:
        src = lat.get(key) or {}
        if not isinstance(src, dict):
            continue
        p50 = src.get("p50")
        if isinstance(p50, (int, float)):
            p50_val = float(p50)
            if 0.0 <= p50_val <= LATENCY_CANDIDATES_SAFE_MAX_MS:
                return p50_val
    return None


def _extract_features(call: dict[str, Any], *, replay_ms: float | None = None) -> CallFeatures:
    call_id = str(call.get("call_id") or "")
    status = str(call.get("call_status") or "").lower()
    ended = status == "ended"

    lines = _extract_text_lines(call)
    answered = any(role == "user" for role, _, _ in lines)
    fr = _first_response_latency_ms(call, lines, replay_ms=replay_ms)

    captured, direct, t_cap, turns = _find_email_capture(lines)
    close_intent, close_to_email_success, close_turn, close_success_turn = _extract_close_progression(lines)
    if close_turn and close_success_turn and close_success_turn < close_turn:
        close_to_email_success = False

    objection_hits = {k: 0 for k in OBJECTION_PATTERNS}
    for role, text, _ in lines:
        if role != "user":
            continue
        for name, pat in OBJECTION_PATTERNS.items():
            if pat.search(text):
                objection_hits[name] += 1

    return CallFeatures(
        call_id=call_id,
        ended=ended,
        answered=answered,
        first_response_latency_ms=fr,
        email_captured=captured,
        direct_email_captured=(captured and direct),
        close_intent=close_intent,
        close_to_email_success=(captured and close_intent and close_to_email_success),
        time_to_email_capture_sec=t_cap,
        turns_to_capture=turns,
        objection_hits=objection_hits,
    )


async def _replay_first_response_ms(call: dict[str, Any], *, profile: str = "b2b") -> float | None:
    call_id = str(call.get("call_id") or "replay-call")
    lines = _extract_text_lines(call)
    if not lines:
        return None

    cfg = BrainConfig(
        conversation_profile=profile,
        speak_first=False,
        retell_send_update_agent_on_connect=False,
    )
    session = await HarnessSession.start(
        session_id=call_id,
        cfg=cfg,
        use_real_clock=True,
    )
    metric_key = VIC["turn_final_to_first_segment_ms"]

    try:
        # Consume startup frames (config + initial empty speech response).
        _ = await session.recv_outbound()
        _ = await session.recv_outbound()

        transcript: list[dict[str, str]] = []
        response_id = 1
        for role, content, _ in lines:
            if role not in {"agent", "user"}:
                continue
            if not str(content).strip():
                continue
            transcript.append({"role": role, "content": content})
            if role != "user":
                continue

            before = len(session.metrics.get_hist(metric_key))
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": response_id,
                    "transcript": transcript,
                },
                expect_ack=False,
            )
            response_id += 1

            for _ in range(240):
                observed = session.metrics.get_hist(metric_key)
                if len(observed) > before:
                    samples = [
                        float(v)
                        for v in observed[before:]
                        if isinstance(v, (int, float))
                    ]
                    if samples:
                        return float(samples[0])
                await asyncio.sleep(0)
    finally:
        await session.stop()
    return None


async def _replay_latency_map(
    calls: list[dict[str, Any]],
    *,
    seed: int | None = None,
    default_profile: str = "b2b",
) -> dict[str, float]:
    ordered = _apply_call_order(calls, seed=seed)
    result: dict[str, float] = {}
    for call in ordered:
        cid = str(call.get("call_id") or "").strip()
        if not cid:
            continue
        profile = str(call.get("conversation_profile") or default_profile).lower()
        if profile not in {"b2b", "clinic"}:
            profile = default_profile
        latency_ms = await _replay_first_response_ms(call, profile=profile)
        if isinstance(latency_ms, (int, float)):
            result[cid] = float(latency_ms)
    return result


def _load_calls(calls_dir: Path) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if not calls_dir.exists():
        return calls
    seen_call_ids: set[str] = set()
    for p in sorted(calls_dir.rglob("*.json")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not _looks_like_call_record(obj):
            continue

        # Deduplicate by call_id when both legacy and alt layouts are present.
        call_id = str((obj or {}).get("call_id", "")).strip()
        if call_id and call_id in seen_call_ids:
            continue
        if call_id:
            seen_call_ids.add(call_id)
        calls.append(obj)
    return calls


def _apply_call_order(
    calls: list[dict[str, Any]], *, seed: int | None = None
) -> list[dict[str, Any]]:
    ordered = list(calls)
    if seed is not None:
        rng = random.Random(int(seed))
        rng.shuffle(ordered)
    return ordered


def _objective_score(
    *,
    answered_calls: int,
    email_capture_count: int,
    direct_email_capture_count: int,
    close_request_count: int,
    close_to_email_success_count: int,
    first_response_latency_p95_ms: float | None,
    turns_to_capture_p50: float | None,
    time_to_capture_p50_sec: float | None,
) -> float:
    # 0..100 score with shrinkage to prevent small samples overfitting.
    fr_penalty = min(1.0, (first_response_latency_p95_ms or 4000.0) / 2500.0)
    turns_penalty = min(1.0, (turns_to_capture_p50 or 12.0) / 12.0)
    tcap_penalty = min(1.0, (time_to_capture_p50_sec or 120.0) / 120.0)

    email_capture_rate = _beta_success_rate(email_capture_count, answered_calls)
    direct_email_capture_rate = _beta_success_rate(direct_email_capture_count, answered_calls)
    close_request_rate = _beta_success_rate(close_request_count, answered_calls, default=0.0)
    close_to_email_rate = _beta_success_rate(
        close_to_email_success_count,
        close_request_count,
        default=0.0,
    )

    base = (
        0.30 * email_capture_rate
        + 0.20 * direct_email_capture_rate
        + 0.20 * close_to_email_rate
        + 0.10 * close_request_rate
        + 0.10 * (1.0 - fr_penalty)
        + 0.05 * (1.0 - turns_penalty)
        + 0.05 * (1.0 - tcap_penalty)
    )
    return round(max(0.0, min(100.0, base * 100.0)), 2)


def _beta_success_rate(successes: int, trials: int, *, default: float = 0.0) -> float:
    if trials <= 0:
        return float(default)
    return (BETA_ALPHA + successes) / (BETA_ALPHA + BETA_BETA + trials)


def _speed_grade(first_response_latency_p95_ms: float | None) -> str:
    if first_response_latency_p95_ms is None:
        return "unknown"
    if first_response_latency_p95_ms < 700:
        return "excellent"
    if first_response_latency_p95_ms < 1000:
        return "good"
    if first_response_latency_p95_ms < 1500:
        return "warning"
    return "poor"


def build_summary(
    calls: list[dict[str, Any]],
    *,
    replay_latencies: dict[str, float] | None = None,
) -> RevenueOpsSummary:
    replay_latencies = replay_latencies or {}
    features = [
        _extract_features(c, replay_ms=replay_latencies.get(str(c.get("call_id") or "")))
        for c in calls
    ]

    ended_calls = [f for f in features if f.ended]
    answered_calls = [f for f in ended_calls if f.answered]

    email_caps = [f for f in answered_calls if f.email_captured]
    direct_caps = [f for f in answered_calls if f.direct_email_captured]
    close_reqs = [f for f in answered_calls if f.close_intent]
    close_success = [f for f in close_reqs if f.close_to_email_success]
    generic_caps = len(email_caps) - len(direct_caps)

    fr_vals = [f.first_response_latency_ms for f in answered_calls if f.first_response_latency_ms is not None]
    tcap_vals = [f.time_to_email_capture_sec for f in email_caps if f.time_to_email_capture_sec is not None]
    turns_vals = [float(f.turns_to_capture) for f in email_caps if f.turns_to_capture is not None]

    objection_counts = {k: 0 for k in OBJECTION_PATTERNS}
    for f in answered_calls:
        for k, v in f.objection_hits.items():
            objection_counts[k] += int(v)

    denom = len(answered_calls)
    email_rate = len(email_caps) / denom if denom else 0.0
    direct_rate = len(direct_caps) / denom if denom else 0.0
    fr_p50 = _quantile([float(x) for x in fr_vals], 0.50)
    fr_p95 = _quantile([float(x) for x in fr_vals], 0.95, trim_fraction=FR_P95_TRIM_FRACTION)
    tcap_p50 = _quantile([float(x) for x in tcap_vals], 0.50, trim_fraction=FR_P95_TRIM_FRACTION)
    tcap_p95 = _quantile([float(x) for x in tcap_vals], 0.95, trim_fraction=FR_P95_TRIM_FRACTION)
    turns_p50 = _quantile([float(x) for x in turns_vals], 0.50, trim_fraction=FR_P95_TRIM_FRACTION)
    turns_p95 = _quantile([float(x) for x in turns_vals], 0.95, trim_fraction=FR_P95_TRIM_FRACTION)

    score = _objective_score(
        answered_calls=denom,
        email_capture_count=len(email_caps),
        direct_email_capture_count=len(direct_caps),
        close_request_count=len(close_reqs),
        close_to_email_success_count=len(close_success),
        first_response_latency_p95_ms=fr_p95,
        turns_to_capture_p50=turns_p50,
        time_to_capture_p50_sec=tcap_p50,
    )

    return RevenueOpsSummary(
        corpus_total_calls=len(features),
        ended_calls=len(ended_calls),
        answered_calls=len(answered_calls),
        email_captures=len(email_caps),
        direct_email_captures=len(direct_caps),
        generic_email_captures=generic_caps,
        email_capture_rate=round(email_rate, 4),
        direct_email_capture_rate=round(direct_rate, 4),
        close_request_count=len(close_reqs),
        close_to_email_success_count=len(close_success),
        close_request_rate=round(len(close_reqs) / denom, 4) if denom else 0.0,
        close_to_email_rate=round(len(close_success) / max(1, len(close_reqs)), 4) if len(close_reqs) else 0.0,
        first_response_latency_p50_ms=fr_p50,
        first_response_latency_p95_ms=fr_p95,
        time_to_email_capture_p50_sec=tcap_p50,
        time_to_email_capture_p95_sec=tcap_p95,
        turns_to_capture_p50=turns_p50,
        turns_to_capture_p95=turns_p95,
        objection_counts=objection_counts,
        objective_score=score,
    )


def _recommend_actions(s: RevenueOpsSummary) -> list[str]:
    actions: list[str] = []

    if s.first_response_latency_p95_ms is None or s.first_response_latency_p95_ms > 1000:
        actions.append(
            "Latency: first response p95 is too high. Keep start_speaker=user, trim prompt opening, and stay on gemini-2.5-flash-lite."
        )

    if s.email_capture_rate < 0.20:
        actions.append(
            "Capture: email capture rate is low. Force one-question flow: identity -> value in 8 words -> direct email ask."
        )

    if s.direct_email_capture_rate < 0.10:
        actions.append(
            "Direct inbox: push once for direct manager email, then accept best routing inbox immediately to avoid dead turns."
        )

    if s.close_request_rate < 0.60:
        actions.append(
            "Progression: close-or-send path is under-triggered. Keep asking 'close this out or send a short manager email' every 1-2 turns."
        )

    if s.close_request_count > 0 and s.close_to_email_rate < 0.70:
        actions.append(
            "Close completion: close requests are not converting. Add a forced one-turn retry and transfer to inbox fallback only after two failed manager-email turns."
        )

    if (s.turns_to_capture_p50 or 99) > 6:
        actions.append(
            "Efficiency: median turns to capture is high. Cap to one objection response + one binary close (archive vs send)."
        )

    if s.objection_counts.get("is_sales", 0) > 0:
        actions.append(
            "Objection 'is sales' is recurring. Use one-liner: 'No pitch. Just sending the missed-call report.' then ask email again."
        )

    if s.objection_counts.get("no_email_policy", 0) > 0:
        actions.append(
            "No-email policy hit detected. Add fallback close: ask who to address in subject line and send to provided inbox."
        )

    if not actions:
        actions.append("Maintain current script/settings; objective metrics are inside target bands.")
    return actions


def _summary_to_dict(s: RevenueOpsSummary) -> dict[str, Any]:
    return {
        "corpus_total_calls": s.corpus_total_calls,
        "ended_calls": s.ended_calls,
        "answered_calls": s.answered_calls,
        "email_captures": s.email_captures,
        "direct_email_captures": s.direct_email_captures,
        "generic_email_captures": s.generic_email_captures,
        "email_capture_rate": s.email_capture_rate,
        "direct_email_capture_rate": s.direct_email_capture_rate,
        "close_request_count": s.close_request_count,
        "close_to_email_success_count": s.close_to_email_success_count,
        "close_request_rate": s.close_request_rate,
        "close_to_email_rate": s.close_to_email_rate,
        "time_to_email_capture_p50_sec": s.time_to_email_capture_p50_sec,
        "time_to_email_capture_p95_sec": s.time_to_email_capture_p95_sec,
        "turns_to_capture_p50": s.turns_to_capture_p50,
        "turns_to_capture_p95": s.turns_to_capture_p95,
        "first_response_latency_p50_ms": s.first_response_latency_p50_ms,
        "first_response_latency_p95_ms": s.first_response_latency_p95_ms,
        "first_response_latency_band": _speed_grade(s.first_response_latency_p95_ms),
        "objection_counts": s.objection_counts,
        "objective_score": s.objective_score,
    }


def _write_report(*, out_dir: Path, summary: RevenueOpsSummary, actions: list[str]) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts_unix": int(time.time()),
        "objective_function": {
            "maximize": ["email_capture_rate", "direct_email_capture_rate", "close_request_rate", "close_to_email_rate"],
            "minimize": ["time_to_email_capture", "turns_to_capture", "first_response_latency"],
        },
        "summary": _summary_to_dict(summary),
        "recommended_actions": actions,
    }
    json_path = out_dir / "latest.json"
    md_path = out_dir / "latest.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# Revenue Ops Report",
        "",
        f"- objective_score: {summary.objective_score}",
        f"- email_capture_rate: {summary.email_capture_rate}",
        f"- direct_email_capture_rate: {summary.direct_email_capture_rate}",
        f"- close_request_rate: {summary.close_request_rate}",
        f"- close_to_email_rate: {summary.close_to_email_rate}",
        f"- first_response_latency_p95_ms: {summary.first_response_latency_p95_ms}",
        f"- first_response_latency_band: {_speed_grade(summary.first_response_latency_p95_ms)}",
        f"- turns_to_capture_p50: {summary.turns_to_capture_p50}",
        f"- time_to_email_capture_p50_sec: {summary.time_to_email_capture_p50_sec}",
        "",
        "## Recommended Next Actions",
    ]
    for i, a in enumerate(actions, start=1):
        lines.append(f"{i}. {a}")
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def _post_json(url: str, payload: dict[str, Any], timeout_s: float = 10.0) -> None:
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout_s) as r:
        _ = r.read()


def main() -> int:
    ap = argparse.ArgumentParser(description="Revenue Ops loop from Retell call corpus.")
    ap.add_argument("--calls-dir", default="data/retell_calls", help="Directory containing call_*/call.json")
    ap.add_argument("--out-dir", default="data/revenue_ops", help="Where to write latest report")
    ap.add_argument("--limit", type=int, default=0, help="Optional max calls to include; 0 disables.")
    ap.add_argument("--max-calls", type=int, default=0, help="Optional hard cap on calls; 0 disables.")
    ap.add_argument("--min-calls", type=int, default=0, help="Fail if fewer calls are available.")
    ap.add_argument("--seed", type=int, default=None, help="Optional deterministic seed for call ordering.")
    ap.add_argument(
        "--replay-latency",
        action="store_true",
        default=False,
        help="Replay call transcripts offline with deterministic local run to compute first-response latency.",
    )
    ap.add_argument("--push-webhook", default=os.getenv("N8N_OUTCOME_WEBHOOK_URL", ""), help="Optional webhook URL")
    ap.add_argument("--print-json", action="store_true", default=True)
    ap.add_argument("--no-print-json", dest="print_json", action="store_false")
    args = ap.parse_args()

    calls = _load_calls(Path(args.calls_dir))
    calls = _apply_call_order(calls, seed=None if args.seed is None else int(args.seed))

    if args.max_calls and args.max_calls > 0 and args.limit and args.limit > 0:
        calls = calls[: min(int(args.max_calls), int(args.limit))]
    elif args.max_calls and args.max_calls > 0:
        calls = calls[: int(args.max_calls)]
    elif args.limit and args.limit > 0:
        calls = calls[: int(args.limit)]
    if args.min_calls and args.min_calls > 0 and len(calls) < int(args.min_calls):
        return 2

    replay_latencies: dict[str, float] = {}
    if args.replay_latency:
        replay_latencies = asyncio.run(_replay_latency_map(calls, seed=args.seed))

    summary = build_summary(calls, replay_latencies=replay_latencies)
    actions = _recommend_actions(summary)
    json_path, md_path = _write_report(out_dir=Path(args.out_dir), summary=summary, actions=actions)

    out = {
        "status": "ok",
        "report_json": str(json_path),
        "report_md": str(md_path),
        "summary": _summary_to_dict(summary),
        "recommended_actions": actions,
    }

    if args.push_webhook:
        _post_json(args.push_webhook, out)
        out["webhook_pushed"] = True

    if args.print_json:
        print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
