from __future__ import annotations

import asyncio

from app.config import BrainConfig
from app.metrics import VIC
from app.protocol import OutboundPingPong, OutboundResponse

from tests.harness.transport_harness import HarnessSession


def test_writer_write_timeout_closes_session() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(
            cfg=BrainConfig(
                speak_first=False,
                idle_timeout_ms=60000,
                ws_write_timeout_ms=50,
                ws_max_consecutive_write_timeouts=1,
                ws_close_on_write_timeout=True,
            ),
            tool_latencies={"get_pricing": 2000},
        )
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            # Block writer sends to simulate socket/TCP backpressure.
            session.transport.send_allowed.clear()

            # Queue speech first, then keepalive control frame.
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "What is your pricing?"}],
                }
            )
            await session.send_inbound_obj({"interaction_type": "ping_pong", "timestamp": 777})

            for _ in range(10):
                if session.shutdown_evt.is_set():
                    break
                await session.clock.advance(50)
                for _ in range(20):
                    await asyncio.sleep(0)

            assert session.shutdown_evt.is_set() is True
            assert session.metrics.get(VIC["ws_write_timeout_total"]) >= 1
            assert session.metrics.get(VIC["keepalive_ping_pong_write_attempt_total"]) >= 1
            assert session.metrics.get(VIC["keepalive_ping_pong_write_timeout_total"]) >= 1
            assert session.metrics.get("ws.close_reason_total.WRITE_TIMEOUT_BACKPRESSURE") >= 1
        finally:
            await session.stop()

    asyncio.run(_run())


def test_control_plane_priority_still_holds_when_not_blocked() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(
            cfg=BrainConfig(
                speak_first=False,
                idle_timeout_ms=60000,
                ws_write_timeout_ms=500,
                ws_max_consecutive_write_timeouts=2,
                ws_close_on_write_timeout=True,
            ),
            tool_latencies={"get_pricing": 2000},
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
            await session.send_inbound_obj({"interaction_type": "ping_pong", "timestamp": 999})
            for _ in range(20):
                await asyncio.sleep(0)

            session.transport.send_allowed.set()
            first = await session.recv_outbound()
            assert isinstance(first, OutboundPingPong)
            assert first.timestamp == 999

            # Speech should still follow after control.
            saw_speech = False
            for _ in range(20):
                m = await session.recv_outbound()
                if isinstance(m, OutboundResponse) and m.response_id == 1:
                    saw_speech = True
                    break
            assert saw_speech is True
            assert session.metrics.get(VIC["ws_write_timeout_total"]) == 0
        finally:
            await session.stop()

    asyncio.run(_run())
