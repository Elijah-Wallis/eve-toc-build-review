from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config import BrainConfig
from app.metrics import VIC
from app.protocol import (
    InboundPingPong,
    InboundReminderRequired,
    InboundResponseRequired,
    OutboundPingPong,
    OutboundResponse,
    dumps_outbound,
    parse_inbound_json,
    parse_outbound_json,
)

from tests.harness.transport_harness import HarnessSession


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "retell_wire"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8").strip()


def test_retell_inbound_fixtures_parse() -> None:
    assert isinstance(parse_inbound_json(_load("inbound_ping_pong_valid.json")), InboundPingPong)
    assert isinstance(
        parse_inbound_json(_load("inbound_response_required_valid.json")),
        InboundResponseRequired,
    )
    assert isinstance(
        parse_inbound_json(_load("inbound_reminder_required_valid.json")),
        InboundReminderRequired,
    )


def test_retell_outbound_fixtures_serialize() -> None:
    items = [
        parse_outbound_json(_load("outbound_ping_pong_valid.json")),
        parse_outbound_json(_load("outbound_response_chunk_valid.json")),
        parse_outbound_json(_load("outbound_response_terminal_valid.json")),
    ]
    assert isinstance(items[0], OutboundPingPong)
    assert isinstance(items[1], OutboundResponse)
    assert isinstance(items[2], OutboundResponse)
    for ev in items:
        rt = parse_outbound_json(dumps_outbound(ev))
        assert type(rt) is type(ev)


def test_required_fields_enforced() -> None:
    with pytest.raises(Exception):
        parse_inbound_json(_load("inbound_ping_pong_missing_timestamp_invalid.json"))
    with pytest.raises(Exception):
        parse_inbound_json(_load("inbound_response_required_missing_id_invalid.json"))


def test_keepalive_deadline_behavior_under_blocked_send() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(
            cfg=BrainConfig(
                speak_first=False,
                idle_timeout_ms=60000,
                ws_write_timeout_ms=40,
                ws_max_consecutive_write_timeouts=1,
                ws_close_on_write_timeout=True,
            ),
        )
        try:
            await session.recv_outbound()
            await session.recv_outbound()
            session.transport.send_allowed.clear()
            await session.send_inbound_obj({"interaction_type": "ping_pong", "timestamp": 111})

            for _ in range(10):
                if session.shutdown_evt.is_set():
                    break
                await session.clock.advance(40)
                for _ in range(20):
                    await asyncio.sleep(0)

            assert session.shutdown_evt.is_set() is True
            assert session.metrics.get(VIC["keepalive_ping_pong_write_attempt_total"]) >= 1
            assert session.metrics.get(VIC["keepalive_ping_pong_write_timeout_total"]) >= 1
            assert session.metrics.get("ws.close_reason_total.WRITE_TIMEOUT_BACKPRESSURE") >= 1
        finally:
            await session.stop()

    asyncio.run(_run())
