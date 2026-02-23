from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


def _load_module():
    p = Path(__file__).resolve().parents[1] / "scripts" / "self_improve_cycle.py"
    spec = importlib.util.spec_from_file_location("self_improve_cycle", p)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)  # type: ignore[attr-defined]
    return m


def test_trim_text_tail_bounds_memory() -> None:
    m = _load_module()
    text = "a" * 5000
    got = m._trim_text_tail(text, 1000)
    assert len(got) == 1000
    assert got == ("a" * 1000)


def test_self_improve_survives_large_command_output() -> None:
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
        "python3 -c \"print('x'*2500000); print('FAILED tests/test_big.py::test_case'); raise SystemExit(1)\"",
    ]
    cp = subprocess.run(cmd, cwd=str(repo), env=env, text=True, capture_output=True, check=False)
    assert cp.returncode == 0, cp.stderr

    hist = repo / "docs/self_improve/history"
    newest = sorted(hist.glob("*.json"))[-1]
    payload = json.loads(newest.read_text(encoding="utf-8"))
    assert payload["mode"] == "propose"
    assert payload["propose_only"] is True
    assert payload["failed_tests"] == ["tests/test_big.py::test_case"]

