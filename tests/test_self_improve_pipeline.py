from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_self_improve_propose_generates_artifacts() -> None:
    repo = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["SELF_IMPROVE_MODE"] = "propose"
    env["SHELL_MODE"] = "local"

    cmd = [
        sys.executable,
        "scripts/self_improve_cycle.py",
        "--mode",
        "propose",
        "--command",
        "python3 -c \"print('FAILED tests/test_demo.py::test_case')\" && false",
    ]
    cp = subprocess.run(cmd, cwd=str(repo), env=env, text=True, capture_output=True, check=False)
    assert cp.returncode == 0, cp.stderr

    last_run = repo / "docs/self_improve/last_run.md"
    assert last_run.exists()
    text = last_run.read_text(encoding="utf-8")
    assert "Self-Improve Cycle" in text
    assert "Suggested Skill Captures" in text

    hist = repo / "docs/self_improve/history"
    newest = sorted(hist.glob("*.json"))[-1]
    payload = json.loads(newest.read_text(encoding="utf-8"))
    assert payload["mode"] == "propose"
    assert payload["propose_only"] is True
