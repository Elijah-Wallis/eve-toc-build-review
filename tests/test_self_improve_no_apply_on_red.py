from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_self_improve_apply_blocked_when_gates_red() -> None:
    repo = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["SELF_IMPROVE_MODE"] = "apply"
    env["SHELL_MODE"] = "local"

    cmd = [
        sys.executable,
        "scripts/self_improve_cycle.py",
        "--mode",
        "apply",
        "--command",
        "python3 -c \"print('FAILED tests/test_fail.py::test_case')\" && false",
    ]
    cp = subprocess.run(cmd, cwd=str(repo), env=env, text=True, capture_output=True, check=False)
    assert cp.returncode == 0, cp.stderr

    hist = repo / "docs/self_improve/history"
    newest = sorted(hist.glob("*.json"))[-1]
    payload = json.loads(newest.read_text(encoding="utf-8"))
    assert payload["mode"] == "apply"
    assert payload["blocked_on_gates"] is True
    assert payload["propose_only"] is True
