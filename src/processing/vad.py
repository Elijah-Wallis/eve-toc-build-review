from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class VADConfig:
    interruptions_enabled: bool = True
    interruption_sensitivity: float = 0.6
    hangover_frames: int = 3


class VAD:
    def __init__(self, cfg: VADConfig) -> None:
        self._cfg = cfg
        self._hangover = 0

    def update_config(self, cfg: VADConfig) -> None:
        self._cfg = cfg

    def is_speech(self, frame: bytes) -> bool:
        if not frame:
            return False
        rms = frame_rms(frame)
        thr = speech_threshold(self._cfg.interruption_sensitivity)
        speech = rms >= thr
        if speech:
            self._hangover = self._cfg.hangover_frames
            return True
        if self._hangover > 0:
            self._hangover -= 1
            return True
        return False


def speech_threshold(interruption_sensitivity: float) -> float:
    s = max(0.0, min(1.0, float(interruption_sensitivity)))
    # higher sensitivity => lower threshold => easier interruption detection
    return 1200.0 - (800.0 * s)


def frame_rms(frame: bytes) -> float:
    if len(frame) < 2:
        return 0.0
    n = len(frame) // 2
    if n == 0:
        return 0.0
    total = 0.0
    for i in range(0, n * 2, 2):
        v = int.from_bytes(frame[i : i + 2], "little", signed=True)
        total += float(v * v)
    return (total / n) ** 0.5
