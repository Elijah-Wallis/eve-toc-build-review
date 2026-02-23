from __future__ import annotations

import asyncio
from typing import AsyncIterator

from app.config import BrainConfig
from app.fact_guard import FactTemplate, validate_rewrite
from app.metrics import VIC

from tests.harness.transport_harness import HarnessSession


class BadFactLLM:
    async def stream_text(self, *, prompt: str) -> AsyncIterator[str]:
        yield "For a general visit, it's 999 dollars."

    async def aclose(self) -> None:
        return


def test_fact_guard_validate_rewrite() -> None:
    required = ["[[PRICE]]", "[[SLOT_1]]"]
    assert (
        validate_rewrite(
            rewritten="I can do [[SLOT_1]], and the visit is [[PRICE]].",
            required_tokens=required,
        )
        is True
    )
    assert (
        validate_rewrite(
            rewritten="I can do [[SLOT_1]], and the visit is $120.",
            required_tokens=required,
        )
        is False
    )
    assert (
        validate_rewrite(
            rewritten="I can do Tuesday, and the visit is [[PRICE]].",
            required_tokens=required,
        )
        is False
    )


def test_fact_guard_fallback_metric_on_invalid_llm_rewrite() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(
            cfg=BrainConfig(
                speak_first=False,
                retell_auto_reconnect=False,
                idle_timeout_ms=60000,
                use_llm_nlg=True,
                llm_provider="fake",
                llm_phrasing_for_facts_enabled=True,
            ),
            llm=BadFactLLM(),
            tool_latencies={"get_pricing": 0},
        )
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
            for _ in range(300):
                await asyncio.sleep(0)
                if any(p.epoch == 1 and p.reason == "CONTENT" for p in session.orch.speech_plans):
                    break

            content_plans = [p for p in session.orch.speech_plans if p.epoch == 1 and p.reason == "CONTENT"]
            assert content_plans
            spoken = " ".join(seg.plain_text for p in content_plans for seg in p.segments)
            assert "$120" in spoken
            assert "999" not in spoken
            assert session.metrics.get(VIC["llm_fact_guard_fallback_total"]) >= 1
        finally:
            await session.stop()

    asyncio.run(_run())
