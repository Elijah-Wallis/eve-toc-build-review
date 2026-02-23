from __future__ import annotations

import asyncio
import re

from app.metrics import VIC
from app.voice_guard import readability_grade

from tests.harness.transport_harness import HarnessSession


_TAG = re.compile(r"<[^>]+>")
_REASONING = re.compile(r"\b(let me think|here('?| i)s my reasoning|step by step|thought process|analyz(?:ing|e))\b", re.I)
_JARGON = re.compile(r"\b(eligibility|procedure|consult|facilitate|optimize|utilize|initiate)\b", re.I)


def _spoken(text: str) -> str:
    return _TAG.sub("", text or "")


def test_voice_quality_regression_suite() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(tool_latencies={"get_pricing": 250})
        try:
            # Drain startup (config + begin)
            await session.recv_outbound()
            await session.recv_outbound()

            scenarios = [
                "I need to book an appointment",
                "How much is a general visit?",
                "Are you AI or human?",
                "I'm frustrated, this is confusing",
            ]

            rid = 1
            for prompt in scenarios:
                await session.send_inbound_obj(
                    {
                        "interaction_type": "response_required",
                        "response_id": rid,
                        "transcript": [{"role": "user", "content": prompt}],
                    }
                )
                for _ in range(200):
                    await asyncio.sleep(0)
                    if any(p.epoch == rid and p.reason in {"CLARIFY", "CONTENT", "ERROR", "CONFIRM", "REPAIR"} for p in session.orch.speech_plans):
                        break
                rid += 1

            # Latency gates
            ack_hist = session.metrics.get_hist(VIC["turn_final_to_ack_segment_ms"])
            first_hist = session.metrics.get_hist(VIC["turn_final_to_first_segment_ms"])
            assert ack_hist and sorted(ack_hist)[int(0.95 * (len(ack_hist) - 1))] <= 300
            assert first_hist and sorted(first_hist)[int(0.95 * (len(first_hist) - 1))] <= 700

            # Language gates
            all_text = " ".join(
                _spoken(seg.plain_text)
                for plan in session.orch.speech_plans
                for seg in plan.segments
            )
            assert not _REASONING.search(all_text)
            assert not _JARGON.search(all_text)

            grades = [
                readability_grade(_spoken(seg.plain_text))
                for plan in session.orch.speech_plans
                for seg in plan.segments
                if _spoken(seg.plain_text).strip()
            ]
            assert grades
            assert max(grades) <= 8

            # Barge-in behavior unchanged
            session.tools.set_latency_ms("get_pricing", 5000)
            session.transport.send_allowed.clear()
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": rid,
                    "transcript": [{"role": "user", "content": "Tell me pricing"}],
                }
            )
            for _ in range(50):
                await asyncio.sleep(0)
            await session.send_inbound_obj(
                {
                    "interaction_type": "update_only",
                    "transcript": [{"role": "user", "content": "wait"}],
                    "turntaking": "user_turn",
                }
            )
            for _ in range(100):
                await asyncio.sleep(0)
                if session.metrics.get_hist(VIC["barge_in_cancel_latency_ms"]):
                    break
            cancel_hist = session.metrics.get_hist(VIC["barge_in_cancel_latency_ms"])
            assert cancel_hist and sorted(cancel_hist)[int(0.95 * (len(cancel_hist) - 1))] <= 250
            session.transport.send_allowed.set()
        finally:
            session.transport.send_allowed.set()
            await session.stop()

    asyncio.run(_run())
