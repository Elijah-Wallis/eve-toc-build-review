from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from .vad import frame_rms


@dataclass(frozen=True, slots=True)
class ProsodyFeatures:
    energy_rms: float
    pitch_proxy: float
    speaking_rate_proxy: float
    pause_stability: float


class ProsodyTracker:
    def __init__(self, *, max_frames: int = 50) -> None:
        self._max_frames = max(5, int(max_frames))
        self._rms: list[float] = []
        self._zc: list[float] = []
        self._speech_flags: list[bool] = []

    def ingest(self, frame: bytes, *, has_speech: bool) -> ProsodyFeatures:
        rms = frame_rms(frame)
        zc = _zero_crossing_rate(frame)
        self._rms.append(rms)
        self._zc.append(zc)
        self._speech_flags.append(bool(has_speech))
        if len(self._rms) > self._max_frames:
            self._rms.pop(0)
            self._zc.pop(0)
            self._speech_flags.pop(0)

        return ProsodyFeatures(
            energy_rms=(mean(self._rms) if self._rms else 0.0),
            pitch_proxy=(mean(self._zc[-10:]) if self._zc else 0.0),
            speaking_rate_proxy=_speaking_rate_proxy(self._speech_flags),
            pause_stability=_pause_stability(self._speech_flags),
        )


def prosody_adjustment_ms(features: ProsodyFeatures) -> int:
    # Rising + unstable cadence => wait longer
    if features.pitch_proxy >= 0.18 and features.pause_stability <= 0.45:
        return 120
    # Falling + stable cadence => respond earlier
    if features.pitch_proxy <= 0.08 and features.pause_stability >= 0.7:
        return -100
    return 0


def _zero_crossing_rate(frame: bytes) -> float:
    if len(frame) < 4:
        return 0.0
    prev = int.from_bytes(frame[0:2], "little", signed=True)
    crossings = 0
    n = len(frame) // 2
    for i in range(2, n * 2, 2):
        cur = int.from_bytes(frame[i : i + 2], "little", signed=True)
        if (prev < 0 <= cur) or (prev > 0 >= cur):
            crossings += 1
        prev = cur
    return crossings / max(1, n)


def _speaking_rate_proxy(flags: list[bool]) -> float:
    if not flags:
        return 0.0
    speech_runs = 0
    in_run = False
    for f in flags:
        if f and not in_run:
            speech_runs += 1
            in_run = True
        elif not f:
            in_run = False
    return speech_runs / max(1, len(flags))


def _pause_stability(flags: list[bool]) -> float:
    if not flags:
        return 0.0
    pauses: list[int] = []
    run = 0
    for f in flags:
        if not f:
            run += 1
        elif run > 0:
            pauses.append(run)
            run = 0
    if run > 0:
        pauses.append(run)
    if not pauses:
        return 1.0
    avg = mean(pauses)
    spread = sum(abs(p - avg) for p in pauses) / len(pauses)
    return max(0.0, 1.0 - (spread / max(1.0, avg + 1.0)))
