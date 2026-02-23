#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b")

OBJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "no_email_policy": re.compile(r"\b(don't|do not|cant|can't)\s+(give|share).*(email)\b", re.I),
    "busy": re.compile(r"\b(busy|with a patient|in a meeting|call back)\b", re.I),
    "not_interested": re.compile(r"\b(not interested|we're good|we are good)\b", re.I),
    "is_sales": re.compile(r"\b(is this sales|sales call|are you selling)\b", re.I),
    "generic_inbox": re.compile(r"\b(info@|admin@|frontdesk@|contact@|hello@)\b", re.I),
}


@dataclass
class LearningStats:
    total_calls: int = 0
    calls_with_transcript: int = 0
    calls_with_recording_url: int = 0
    direct_email_captures: int = 0
    generic_email_captures: int = 0
    avg_llm_p50_ms: float = 0.0
    avg_e2e_p50_ms: float = 0.0
    objections: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.objections is None:
            self.objections = {k: 0 for k in OBJECTION_PATTERNS}


def _curl_json(*, api_key: str, method: str, url: str, payload: dict[str, Any] | None = None) -> Any:
    cmd = [
        "curl",
        "-sS",
        "-X",
        method,
        url,
        "-H",
        f"Authorization: Bearer {api_key}",
    ]
    if payload is not None:
        cmd += ["-H", "Content-Type: application/json", "--data", json.dumps(payload)]
    out = subprocess.check_output(cmd, text=True)
    return json.loads(out)


def _load_env_file_fallback() -> None:
    """
    Lightweight .env loader so `make learn` works without manual export.
    Only sets keys that are currently missing in process env.
    """
    env_file = os.getenv("RETELL_ENV_FILE") or str(REPO_ROOT / ".env.retell.local")
    p = Path(env_file)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip("'").strip('"')
        if k and k not in os.environ:
            os.environ[k] = v


def _safe_ext_from_url(url: str) -> str:
    path = urlparse(url).path
    ext = Path(path).suffix.lower()
    if ext in {".mp3", ".wav", ".m4a", ".ogg"}:
        return ext
    return ".bin"


def _download(url: str, to_path: Path) -> None:
    to_path.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=20) as r:
        data = r.read()
    to_path.write_bytes(data)


def _persist_call(call: dict[str, Any], out_dir: Path, download_recordings: bool) -> dict[str, Any]:
    if not isinstance(call, dict):
        return {"call_id": "", "saved": False, "downloaded": False}
    call_id = str(call.get("call_id") or "")
    if not call_id:
        return {"call_id": "", "saved": False, "downloaded": False}
    call_dir = out_dir / call_id
    call_dir.mkdir(parents=True, exist_ok=True)

    (call_dir / "call.json").write_text(json.dumps(call, indent=2, sort_keys=True), encoding="utf-8")
    transcript = str(call.get("transcript") or "").strip()
    (call_dir / "transcript.txt").write_text(transcript + ("\n" if transcript else ""), encoding="utf-8")
    twtc = call.get("transcript_with_tool_calls")
    if twtc is not None:
        (call_dir / "transcript_with_tool_calls.json").write_text(
            json.dumps(twtc, indent=2, sort_keys=True), encoding="utf-8"
        )

    rec_url = str(call.get("recording_url") or "").strip()
    downloaded = False
    if download_recordings and rec_url:
        ext = _safe_ext_from_url(rec_url)
        rec_path = call_dir / f"recording{ext}"
        if not rec_path.exists():
            try:
                _download(rec_url, rec_path)
                downloaded = True
            except Exception:
                # Keep loop durable even if signed URL is expired.
                (call_dir / "recording_download_error.txt").write_text(
                    f"failed_at={int(time.time())}\nurl={rec_url}\n", encoding="utf-8"
                )
    return {"call_id": call_id, "saved": True, "downloaded": downloaded}


def _load_call_jsons(out_dir: Path) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if not out_dir.exists():
        return calls
    for p in sorted(out_dir.glob("*/call.json")):
        try:
            calls.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return calls


