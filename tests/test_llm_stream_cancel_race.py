from __future__ import annotations

import asyncio

from app.config import BrainConfig
from app.metrics import VIC
from app.protocol import OutboundResponse

from tests.harness.transport_harness import HarnessSession


class DeterministicLLM:
    async def stream_text(self, *, prompt: str):
        # Deterministic, punctuation-terminated deltas to force early flush.
        yield "Sure."
        yield " How can I help?"

    async def aclose(self) -> None:
        return


def test_llm_stream_cancel_race_no_stale_chunks_after_barge_in() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(
            cfg=BrainConfig(
                speak_first=False,
                retell_auto_reconnect=False,
                use_llm_nlg=True,
                llm_provider="fake",
                idle_timeout_ms=60000,
            ),
            llm=DeterministicLLM(),
        )
        try:
            # Drain initial config + BEGIN terminal response_id=0.
            await session.recv_outbound()
            await session.recv_outbound()

            # Pause writer output so outbound_q accumulates deterministically.
            session.transport.send_allowed.clear()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "Hi"}],
                }
            )
            for _ in range(50):
                await asyncio.sleep(0)

            # Barge-in hint within the same epoch: must stop queued chunks immediately.
            await session.send_inbound_obj(
                {
                    "interaction_type": "update_only",
                    "transcript": [{"role": "user", "content": "Wait"}],
                    "turntaking": "user_turn",
                }
            )
            for _ in range(50):
                await asyncio.sleep(0)

            session.transport.send_allowed.set()

            saw_terminal = False
            saw_non_terminal = False
            for _ in range(100):
                if session.transport.outbound_qsize() == 0:
                    await asyncio.sleep(0)
                    continue
                m = await session.recv_outbound()
                if isinstance(m, OutboundResponse) and m.response_id == 1:
                    if m.content_complete:
                        saw_terminal = True
                    else:
                        saw_non_terminal = True
                if saw_terminal:
                    break

            assert saw_terminal is True
            assert saw_non_terminal is False
            assert session.metrics.get(VIC["stale_segment_dropped_total"]) >= 1
        finally:
            await session.stop()

    asyncio.run(_run())

