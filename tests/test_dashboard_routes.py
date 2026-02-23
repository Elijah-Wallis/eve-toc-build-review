from __future__ import annotations

import contextlib
import importlib.util
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(base_url: str, timeout_s: float = 8.0) -> None:
    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/healthz", timeout=1.5) as resp:
                if resp.status == 200:
                    return
        except Exception as e:  # pragma: no cover
            last_err = e
            time.sleep(0.1)
    raise RuntimeError(f"server did not become ready: {last_err}")


def _get(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=5.0) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return int(resp.status), body


def _uvicorn_python(repo_root: Path) -> str:
    if importlib.util.find_spec("uvicorn") is not None:
        return sys.executable
    venv_py = repo_root / ".venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def test_dashboard_routes_smoke() -> None:
    pytest.importorskip("uvicorn")
    repo_root = Path(__file__).resolve().parents[1]
    port = _free_port()
    env = os.environ.copy()
    py = _uvicorn_python(repo_root)
    proc = subprocess.Popen(
        [
            py,
            "-m",
            "uvicorn",
            "app.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        _wait_ready(base)

        status, body = _get(f"{base}/dashboard/")
        assert status == 200
        assert "Eve Dashboard" in body

        status, body = _get(f"{base}/api/dashboard/summary")
        assert status == 200
        assert '"status"' in body
        assert '"checks"' in body

        status, body = _get(f"{base}/api/dashboard/repo-map")
        assert status == 200
        assert '"components"' in body

        status, body = _get(f"{base}/api/dashboard/sop")
        assert status == 200
        assert '"markdown"' in body
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
            proc.wait(timeout=5)
