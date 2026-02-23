from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _wait_health(url: str, *, timeout_sec: int = 30) -> None:
    deadline = time.time() + int(timeout_sec)
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if int(resp.status) == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise AssertionError(f"server did not become healthy: {url}")


def _read_metrics(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.read().decode("utf-8")


def _counter_value(metrics_text: str, name: str) -> float:
    for raw in metrics_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            continue
        if parts[0] == name:
            try:
                return float(parts[1])
            except Exception:
                return 0.0
    return 0.0


def test_at_ws_torture_5min() -> None:
    pytest.importorskip("websockets")
    repo_root = Path(__file__).resolve().parents[2]
    port = _free_port()
    base_http = f"http://127.0.0.1:{port}"
    base_ws = f"ws://127.0.0.1:{port}/llm-websocket"

    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.server:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_health(f"{base_http}/healthz", timeout_sec=30)
        baseline = _read_metrics(f"{base_http}/metrics")

        cmd = [
            sys.executable,
            "scripts/ws_load_test.py",
            "--url",
            base_ws,
            "--sessions",
            "10",
            "--duration-sec",
            "300",
            "--turn-interval-ms",
            "250",
            "--torture-pause-reads-ms",
            "1500",
            "--torture-pause-reads-every-turn",
            "--assert-keepalive",
        ]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")
        run = subprocess.run(
            cmd,
            cwd=str(repo_root),
            env=env,
            text=True,
            capture_output=True,
            timeout=390,
            check=False,
        )
        if run.returncode != 0:
            raise AssertionError(
                "ws torture load test failed\n"
                f"returncode={run.returncode}\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
            )

        ending = _read_metrics(f"{base_http}/metrics")
        miss_before = _counter_value(baseline, "keepalive_ping_pong_missed_deadline_total")
        miss_after = _counter_value(ending, "keepalive_ping_pong_missed_deadline_total")
        assert (miss_after - miss_before) == 0

        wt_before = _counter_value(baseline, "ws_write_timeout_total")
        wt_after = _counter_value(ending, "ws_write_timeout_total")
        close_before = _counter_value(
            baseline, "ws_close_reason_total_WRITE_TIMEOUT_BACKPRESSURE"
        )
        close_after = _counter_value(
            ending, "ws_close_reason_total_WRITE_TIMEOUT_BACKPRESSURE"
        )
        if (wt_after - wt_before) > 0:
            assert (close_after - close_before) > 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except Exception:
            server.kill()
            server.wait(timeout=5)
