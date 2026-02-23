from __future__ import annotations

import asyncio

from app.metrics import VIC

from tests.harness.transport_harness import HarnessSession


def test_latency_masking_ack_and_tool_filler_timing() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(tool_latencies={"get_pricing": 2000})
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "What is your pricing?"}],
                }
            )

            # Let ACK plan flow through.
            for _ in range(20):
                await asyncio.sleep(0)

            ack_hist = session.metrics.get_hist(VIC["turn_final_to_ack_segment_ms"])
            assert ack_hist, "expected ack latency metric"
            assert ack_hist[-1] <= 300

            # Before filler threshold, no filler plan should exist.
            await session.clock.advance(session.cfg.vic_tool_filler_threshold_ms - 1)
            for _ in range(5):
                await asyncio.sleep(0)
            assert not any(p.reason == "FILLER" for p in session.orch.speech_plans)

            # At/after threshold, filler appears (interruptible).
            await session.clock.advance(1)
            for _ in range(20):
                await asyncio.sleep(0)
            fillers = [p for p in session.orch.speech_plans if p.reason == "FILLER"]
            assert fillers, "expected filler plan when tool latency exceeds threshold"
            assert all(seg.interruptible for p in fillers for seg in p.segments)
            assert len(fillers) <= 2
        finally:
            await session.stop()

    asyncio.run(_run())
