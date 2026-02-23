from __future__ import annotations

import asyncio
import json

from app.clock import RealClock
from app.metrics import Metrics
from app.shell.executor import ShellExecutor
from app.tools import ToolRegistry


def test_shell_tool_disabled_returns_explicit_error(tmp_path) -> None:
    async def _run() -> None:
        metrics = Metrics()
        reg = ToolRegistry(
            session_id="s-disabled",
            clock=RealClock(),
            metrics=metrics,
            shell_tool_enabled=False,
        )
        rec = await reg.invoke(
            name="run_shell_command",
            arguments={"command": "python3 -V"},
            timeout_ms=2_000,
        )
        payload = json.loads(rec.content)
        assert payload["ok"] is False
        assert payload["error"] == "shell_tool_disabled"
        assert metrics.get("shell.exec_denied_total") == 1

    asyncio.run(_run())


def test_shell_tool_can_execute_command_when_enabled(tmp_path) -> None:
    async def _run() -> None:
        log_path = tmp_path / "shell_exec.jsonl"
        metrics = Metrics()
        executor = ShellExecutor(
            mode="local",
            enable_hosted=False,
            workdir=str(tmp_path),
            log_path=str(log_path),
        )
        reg = ToolRegistry(
            session_id="s-enabled",
            clock=RealClock(),
            metrics=metrics,
            shell_executor=executor,
            shell_tool_enabled=True,
        )
        rec = await reg.invoke(
            name="run_shell_command",
            arguments={"command": "python3 -c 'print(321)'", "timeout_s": 10},
            timeout_ms=15_000,
        )
        payload = json.loads(rec.content)
        assert payload["ok"] is True
        assert payload["returncode"] == 0
        assert "321" in payload["stdout"]
        assert metrics.get("shell.exec_total") == 1

    asyncio.run(_run())


def test_shell_tool_canary_blocks_when_not_selected(tmp_path) -> None:
    async def _run() -> None:
        metrics = Metrics()
        reg = ToolRegistry(
            session_id="s-canary-blocked",
            clock=RealClock(),
            metrics=metrics,
            shell_tool_enabled=True,
            shell_tool_canary_enabled=True,
            shell_tool_canary_percent=0,
        )
        rec = await reg.invoke(
            name="run_shell_command",
            arguments={"command": "python3 -V"},
            timeout_ms=2_000,
        )
        payload = json.loads(rec.content)
        assert payload["ok"] is False
        assert payload["error"] == "shell_tool_not_in_canary"
        assert metrics.get("shell.exec_denied_total") == 1

    asyncio.run(_run())
