#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import shlex
import sys
from pathlib import Path

# Allow direct script execution from repo root without editable install.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import BrainConfig  # noqa: E402
from app.metrics import VIC  # noqa: E402
from app.shell.executor import ShellExecutor  # noqa: E402


_FAILED_TEST_RE = re.compile(r"FAILED\s+([\w./:-]+)")
_MAX_COMBINED_OUT_CHARS = 2_000_000


def now_stamp() -> str:
    return dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def parse_failed_tests(text: str) -> list[str]:
    out: list[str] = []
    for m in _FAILED_TEST_RE.finditer(text or ""):
        out.append(m.group(1))
    # de-dupe preserve order
    seen: set[str] = set()
    dedup: list[str] = []
    for x in out:
        if x in seen:
            continue
        seen.add(x)
        dedup.append(x)
    return dedup


def _trim_text_tail(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def root_cause_clusters(failed_tests: list[str]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {}
    for t in failed_tests:
        top = t.split("/")[0] if "/" in t else t.split("::")[0]
        buckets.setdefault(top, []).append(t)
    return buckets


def build_markdown_report(payload: dict) -> str:
    lines: list[str] = []
    lines.append("# Self-Improve Cycle")
    lines.append("")
    lines.append(f"- Timestamp: {payload['timestamp']}")
    lines.append(f"- Mode: {payload['mode']}")
    lines.append(f"- Proposed only: {str(payload['propose_only']).lower()}")
    lines.append("")

    lines.append("## Command Results")
    for c in payload.get("commands", []):
        lines.append(f"- `{c['command']}` -> {'PASS' if c['ok'] else 'FAIL'} ({c['reason']})")
    lines.append("")

    failed = payload.get("failed_tests", [])
    lines.append("## Failed Tests")
    if failed:
        for t in failed:
            lines.append(f"- `{t}`")
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## Root Cause Clusters")
    clusters = payload.get("clusters", {})
    if clusters:
        for k, vals in clusters.items():
            lines.append(f"- **{k}**: {len(vals)}")
            for v in vals:
                lines.append(f"  - `{v}`")
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## Suggested Skill Captures")
    suggestions = payload.get("skill_capture_suggestions", [])
    if suggestions:
        for s in suggestions:
            lines.append(f"- `{s}`")
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## Safety Gates")
    lines.append("- Apply mode is blocked unless all commands are green.")
    lines.append("- Autonomous deploy remains disabled by policy.")
    return "\n".join(lines) + "\n"


def main() -> int:
    cfg = BrainConfig.from_env()

    ap = argparse.ArgumentParser(description="Offline self-improvement cycle (safe by default).")
    ap.add_argument("--mode", choices=["off", "propose", "apply"], default=cfg.self_improve_mode)
    ap.add_argument("--hard-gates", action="store_true", help="Run scripts/ci_hard_gates.sh instead of lightweight pytest.")
    ap.add_argument("--maxfail", type=int, default=10)
    ap.add_argument(
        "--command",
        action="append",
        default=[],
        help="Override default gate command(s). Can be passed multiple times.",
    )
    args = ap.parse_args()

    mode = args.mode
    if mode == "off":
        print("SELF_IMPROVE_MODE=off; no action.")
        return 0

    repo = Path(__file__).resolve().parents[1]
    hist_dir = repo / "docs" / "self_improve" / "history"
    hist_dir.mkdir(parents=True, exist_ok=True)

    shell = ShellExecutor(
        mode=cfg.shell_mode,
        enable_hosted=cfg.shell_enable_hosted,
        allowed_commands=cfg.shell_allowed_commands,
        workdir=str(repo),
        log_path=str(hist_dir / "shell_exec.jsonl"),
    )

    commands: list[str] = list(args.command or [])
    if not commands and args.hard_gates:
        commands.append("bash scripts/ci_hard_gates.sh")
    if not commands:
        commands.append(f"python3 -m pytest -q --maxfail={max(1, int(args.maxfail))}")

    command_results = []
    combined_out = ""
    for cmd in commands:
        result = asyncio.run(shell.execute(cmd, timeout_s=1800))
        command_results.append(
            {
                "command": cmd,
                "ok": result.ok,
                "reason": result.reason,
                "returncode": result.returncode,
                "duration_ms": result.duration_ms,
            }
        )
        combined_out += (result.stdout or "") + "\n" + (result.stderr or "") + "\n"
        combined_out = _trim_text_tail(combined_out, _MAX_COMBINED_OUT_CHARS)

    failed_tests = parse_failed_tests(combined_out)
    clusters = root_cause_clusters(failed_tests)

    suggestions: list[str] = []
    for bucket, tests in clusters.items():
        sample = tests[0]
        sid = f"self_improve_{bucket.replace('/', '_').replace('.', '_')}"
        q_sid = shlex.quote(sid)
        q_sample = shlex.quote(sample)
        suggestions.append(
            "python3 scripts/skills/capture_skill.py "
            f"--id {q_sid} "
            f"--intent {shlex.quote(f'Fix recurring failures in {bucket}')} "
            f"--tests {q_sample}"
        )

    all_green = all(bool(x.get("ok")) for x in command_results) if command_results else True
    propose_only = True
    blocked_on_gates = False
    if mode == "apply":
        if all_green:
            propose_only = False
        else:
            propose_only = True
            blocked_on_gates = True

    payload = {
        "timestamp": now_stamp(),
        "mode": mode,
        "propose_only": propose_only,
        "all_green": all_green,
        "blocked_on_gates": blocked_on_gates,
        "commands": command_results,
        "failed_tests": failed_tests,
        "clusters": clusters,
        "skill_capture_suggestions": suggestions,
        "metrics": {
            VIC["self_improve_cycles_total"]: 1,
            VIC["self_improve_proposals_total"]: 1 if propose_only else 0,
            VIC["self_improve_applies_total"]: 0 if propose_only else 1,
            VIC["self_improve_blocked_on_gates_total"]: 1 if blocked_on_gates else 0,
        },
    }

    stamp = payload["timestamp"]
    json_path = hist_dir / f"{stamp}.json"
    md_path = hist_dir / f"{stamp}.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(build_markdown_report(payload), encoding="utf-8")

    last_run = repo / "docs" / "self_improve" / "last_run.md"
    last_run.parent.mkdir(parents=True, exist_ok=True)
    last_run.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")

    print(str(last_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
