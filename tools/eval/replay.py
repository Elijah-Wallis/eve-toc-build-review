from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agent.orchestrator import SessionOrchestrator
from src.utils.clock import FakeClock


async def run_replay(*, input_path: Path) -> dict:
    data = json.loads(input_path.read_text())
    clock = FakeClock(start_ms=0)
    orch = SessionOrchestrator(session_id=data.get("session_id", "replay"), clock=clock)
    await orch.start()

    await orch.submit_control(
        {
            "type": "session.start",
            "session_id": data.get("session_id", "replay"),
            "config": data.get("config", {}),
        }
    )

    for step in data.get("steps", []):
        dt = int(step.get("advance_ms", 0))
        if dt > 0:
            await clock.advance(dt)

        if step.get("kind") == "audio":
            energy = int(step.get("energy", 0))
            frame = _make_frame(energy=energy)
            await orch.submit_audio(frame)
        elif step.get("kind") == "control":
            await orch.submit_control(step.get("payload", {}))

        # let orchestrator process
        for _ in range(5):
            await asyncio.sleep(0)

    # Drain outbound briefly
    drained = 0
    while drained < 300:
        await asyncio.sleep(0)
        drained += 1

    await orch.stop()
    return {
        "session_id": data.get("session_id", "replay"),
        "metrics": orch.metrics.snapshot(),
        "state": orch.state.value,
    }


def _make_frame(*, energy: int) -> bytes:
    # 20ms mono PCM16 frame at 16kHz = 320 samples.
    v = max(-30000, min(30000, int(energy)))
    return b"".join(int(v).to_bytes(2, "little", signed=True) for _ in range(320))


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay expressive session fixtures and emit metrics JSON.")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    input_path = Path(args.input)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    result = asyncio.run(run_replay(input_path=input_path))
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps({"ok": True, "out": str(out_path)}, indent=2))


if __name__ == "__main__":
    main()
