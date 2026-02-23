from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Iterable

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import BrainConfig
from app.metrics import VIC

from tests.harness.transport_harness import HarnessSession


def _percentile(values: Iterable[int], p: float) -> int | None:
    v = sorted(int(x) for x in values)
    if not v:
        return None
    if p <= 0:
        return v[0]
    if p >= 100:
        return v[-1]
    k = int(round((p / 100.0) * (len(v) - 1)))
    return v[k]


async def _run_sessions(n: int) -> None:
    cfg = BrainConfig(speak_first=False, retell_auto_reconnect=False, idle_timeout_ms=10_000_000)
    sessions: list[HarnessSession] = []
    try:
        for i in range(n):
            sessions.append(await HarnessSession.start(session_id=f"lt{i}", cfg=cfg))

        # Drain initial config + BEGIN terminal for all sessions.
        for s in sessions:
            await s.recv_outbound()
            await s.recv_outbound()

        # One turn per session.
        for s in sessions:
            await s.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "Hi"}],
                }
            )

        # Wait deterministically until all sessions have observed ACK latency.
        for _ in range(5000):
            if all(s.metrics.get_hist(VIC["turn_final_to_ack_segment_ms"]) for s in sessions):
                break
            await asyncio.sleep(0)

        ack_lats: list[int] = []
        first_lats: list[int] = []
        stale_drops = 0
        schema_violations = 0
        for s in sessions:
            ack_lats.extend(s.metrics.get_hist(VIC["turn_final_to_ack_segment_ms"]))
            first_lats.extend(s.metrics.get_hist(VIC["turn_final_to_first_segment_ms"]))
            stale_drops += s.metrics.get(VIC["stale_segment_dropped_total"])
            schema_violations += s.trace.schema_violations_total

        print("**Load Test Summary**")
        print(f"sessions={n}")
        print(f"schema_violations_total={schema_violations}")
        print(f"stale_segment_dropped_total={stale_drops}")
        ack_p50 = _percentile(ack_lats, 50)
        ack_p95 = _percentile(ack_lats, 95)
        ack_p99 = _percentile(ack_lats, 99)
        print(
            "ack_latency_ms="
            f"p50={ack_p50 if ack_p50 is not None else 'n/a'} "
            f"p95={ack_p95 if ack_p95 is not None else 'n/a'} "
            f"p99={ack_p99 if ack_p99 is not None else 'n/a'}"
        )
        first_p50 = _percentile(first_lats, 50)
        first_p95 = _percentile(first_lats, 95)
        first_p99 = _percentile(first_lats, 99)
        print(
            "first_segment_latency_ms="
            f"p50={first_p50 if first_p50 is not None else 'n/a'} "
            f"p95={first_p95 if first_p95 is not None else 'n/a'} "
            f"p99={first_p99 if first_p99 is not None else 'n/a'}"
        )
    finally:
        await asyncio.gather(*(s.stop() for s in sessions), return_exceptions=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Deterministic in-memory load test for the Retell WS Brain.")
    ap.add_argument("--sessions", type=int, default=100, help="number of concurrent sessions to simulate")
    args = ap.parse_args()
    asyncio.run(_run_sessions(int(args.sessions)))


if __name__ == "__main__":
    main()
