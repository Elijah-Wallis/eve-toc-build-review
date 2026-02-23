from __future__ import annotations

import asyncio
import json

from app.config import BrainConfig
from app.protocol import OutboundResponse, OutboundToolCallInvocation, OutboundToolCallResult

from tests.harness.transport_harness import HarnessSession


def test_b2b_explicit_dnc_invokes_mark_dnc_compliant_tool() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(
            cfg=BrainConfig(
                speak_first=False,
                conversation_profile="b2b",
                retell_auto_reconnect=False,
                idle_timeout_ms=60000,
            )
        )
        try:
            # Drain initial config + BEGIN terminal response_id=0.
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "stop calling me"}],
                },
                expect_ack=False,
            )

            inv: OutboundToolCallInvocation | None = None
            res: OutboundToolCallResult | None = None
            saw_end_call_terminal = False

            for _ in range(50):
                m = await session.recv_outbound()
                if isinstance(m, OutboundToolCallInvocation) and m.name == "mark_dnc_compliant":
                    inv = m
                if isinstance(m, OutboundToolCallResult) and inv is not None and m.tool_call_id == inv.tool_call_id:
                    res = m
                if isinstance(m, OutboundResponse) and m.response_id == 1 and m.content_complete:
                    if bool(getattr(m, "end_call", False)):
                        saw_end_call_terminal = True
                if inv is not None and res is not None and saw_end_call_terminal:
                    break

            assert inv is not None, "expected tool_call_invocation for mark_dnc_compliant"
            assert json.loads(inv.arguments or "{}").get("reason") == "USER_REQUEST"

            assert res is not None, "expected tool_call_result for mark_dnc_compliant"
            payload = json.loads(res.content or "{}")
            assert payload.get("ok") is True
            assert payload.get("tool") == "mark_dnc_compliant"
            assert payload.get("reason") == "USER_REQUEST"

            assert saw_end_call_terminal is True, "expected terminal response with end_call=True"
        finally:
            await session.stop()

    asyncio.run(_run())

