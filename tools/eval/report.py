from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description="Print concise replay report.")
    ap.add_argument("--input", required=True)
    args = ap.parse_args()

    data = json.loads(Path(args.input).read_text())
    metrics = data.get("metrics", {})
    counters = metrics.get("counters", {})
    timings = metrics.get("timings", {})

    def p95(name: str) -> int | None:
        vals = sorted(timings.get(name, []))
        if not vals:
            return None
        idx = int(round(0.95 * (len(vals) - 1)))
        return vals[idx]

    print("Replay Summary")
    print("==============")
    print(f"session_id: {data.get('session_id')}")
    print(f"state:      {data.get('state')}")
    print(f"EOU p95:    {p95('turn.eou_detection_ms')}")
    print(f"FirstTok p95: {p95('turn.first_token_latency_ms')}")
    print(f"FirstAud p95: {p95('turn.first_audio_latency_ms')}")
    print(f"Barge p95:    {p95('turn.barge_in_stop_latency_ms')}")
    print(f"Soft timeout triggers: {counters.get('turn.soft_timeout_trigger_total', 0)}")
    print(f"False interruptions:   {counters.get('turn.false_interruption_total', 0)}")


if __name__ == "__main__":
    main()
