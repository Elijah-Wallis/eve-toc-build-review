from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from app.config import BrainConfig
from app.phrase_selector import select_phrase

from tests.harness.transport_harness import HarnessSession


def test_phrase_selector_varies_across_turns_but_is_deterministic() -> None:
    options = ["a", "b", "c", "d", "e", "f"]
    picks_1 = [
        select_phrase(
            options=options,
            call_id="c1",
            turn_id=i,
            segment_kind="ACK",
            segment_index=0,
        )
        for i in range(1, 10)
    ]
    picks_2 = [
        select_phrase(
            options=options,
            call_id="c1",
            turn_id=i,
            segment_kind="ACK",
            segment_index=0,
        )
        for i in range(1, 10)
    ]
    assert picks_1 == picks_2
    assert len(set(picks_1)) >= 2


def test_ack_variation_replay_stable() -> None:
    async def run_once() -> list[str]:
        session = await HarnessSession.start(
            session_id="variation",
            cfg=BrainConfig(speak_first=False, retell_auto_reconnect=False, idle_timeout_ms=60000),
        )
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            picks: list[str] = []
            for rid in range(1, 7):
                await session.send_inbound_obj(
                    {
                        "interaction_type": "response_required",
                        "response_id": rid,
                        "transcript": [{"role": "user", "content": "hi"}],
                    }
                )
                for _ in range(200):
                    await asyncio.sleep(0)
                    plans = [p for p in session.orch.speech_plans if p.epoch == rid and p.reason == "ACK"]
                    if plans:
                        picks.append(" ".join(seg.plain_text for seg in plans[-1].segments))
                        break
            return picks
        finally:
            await session.stop()

    p1 = asyncio.run(run_once())
    p2 = asyncio.run(run_once())
    assert p1 == p2
    assert len(set(p1)) >= 2


def test_phrase_selector_stable_across_pythonhashseed_subprocesses() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    code = textwrap.dedent(
        """
        from app.phrase_selector import select_phrase

        options = ["a", "b", "c", "d", "e", "f"]
        selected = select_phrase(
            options=options,
            call_id="seed-check",
            turn_id=7,
            segment_kind="ACK",
            segment_index=0,
        )
        print(options.index(selected))
        """
    )

    def _run(seed: str) -> int:
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")
        out = subprocess.check_output(
            [sys.executable, "-c", code],
            cwd=str(repo_root),
            env=env,
            text=True,
        ).strip()
        return int(out)

    idx1 = _run("1")
    idx2 = _run("2")
    assert idx1 == idx2
