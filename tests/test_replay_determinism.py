from __future__ import annotations

import asyncio

from app.metrics import VIC
from tests.harness.transport_harness import HarnessSession


def test_replay_determinism_digest_equality() -> None:
    async def run_once() -> str:
        session = await HarnessSession.start(session_id="replay", tool_latencies={"get_pricing": 0})
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "How much does it cost?"}],
                }
            )

            # Let the turn complete (tool latency 0 => quick).
            for _ in range(100):
                await asyncio.sleep(0)
                # Terminal response is written; ensure speech plans exist.
                if any(p.epoch == 1 for p in session.orch.speech_plans):
                    # also allow writer to flush terminal
                    if session.transport.outbound_qsize() > 0:
                        pass

            assert session.trace.schema_violations_total == 0
            assert session.metrics.get(VIC["replay_hash_mismatch_total"]) == 0
            return session.trace.replay_digest()
        finally:
            await session.stop()

    d1 = asyncio.run(run_once())
    d2 = asyncio.run(run_once())
    assert d1 == d2
