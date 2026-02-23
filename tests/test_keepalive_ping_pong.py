from __future__ import annotations

import asyncio

from app.protocol import OutboundConfig, OutboundPingPong, OutboundResponse

from tests.harness.transport_harness import HarnessSession


def test_keepalive_ping_pong_and_idle_timeout() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
        try:
            # On connect: config + BEGIN response_id=0 terminal (speak_first=False).
            m1 = await session.recv_outbound()
            assert isinstance(m1, OutboundConfig)

            m2 = await session.recv_outbound()
            assert isinstance(m2, OutboundResponse)
            assert m2.response_id == 0
            assert m2.content_complete is True

            # Inbound ping -> outbound ping echo.
            await session.send_inbound_obj({"interaction_type": "ping_pong", "timestamp": 123})
            m3 = await session.recv_outbound()
            assert isinstance(m3, OutboundPingPong)
            assert m3.timestamp == 123

            # Idle watchdog ends session deterministically.
            await session.clock.advance(session.cfg.idle_timeout_ms)
            await session.trace.wait_for_event_type("conv_state_transition")
            assert session.shutdown_evt.is_set() is True
        finally:
            await session.stop()

    asyncio.run(_run())


def test_call_details_then_ping_pong_echo() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
        try:
            # On connect: config + BEGIN response_id=0 terminal (speak_first=False).
            _ = await session.recv_outbound()
            _ = await session.recv_outbound()

            await session.send_inbound_obj(
                {"interaction_type": "call_details", "call": {"id": "call_123", "foo": "bar"}},
                expect_ack=False,
            )
            await session.send_inbound_obj({"interaction_type": "ping_pong", "timestamp": 777}, expect_ack=False)
            m = await session.recv_outbound()
            assert isinstance(m, OutboundPingPong)
            assert m.timestamp == 777
        finally:
            await session.stop()

    asyncio.run(_run())
