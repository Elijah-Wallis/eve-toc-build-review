from __future__ import annotations

import asyncio

from app.config import BrainConfig
from app.metrics import VIC
from app.protocol import OutboundAgentInterrupt, OutboundResponse

from tests.harness.transport_harness import HarnessSession


def test_backchannel_default_path_no_agent_interrupt() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(
            cfg=BrainConfig(speak_first=False, backchannel_enabled=False, retell_auto_reconnect=False, idle_timeout_ms=60000)
        )
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "update_only",
                    "transcript": [{"role": "user", "content": "Just talking..."}],
                    "turntaking": "user_turn",
                }
            )
            await session.clock.advance(10_000)
            await session.send_inbound_obj(
                {
                    "interaction_type": "update_only",
                    "transcript": [{"role": "user", "content": "Still talking..."}],
                    "turntaking": "user_turn",
                }
            )

            for _ in range(100):
                await asyncio.sleep(0)

            # Drain outbound and ensure no agent_interrupt appears.
            while session.transport.outbound_qsize():
                m = await session.recv_outbound()
                assert not isinstance(m, OutboundAgentInterrupt)

            assert session.metrics.get(VIC["backchannel_detected_total"]) == 0
            assert session.metrics.get(VIC["overtalk_incidents_total"]) == 0
        finally:
            await session.stop()

    asyncio.run(_run())


def test_backchannel_experimental_never_interrupts_user_turn_or_sensitive_capture() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(
            cfg=BrainConfig(speak_first=False, backchannel_enabled=True, retell_auto_reconnect=False, idle_timeout_ms=60000)
        )
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            # Long user monologue: even if the classifier would trigger, agent_interrupt must not be used
            # while turntaking == user_turn.
            await session.send_inbound_obj(
                {
                    "interaction_type": "update_only",
                    "transcript": [{"role": "user", "content": "So I'm just talking for a bit..."}],
                    "turntaking": "user_turn",
                }
            )
            await session.clock.advance(10_000)
            await session.send_inbound_obj(
                {
                    "interaction_type": "update_only",
                    "transcript": [{"role": "user", "content": "And continuing..."}],
                    "turntaking": "user_turn",
                }
            )
            for _ in range(200):
                await asyncio.sleep(0)

            # Trigger booking intake (sensitive capture until phone confirmed).
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "I'd like to schedule an appointment."}],
                }
            )

            # Drain epoch=1 terminal.
            for _ in range(200):
                m = await session.recv_outbound()
                if isinstance(m, OutboundResponse) and m.response_id == 1 and m.content_complete:
                    break

            await session.clock.advance(10_000)
            await session.send_inbound_obj(
                {
                    "interaction_type": "update_only",
                    "transcript": [{"role": "user", "content": "My number is 972-123-4567."}],
                    "turntaking": "user_turn",
                }
            )
            for _ in range(200):
                await asyncio.sleep(0)

            # Drain outbound and ensure no agent_interrupt was emitted in any scenario above.
            while session.transport.outbound_qsize():
                m = await session.recv_outbound()
                assert not isinstance(m, OutboundAgentInterrupt)

            assert session.metrics.get(VIC["overtalk_incidents_total"]) == 0
        finally:
            await session.stop()

    asyncio.run(_run())

