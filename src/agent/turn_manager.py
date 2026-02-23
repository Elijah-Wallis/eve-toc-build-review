from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.interfaces.events import RuntimeConfig, TurnEagerness, TurnState
from src.processing.prosody import ProsodyFeatures, prosody_adjustment_ms

TurnEvent = Literal["none", "eou_detected", "barge_in", "turn_timeout_prompt"]


@dataclass(slots=True)
class TurnSnapshot:
    state: TurnState = TurnState.IDLE
    last_speech_ms: int = 0
    turn_started_ms: int = 0
    timeout_prompted: bool = False


class TurnManager:
    def __init__(self, *, config: RuntimeConfig) -> None:
        self._cfg = config
        self._snap = TurnSnapshot()

    @property
    def state(self) -> TurnState:
        return self._snap.state

    def update_config(self, cfg: RuntimeConfig) -> None:
        self._cfg = cfg

    @staticmethod
    def eagerness_scalar(mode: TurnEagerness) -> float:
        if mode == TurnEagerness.EAGER:
            return 1.0
        if mode == TurnEagerness.NORMAL:
            return 0.5
        return 0.0

    def base_eou_ms(self) -> int:
        e = self.eagerness_scalar(self._cfg.turn_eagerness)
        return int(800 - (600 * e))

    def final_eou_ms(self, prosody: ProsodyFeatures | None) -> int:
        base = self.base_eou_ms()
        adjust = prosody_adjustment_ms(prosody) if prosody is not None else 0
        return int(max(120, min(1200, base + adjust)))

    def set_state(self, state: TurnState, now_ms: int) -> None:
        self._snap.state = state
        if state == TurnState.USER_SPEAKING:
            self._snap.turn_started_ms = int(now_ms)
            self._snap.timeout_prompted = False

    def on_audio(self, *, has_speech: bool, now_ms: int, prosody: ProsodyFeatures | None) -> TurnEvent:
        s = self._snap.state
        now_ms = int(now_ms)

        if s == TurnState.AGENT_SPEAKING and self._cfg.interruptions_enabled and has_speech:
            self._snap.state = TurnState.BARGED_IN_RECOVERY
            return "barge_in"

        if s in {TurnState.IDLE, TurnState.BARGED_IN_RECOVERY} and has_speech:
            self.set_state(TurnState.USER_SPEAKING, now_ms)
            self._snap.last_speech_ms = now_ms
            return "none"

        if s == TurnState.USER_SPEAKING:
            if has_speech:
                self._snap.last_speech_ms = now_ms
                return "none"

            # turn timeout prompt once while waiting in user turn
            if (
                not self._snap.timeout_prompted
                and now_ms - self._snap.last_speech_ms >= int(self._cfg.turn_timeout_sec * 1000)
            ):
                self._snap.timeout_prompted = True
                return "turn_timeout_prompt"

            eou_ms = self.final_eou_ms(prosody)
            if now_ms - self._snap.last_speech_ms >= eou_ms:
                self._snap.state = TurnState.AGENT_THINKING
                return "eou_detected"

        return "none"
