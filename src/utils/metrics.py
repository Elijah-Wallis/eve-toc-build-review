from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricsStore:
    counters: dict[str, int] = field(default_factory=dict)
    timings: dict[str, list[int]] = field(default_factory=dict)
    gauges: dict[str, int] = field(default_factory=dict)

    def inc(self, name: str, value: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + int(value)

    def observe(self, name: str, value_ms: int) -> None:
        self.timings.setdefault(name, []).append(int(value_ms))

    def set(self, name: str, value: int) -> None:
        self.gauges[name] = int(value)

    def percentile(self, name: str, p: float) -> int | None:
        arr = sorted(self.timings.get(name, []))
        if not arr:
            return None
        idx = int(round((max(0.0, min(100.0, p)) / 100.0) * (len(arr) - 1)))
        return arr[idx]

    def snapshot(self) -> dict[str, Any]:
        return {
            "counters": dict(self.counters),
            "timings": {k: list(v) for k, v in self.timings.items()},
            "gauges": dict(self.gauges),
        }


METRIC_KEYS = {
    "eou_detection_ms": "turn.eou_detection_ms",
    "first_token_latency_ms": "turn.first_token_latency_ms",
    "first_audio_latency_ms": "turn.first_audio_latency_ms",
    "barge_in_stop_latency_ms": "turn.barge_in_stop_latency_ms",
    "soft_timeout_trigger_total": "turn.soft_timeout_trigger_total",
    "false_interruption_total": "turn.false_interruption_total",
    "audio_in_dropped_total": "queue.audio_in_dropped_total",
    "audio_out_dropped_total": "queue.audio_out_dropped_total",
}
