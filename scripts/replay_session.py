from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from typing import Any, Iterable

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.harness.transport_harness import HarnessSession


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest_from_events(events: Iterable[dict[str, Any]]) -> str:
    parts: list[str] = []
    for e in events:
        parts.append(
            f"{e.get('seq')}:{e.get('t_ms')}:{e.get('session_id')}:{e.get('call_id')}:"
            f"{e.get('turn_id')}:{e.get('epoch')}:{e.get('ws_state')}:{e.get('conv_state')}:"
            f"{e.get('event_type')}:{e.get('payload_hash')}:{e.get('segment_hash') or ''}"
        )
    blob = "|".join(parts).encode("utf-8")
    return _sha256_hex(blob)


def _load_jsonl(path: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


async def _run_builtin() -> tuple[str, str]:
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
            for _ in range(200):
                await asyncio.sleep(0)
                if any(p.epoch == 1 for p in session.orch.speech_plans):
                    break
            assert session.trace.schema_violations_total == 0
            return session.trace.replay_digest()
        finally:
            await session.stop()

    d1 = await run_once()
    d2 = await run_once()
    return d1, d2


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay determinism helper (digest comparison).")
    ap.add_argument("--trace-a", type=str, default="", help="path to trace JSONL A")
    ap.add_argument("--trace-b", type=str, default="", help="path to trace JSONL B")
    args = ap.parse_args()

    if args.trace_a and args.trace_b:
        a = _load_jsonl(args.trace_a)
        b = _load_jsonl(args.trace_b)
        da = _digest_from_events(a)
        db = _digest_from_events(b)
        print(f"digest_a={da}")
        print(f"digest_b={db}")
        if da != db:
            print("replay_digest_mismatch", file=sys.stderr)
            raise SystemExit(1)
        return

    if args.trace_a:
        a = _load_jsonl(args.trace_a)
        da = _digest_from_events(a)
        print(f"digest={da}")
        return

    d1, d2 = asyncio.run(_run_builtin())
    print(f"digest_run_1={d1}")
    print(f"digest_run_2={d2}")
    if d1 != d2:
        print("replay_digest_mismatch", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
