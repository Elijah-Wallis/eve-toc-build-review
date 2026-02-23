from __future__ import annotations

import struct
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


AUDIO_MAGIC = b"EVA1"
AUDIO_HEADER_STRUCT = struct.Struct("<4sHHIH")
FLAG_END_OF_TURN = 1
FLAG_BARGE_FLUSH = 2


class TurnState(str, Enum):
    IDLE = "IDLE"
    USER_SPEAKING = "USER_SPEAKING"
    AGENT_THINKING = "AGENT_THINKING"
    AGENT_SPEAKING = "AGENT_SPEAKING"
    BARGED_IN_RECOVERY = "BARGED_IN_RECOVERY"
    ENDED = "ENDED"


class TurnEagerness(str, Enum):
    EAGER = "EAGER"
    NORMAL = "NORMAL"
    PATIENT = "PATIENT"


class StyleModifier(str, Enum):
    BASELINE = "baseline"
    LAUGHS = "laughs"
    WHISPERS = "whispers"
    SIGHS = "sighs"
    SLOW = "slow"
    EXCITED = "excited"


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_timeout_sec: int = Field(default=8, ge=1, le=30)
    soft_timeout_enabled: bool = True
    soft_timeout_sec: float = Field(default=0.8, ge=0.2, le=10.0)
    interruptions_enabled: bool = True
    interruption_sensitivity: float = Field(default=0.6, ge=0.0, le=1.0)
    turn_eagerness: TurnEagerness = TurnEagerness.NORMAL
    expressive_scope_words: int = Field(default=5, ge=1, le=20)
    target_voice_id: Optional[str] = None

    # Explicitly blocked impersonation-style fields.
    target_person_voice: Optional[str] = None
    voice_clone_id: Optional[str] = None
    voice_clone_source: Optional[str] = None

    @model_validator(mode="after")
    def validate_safety(self) -> "RuntimeConfig":
        if self.target_person_voice:
            raise ValueError("target_person_voice is not allowed")
        if self.voice_clone_id:
            raise ValueError("voice_clone_id is not allowed")
        if self.voice_clone_source:
            raise ValueError("voice_clone_source is not allowed")
        if self.target_voice_id and not self.target_voice_id.startswith("neutral_"):
            raise ValueError("target_voice_id must reference a neutral catalog voice")
        return self


class SpeechSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    style_modifier: StyleModifier = StyleModifier.BASELINE
    speed_multiplier: float = 1.0
    scope_id: str
    word_count: int


class SessionStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["session.start"]
    session_id: str
    config: RuntimeConfig


class ConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["config.update"]
    config_patch: dict[str, Any]


class UserTurnEnd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["user.turn_end"]
    ts_ms: int


class Ping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ping"]
    ts_ms: int


ClientControl = SessionStart | ConfigUpdate | UserTurnEnd | Ping


def parse_client_control(payload: dict[str, Any]) -> ClientControl:
    t = payload.get("type")
    if t == "session.start":
        return SessionStart.model_validate(payload)
    if t == "config.update":
        return ConfigUpdate.model_validate(payload)
    if t == "user.turn_end":
        return UserTurnEnd.model_validate(payload)
    if t == "ping":
        return Ping.model_validate(payload)
    raise ValueError(f"unknown control event type: {t}")


def build_audio_packet(*, stream_kind: int, seq: int, flags: int, pcm: bytes) -> bytes:
    header = AUDIO_HEADER_STRUCT.pack(AUDIO_MAGIC, int(stream_kind), 0, int(seq), int(flags))
    return header + pcm


def parse_audio_packet(blob: bytes) -> tuple[int, int, int, bytes]:
    if len(blob) < AUDIO_HEADER_STRUCT.size:
        raise ValueError("audio packet too short")
    magic, stream_kind, _reserved, seq, flags = AUDIO_HEADER_STRUCT.unpack_from(blob, 0)
    if magic != AUDIO_MAGIC:
        raise ValueError("invalid audio packet magic")
    return int(stream_kind), int(seq), int(flags), blob[AUDIO_HEADER_STRUCT.size :]
