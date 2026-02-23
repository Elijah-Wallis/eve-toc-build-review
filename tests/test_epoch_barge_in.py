from __future__ import annotations

import asyncio
import json

from app.metrics import VIC
from app.protocol import OutboundResponse

from tests.harness.transport_harness import HarnessSession


def test_epoch_preemption_drops_stale_chunks() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(tool_latencies={"get_pricing": 2000})
        try:
            # Drain initial config + BEGIN terminal response_id=0.
            await session.recv_outbound()
            await session.recv_outbound()

            # response_id=1 (will start tool call and emit ACK) then preempt quickly with response_id=2.
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "What is your pricing?"}],
                }
            )
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 2,
                    "transcript": [{"role": "user", "content": "Actually, can I book an appointment?"}],
                }
            )
            for _ in range(40):
                await asyncio.sleep(0)

            # Read until epoch=2 completes (bounded).
            out = []
            for _ in range(20):
                m = await session.recv_outbound()
                out.append(m)
                if isinstance(m, OutboundResponse) and m.response_id == 2 and m.content_complete:
                    break

            # Find first epoch=2 response chunk.
            first_2 = None
            for i, m in enumerate(out):
                if isinstance(m, OutboundResponse) and m.response_id == 2 and not m.content_complete:
                    first_2 = i
                    break

            assert first_2 is not None, "expected epoch=2 response chunk"

            # No epoch=1 response chunks after epoch=2 has started.
            for m in out[first_2:]:
                if isinstance(m, OutboundResponse):
                    assert m.response_id != 1

            assert session.metrics.get(VIC["stale_segment_dropped_total"]) >= 1
        finally:
            await session.stop()

    asyncio.run(_run())


def test_barge_in_hint_drops_same_epoch_queued_chunks() -> None:
    async def _run() -> None:
        # Slow tool so we have queued chunks that would otherwise be written after the hint.
        session = await HarnessSession.start(tool_latencies={"get_pricing": 5000})
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
                    "transcript": [{"role": "user", "content": "What is your pricing?"}],
                }
            )
            for _ in range(50):
                await asyncio.sleep(0)

            # Barge-in hint within the same epoch.
            await session.send_inbound_obj(
                {
                    "interaction_type": "update_only",
                    "transcript": [{"role": "user", "content": "Wait"}],
                    "turntaking": "user_turn",
                }
            )
            for _ in range(50):
                await asyncio.sleep(0)

            # Resume writer and assert that queued epoch=1 chunks (content_complete=False)
            # from the old speak-generation do not reach the transport.
            session.transport.send_allowed.set()

            saw_terminal = False
            saw_non_terminal = False
            for _ in range(50):
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


def test_clear_event_drops_same_epoch_queued_chunks() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(tool_latencies={"get_pricing": 5000})
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
                    "transcript": [{"role": "user", "content": "What is your pricing?"}],
                }
            )
            for _ in range(50):
                await asyncio.sleep(0)

            # Explicit interruption signal.
            await session.send_inbound_obj({"interaction_type": "clear"}, expect_ack=False)
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
