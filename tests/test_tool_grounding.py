from __future__ import annotations

import asyncio
import re

from app.config import BrainConfig
from app.metrics import VIC
from app.protocol import OutboundResponse

from tests.harness.transport_harness import HarnessSession


_HAS_DIGIT = re.compile(r"\\d")
_TAG = re.compile(r"<[^>]+>")


def test_tool_grounding_pricing_success_has_evidence_ids() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(tool_latencies={"get_pricing": 0})
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "How much does it cost?"}],
                }
            )

            # Yield until the CONTENT plan is captured.
            for _ in range(100):
                if any(p.epoch == 1 and p.reason == "CONTENT" for p in session.orch.speech_plans):
                    break
                await asyncio.sleep(0)

            # Find the content plan and assert tool evidence is present for factual pricing.
            content_plans = [p for p in session.orch.speech_plans if p.epoch == 1 and p.reason == "CONTENT"]
            assert content_plans, "expected a CONTENT SpeechPlan"
            plan = content_plans[-1]
            assert any(seg.requires_tool_evidence for seg in plan.segments)
            for seg in plan.segments:
                if seg.requires_tool_evidence:
                    assert seg.tool_evidence_ids, "missing tool evidence ids for factual segment"

            assert session.metrics.get(VIC["factual_segment_without_tool_evidence_total"]) == 0
        finally:
            await session.stop()

    asyncio.run(_run())


def test_tool_timeout_falls_back_without_numbers() -> None:
    async def _run() -> None:
        # Force a timeout: tool latency > tool timeout.
        cfg = BrainConfig(speak_first=False, vic_tool_timeout_ms=3000)
        session = await HarnessSession.start(cfg=cfg, tool_latencies={"get_pricing": 4000})
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

            # Advance time to trigger filler (>=800ms) and then tool timeout (>=3000ms).
            await session.clock.advance(session.cfg.vic_tool_filler_threshold_ms)
            for _ in range(5):
                await asyncio.sleep(0)
            remaining_to_timeout = max(
                1,
                session.cfg.vic_tool_timeout_ms - session.cfg.vic_tool_filler_threshold_ms + 10,
            )
            await session.clock.advance(remaining_to_timeout)
            for _ in range(10):
                await asyncio.sleep(0)

            # Drain outbound response messages for this epoch until terminal.
            contents = []
            for _ in range(50):
                m = await asyncio.wait_for(session.recv_outbound(), timeout=0.02)
                if isinstance(m, OutboundResponse) and m.response_id == 1:
                    contents.append(m.content)
                    if m.content_complete:
                        break

            combined = " ".join(contents)
            spoken = _TAG.sub("", combined)
            assert not _HAS_DIGIT.search(spoken), "fallback response must not guess spoken numbers"
            assert session.metrics.get(VIC["fallback_used_total"]) >= 1
        finally:
            await session.stop()

    asyncio.run(_run())
