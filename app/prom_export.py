from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Iterable


_DEFAULT_MS_BUCKETS = (
    25,
    50,
    100,
    150,
    200,
    250,
    300,
    400,
    500,
    800,
    1000,
    1500,
    2000,
    5000,
    10000,
)


def _prom_name(name: str) -> str:
    # Prometheus does not allow '.' in metric names.
    return (name or "").replace(".", "_")


@dataclass(slots=True)
class _BucketHistogram:
    buckets: tuple[int, ...]
    counts: list[int] = field(default_factory=list)  # per-bucket (non-cumulative)
    sum: int = 0
    count: int = 0

    def __post_init__(self) -> None:
        if not self.counts:
            self.counts = [0 for _ in self.buckets] + [0]  # +Inf bucket

    def observe(self, v: int) -> None:
        x = int(v)
        self.sum += x
        self.count += 1
        idx = len(self.buckets)  # +Inf by default
        for i, b in enumerate(self.buckets):
            if x <= int(b):
                idx = i
                break
        self.counts[idx] += 1

    def iter_cumulative(self) -> Iterable[tuple[str, int]]:
        running = 0
        for i, b in enumerate(self.buckets):
            running += self.counts[i]
            yield (str(int(b)), running)
        running += self.counts[len(self.buckets)]
        yield ("+Inf", running)


class PromExporter:
    """
    Minimal Prometheus text exporter for counters and bucketed histograms.

    This intentionally avoids storing raw samples to keep memory bounded.
    """

    def __init__(self, *, ms_buckets: tuple[int, ...] = _DEFAULT_MS_BUCKETS) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._hists: dict[str, _BucketHistogram] = {}
        self._gauges: dict[str, int] = {}
        self._ms_buckets = tuple(int(b) for b in ms_buckets)

    def inc(self, name: str, value: int = 1) -> None:
        key = _prom_name(name)
        with self._lock:
            self._counters[key] = int(self._counters.get(key, 0)) + int(value)

    def observe(self, name: str, value: int) -> None:
        key = _prom_name(name)
        with self._lock:
            h = self._hists.get(key)
            if h is None:
                h = _BucketHistogram(buckets=self._ms_buckets)
                self._hists[key] = h
            h.observe(int(value))

    def set(self, name: str, value: int) -> None:
        key = _prom_name(name)
        with self._lock:
            self._gauges[key] = int(value)

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            # Counters.
            for name in sorted(self._counters.keys()):
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name} {int(self._counters[name])}")

            # Histograms.
            for name in sorted(self._hists.keys()):
                h = self._hists[name]
                lines.append(f"# TYPE {name} histogram")
                for le, c in h.iter_cumulative():
                    lines.append(f'{name}_bucket{{le="{le}"}} {int(c)}')
                lines.append(f"{name}_sum {int(h.sum)}")
                lines.append(f"{name}_count {int(h.count)}")

            # Gauges.
            for name in sorted(self._gauges.keys()):
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{name} {int(self._gauges[name])}")

        return "\n".join(lines) + "\n"


GLOBAL_PROM = PromExporter()