def _select_local_calls(
    calls: list[dict[str, Any]],
    *,
    limit: int,
    agent_id: str,
    include_non_ended: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c in calls:
        if not isinstance(c, dict):
            continue
        call_id = str(c.get("call_id") or "").strip()
        if not call_id or call_id in seen:
            continue
        if agent_id and str(c.get("agent_id") or "") != agent_id:
            continue
        if not include_non_ended:
            status = str(c.get("call_status") or "").lower()
            if status and status != "ended":
                continue
        seen.add(call_id)
        out.append(c)
        if len(out) >= int(limit):
            break
    return out


def _extract_agent_lines(transcript: str) -> list[str]:
    out: list[str] = []
    for line in transcript.splitlines():
        line = line.strip()
        if line.lower().startswith("agent:"):
            out.append(line[6:].strip())
    return out


def _extract_user_lines(transcript: str) -> list[str]:
    out: list[str] = []
    for line in transcript.splitlines():
        line = line.strip()
        if line.lower().startswith("user:"):
            out.append(line[5:].strip())
    return out


def _is_generic_email(email: str) -> bool:
    local = email.split("@", 1)[0].lower()
    return local in {"info", "admin", "frontdesk", "contact", "hello"}


def _analyze(calls: list[dict[str, Any]]) -> LearningStats:
    s = LearningStats()
    llm_p50_vals: list[float] = []
    e2e_p50_vals: list[float] = []

    for c in calls:
        if not isinstance(c, dict):
            continue
        s.total_calls += 1
        transcript = str(c.get("transcript") or "")
        if transcript.strip():
            s.calls_with_transcript += 1
        if c.get("recording_url"):
            s.calls_with_recording_url += 1

        emails = EMAIL_RE.findall(transcript)
        for e in emails:
            if _is_generic_email(e):
                s.generic_email_captures += 1
            else:
                s.direct_email_captures += 1

        for u in _extract_user_lines(transcript):
            for k, pat in OBJECTION_PATTERNS.items():
                if pat.search(u):
                    s.objections[k] += 1

        lat = c.get("latency") or {}
        llm = lat.get("llm") or {}
        e2e = lat.get("e2e") or {}
        if isinstance(llm.get("p50"), (int, float)):
            llm_p50_vals.append(float(llm["p50"]))
        if isinstance(e2e.get("p50"), (int, float)):
            e2e_p50_vals.append(float(e2e["p50"]))

    if llm_p50_vals:
        s.avg_llm_p50_ms = sum(llm_p50_vals) / len(llm_p50_vals)
    if e2e_p50_vals:
        s.avg_e2e_p50_ms = sum(e2e_p50_vals) / len(e2e_p50_vals)
    return s


def _build_learned_block(stats: LearningStats) -> str:
    ranked = sorted(stats.objections.items(), key=lambda kv: kv[1], reverse=True)
    top = [x for x in ranked if x[1] > 0][:3]
    lines = [
        "Live optimization notes from recent calls:",
        f"- Corpus size: {stats.total_calls} calls",
        f"- Direct email captures: {stats.direct_email_captures}",
        f"- Generic inbox captures: {stats.generic_email_captures}",
        f"- Mean LLM p50: {stats.avg_llm_p50_ms:.1f} ms",
        f"- Mean E2E p50: {stats.avg_e2e_p50_ms:.1f} ms",
    ]
    if top:
        lines.append("- Top objections and responses:")
        mapping = {
            "no_email_policy": "If they refuse direct email, ask for best inbox and proceed.",
            "busy": "If busy, skip pitch and ask only for routing email.",
            "not_interested": "Offer archive-or-send binary choice once, then route.",
            "is_sales": "Use one-line no-pitch reply and return to email ask.",
            "generic_inbox": "Push back once, then accept generic inbox immediately.",
        }
        for k, n in top:
            lines.append(f"  - {k}: seen {n} times. {mapping.get(k, '')}".rstrip())
    return "\n".join(lines).strip()


def _write_reports(*, stats: LearningStats, out_dir: Path) -> Path:
    report_dir = out_dir / "analysis"
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "total_calls": stats.total_calls,
        "calls_with_transcript": stats.calls_with_transcript,
        "calls_with_recording_url": stats.calls_with_recording_url,
        "direct_email_captures": stats.direct_email_captures,
        "generic_email_captures": stats.generic_email_captures,
        "avg_llm_p50_ms": round(stats.avg_llm_p50_ms, 2),
        "avg_e2e_p50_ms": round(stats.avg_e2e_p50_ms, 2),
        "objections": stats.objections,
        "learned_block": _build_learned_block(stats),
    }
    json_path = report_dir / "latest.json"
    md_path = report_dir / "latest.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_lines = [
        "# Retell Learning Loop Report",
        "",
        f"- total_calls: {payload['total_calls']}",
        f"- calls_with_transcript: {payload['calls_with_transcript']}",
        f"- calls_with_recording_url: {payload['calls_with_recording_url']}",
        f"- direct_email_captures: {payload['direct_email_captures']}",
        f"- generic_email_captures: {payload['generic_email_captures']}",
        f"- avg_llm_p50_ms: {payload['avg_llm_p50_ms']}",
        f"- avg_e2e_p50_ms: {payload['avg_e2e_p50_ms']}",
        "",
        "## Objection Counts",
    ]
    for k, v in sorted(stats.objections.items()):
        md_lines.append(f"- {k}: {v}")
    md_lines += ["", "## Learned Block", "", payload["learned_block"], ""]
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return json_path


