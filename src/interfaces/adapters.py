from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from .events import SpeechSegment


@dataclass(frozen=True, slots=True)
class ASRResult:
    text: str
    is_final: bool
    stability: float


class ASRAdapter(Protocol):
    async def ingest_audio(self, *, frame: bytes, has_speech: bool, now_ms: int) -> list[ASRResult]: ...

    async def force_finalize(self) -> ASRResult | None: ...


class LLMAdapter(Protocol):
    async def stream_text(self, *, prompt: str) -> AsyncIterator[str]: ...


class TTSAdapter(Protocol):
    async def synthesize(self, *, segment: SpeechSegment, sample_rate_hz: int = 16000) -> AsyncIterator[bytes]: ...


class MockASRAdapter:
    """
    Deterministic no-key ASR mock.
    Emits partials while speech is detected and final text after brief silence.
    """

    def __init__(self, *, scripted_turns: list[str] | None = None) -> None:
        self._script = list(
            scripted_turns
            or [
                "i want to book an appointment",
                "what times are available tomorrow",
                "how much is a general visit",
            ]
        )
        self._turn_idx = 0
        self._speech_frames = 0
        self._silence_frames = 0
        self._active = False

    def _current_script(self) -> str:
        if not self._script:
            return "hello"
        return self._script[self._turn_idx % len(self._script)]

    async def ingest_audio(self, *, frame: bytes, has_speech: bool, now_ms: int) -> list[ASRResult]:
        out: list[ASRResult] = []
        if has_speech:
            self._active = True
            self._speech_frames += 1
            self._silence_frames = 0
            if self._speech_frames % 3 == 0:
                words = self._current_script().split()
                n = min(len(words), max(1, self._speech_frames // 3))
                out.append(
                    ASRResult(
                        text=" ".join(words[:n]),
                        is_final=False,
                        stability=min(0.95, 0.2 + (0.1 * n)),
                    )
                )
        else:
            self._silence_frames += 1
            if self._active and self._silence_frames >= 5:
                out.append(ASRResult(text=self._current_script(), is_final=True, stability=1.0))
                self._turn_idx += 1
                self._speech_frames = 0
                self._silence_frames = 0
                self._active = False
        return out

    async def force_finalize(self) -> ASRResult | None:
        if not self._active:
            return None
        self._active = False
        self._speech_frames = 0
        self._silence_frames = 0
        txt = self._current_script()
        self._turn_idx += 1
        return ASRResult(text=txt, is_final=True, stability=1.0)


class MockLLMAdapter:
    def __init__(self, *, token_delay_ms: int = 20) -> None:
        self._token_delay_ms = int(token_delay_ms)

    async def stream_text(self, *, prompt: str) -> AsyncIterator[str]:
        p = (prompt or "").lower()
        if "price" in p or "cost" in p:
            reply = "[excited] Great question, the general visit is $120, and I can help you book now."
        elif "available" in p or "appointment" in p:
            reply = "[slow] I can offer Tuesday at 9:00 AM, Tuesday at 11:30 AM, or Wednesday at 2:15 PM."
        else:
            reply = "[laughs] I can help with that, and we can do this step by step."

        for tok in reply.split(" "):
            if self._token_delay_ms > 0:
                await asyncio.sleep(self._token_delay_ms / 1000.0)
            yield tok + " "


class MockTTSAdapter:
    """
    Deterministic tone generator for streaming PCM16LE chunks.
    """

    def __init__(self) -> None:
        self._phase = 0.0

    async def synthesize(self, *, segment: SpeechSegment, sample_rate_hz: int = 16000) -> AsyncIterator[bytes]:
        words = max(1, segment.word_count)
        frames = max(1, words)
        base_freq = 220.0
        if segment.style_modifier.value == "excited":
            base_freq = 290.0
        elif segment.style_modifier.value == "whispers":
            base_freq = 160.0
        elif segment.style_modifier.value == "slow":
            base_freq = 180.0

        for _ in range(frames):
            pcm = _tone_frame(
                sample_rate_hz=sample_rate_hz,
                freq_hz=base_freq,
                phase=self._phase,
                amplitude=0.2,
            )
            self._phase += 0.1
            yield pcm
            await asyncio.sleep(0)


def _tone_frame(*, sample_rate_hz: int, freq_hz: float, phase: float, amplitude: float) -> bytes:
    # 20ms at 16kHz = 320 samples.
    n_samples = int(sample_rate_hz * 0.02)
    out = bytearray()
    for i in range(n_samples):
        t = (i / sample_rate_hz) + phase
        v = int(max(-32767, min(32767, math.sin(2 * math.pi * freq_hz * t) * 32767 * amplitude)))
        out.extend(int(v).to_bytes(2, "little", signed=True))
    return bytes(out)
