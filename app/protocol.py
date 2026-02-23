from __future__ import annotations
import json
from typing import Annotated, Any, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

class TranscriptUtterance(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: Literal["user", "agent"]
    content: str

class RetellConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_reconnect: bool
    call_details: bool
    transcript_with_tool_calls: bool

class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    responsiveness: Optional[float] = None
    interruption_sensitivity: Optional[float] = None
    reminder_trigger_ms: Optional[int] = None
    reminder_max_count: Optional[int] = None

class InboundPingPong(BaseModel):
    model_config = ConfigDict(extra="ignore")
    interaction_type: Literal["ping_pong"]
    timestamp: int

class InboundCallDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")
    interaction_type: Literal["call_details"]
    call: dict[str, Any]

class InboundUpdateOnly(BaseModel):
    model_config = ConfigDict(extra="ignore")
    interaction_type: Literal["update_only"]
    transcript: list[TranscriptUtterance]
    transcript_with_tool_calls: Optional[list[Any]] = None
    turntaking: Optional[Literal["agent_turn", "user_turn"]] = None

class InboundResponseRequired(BaseModel):
    model_config = ConfigDict(extra="ignore")
    interaction_type: Literal["response_required"]
    response_id: int
    transcript: list[TranscriptUtterance]
    transcript_with_tool_calls: Optional[list[Any]] = None

class InboundReminderRequired(BaseModel):
    model_config = ConfigDict(extra="ignore")
    interaction_type: Literal["reminder_required"]
    response_id: int
    transcript: list[TranscriptUtterance]
    transcript_with_tool_calls: Optional[list[Any]] = None

# Clear events are emitted by Retell during interruption handling; treat as a first-class inbound type.
class InboundClear(BaseModel):
    model_config = ConfigDict(extra="ignore")
    interaction_type: Literal["clear"]

InboundEvent = Annotated[
    Union[
        InboundPingPong,
        InboundCallDetails,
        InboundUpdateOnly,
        InboundResponseRequired,
        InboundReminderRequired,
        InboundClear,
    ],
    Field(discriminator="interaction_type"),
]

_inbound_adapter = TypeAdapter(InboundEvent)

TIMING_MARKER_PHASES = frozenset({
    "policy_decision_start_ms", "policy_decision_ms", "speech_plan_build_start_ms",
    "speech_plan_build_ms", "speech_plan_ack_ms", "pre_ack_enqueued",
    "outbound_enqueue_start_ms", "outbound_enqueue_ms", "first_response_latency_ms",
})

def is_timing_marker_phase(phase: str) -> bool:
    return str(phase) in TIMING_MARKER_PHASES

class OutboundConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_type: Literal["config"]
    config: RetellConfig

class OutboundUpdateAgent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_type: Literal["update_agent"]
    agent_config: AgentConfig

class OutboundPingPong(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_type: Literal["ping_pong"]
    timestamp: int

class OutboundResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_type: Literal["response"]
    response_id: int
    content: str
    content_complete: bool
    no_interruption_allowed: Optional[bool] = None
    end_call: Optional[bool] = None
    transfer_number: Optional[str] = None
    digit_to_press: Optional[str] = None

class OutboundAgentInterrupt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_type: Literal["agent_interrupt"]
    interrupt_id: int
    content: str
    content_complete: bool
    no_interruption_allowed: Optional[bool] = None
    end_call: Optional[bool] = None
    transfer_number: Optional[str] = None
    digit_to_press: Optional[str] = None

class OutboundToolCallInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_type: Literal["tool_call_invocation"]
    tool_call_id: str
    name: str
    arguments: str

class OutboundToolCallResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_type: Literal["tool_call_result"]
    tool_call_id: str
    content: str

class OutboundMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_type: Literal["metadata"]
    metadata: Any

OutboundEvent = Annotated[
    Union[
        OutboundConfig,
        OutboundUpdateAgent,
        OutboundPingPong,
        OutboundResponse,
        OutboundAgentInterrupt,
        OutboundToolCallInvocation,
        OutboundToolCallResult,
        OutboundMetadata,
    ],
    Field(discriminator="response_type"),
]

_outbound_adapter = TypeAdapter(OutboundEvent)

def parse_inbound_json(raw_text: str) -> InboundEvent:
    return parse_inbound_obj(json.loads(raw_text))

def parse_inbound_obj(obj: Any) -> InboundEvent:
    return _inbound_adapter.validate_python(obj)

def parse_outbound_json(raw_text: str) -> OutboundEvent:
    return _outbound_adapter.validate_python(json.loads(raw_text))

def dumps_outbound(event: OutboundEvent) -> str:
    return json.dumps(event.model_dump(exclude_none=True), separators=(",", ":"), sort_keys=True)
