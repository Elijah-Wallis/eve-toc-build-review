from __future__ import annotations

import asyncio

from app.config import BrainConfig
from app.metrics import VIC

from tests.harness.transport_harness import HarnessSession


def test_at_vic_100_sessions() -> None:
    async def _run() -> None:
        cfg = BrainConfig(speak_first=False, retell_auto_reconnect=False, idle_timeout_ms=10_000_000)
        sessions = []
        try:
            for i in range(100):
                sessions.append(await HarnessSession.start(session_id=f"s{i}", cfg=cfg))

            # Fire one turn per session.
            for s in sessions:
                await s.send_inbound_obj(
                    {
                        "interaction_type": "response_required",
                        "response_id": 1,
                        "transcript": [{"role": "user", "content": "Hi"}],
                    }
                )

            for _ in range(200):
                await asyncio.sleep(0)

            # Basic VIC sanity: ack metric present, no trace schema violations.
            for s in sessions:
                assert s.metrics.get_hist(VIC["turn_final_to_ack_segment_ms"])
                assert s.trace.schema_violations_total == 0
        finally:
            await asyncio.gather(*(s.stop() for s in sessions), return_exceptions=True)

    asyncio.run(_run())

