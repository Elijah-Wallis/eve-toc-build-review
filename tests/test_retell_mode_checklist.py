from __future__ import annotations

import asyncio

from app.config import BrainConfig
from app.metrics import VIC
from app.protocol import OutboundAgentInterrupt, OutboundResponse

from tests.harness.transport_harness import HarnessSession


def test_retell_mode_defaults() -> None:
    cfg = BrainConfig()
    assert cfg.speech_markup_mode == "DASH_PAUSE"
    assert cfg.backchannel_enabled is False


def test_retell_mode_begin_has_no_ssml_breaks() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(
            cfg=BrainConfig(speak_first=True, retell_auto_reconnect=False, idle_timeout_ms=60000)
        )
        try:
            # config
            await session.recv_outbound()

            # BEGIN: stream response_id=0 chunks, then terminal.
            while True:
                m = await session.recv_outbound()
                if isinstance(m, OutboundResponse) and m.response_id == 0:
                    assert "<break" not in (m.content or "")
                    if m.content_complete:
                        break
        finally:
            await session.stop()

    asyncio.run(_run())


def test_retell_mode_no_server_backchannel_by_default() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(cfg=BrainConfig(speak_first=False, retell_auto_reconnect=False))
        try:
            # Drain initial config + BEGIN terminal response_id=0.
            await session.recv_outbound()
            await session.recv_outbound()

            for _ in range(10):
                await session.send_inbound_obj(
                    {
                        "interaction_type": "update_only",
                        "transcript": [{"role": "user", "content": "Just talking..."}],
                        "turntaking": "user_turn",
                    }
                )
            for _ in range(50):
                await asyncio.sleep(0)

            # No agent_interrupt backchannels should ever be emitted by default config.
            while session.transport.outbound_qsize():
                m = await session.recv_outbound()
                assert not isinstance(m, OutboundAgentInterrupt)

            assert session.metrics.get(VIC["backchannel_detected_total"]) == 0
        finally:
            await session.stop()

    asyncio.run(_run())

