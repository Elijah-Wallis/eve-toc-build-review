from __future__ import annotations

import asyncio
import json

from app.bounded_queue import BoundedDequeQueue
from app.clock import FakeClock
from app.config import BrainConfig
from app.metrics import Metrics, VIC
from app.protocol import InboundCallDetails, InboundPingPong, InboundUpdateOnly, OutboundPingPong
from app.transport_ws import InboundItem, socket_reader

from tests.harness.transport_harness import HarnessSession, InMemoryTransport


def test_ping_pong_not_starved_by_outbound_backpressure() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(
                cfg=BrainConfig(
                    speak_first=False,
                    idle_timeout_ms=60000,
                    outbound_queue_max=8,
                ),
            tool_latencies={"get_pricing": 3000},
        )
        try:
            await session.recv_outbound()
            await session.recv_outbound()

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

            await session.send_inbound_obj({"interaction_type": "ping_pong", "timestamp": 4242})
            for _ in range(50):
                await asyncio.sleep(0)

            session.transport.send_allowed.set()
            first = await session.recv_outbound()
            assert isinstance(first, OutboundPingPong)
            assert first.timestamp == 4242
            assert session.metrics.get_hist(VIC["keepalive_ping_pong_queue_delay_ms"])
            assert session.metrics.get(VIC["keepalive_ping_pong_missed_deadline_total"]) == 0
        finally:
            await session.stop()

    asyncio.run(_run())


def test_ping_pong_not_delayed_by_update_only_flood() -> None:
    async def _run() -> None:
        clock = FakeClock(start_ms=0)
        metrics = Metrics()
        transport = InMemoryTransport()
        shutdown_evt = asyncio.Event()
        inbound_q: BoundedDequeQueue[InboundItem] = BoundedDequeQueue(maxsize=3)

        # Pre-fill queue to capacity with one update_only + two call_details.
        await inbound_q.put(
            InboundUpdateOnly(
                interaction_type="update_only",
                transcript=[{"role": "user", "content": "u1"}],
                transcript_with_tool_calls=None,
                turntaking=None,
            )
        )
        await inbound_q.put(
            InboundCallDetails(interaction_type="call_details", call={"id": "c1"})
        )
        await inbound_q.put(
            InboundCallDetails(interaction_type="call_details", call={"id": "c2"})
        )

        reader = asyncio.create_task(
            socket_reader(
                transport=transport,
                inbound_q=inbound_q,
                metrics=metrics,
                shutdown_evt=shutdown_evt,
            )
        )
        try:
            await transport.push_inbound(
                json.dumps(
                    {"interaction_type": "ping_pong", "timestamp": 111},
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            for _ in range(50):
                await asyncio.sleep(0)

            assert metrics.get(VIC["inbound_queue_evictions_total"]) >= 1
            assert metrics.get("inbound.queue_evictions.drop_update_only_for_ping_total") >= 1

            # Queue should now contain ping_pong and no update_only.
            items: list[InboundItem] = []
            for _ in range(inbound_q.qsize()):
                items.append(await inbound_q.get())
            assert any(isinstance(x, InboundPingPong) for x in items)
            assert not any(isinstance(x, InboundUpdateOnly) for x in items)
        finally:
            shutdown_evt.set()
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    asyncio.run(_run())
