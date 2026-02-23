from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional


_INTERRUPT_WORDS_PAT = re.compile(r"\b(no|wait|hold on|stop|cancel|don't)\b", re.I)


def _det_jitter_ms(*, session_id: str, n: int, span_ms: int) -> int:
    if span_ms <= 0:
        return 0
    seed = f"{session_id}:{n}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    # Use 2 bytes for a small deterministic jitter.
    v = int.from_bytes(digest[:2], "big")
    return int(v % int(span_ms))


@dataclass(slots=True)
class BackchannelState:
    monologue_started_ms: Optional[int] = None
    last_backchannel_ms: Optional[int] = None
    count: int = 0


class BackchannelClassifier:
    """
    Deterministic backchannel trigger for long user monologues.

    - Rate limited to 1 per [min_interval_ms, max_interval_ms] with deterministic jitter.
    - Suppressed during sensitive capture.
    - Treats interruption keywords as "do not backchannel" signals.
    """

    def __init__(self, *, session_id: str, min_interval_ms: int = 2500, max_interval_ms: int = 4000) -> None:
        self._session_id = session_id
        self._min_ms = int(min_interval_ms)
        self._max_ms = int(max_interval_ms)
        self._state = BackchannelState()

    def consider(
        self,
        *,
        now_ms: int,
        user_text: str,
        user_turn: bool,
        sensitive_capture: bool,
    ) -> Optional[str]:
        if not user_turn:
            self._state.monologue_started_ms = None
            return None

        if sensitive_capture:
            self._state.monologue_started_ms = None
            return None

        if _INTERRUPT_WORDS_PAT.search(user_text or ""):
            self._state.monologue_started_ms = None
            return None

        if self._state.monologue_started_ms is None:
            self._state.monologue_started_ms = int(now_ms)
            return None

        # Deterministic interval within the allowed window.
        span = max(0, self._max_ms - self._min_ms)
        jitter = _det_jitter_ms(session_id=self._session_id, n=self._state.count, span_ms=span + 1)
        interval_ms = int(self._min_ms + jitter)

        last = self._state.last_backchannel_ms
        if last is None:
            if int(now_ms) - int(self._state.monologue_started_ms) < interval_ms:
                return None
        else:
            if int(now_ms) - int(last) < interval_ms:
                return None

        phrase = self._choose_phrase(self._state.count)
        self._state.last_backchannel_ms = int(now_ms)
        self._state.count += 1
        return phrase

    def _choose_phrase(self, n: int) -> str:
        # Deterministic choice to avoid repetition without randomness.
        phrases = ["Mm-hmm.", "Okay.", "Got it."]
        return phrases[int(n) % len(phrases)]

