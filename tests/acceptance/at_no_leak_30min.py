from __future__ import annotations

import asyncio

from app.config import BrainConfig

from tests.harness.transport_harness import HarnessSession


def test_at_no_leak_30min() -> None:
    async def _run() -> None:
        cfg = BrainConfig(speak_first=False, retell_auto_reconnect=False, idle_timeout_ms=10_000_000)
        session = await HarnessSession.start(session_id="leak", cfg=cfg)
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            # 30 minutes simulated as 300 turns (one every ~6s). FakeClock stays deterministic.
            for rid in range(1, 301):
                await session.send_inbound_obj(
                    {
                        "interaction_type": "response_required",
                        "response_id": rid,
                        "transcript": [{"role": "user", "content": "Hi"}],
                    }
                )
                for _ in range(5):
                    await asyncio.sleep(0)

            # Bounded memory: trace + speech plan buffers must not grow unbounded.
            assert len(session.orch.speech_plans) <= 512
            assert len(session.trace.events) <= 20000
            assert session.inbound_q.qsize() <= session.cfg.inbound_queue_max
            assert session.outbound_q.qsize() <= session.cfg.outbound_queue_max
        finally:
            await session.stop()

    asyncio.run(_run())

