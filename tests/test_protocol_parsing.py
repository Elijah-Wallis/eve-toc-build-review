from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.protocol import (
    InboundCallDetails,
    InboundClear,
    InboundPingPong,
    InboundReminderRequired,
    InboundResponseRequired,
    InboundUpdateOnly,
    OutboundAgentInterrupt,
    OutboundConfig,
    OutboundMetadata,
    OutboundPingPong,
    OutboundResponse,
    OutboundToolCallInvocation,
    OutboundToolCallResult,
    OutboundUpdateAgent,
    dumps_outbound,
    parse_inbound_json,
    parse_outbound_json,
)


FIX = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8").strip()


def test_inbound_parsing() -> None:
    assert isinstance(parse_inbound_json(_load("in_ping_pong.json")), InboundPingPong)
    assert isinstance(parse_inbound_json(_load("in_call_details.json")), InboundCallDetails)
    assert isinstance(parse_inbound_json(_load("in_update_only.json")), InboundUpdateOnly)
    assert isinstance(parse_inbound_json(_load("in_response_required.json")), InboundResponseRequired)
    assert isinstance(parse_inbound_json(_load("in_reminder_required.json")), InboundReminderRequired)
    assert isinstance(parse_inbound_json(_load("in_clear.json")), InboundClear)


def test_outbound_parsing_and_roundtrip() -> None:
    samples = [
        ("out_config.json", OutboundConfig),
        ("out_update_agent.json", OutboundUpdateAgent),
        ("out_ping_pong.json", OutboundPingPong),
        ("out_response_chunk.json", OutboundResponse),
        ("out_response_terminal.json", OutboundResponse),
        ("out_agent_interrupt_chunk.json", OutboundAgentInterrupt),
        ("out_agent_interrupt_terminal.json", OutboundAgentInterrupt),
        ("out_tool_call_invocation.json", OutboundToolCallInvocation),
        ("out_tool_call_result.json", OutboundToolCallResult),
        ("out_metadata.json", OutboundMetadata),
    ]

    for fname, cls in samples:
        ev = parse_outbound_json(_load(fname))
        assert isinstance(ev, cls)
        # Canonical dump roundtrips.
        dumped = dumps_outbound(ev)
        rt = parse_outbound_json(dumped)
        assert isinstance(rt, cls)


def test_unknown_discriminators_fail() -> None:
    with pytest.raises(Exception):
        parse_inbound_json(json.dumps({"interaction_type": "nope"}))
    with pytest.raises(Exception):
        parse_outbound_json(json.dumps({"response_type": "nope"}))
