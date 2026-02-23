from __future__ import annotations

import asyncio

from app.shell.executor import ShellExecutor


def test_local_exec_success(tmp_path) -> None:
    async def _run() -> None:
        ex = ShellExecutor(mode="local", workdir=str(tmp_path), log_path=str(tmp_path / "log.jsonl"))
        r = await ex.execute("python3 -c 'print(123)'", timeout_s=10)
        assert r.ok is True
        assert "123" in r.stdout

    asyncio.run(_run())


def test_hybrid_hosted_disabled_blocks(tmp_path) -> None:
    async def _run() -> None:
        ex = ShellExecutor(mode="hybrid", enable_hosted=False, workdir=str(tmp_path), log_path=str(tmp_path / "log.jsonl"))
        r = await ex.execute("python3 -V", prefer_hosted=True)
        assert r.ok is False
        assert r.reason == "hosted_disabled"

    asyncio.run(_run())


def test_timeout(tmp_path) -> None:
    async def _run() -> None:
        ex = ShellExecutor(mode="local", workdir=str(tmp_path), log_path=str(tmp_path / "log.jsonl"))
        r = await ex.execute("python3 -c 'import time; time.sleep(2)'", timeout_s=1)
        assert r.ok is False
        assert r.reason == "timeout"

    asyncio.run(_run())