def _build_generated_prompt(base_prompt: str, learned_block: str) -> str:
    marker_start = "## LEARNED_CALL_PLAYBOOK_START"
    marker_end = "## LEARNED_CALL_PLAYBOOK_END"
    block = f"{marker_start}\n{learned_block}\n{marker_end}"
    if marker_start in base_prompt and marker_end in base_prompt:
        pre = base_prompt.split(marker_start, 1)[0].rstrip()
        post = base_prompt.split(marker_end, 1)[1].lstrip()
        return f"{pre}\n\n{block}\n\n{post}".strip() + "\n"
    return base_prompt.rstrip() + "\n\n" + block + "\n"


def main() -> int:
    _load_env_file_fallback()

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(REPO_ROOT))
        except Exception:
            return str(p)

    ap = argparse.ArgumentParser(description="Sync Retell calls and auto-refine prompt after threshold.")
    ap.add_argument("--limit", type=int, default=100, help="list-calls limit per run")
    ap.add_argument("--threshold", type=int, default=250, help="minimum call corpus to auto-refine")
    ap.add_argument("--out-dir", default="data/retell_calls")
    ap.add_argument(
        "--local-calls-dir",
        default=str((REPO_ROOT / "data/retell_calls")),
        help="offline mode input directory containing call.json files",
    )
    ap.add_argument("--offline", action="store_true", default=False, help="analyze local corpus only")
    ap.add_argument("--agent-id", default=os.getenv("B2B_AGENT_ID", ""))
    ap.add_argument("--download-recordings", action="store_true", default=True)
    ap.add_argument("--no-download-recordings", dest="download_recordings", action="store_false")
    ap.add_argument(
        "--include-non-ended",
        action="store_true",
        default=False,
        help="include non-ended calls in corpus sync (default false)",
    )
    ap.add_argument("--apply", action="store_true", default=True, help="apply refined prompt to live Retell LLM")
    ap.add_argument("--no-apply", dest="apply", action="store_false")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.offline:
        api_key = os.getenv("RETELL_API_KEY", "").strip()
        if api_key:
            os.environ["RETELL_API_KEY"] = api_key
        calls = _select_local_calls(
            _load_call_jsons(Path(args.local_calls_dir)),
            limit=int(args.limit),
            agent_id=args.agent_id,
            include_non_ended=args.include_non_ended,
        )
        stats = _analyze(calls)
        report_path = _write_reports(stats=stats, out_dir=out_dir)

        generated_prompt = REPO_ROOT / "scripts" / "prompts" / "b2b_fast_plain.generated.prompt.txt"
        base_prompt_path = REPO_ROOT / "scripts" / "prompts" / "b2b_fast_plain.prompt.txt"
        base_prompt = base_prompt_path.read_text(encoding="utf-8")
        learned_block = _build_learned_block(stats)
        generated_prompt.write_text(_build_generated_prompt(base_prompt, learned_block), encoding="utf-8")

        print(
            json.dumps(
                {
                    "status": "ok",
                    "mode": "offline",
                    "saved_calls_this_run": len(calls),
                    "downloaded_recordings_this_run": 0,
                    "corpus_total_calls": stats.total_calls,
                    "threshold": int(args.threshold),
                    "applied_refinement": False,
                    "report_json": _rel(report_path),
                    "generated_prompt": _rel(generated_prompt),
                },
                indent=2,
                    )
        )
        return 0

    api_key = os.getenv("RETELL_API_KEY", "").strip()
    if not api_key:
        print("RETELL_API_KEY is required", file=sys.stderr)
        return 2

    state_path = out_dir / "_state.json"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    seen_ids: set[str] = set(state.get("seen_call_ids", []))

    calls = _curl_json(
        api_key=api_key,
        method="POST",
        url="https://api.retellai.com/v2/list-calls",
        payload={"limit": int(args.limit)},
    )
    if not isinstance(calls, list):
        print("Unexpected list-calls response shape", file=sys.stderr)
        return 1

    saved = 0
    downloaded = 0
    processed_ids: set[str] = set()
    for c in calls:
        if not isinstance(c, dict):
            continue
        call_id = str((c or {}).get("call_id") or "")
        if not call_id:
            continue
        if call_id in processed_ids:
            continue
        processed_ids.add(call_id)
        if args.agent_id and str(c.get("agent_id") or "") != args.agent_id:
            continue
        if not args.include_non_ended:
            status = str(c.get("call_status") or "").lower()
            if status and status != "ended":
                continue
        # Refresh call details to capture late-added artifacts.
        call_full = _curl_json(
            api_key=api_key,
            method="GET",
            url=f"https://api.retellai.com/v2/get-call/{call_id}",
        )
        result = _persist_call(call_full, out_dir, args.download_recordings)
        if result.get("saved"):
            saved += 1
            seen_ids.add(call_id)
        if result.get("downloaded"):
            downloaded += 1

    state["seen_call_ids"] = sorted(seen_ids)
    state["last_sync_unix"] = int(time.time())
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

    corpus = _load_call_jsons(out_dir)
    stats = _analyze(corpus)
    report_path = _write_reports(stats=stats, out_dir=out_dir)

    generated_prompt = REPO_ROOT / "scripts" / "prompts" / "b2b_fast_plain.generated.prompt.txt"
    base_prompt_path = REPO_ROOT / "scripts" / "prompts" / "b2b_fast_plain.prompt.txt"
    base_prompt = base_prompt_path.read_text(encoding="utf-8")
    learned_block = _build_learned_block(stats)
    generated_prompt.write_text(_build_generated_prompt(base_prompt, learned_block), encoding="utf-8")

    applied = False
    if args.apply and stats.total_calls >= int(args.threshold):
        env = os.environ.copy()
        env["RETELL_PROMPT_FILE"] = str(generated_prompt)
        subprocess.check_call(["bash", str(REPO_ROOT / "scripts" / "retell_fast_recover.sh")], env=env)
        applied = True

    print(
        json.dumps(
            {
                "status": "ok",
                "mode": "live",
                "saved_calls_this_run": saved,
                "downloaded_recordings_this_run": downloaded,
                "corpus_total_calls": stats.total_calls,
                "threshold": int(args.threshold),
                "applied_refinement": applied,
                "report_json": _rel(report_path),
                "generated_prompt": _rel(generated_prompt),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
