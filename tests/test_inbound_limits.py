from __future__ import annotations

import asyncio
import json

from app.config import BrainConfig

from tests.harness.transport_harness import HarnessSession


def test_reject_frame_too_large() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(
            cfg=BrainConfig(
                speak_first=False,
                retell_auto_reconnect=False,
                idle_timeout_ms=60000,
                ws_max_frame_bytes=64,
            )
        )
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            large_payload = json.dumps(
                {
                    "interaction_type": "update_only",
                    "transcript": [{"role": "user", "content": ("x" * 400)}],
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            await session.transport.push_inbound(large_payload)

            for _ in range(100):
                if session.shutdown_evt.is_set():
                    break
                await asyncio.sleep(0)
            assert session.shutdown_evt.is_set() is True
            assert session.metrics.get("ws.close_reason_total.FRAME_TOO_LARGE") >= 1
        finally:
            await session.stop()

    asyncio.run(_run())


def test_bad_json_close_reason_is_deterministic() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(
            cfg=BrainConfig(
                speak_first=False,
                retell_auto_reconnect=False,
                idle_timeout_ms=60000,
            )
        )
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.transport.push_inbound("{not-valid-json")
            for _ in range(100):
                if session.shutdown_evt.is_set():
                    break
                await asyncio.sleep(0)
            assert session.shutdown_evt.is_set() is True
            assert session.metrics.get("ws.close_reason_total.BAD_JSON") >= 1
        finally:
            await session.stop()

    asyncio.run(_run())


def test_bad_schema_is_dropped_and_session_stays_open() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(
            cfg=BrainConfig(
                speak_first=False,
                retell_auto_reconnect=False,
                idle_timeout_ms=60000,
            )
        )
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            # Missing required "timestamp" (schema error) and unknown future event type:
            # must be dropped without tearing down the websocket.
            await session.transport.push_inbound(
                json.dumps({"interaction_type": "ping_pong"}, separators=(",", ":"), sort_keys=True)
            )
            await session.transport.push_inbound(
                json.dumps(
                    {"interaction_type": "future_event", "foo": "bar"},
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            for _ in range(100):
                if session.shutdown_evt.is_set():
                    break
                await asyncio.sleep(0)

            assert session.shutdown_evt.is_set() is False
            assert session.metrics.get("inbound.bad_schema_total") >= 2
            assert session.metrics.get("ws.close_reason_total.BAD_SCHEMA") == 0

            # Prove the session remains functional by completing a normal turn.
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "Hi"}],
                }
            )
            # Drain until epoch=1 terminal.
            while True:
                m = await session.recv_outbound()
                if getattr(m, "response_type", "") == "response" and getattr(m, "response_id", 0) == 1:
                    if getattr(m, "content_complete", False):
                        break
        finally:
            await session.stop()

    asyncio.run(_run())


def test_frame_limit_uses_utf8_bytes_not_char_count() -> None:
    async def _run() -> None:
        multibyte_text = "🙂" * 40
        payload = json.dumps(
            {
                "interaction_type": "update_only",
                "transcript": [{"role": "user", "content": multibyte_text}],
            },
            separators=(",", ":"),
            sort_keys=True,
            ensure_ascii=False,
        )
        char_len = len(payload)
        byte_len = len(payload.encode("utf-8"))
        assert byte_len > char_len

        session = await HarnessSession.start(
            cfg=BrainConfig(
                speak_first=False,
                retell_auto_reconnect=False,
                idle_timeout_ms=60000,
                ws_max_frame_bytes=char_len + 1,
            )
        )
        try:
            await session.recv_outbound()
            await session.recv_outbound()
            await session.transport.push_inbound(payload)
            for _ in range(100):
                if session.shutdown_evt.is_set():
                    break
                await asyncio.sleep(0)
            assert session.shutdown_evt.is_set() is True
            assert session.metrics.get("ws.close_reason_total.FRAME_TOO_LARGE") >= 1
        finally:
            await session.stop()

    asyncio.run(_run())
