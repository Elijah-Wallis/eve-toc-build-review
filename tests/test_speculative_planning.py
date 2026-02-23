from __future__ import annotations

import asyncio

from app.config import BrainConfig
from app.metrics import VIC

from tests.harness.transport_harness import HarnessSession


def test_speculative_prefetch_does_not_emit_tools_early_and_avoids_filler() -> None:
    async def _run() -> None:
        cfg = BrainConfig(
            speak_first=False,
            speculative_planning_enabled=True,
            speculative_debounce_ms=0,
            speculative_tool_prefetch_enabled=True,
            speculative_tool_prefetch_timeout_ms=5000,
            vic_tool_filler_threshold_ms=250,
            vic_tool_timeout_ms=3000,
        )
        session = await HarnessSession.start(cfg=cfg, tool_latencies={"get_pricing": 2000})
        try:
            # config + begin
            await session.recv_outbound()
            await session.recv_outbound()

            # update_only arrives while user is still talking; we should speculate/prefetch but emit nothing.
            await session.send_inbound_obj(
                {
                    "interaction_type": "update_only",
                    "transcript": [{"role": "user", "content": "What is your pricing?"}],
                    "turntaking": "user_turn",
                }
            )

            # Yield so orchestrator can start the speculative task before we advance time.
            for _ in range(50):
                await asyncio.sleep(0)

            # Let speculative tool prefetch complete.
            await session.clock.advance(2100)
            for _ in range(50):
                await asyncio.sleep(0)

            # Ensure speculation completed and produced a result.
            for _ in range(200):
                if session.metrics.get("speculative.plans_total") >= 1:
                    break
                await asyncio.sleep(0)
            assert session.metrics.get("speculative.plans_total") >= 1

            # Ensure no tool weaving events were emitted pre-finalization.
            saw_tool = False
            while session.transport.outbound_qsize():
                m = await session.recv_outbound()
                if getattr(m, "response_type", "") in {"tool_call_invocation", "tool_call_result"}:
                    saw_tool = True
            assert saw_tool is False

            # Now finalization arrives.
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "What is your pricing?"}],
                }
            )

            # Yield; since tool is prefetched, we should not need to wait 2s or emit fillers.
            for _ in range(200):
                await asyncio.sleep(0)
                if any(p.epoch == 1 and p.reason == "CONTENT" for p in session.orch.speech_plans):
                    break

            assert any(p.epoch == 1 and p.reason == "CONTENT" for p in session.orch.speech_plans)
            fillers = [p for p in session.orch.speech_plans if p.epoch == 1 and p.reason == "FILLER"]
            assert not fillers, "prefetched tool should avoid filler"

            # Sanity: speculation counters
            assert session.metrics.get("speculative.plans_total") >= 1
            assert session.metrics.get("speculative.used_total") >= 1
        finally:
            await session.stop()

    asyncio.run(_run())
