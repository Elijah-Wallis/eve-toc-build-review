from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import OrderedDict, deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from .bounded_queue import BoundedDequeQueue, QueueClosed
from .backchannel import BackchannelClassifier
from .clock import Clock
from .config import BrainConfig
from .conversation_memory import ConversationMemory
from .dialogue_policy import DialogueAction, SlotState, decide_action
from .llm_client import LLMClient
from .metrics import Metrics, VIC
from .outcome_schema import CallOutcome, detect_objection
from .playbook_policy import apply_playbook
from .eve_prompt import load_eve_v7_opener
from .protocol import (
    AgentConfig,
    InboundCallDetails,
    InboundClear,
    InboundPingPong,
    InboundReminderRequired,
    InboundResponseRequired,
    InboundUpdateOnly,
    OutboundAgentInterrupt,
    OutboundConfig,
    OutboundEvent,
    OutboundPingPong,
    OutboundResponse,
    OutboundUpdateAgent,
    RetellConfig,
)
from .safety_policy import evaluate_user_text
from .speech_planner import (
    PlanReason,
    SpeechPlan,
    SpeechSegment,
    build_plan,
    micro_chunk_text,
    micro_chunk_text_cached,
)
from .tools import ToolCallRecord, ToolRegistry
from .trace import TraceSink
from .transport_ws import GateRef, InboundItem, OutboundEnvelope, TransportClosed
from .turn_handler import TurnHandler, TurnOutput


_NO_SIGNAL_CHAR_PAT = re.compile(r"^[\W_]+$", re.I)
_NO_SIGNAL_REPEAT_PUNCT = re.compile(r"^(.)\1+$")
_NO_SIGNAL_ACK_PAT = re.compile(
    r"^(?:got\s*it|gotcha|i\s+got\s+it|yep\s+got\s+it|yup\s+got\s+it|ya\s+got\s+it|"
    r"understand\b|understood\b|"
    r"yep\b|yup\b|ok\b|okay\b|right\b|alright\b|all\s+right)$",
    re.I,
)
_NO_SIGNAL_NOISE_TOKENS = {
    "got",
    "it",
    "gotcha",
    "yep",
    "yup",
    "ya",
    "understand",
    "understood",
    "ok",
    "okay",
    "right",
    "alright",
    "hey",
    "hi",
    "hello",
    "this",
    "is",
    "from",
    "cassidy",
    "eve",
    "sarah",
    "agent",
    "with",
    "the",
    "a",
    "an",
    "and",
    "to",
    "all",
}
_NO_SIGNAL_NOISE_PREFIX_TOKENS = {
    "hey",
    "hi",
    "hello",
    "cassidy",
    "sarah",
    "agent",
    "eve",
    "this",
    "is",
    "from",
    "with",
}


def _is_intro_noise_like(text: str) -> bool:
    compact_alpha = re.sub(r"[^a-z0-9\s]", " ", (text or "").strip().lower())
    compact_words = [w for w in re.sub(r"\s+", " ", compact_alpha).split(" ") if w]
    if not compact_words:
        return False
    has_prefix = any(w in _NO_SIGNAL_NOISE_PREFIX_TOKENS for w in compact_words)
    has_ack = any(w in {"got", "gotcha", "it", "yep", "yup", "yes", "okay", "ok"} for w in compact_words)
    if has_prefix and has_ack and all(w in _NO_SIGNAL_NOISE_TOKENS for w in compact_words):
        return True
    if len(compact_words) <= 14 and has_prefix and has_ack and compact_words[0] in {"hey", "hi", "hello"}:
        return True
    return False


class WSState(str, Enum):
    CONNECTING = "CONNECTING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"


class ConvState(str, Enum):
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    ENDED = "ENDED"


@dataclass(slots=True)
class TurnRuntime:
    epoch: int
    finalized_ms: int
    first_segment_ms: Optional[int] = None
    ack_segment_ms: Optional[int] = None


@dataclass(slots=True)
class SpeculativeResult:
    transcript_key: str
    tool_req_key: str
    tool_records: list[ToolCallRecord]
    created_at_ms: int


class Orchestrator:
    """
    Single Source of Truth / Actor.
    Owns: epoch, FSMs, transcript memory, turn controller.
    """

    def __init__(
        self,
        *,
        session_id: str,
        call_id: str,
        config: BrainConfig,
        clock: Clock,
        metrics: Metrics,
        trace: TraceSink,
        inbound_q: BoundedDequeQueue[InboundItem],
        outbound_q: BoundedDequeQueue[OutboundEnvelope],
        shutdown_evt: asyncio.Event,
        gate: GateRef,
        tools: ToolRegistry,
        llm: Optional[LLMClient] = None,
    ) -> None:
        self._session_id = session_id
        self._call_id = call_id
        self._config = config
        self._clock = clock
        self._metrics = metrics
        self._trace = trace
        self._inbound_q = inbound_q
        self._outbound_q = outbound_q
        self._shutdown_evt = shutdown_evt
        self._gate_ref = gate
        self._tools = tools
        self._llm = llm

        self._ws_state = WSState.CONNECTING
        self._conv_state = ConvState.LISTENING
        self._epoch = 0

        self._slot_state = SlotState()
        # SlotState is mutated during policy decisions. Keep a per-epoch snapshot so we can
        # roll back when the epoch is interrupted/canceled before its terminal response.
        self._slot_state_backup: Optional[SlotState] = None
        self._slot_state_backup_epoch: int = -1
        self._memory = ConversationMemory(
            max_utterances=self._config.transcript_max_utterances,
            max_chars=self._config.transcript_max_chars,
        )
        self._transcript = []  # bounded list[TranscriptUtterance]
        self._memory_summary = ""

        self._turn_task: Optional[asyncio.Task[None]] = None
        self._turn_output_q: Optional[asyncio.Queue[TurnOutput]] = None
        self._turn_rt: Optional[TurnRuntime] = None
        self._terminal_sent_for_epoch: int = -1
        self._needs_apology = False
        self._disclosure_sent = False

        # Speculative planning: compute early on update_only, emit only after response_required.
        self._spec_task: Optional[asyncio.Task[None]] = None
        self._spec_out_q: asyncio.Queue[SpeculativeResult] = asyncio.Queue(maxsize=1)
        self._spec_transcript_key: str = ""
        self._spec_result: Optional[SpeculativeResult] = None
        self._fast_plan_cache: OrderedDict[
            tuple[str, str, str, str], tuple[PlanReason, tuple[SpeechSegment, ...], bool]
        ] = OrderedDict()
        self._fast_plan_cache_max = 256

        self._idle_task: Optional[asyncio.Task[None]] = None
        self._ping_task: Optional[asyncio.Task[None]] = None

        self._speech_plans = deque(maxlen=512)
        self._outcomes = deque(maxlen=1024)
        self._interrupt_id = 0
        self._pre_ack_sent_for_epoch = -1
        self._backchannel: Optional[BackchannelClassifier] = None
        if self._config.backchannel_enabled:
            self._backchannel = BackchannelClassifier(session_id=self._session_id)

    @property
    def speech_plans(self) -> list[SpeechPlan]:
        return list(self._speech_plans)

    @property
    def outcomes(self) -> list[CallOutcome]:
        return list(self._outcomes)

    # ---------------------------------------------------------------------
    # FSM transitions (centralized)
    # ---------------------------------------------------------------------

    async def _set_ws_state(self, new_state: WSState, *, reason: str) -> None:
        if self._ws_state == new_state:
            return
        self._ws_state = new_state
        await self._trace.emit(
            t_ms=self._clock.now_ms(),
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=self._epoch,
            epoch=self._epoch,
            ws_state=self._ws_state.value,
            conv_state=self._conv_state.value,
            event_type="ws_state_transition",
            payload_obj={"new": new_state.value, "reason": reason},
        )

    async def _set_conv_state(self, new_state: ConvState, *, reason: str) -> None:
        if self._conv_state == new_state:
            return
        self._conv_state = new_state
        await self._trace.emit(
            t_ms=self._clock.now_ms(),
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=self._epoch,
            epoch=self._epoch,
            ws_state=self._ws_state.value,
            conv_state=self._conv_state.value,
            event_type="conv_state_transition",
            payload_obj={"new": new_state.value, "reason": reason},
        )

    # ---------------------------------------------------------------------
    # Session lifecycle
    # ---------------------------------------------------------------------

    async def start(self) -> None:
        await self._set_ws_state(WSState.OPEN, reason="ws_accepted")
        await self._send_config()
        await self._send_update_agent()

        # Keepalive ping loop is optional, but enabled for auto_reconnect.
        if self._config.retell_auto_reconnect:
            self._ping_task = asyncio.create_task(self._ping_loop())

        # Idle watchdog (no inbound traffic).
        self._reset_idle_watchdog()

        # BEGIN response_id=0.
        if self._config.speak_first:
            await self._send_begin_greeting()
        else:
            # Empty terminal response: wait for user.
            await self._enqueue_outbound(
                OutboundResponse(
                    response_type="response",
                    response_id=0,
                    content="",
                    content_complete=True,
                )
            )

    async def run(self) -> None:
        await self.start()
        inbound_task: asyncio.Task[InboundItem] = asyncio.create_task(
            self._inbound_q.get_prefer(self._is_control_inbound)
        )
        spec_task: asyncio.Task[SpeculativeResult] = asyncio.create_task(self._spec_out_q.get())

        # Persistent turn-output waiter (bounded to a single `.get()` task at a time).
        active_turn_q: Optional[asyncio.Queue[TurnOutput]] = None
        turn_task: Optional[asyncio.Task[TurnOutput]] = None

        try:
            while not self._shutdown_evt.is_set():
                if self._conv_state == ConvState.ENDED:
                    break

                # REQUIRED caveat: if the turn output queue object changes, the old waiter can hang
                # forever on an orphaned queue. Swap it immediately.
                if self._turn_output_q is not active_turn_q:
                    if turn_task is not None:
                        turn_task.cancel()
                        await asyncio.gather(turn_task, return_exceptions=True)
                    turn_task = None
                    active_turn_q = self._turn_output_q
                    if active_turn_q is not None:
                        turn_task = asyncio.create_task(active_turn_q.get())

                wait_set: set[asyncio.Task[Any]] = {inbound_task, spec_task}
                if turn_task is not None:
                    wait_set.add(turn_task)

                done, _ = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)

                inbound_item: Any | None = None
                spec_item: Optional[SpeculativeResult] = None
                turn_item: Optional[TurnOutput] = None

                if inbound_task in done:
                    exc = inbound_task.exception()
                    if exc is not None:
                        if isinstance(exc, QueueClosed):
                            await self.end_session(reason="queue_closed")
                            return
                        raise exc
                    inbound_item = inbound_task.result()
                    inbound_task = asyncio.create_task(
                        self._inbound_q.get_prefer(self._is_control_inbound)
                    )

                if spec_task in done:
                    exc = spec_task.exception()
                    if exc is not None:
                        raise exc
                    spec_item = spec_task.result()
                    spec_task = asyncio.create_task(self._spec_out_q.get())

                if turn_task is not None and turn_task in done:
                    exc = turn_task.exception()
                    if exc is not None:
                        raise exc
                    turn_item = turn_task.result()
                    # Re-arm the waiter on the current active queue (may be swapped next loop).
                    if active_turn_q is not None:
                        turn_task = asyncio.create_task(active_turn_q.get())
                    else:
                        turn_task = None

                # Stable ordering: TransportClosed > inbound events > speculative > turn outputs.
                if isinstance(inbound_item, TransportClosed):
                    await self.end_session(reason=inbound_item.reason)
                    return
                if inbound_item is not None:
                    await self._dispatch_item(inbound_item)
                if spec_item is not None:
                    self._spec_result = spec_item
                if turn_item is not None:
                    await self._handle_turn_output(turn_item)
        finally:
            for t in (inbound_task, spec_task, turn_task):
                if t is None:
                    continue
                if not t.done():
                    t.cancel()
            await asyncio.gather(
                inbound_task,
                spec_task,
                *( [turn_task] if turn_task is not None else [] ),
                return_exceptions=True,
            )

    async def _dispatch_item(self, item: Any) -> None:
        if isinstance(item, TransportClosed):
            await self.end_session(reason=item.reason)
            return
        await self._handle_inbound_event(item)

    async def end_session(self, *, reason: str) -> None:
        if self._conv_state == ConvState.ENDED:
            return
        safe_reason = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in str(reason))
        self._metrics.inc(f"{VIC['ws_close_reason_total']}.{safe_reason}", 1)

        await self._set_conv_state(ConvState.ENDED, reason=reason)
        await self._set_ws_state(WSState.CLOSING, reason=reason)

        # Cancel turn handler.
        if self._turn_task is not None:
            self._turn_task.cancel()
            self._turn_task = None
        self._turn_output_q = None

        await self._cancel_speculative_planning()

        # Stop watchdogs.
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None
        if self._ping_task is not None:
            self._ping_task.cancel()
            self._ping_task = None

        # Close queues (unblock reader/writer).
        await self._inbound_q.close()
        await self._outbound_q.close()

        self._shutdown_evt.set()
        await self._set_ws_state(WSState.CLOSED, reason=reason)

    # ---------------------------------------------------------------------
    # Turn rollback / slot-state backup
    # ---------------------------------------------------------------------

    def _snapshot_slot_state(self) -> SlotState:
        s = self._slot_state
        return SlotState(
            intent=s.intent,
            patient_name=s.patient_name,
            phone=s.phone,
            phone_confirmed=bool(s.phone_confirmed),
            requested_dt=s.requested_dt,
            requested_dt_confirmed=bool(s.requested_dt_confirmed),
            b2b_funnel_stage=str(s.b2b_funnel_stage or "OPEN"),
            manager_email=s.manager_email,
            b2b_last_stage=str(s.b2b_last_stage or "OPEN"),
            b2b_last_signal=str(s.b2b_last_signal or ""),
            b2b_no_signal_streak=int(s.b2b_no_signal_streak or 0),
            b2b_last_user_signature=str(s.b2b_last_user_signature or ""),
            campaign_id=str(s.campaign_id or ""),
            clinic_id=str(s.clinic_id or ""),
            clinic_name=str(s.clinic_name or ""),
            lead_id=str(s.lead_id or ""),
            to_number=str(s.to_number or ""),
            tenant=str(s.tenant or ""),
            reprompts=dict(getattr(s, "reprompts", {}) or {}),
            b2b_autonomy_mode=str(s.b2b_autonomy_mode or "baseline"),
            question_depth=int(s.question_depth or 1),
            objection_pressure=int(s.objection_pressure or 0),
        )

    def _restore_slot_state(self, snap: SlotState) -> None:
        s = self._slot_state
        s.intent = snap.intent
        s.patient_name = snap.patient_name
        s.phone = snap.phone
        s.phone_confirmed = bool(snap.phone_confirmed)
        s.requested_dt = snap.requested_dt
        s.requested_dt_confirmed = bool(snap.requested_dt_confirmed)
        s.b2b_funnel_stage = str(snap.b2b_funnel_stage or "OPEN")
        s.manager_email = snap.manager_email
        s.b2b_last_stage = str(snap.b2b_last_stage or "OPEN")
        s.b2b_last_signal = str(snap.b2b_last_signal or "")
        s.b2b_no_signal_streak = int(snap.b2b_no_signal_streak or 0)
        s.b2b_last_user_signature = str(snap.b2b_last_user_signature or "")
        s.campaign_id = str(snap.campaign_id or "")
        s.clinic_id = str(snap.clinic_id or "")
        s.clinic_name = str(snap.clinic_name or "")
        s.lead_id = str(snap.lead_id or "")
        s.to_number = str(snap.to_number or "")
        s.tenant = str(snap.tenant or "")
        s.reprompts = dict(getattr(snap, "reprompts", {}) or {})
        s.b2b_autonomy_mode = str(snap.b2b_autonomy_mode or "baseline")
        s.question_depth = int(snap.question_depth or 1)
        s.objection_pressure = int(snap.objection_pressure or 0)

    def _arm_turn_state_backup(self, *, epoch: int) -> None:
        # Overwrite any prior backup; callers must rollback/commit the previous epoch first.
        self._slot_state_backup = self._snapshot_slot_state()
        self._slot_state_backup_epoch = int(epoch)

    def _commit_turn_state_backup(self, *, epoch: int) -> None:
        if self._slot_state_backup is None:
            return
        if int(self._slot_state_backup_epoch) != int(epoch):
            return
        self._slot_state_backup = None
        self._slot_state_backup_epoch = -1

    def _rollback_turn_state_backup(self, *, epoch: int, reason: str) -> None:
        if self._slot_state_backup is None:
            return
        if int(self._slot_state_backup_epoch) != int(epoch):
            return
        snap = self._slot_state_backup
        self._restore_slot_state(snap)
        self._slot_state_backup = None
        self._slot_state_backup_epoch = -1
        self._metrics.inc("turn.rollback_total", 1)

    async def _has_pending_speech(self) -> bool:
        return await self._outbound_q.any_where(
            lambda env: env.epoch == self._epoch
            and str(getattr(env.msg, "response_type", "")) == "response"
            and not bool(getattr(env.msg, "content_complete", False))
        )

    async def _barge_in_cancel(self, *, reason: str) -> bool:
        """
        Stop speaking immediately and close the current epoch with an empty terminal chunk.
        Also roll back SlotState mutations for the interrupted epoch.
        """
        has_pending_speech = await self._has_pending_speech()
        if self._conv_state != ConvState.SPEAKING and not has_pending_speech:
            return False

        t0 = self._clock.now_ms()

        # Speak-generation gate: invalidate any already-queued chunks for this epoch.
        new_speak_gen = self._gate_ref.bump_speak_gen()
        dropped = await self._outbound_q.drop_where(
            lambda env: env.epoch == self._epoch
            and env.speak_gen is not None
            and env.speak_gen != int(new_speak_gen)
        )
        if dropped > 0:
            self._metrics.inc(VIC["stale_segment_dropped_total"], int(dropped))

        # Roll back any SlotState mutations that were made for this epoch before it terminally completed.
        self._rollback_turn_state_backup(epoch=self._epoch, reason=reason)

        await self._cancel_turn(reason=reason)
        await self._enqueue_outbound(
            OutboundResponse(
                response_type="response",
                response_id=self._epoch,
                content="",
                content_complete=True,
            ),
            epoch=self._epoch,
            speak_gen=int(new_speak_gen),
            priority=100,
        )
        await self._set_conv_state(ConvState.LISTENING, reason=reason)
        self._needs_apology = True
        self._metrics.observe(VIC["barge_in_cancel_latency_ms"], self._clock.now_ms() - t0)
        return True

    # ---------------------------------------------------------------------
    # Inbound handlers
    # ---------------------------------------------------------------------

    def _ingest_call_details(self, call: Any) -> None:
        if not isinstance(call, dict):
            return

        metadata = call.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        def _pick(*vals: Any) -> str:
            for v in vals:
                if isinstance(v, str):
                    v = v.strip()
                    if v:
                        return v
            return ""

        campaign_id = _pick(
            metadata.get("campaign_id"),
            metadata.get("campaignId"),
            call.get("campaign_id"),
            self._slot_state.campaign_id,
        )
        clinic_id = _pick(
            metadata.get("clinic_id"),
            metadata.get("clinicId"),
            call.get("clinic_id"),
            self._slot_state.clinic_id,
        )
        clinic_name = _pick(
            metadata.get("clinic_name"),
            metadata.get("clinicName"),
            call.get("clinic_name"),
            self._slot_state.clinic_name,
        )
        lead_id = _pick(
            metadata.get("lead_id"),
            metadata.get("leadId"),
            call.get("lead_id"),
            self._slot_state.lead_id,
        )
        tenant = _pick(
            metadata.get("tenant"),
            call.get("tenant"),
            self._slot_state.tenant,
            "synthetic_medspa",
        )
        to_number = _pick(
            metadata.get("to_number"),
            metadata.get("clinic_phone"),
            call.get("to_number"),
            call.get("to"),
            metadata.get("to"),
        )

        self._slot_state.campaign_id = campaign_id
        self._slot_state.clinic_id = clinic_id
        self._slot_state.clinic_name = clinic_name
        self._slot_state.lead_id = lead_id
        self._slot_state.tenant = tenant
        if to_number:
            self._slot_state.to_number = to_number

    async def _handle_inbound_event(self, ev: Any) -> None:
        # Terminal means terminal.
        if self._conv_state == ConvState.ENDED:
            return

        self._reset_idle_watchdog()
        await self._trace.emit(
            t_ms=self._clock.now_ms(),
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=self._epoch,
            epoch=self._epoch,
            ws_state=self._ws_state.value,
            conv_state=self._conv_state.value,
            event_type="inbound_event",
            payload_obj=getattr(ev, "model_dump", lambda: {"type": type(ev).__name__})(),
        )

        if isinstance(ev, InboundPingPong):
            if self._config.retell_auto_reconnect:
                await self._enqueue_outbound(
                    OutboundPingPong(response_type="ping_pong", timestamp=ev.timestamp)
                )
            return

        if isinstance(ev, InboundCallDetails):
            self._ingest_call_details(ev.call)
            return

        if isinstance(ev, InboundClear):
            # Retell "clear" is an explicit interruption signal; treat it like a barge-in hint.
            _ = await self._barge_in_cancel(reason="clear")
            return

        if isinstance(ev, InboundUpdateOnly):
            self._update_transcript(ev.transcript)

            if (
                ev.turntaking == "agent_turn"
                and self._config.interrupt_pre_ack_on_agent_turn_enabled
                and self._config.conversation_profile == "b2b"
                and self._conv_state == ConvState.LISTENING
                and self._pre_ack_sent_for_epoch != self._epoch
            ):
                self._interrupt_id += 1
                self._pre_ack_sent_for_epoch = self._epoch
                await self._enqueue_outbound(
                    OutboundAgentInterrupt(
                        response_type="agent_interrupt",
                        interrupt_id=self._interrupt_id,
                        content="",
                        content_complete=True,
                        no_interruption_allowed=False,
                    ),
                    priority=95,
                )
            if ev.turntaking == "user_turn":
                # Under transport backpressure the writer may still have queued speech even if the
                # conversation FSM has already transitioned back to LISTENING. Treat "user_turn"
                # as a barge-in hint whenever there are pending non-terminal response frames.
                if await self._barge_in_cancel(reason="barge_in_hint"):
                    return

            # Backchannel note:
            # Retell's recommended backchanneling is configured at the agent level
            # (enable_backchannel/backchannel_frequency/backchannel_words). Server-generated
            # backchannels via `agent_interrupt` are experimental and OFF by default because
            # `agent_interrupt` is an explicit interruption mechanism.
            #
            # Even if enabled, we do not emit `agent_interrupt` while turntaking == user_turn
            # or during sensitive capture, to avoid overtalk.
            if self._backchannel is not None and self._conv_state == ConvState.LISTENING:
                # Maintain classifier state deterministically, but do not emit.
                last_user = ""
                for u in reversed(ev.transcript):
                    if getattr(u, "role", "") == "user":
                        last_user = getattr(u, "content", "") or ""
                        break
                _ = self._backchannel.consider(
                    now_ms=self._clock.now_ms(),
                    user_text=last_user,
                    user_turn=bool(ev.turntaking == "user_turn"),
                    sensitive_capture=self._is_sensitive_capture(),
                )

            if self._config.speculative_planning_enabled:
                await self._maybe_start_speculative_planning(ev)
            return

        if isinstance(ev, (InboundResponseRequired, InboundReminderRequired)):
            await self._on_response_required(ev)
            return

    async def _on_response_required(self, ev: InboundResponseRequired | InboundReminderRequired) -> None:
        await self._cancel_speculative_planning(keep_result=True)
        new_epoch = int(ev.response_id)
        was_speaking = self._conv_state == ConvState.SPEAKING
        old_epoch = int(self._epoch)
        old_turn_rt = self._turn_rt

        # If the previous epoch was interrupted before its terminal response, roll back any
        # SlotState mutations so the next turn re-derives state from transcript deterministically.
        #
        # Practical note: if we've already emitted at least one response chunk for the old epoch,
        # keep its SlotState mutations. That allows confirmation-style flows (VIC) to accept fast
        # user replies that arrive before the terminal frame without losing progress.
        if new_epoch != old_epoch:
            spoke_any = (
                old_turn_rt is not None
                and int(old_turn_rt.epoch) == int(old_epoch)
                and old_turn_rt.first_segment_ms is not None
            )
            if not spoke_any:
                self._rollback_turn_state_backup(epoch=old_epoch, reason="new_epoch")
            else:
                self._commit_turn_state_backup(epoch=old_epoch)

        # Atomically bump epoch.
        self._epoch = new_epoch
        self._pre_ack_sent_for_epoch = -1
        self._terminal_sent_for_epoch = -1
        self._gate_ref.set_epoch(new_epoch)
        self._turn_rt = TurnRuntime(epoch=new_epoch, finalized_ms=self._clock.now_ms())
        self._arm_turn_state_backup(epoch=new_epoch)

        if was_speaking:
            self._needs_apology = True

        await self._cancel_turn(reason="new_epoch")

        # Drop stale turn-bound messages queued for older epochs.
        dropped = await self._outbound_q.drop_where(
            lambda env: env.epoch is not None and env.epoch != self._epoch
        )
        if dropped > 0:
            self._metrics.inc(VIC["stale_segment_dropped_total"], int(dropped))

        # Update transcript snapshot (bounded).
        self._update_transcript(ev.transcript)

        # B2B route sync: Retell transcript snapshots can arrive duplicated/reordered during
        # reconnects. If the last agent utterance is the canonical opener, treat this as the OPEN
        # stage regardless of internal stage drift so fast-path caching remains stable.
        if self._config.conversation_profile == "b2b":
            last_agent = ""
            for u in reversed(ev.transcript or []):
                if getattr(u, "role", "") == "agent":
                    last_agent = getattr(u, "content", "") or ""
                    break
            la = (last_agent or "").lower()
            if "bad time" in la and "quick question" in la:
                self._slot_state.b2b_funnel_stage = "OPEN"

        last_stage = str(self._slot_state.b2b_funnel_stage or "OPEN")

        await self._set_conv_state(ConvState.PROCESSING, reason="response_required")

        # Compute safety + dialogue action (mutates slot state inside orchestrator only).
        last_user = ""
        for u in reversed(ev.transcript):
            if u.role == "user":
                last_user = u.content or ""
                break
        normalized_last_user = self._normalized_b2b_user_signature(last_user)
        low_signal = self._looks_like_low_signal(last_user)
        b2b_repeated_low_signal = False
        b2b_repeated_empty_or_noise = False
        if self._config.conversation_profile == "b2b":
            same_stage = str(self._slot_state.b2b_last_stage or "OPEN") == last_stage
            last_signal = str(self._slot_state.b2b_last_signal or "")
            last_signature = str(self._slot_state.b2b_last_user_signature or "")
            b2b_repeated_low_signal = (
                bool(normalized_last_user)
                and normalized_last_user == str(self._slot_state.b2b_last_user_signature or "")
                and str(self._slot_state.b2b_last_signal or "") in {"NO_SIGNAL", "NEW_CALL"}
                and str(self._slot_state.b2b_last_stage or "OPEN") == last_stage
            )
            b2b_repeated_empty_or_noise = (
                not bool((last_user or "").strip())
                and same_stage
                and last_signal in {"NO_SIGNAL", "NEW_CALL", ""}
                and (not last_signature or normalized_last_user == last_signature)
            )

        # Reminder handling: if Retell asks for a reminder but we have no user utterance yet,
        # do not speak. Complete the epoch with an empty terminal chunk to avoid accidental overtalk.
        if isinstance(ev, InboundReminderRequired) and not (last_user or "").strip():
            await self._enqueue_outbound(
                OutboundResponse(
                    response_type="response",
                    response_id=self._epoch,
                    content="",
                    content_complete=True,
                ),
                priority=95,
            )
            self._commit_turn_state_backup(epoch=self._epoch)
            await self._set_conv_state(ConvState.LISTENING, reason="reminder_no_user_silence")
            return

        # Fast-path silence/noise handling for B2B:
        # ambient turns do not progress the state and should never emit opener/ack.
        if self._config.conversation_profile == "b2b" and low_signal and (
            b2b_repeated_low_signal or b2b_repeated_empty_or_noise
        ):
            self._slot_state.b2b_last_stage = last_stage
            self._slot_state.b2b_last_signal = "NO_SIGNAL"
            self._slot_state.b2b_last_user_signature = normalized_last_user
            self._slot_state.b2b_no_signal_streak = int(self._slot_state.b2b_no_signal_streak or 0) + 1
            await self._enqueue_outbound(
                OutboundResponse(
                    response_type="response",
                    response_id=self._epoch,
                    content="",
                    content_complete=True,
                    no_interruption_allowed=False,
                ),
                priority=95,
            )
            self._commit_turn_state_backup(epoch=self._epoch)
            await self._set_conv_state(ConvState.LISTENING, reason="low_signal_noop")
            return

        if self._config.conversation_profile == "b2b" and low_signal:
            self._slot_state.b2b_last_stage = last_stage
            self._slot_state.b2b_last_signal = "NO_SIGNAL"
            self._slot_state.b2b_last_user_signature = normalized_last_user
            self._slot_state.b2b_no_signal_streak = int(self._slot_state.b2b_no_signal_streak or 0) + 1
            await self._enqueue_outbound(
                OutboundResponse(
                    response_type="response",
                    response_id=self._epoch,
                    content="",
                    content_complete=True,
                    no_interruption_allowed=False,
                ),
                priority=95,
            )
            self._commit_turn_state_backup(epoch=self._epoch)
            await self._set_conv_state(ConvState.LISTENING, reason="low_signal_noop")
            return

        await self._trace.emit(
            event_type="timing_marker",
            t_ms=self._clock.now_ms(),
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=self._epoch,
            epoch=self._epoch,
            ws_state=self._ws_state.value,
            conv_state=self._conv_state.value,
            payload_obj={"phase": "policy_decision_start_ms"},
        )
        decision_start_ms = self._clock.now_ms()
        safety = evaluate_user_text(
            last_user,
            clinic_name=self._config.clinic_name,
            profile=self._config.conversation_profile,
            b2b_org_name=self._config.b2b_org_name,
        )
        action = decide_action(
            state=self._slot_state,
            transcript=ev.transcript,
            needs_apology=self._needs_apology,
            safety_kind=safety.kind,
            safety_message=safety.message,
            call_id=self._call_id,
            profile=self._config.conversation_profile,
        )

        no_progress = bool(action.action_type == "Noop" and action.payload.get("no_progress", False))
        stage_unchanged = (
            self._config.conversation_profile == "b2b"
            and str(self._slot_state.b2b_funnel_stage or "OPEN") == last_stage
        )
        is_low_signal_input = low_signal
        is_noise_noop = (
            no_progress
            and bool(action.payload.get("message", "") == "")
            and bool(action.payload.get("no_signal", False))
        )

        # Additional hard short-circuit to suppress repeated ambient/noise turns quickly.
        if self._config.conversation_profile == "b2b" and is_low_signal_input and no_progress:
            action.payload["skip_ack"] = True
            await self._enqueue_outbound(
                OutboundResponse(
                    response_type="response",
                    response_id=self._epoch,
                    content="",
                    content_complete=True,
                    no_interruption_allowed=False,
                ),
                priority=95,
            )
            self._commit_turn_state_backup(epoch=self._epoch)
            await self._set_conv_state(ConvState.LISTENING, reason="no_progress_noop")
            return

        if no_progress and (is_noise_noop or is_low_signal_input or stage_unchanged or not (last_user or "").strip()):
            action.payload["skip_ack"] = True
            await self._enqueue_outbound(
                OutboundResponse(
                    response_type="response",
                    response_id=self._epoch,
                    content="",
                    content_complete=True,
                    no_interruption_allowed=False,
                ),
                priority=95,
            )
            self._commit_turn_state_backup(epoch=self._epoch)
            await self._set_conv_state(ConvState.LISTENING, reason="no_progress_noop")
            return

        if action.action_type == "Noop":
            action.payload["skip_ack"] = True

        await self._trace.emit(
            t_ms=self._clock.now_ms(),
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=self._epoch,
            epoch=self._epoch,
            ws_state=self._ws_state.value,
            conv_state=self._conv_state.value,
            event_type="timing_marker",
            payload_obj={
                "phase": "policy_decision_ms",
                "duration_ms": self._clock.now_ms() - int(decision_start_ms),
            },
        )

        # Ultra-fast pre-ack: emit only for turns that should advance the conversation.
        pre_ack_sent = False
        has_meaningful_message = bool(str(action.payload.get("message", "") or "").strip())
        if (
            isinstance(ev, InboundResponseRequired)
            and action.action_type != "Noop"
            and not bool(action.payload.get("no_progress", False))
            and not bool(action.payload.get("no_signal", False))
            and has_meaningful_message
            and self._config.safe_pre_ack_on_response_required_enabled
            and self._config.conversation_profile == "clinic"
            and (last_user or "").strip()
            and self._pre_ack_sent_for_epoch != self._epoch
        ):
            self._pre_ack_sent_for_epoch = self._epoch
            pre_ack_sent = True
            await self._enqueue_outbound(
                OutboundResponse(
                    response_type="response",
                    response_id=self._epoch,
                    # Minimal pre-ack to keep latency low without repeated ack loop patterns.
                    content="",
                    content_complete=False,
                    no_interruption_allowed=False,
                ),
                priority=96,
            )
            await self._trace.emit(
                t_ms=self._clock.now_ms(),
                session_id=self._session_id,
                call_id=self._call_id,
                turn_id=self._epoch,
                epoch=self._epoch,
                ws_state=self._ws_state.value,
                conv_state=self._conv_state.value,
                event_type="timing_marker",
                payload_obj={"phase": "pre_ack_enqueued"},
            )
        if pre_ack_sent:
            # Avoid sending two ACK-style chunks back-to-back (pre-ack + TurnHandler ACK).
            action.payload["skip_ack"] = True

        objection = detect_objection(last_user)
        if objection is not None:
            self._metrics.inc(VIC["moat_objection_pattern_total"], 1)
        playbook = apply_playbook(
            action=action,
            objection=objection,
            prior_attempts=int(self._slot_state.reprompts.get("dt", 0)),
            profile=self._config.conversation_profile,
        )
        action = playbook.action
        if playbook.applied:
            self._metrics.inc(VIC["moat_playbook_hit_total"], 1)
        if self._memory_summary:
            action.payload["memory_summary"] = self._memory_summary
        if safety.kind == "identity":
            # Identity responses disclose what we are; do not double-disclose in the ACK.
            self._disclosure_sent = True
        elif (
            self._config.conversation_profile == "clinic"
            or self._config.b2b_auto_disclosure
        ) and not self._disclosure_sent:
            action.payload["disclosure_required"] = True
            self._disclosure_sent = True
        reprompt_count = action.payload.get("reprompt_count")
        if isinstance(reprompt_count, int) and reprompt_count > 1:
            self._metrics.inc(VIC["reprompts_total"], 1)

        outcome = CallOutcome(
            call_id=self._call_id,
            turn_id=self._epoch,
            epoch=self._epoch,
            intent=str(self._slot_state.intent or "unknown"),
            action_type=str(action.action_type),
            objection=objection,
            offered_slots_count=int(len(action.payload.get("offered_slots", []) or [])),
            accepted=bool(action.payload.get("accepted", False)),
            escalated=bool(action.action_type in {"EscalateSafety", "Transfer"}),
            drop_off_point=str(action.payload.get("drop_off_point", "")),
            t_ms=self._clock.now_ms(),
        )
        self._outcomes.append(outcome)
        await self._trace.emit(
            t_ms=self._clock.now_ms(),
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=self._epoch,
            epoch=self._epoch,
            ws_state=self._ws_state.value,
            conv_state=self._conv_state.value,
            event_type="call_outcome",
            payload_obj=outcome.to_payload(),
        )
        if await self._emit_fast_path_plan(action=action):
            await self._set_conv_state(ConvState.LISTENING, reason="fast_path_complete")
            return
        # Apology is one-shot.
        self._needs_apology = False

        prefetched_tool_records: list[ToolCallRecord] = []
        if self._spec_result is not None:
            tkey = self._transcript_key(ev.transcript)
            req_key = self._tool_req_key(action.tool_requests)
            if self._spec_result.transcript_key == tkey and self._spec_result.tool_req_key == req_key:
                prefetched_tool_records = list(self._spec_result.tool_records or [])
                self._metrics.inc("speculative.used_total", 1)
            self._spec_result = None

        # Start turn handler for this epoch.
        self._turn_output_q = asyncio.Queue(maxsize=self._config.turn_queue_max)
        handler = TurnHandler(
            session_id=self._session_id,
            call_id=self._call_id,
            epoch=self._epoch,
            turn_id=self._epoch,
            action=action,
            transcript=list(self._transcript),
            config=self._config,
            clock=self._clock,
            metrics=self._metrics,
            tools=self._tools,
            llm=(self._llm if self._config.use_llm_nlg else None),
            output_q=self._turn_output_q,
            prefetched_tool_records=prefetched_tool_records,
            trace=self._trace,
        )
        self._turn_task = asyncio.create_task(handler.run())
        # Yield once to allow the newly spawned turn handler to enqueue an early ACK plan promptly.
        await asyncio.sleep(0)

    def _b2b_state_signature(self) -> str:
        s = self._slot_state
        return "|".join(
            [
                str(s.b2b_funnel_stage),
                str(s.b2b_last_stage),
                str(s.b2b_last_signal),
                str(s.b2b_no_signal_streak),
                str(s.b2b_autonomy_mode),
                str(s.question_depth),
                str(s.objection_pressure),
                str(s.reprompts.get("b2b_close_request", 0)),
                str(s.reprompts.get("b2b_bad_time", 0)),
                str(int(self._disclosure_sent)),
            ]
        )

    def _b2b_slot_signature(self) -> str:
        s = self._slot_state
        payload = "|".join(
            [
                str(s.b2b_funnel_stage),
                str(s.b2b_last_stage),
                str(s.b2b_autonomy_mode),
                str(s.question_depth),
                str(s.objection_pressure),
                str(s.reprompts.get("b2b_close_request", 0)),
                str(s.reprompts.get("b2b_bad_time", 0)),
                str(s.b2b_last_signal),
                str(s.b2b_no_signal_streak),
                str(bool(s.manager_email)),
                str(int(self._disclosure_sent)),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def _emit_fast_path_plan(self, *, action: DialogueAction) -> bool:
        if self._config.conversation_profile != "b2b":
            return False
        if action.action_type == "Noop":
            return False
        if action.tool_requests:
            return False
        if not bool(action.payload.get("fast_path", False)):
            return False
        if action.payload.get("message") is None:
            return False

        stage = str(self._slot_state.b2b_funnel_stage)
        state_id = self._b2b_state_signature()
        slot_signature = self._b2b_slot_signature()
        intent_sig = str(action.payload.get("intent_signature", ""))
        if not intent_sig:
            return False

        msg = str(action.payload.get("message") or "").strip()
        if not msg:
            return False

        if action.action_type == "EndCall":
            reason: PlanReason = "CONTENT"
        elif action.action_type == "Inform":
            reason = "CONTENT"
        elif action.action_type == "Ask":
            reason = "CLARIFY"
        elif action.action_type == "Confirm":
            reason = "CONFIRM"
        elif action.action_type == "Repair":
            reason = "REPAIR"
        elif action.action_type == "Transfer":
            reason = "ERROR"
        elif action.action_type == "EscalateSafety":
            reason = "ERROR"
        else:
            reason = "CONTENT"

        cache_key = (stage, state_id, slot_signature, intent_sig)
        cached = self._fast_plan_cache.get(cache_key)
        plan_build_start_ms = self._clock.now_ms()
        await self._trace.emit(
            event_type="timing_marker",
            t_ms=self._clock.now_ms(),
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=self._epoch,
            epoch=self._epoch,
            ws_state=self._ws_state.value,
            conv_state=self._conv_state.value,
            payload_obj={
                "phase": "speech_plan_build_start_ms",
                "intent_signature": intent_sig,
                "slot_signature": slot_signature,
            },
        )
        segments: tuple[SpeechSegment, ...]
        cache_hit = False
        if cached is not None and cached[0] == reason:
            _, cached_segments, cached_disclosure = cached
            self._fast_plan_cache.move_to_end(cache_key)
            segments = cached_segments
            disclosure_included = cached_disclosure
            cache_hit = True
        else:
            purpose = reason
            segments = tuple(
                micro_chunk_text_cached(
                    text=msg,
                    max_expected_ms=self._config.vic_max_segment_expected_ms,
                    pace_ms_per_char=self._config.pace_ms_per_char,
                    purpose=purpose,
                    interruptible=True,
                    requires_tool_evidence=False,
                    tool_evidence_ids=[],
                    max_monologue_expected_ms=self._config.vic_max_monologue_expected_ms,
                    markup_mode=self._config.speech_markup_mode,
                    dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                    digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                    dash_pause_scope=self._config.dash_pause_scope,
                    slot_snapshot_signature=slot_signature,
                    intent_signature=intent_sig,
                )
            )
            disclosure_included = bool(action.payload.get("disclosure_required", False))
            self._fast_plan_cache[cache_key] = (reason, segments, disclosure_included)
            while len(self._fast_plan_cache) > self._fast_plan_cache_max:
                self._fast_plan_cache.popitem(last=False)

        await self._trace.emit(
            t_ms=self._clock.now_ms(),
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=self._epoch,
            epoch=self._epoch,
            ws_state=self._ws_state.value,
            conv_state=self._conv_state.value,
            event_type="timing_marker",
            payload_obj={
                "phase": "speech_plan_build_ms",
                "purpose": reason,
                "segments": len(segments),
                "intent_signature": intent_sig,
                "slot_signature": slot_signature,
                "duration_ms": self._clock.now_ms() - int(plan_build_start_ms),
                "cached": cache_hit,
            },
        )
        return await self._emit_fast_path_from_segments(
            action=action,
            segments=segments,
            reason=reason,
            disclosure_included=disclosure_included,
        )

    def _transcript_key(self, transcript: list[Any]) -> str:
        last_user = ""
        for u in reversed(transcript):
            if getattr(u, "role", "") == "user":
                last_user = getattr(u, "content", "") or ""
                break
        payload = f"{len(transcript)}|{last_user.strip().lower()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _tool_req_key(self, reqs: list[Any]) -> str:
        parts: list[str] = []
        for r in reqs or []:
            name = str(getattr(r, "name", ""))
            args = getattr(r, "arguments", {}) or {}
            try:
                args_json = json.dumps(args, separators=(",", ":"), sort_keys=True)
            except Exception:
                args_json = "{}"
            parts.append(f"{name}:{args_json}")
        return "|".join(parts)

    async def _cancel_speculative_planning(self, *, keep_result: bool = False) -> None:
        if self._spec_task is not None:
            self._spec_task.cancel()
            self._spec_task = None
        last: Optional[SpeculativeResult] = None
        while True:
            try:
                last = self._spec_out_q.get_nowait()
            except asyncio.QueueEmpty:
                break
        if keep_result and last is not None:
            self._spec_result = last

    async def _maybe_start_speculative_planning(self, ev: InboundUpdateOnly) -> None:
        if self._config.conversation_profile == "b2b":
            # B2B has a deterministic, mostly non-tooling objection path; skip speculative
            # policy precompute to avoid needless work before the real response turn.
            return
        if self._conv_state != ConvState.LISTENING:
            return
        if ev.turntaking not in (None, "user_turn"):
            return
        tkey = self._transcript_key(ev.transcript)
        if tkey == self._spec_transcript_key and self._spec_task is not None and not self._spec_task.done():
            return
        self._spec_transcript_key = tkey
        await self._cancel_speculative_planning(keep_result=False)

        async def _speculate() -> None:
            try:
                await self._clock.sleep_ms(int(self._config.speculative_debounce_ms))
                if self._shutdown_evt.is_set() or self._conv_state != ConvState.LISTENING:
                    return

                spec_state = SlotState(
                    intent=self._slot_state.intent,
                    patient_name=self._slot_state.patient_name,
                    phone=self._slot_state.phone,
                    phone_confirmed=self._slot_state.phone_confirmed,
                    requested_dt=self._slot_state.requested_dt,
                    requested_dt_confirmed=self._slot_state.requested_dt_confirmed,
                    manager_email=self._slot_state.manager_email,
                    campaign_id=self._slot_state.campaign_id,
                    clinic_id=self._slot_state.clinic_id,
                    clinic_name=self._slot_state.clinic_name,
                    lead_id=self._slot_state.lead_id,
                    to_number=self._slot_state.to_number,
                    tenant=self._slot_state.tenant,
                    reprompts=dict(self._slot_state.reprompts or {}),
                    b2b_funnel_stage=self._slot_state.b2b_funnel_stage,
                    b2b_autonomy_mode=self._slot_state.b2b_autonomy_mode,
                    question_depth=int(self._slot_state.question_depth or 1),
                    objection_pressure=int(self._slot_state.objection_pressure or 0),
                )

                last_user = ""
                for u in reversed(ev.transcript):
                    if getattr(u, "role", "") == "user":
                        last_user = getattr(u, "content", "") or ""
                        break
                safety = evaluate_user_text(
                    last_user,
                    clinic_name=self._config.clinic_name,
                    profile=self._config.conversation_profile,
                    b2b_org_name=self._config.b2b_org_name,
                )
                action = decide_action(
                    state=spec_state,
                    transcript=ev.transcript,
                    needs_apology=False,
                    safety_kind=safety.kind,
                    safety_message=safety.message,
                    call_id=self._call_id,
                    profile=self._config.conversation_profile,
                )
                objection = detect_objection(last_user)
                playbook = apply_playbook(
                    action=action,
                    objection=objection,
                    prior_attempts=int(spec_state.reprompts.get("dt", 0)),
                    profile=self._config.conversation_profile,
                )
                action = playbook.action

                tool_records: list[ToolCallRecord] = []
                if self._config.speculative_tool_prefetch_enabled and action.tool_requests:
                    timeout_ms = max(
                        1,
                        min(
                            int(self._config.vic_tool_timeout_ms),
                            int(self._config.speculative_tool_prefetch_timeout_ms),
                        ),
                    )
                    started = self._clock.now_ms()
                    for req in action.tool_requests:
                        rec = await self._tools.invoke(
                            name=req.name,
                            arguments=req.arguments,
                            timeout_ms=timeout_ms,
                            started_at_ms=started,
                            emit_invocation=None,
                            emit_result=None,
                        )
                        tool_records.append(rec)

                res = SpeculativeResult(
                    transcript_key=tkey,
                    tool_req_key=self._tool_req_key(action.tool_requests),
                    tool_records=tool_records,
                    created_at_ms=self._clock.now_ms(),
                )
                while self._spec_out_q.qsize() > 0:
                    try:
                        _ = self._spec_out_q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                self._metrics.inc("speculative.plans_total", 1)
                self._spec_out_q.put_nowait(res)
            except asyncio.CancelledError:
                return
            except Exception:
                return

        self._spec_task = asyncio.create_task(_speculate())

    def _update_transcript(self, transcript: list[Any]) -> None:
        view = self._memory.ingest_snapshot(transcript=list(transcript), slot_state=self._slot_state)
        self._transcript = list(view.recent_transcript)
        self._memory_summary = view.summary_blob
        if view.compacted:
            self._metrics.inc(VIC["memory_transcript_compactions_total"], 1)
        # "current" memory metrics are gauges, not histograms.
        self._metrics.set(VIC["memory_transcript_chars_current"], view.chars_current)
        self._metrics.set(VIC["memory_transcript_utterances_current"], view.utterances_current)

    def _looks_like_low_signal(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        compact_with_spaces = re.sub(r"\s+", " ", (text or "").strip().lower())
        if not compact_with_spaces:
            return True
        if _is_intro_noise_like(compact_with_spaces):
            return True
        if _NO_SIGNAL_CHAR_PAT.fullmatch(compact):
            return True
        compact_phrase = re.sub(r"[^a-z0-9\s]", " ", compact_with_spaces)
        compact_words = [w for w in re.sub(r"\s+", " ", compact_phrase).strip().split(" ") if w]
        if compact_words and len(compact_words) <= 4:
            compact_phrase = " ".join(compact_words)
            if _NO_SIGNAL_ACK_PAT.fullmatch(compact_phrase):
                return True
        if _NO_SIGNAL_REPEAT_PUNCT.fullmatch(compact) and len(compact) >= 2 and not compact[0].isalnum():
            return True
        lower_compact = compact.lower()
        if _NO_SIGNAL_REPEAT_PUNCT.fullmatch(lower_compact) and lower_compact in {"??", "!!", "~~", "--", "__", "..."}:
            return True
        return False

    def _normalized_b2b_user_signature(self, text: str) -> str:
        compact = re.sub(r"\s+", "", (text or "").strip().lower())
        if not compact:
            return ""
        compact_alpha = re.sub(r"[^a-z0-9]", "", compact)
        if not compact_alpha:
            return compact
        if re.fullmatch(_NO_SIGNAL_REPEAT_PUNCT, compact) and len(compact) >= 2 and not compact[0].isalnum():
            return compact
        return compact_alpha[:100]

    def _is_sensitive_capture(self) -> bool:
        # Conservative suppression: while collecting/confirming contact details, do not backchannel.
        s = self._slot_state
        if getattr(s, "intent", None) != "booking":
            return False
        if not getattr(s, "phone_confirmed", False):
            return True
        # Name repair/spelling attempts also count as sensitive capture.
        rep = getattr(s, "reprompts", {}) or {}
        if int(rep.get("name", 0)) > 0 or int(rep.get("name_confidence", 0)) > 0:
            return True
        return False

    # ---------------------------------------------------------------------
    # Turn output handlers
    # ---------------------------------------------------------------------

    async def _handle_turn_output(self, out: TurnOutput) -> None:
        if out.epoch != self._epoch:
            # Stale output from canceled epoch.
            self._metrics.inc(VIC["stale_segment_dropped_total"], 1)
            return

        if out.kind == "outbound_msg":
            await self._enqueue_outbound(out.payload)
            if (
                isinstance(out.payload, OutboundResponse)
                and bool(getattr(out.payload, "content_complete", False))
                and int(getattr(out.payload, "response_id", -1)) == int(self._epoch)
            ):
                self._terminal_sent_for_epoch = int(self._epoch)
            return

        if out.kind == "speech_plan":
            await self._emit_speech_plan(plan=out.payload)
            return

        if out.kind == "turn_complete":
            self._commit_turn_state_backup(epoch=self._epoch)
            if int(self._terminal_sent_for_epoch) != int(self._epoch):
                await self._enqueue_outbound(
                    OutboundResponse(
                        response_type="response",
                        response_id=self._epoch,
                        content="",
                        content_complete=True,
                    )
                )
            await self._set_conv_state(ConvState.LISTENING, reason="turn_complete")
            return

    async def _emit_speech_plan(self, *, plan: SpeechPlan) -> None:
        self._speech_plans.append(plan)
        await self._trace.emit(
            t_ms=self._clock.now_ms(),
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=self._epoch,
            epoch=self._epoch,
            ws_state=self._ws_state.value,
            conv_state=self._conv_state.value,
            event_type="speech_plan",
            payload_obj={
                "plan_id": plan.plan_id,
                "reason": plan.reason,
                "segment_count": len(plan.segments),
            },
        )
        for seg in plan.segments:
            await self._emit_segment(seg)

    async def _emit_fast_path_from_segments(
        self,
        *,
        action: DialogueAction,
        segments: tuple[SpeechSegment, ...],
        reason: PlanReason,
        disclosure_included: bool,
    ) -> bool:
        plan = build_plan(
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=self._epoch,
            epoch=self._epoch,
            created_at_ms=self._clock.now_ms(),
            reason=reason,
            segments=list(segments),
            source_refs=[],
            disclosure_included=disclosure_included,
            metrics=self._metrics,
        )
        await self._emit_speech_plan(plan=plan)

        self._commit_turn_state_backup(epoch=self._epoch)
        if action.action_type == "EndCall" and bool(action.payload.get("end_call", False)):
            await self._enqueue_outbound(
                OutboundResponse(
                    response_type="response",
                    response_id=self._epoch,
                    content="",
                    content_complete=True,
                    end_call=True,
                )
            )
        else:
            await self._enqueue_outbound(
                OutboundResponse(
                    response_type="response",
                    response_id=self._epoch,
                    content="",
                    content_complete=True,
                )
            )
        return True

    async def _emit_segment(self, seg: SpeechSegment) -> None:
        # Transition to speaking on first segment.
        if self._conv_state != ConvState.SPEAKING:
            await self._set_conv_state(ConvState.SPEAKING, reason="first_segment")

        # Metrics: latency from finalization to first segment + to ACK.
        if self._turn_rt is not None and self._turn_rt.epoch == self._epoch:
            if self._turn_rt.first_segment_ms is None:
                self._turn_rt.first_segment_ms = self._clock.now_ms()
                await self._trace.emit(
                    event_type="timing_marker",
                    t_ms=self._clock.now_ms(),
                    session_id=self._session_id,
                    call_id=self._call_id,
                    turn_id=self._epoch,
                    epoch=self._epoch,
                    ws_state=self._ws_state.value,
                    conv_state=self._conv_state.value,
                    payload_obj={
                        "phase": "first_response_latency_ms",
                        "duration_ms": self._turn_rt.first_segment_ms - self._turn_rt.finalized_ms,
                    },
                )
                self._metrics.observe(
                    VIC["turn_final_to_first_segment_ms"],
                    self._turn_rt.first_segment_ms - self._turn_rt.finalized_ms,
                )
            if seg.purpose == "ACK" and self._turn_rt.ack_segment_ms is None:
                self._turn_rt.ack_segment_ms = self._clock.now_ms()
                self._metrics.observe(
                    VIC["turn_final_to_ack_segment_ms"],
                    self._turn_rt.ack_segment_ms - self._turn_rt.finalized_ms,
                )

        seg_hash = seg.segment_hash(epoch=self._epoch, turn_id=self._epoch)
        await self._trace.emit(
            t_ms=self._clock.now_ms(),
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=self._epoch,
            epoch=self._epoch,
            ws_state=self._ws_state.value,
            conv_state=self._conv_state.value,
            event_type="speech_segment",
            payload_obj={
                "purpose": seg.purpose,
                "segment_index": seg.segment_index,
                "interruptible": seg.interruptible,
                "safe_interrupt_point": seg.safe_interrupt_point,
                "expected_duration_ms": seg.expected_duration_ms,
                "requires_tool_evidence": seg.requires_tool_evidence,
                "tool_evidence_ids": seg.tool_evidence_ids,
            },
            segment_hash=seg_hash,
        )

        priority = 50
        if seg.purpose == "FILLER":
            priority = 20
        elif seg.purpose == "ACK":
            priority = 40

        await self._enqueue_outbound(
            OutboundResponse(
                response_type="response",
                response_id=self._epoch,
                content=seg.ssml,
                content_complete=False,
                no_interruption_allowed=(False if seg.interruptible else True),
            ),
            priority=priority,
        )

    async def _cancel_turn(self, *, reason: str) -> None:
        old_q = self._turn_output_q
        if self._turn_task is not None:
            self._turn_task.cancel()
            self._turn_task = None
        self._turn_output_q = None

        # Drain any pending turn outputs and count them as stale drops. This avoids silent queue
        # accumulation and makes stale-drop behavior measurable/deterministic.
        if old_q is not None:
            while True:
                try:
                    _ = old_q.get_nowait()
                except asyncio.QueueEmpty:
                    break
                else:
                    self._metrics.inc(VIC["stale_segment_dropped_total"], 1)

        await self._trace.emit(
            t_ms=self._clock.now_ms(),
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=self._epoch,
            epoch=self._epoch,
            ws_state=self._ws_state.value,
            conv_state=self._conv_state.value,
            event_type="turn_cancel",
            payload_obj={"reason": reason},
        )

    # ---------------------------------------------------------------------
    # Outbound helpers + initial BEGIN
    # ---------------------------------------------------------------------

    def _is_control_inbound(self, item: InboundItem) -> bool:
        return isinstance(
            item,
            (
                TransportClosed,
                InboundPingPong,
                InboundClear,
                InboundResponseRequired,
                InboundReminderRequired,
            ),
        )

    def _outbound_plane(self, msg: OutboundEvent) -> str:
        rt = str(getattr(msg, "response_type", ""))
        if rt in {"config", "update_agent", "ping_pong"}:
            return "control"
        return "speech"

    def _default_outbound_priority(self, msg: OutboundEvent) -> int:
        rt = str(getattr(msg, "response_type", ""))
        if rt == "config":
            return 100
        if rt == "update_agent":
            return 90
        if rt == "ping_pong":
            return 80
        if rt == "agent_interrupt":
            return 60
        if rt in {"tool_call_invocation", "tool_call_result"}:
            return 70
        if rt == "metadata":
            return 10
        if rt == "response":
            return 100 if bool(getattr(msg, "content_complete", False)) else 50
        return 50

    async def _enqueue_outbound(
        self,
        msg: OutboundEvent,
        *,
        epoch: Optional[int] = None,
        speak_gen: Optional[int] = None,
        priority: Optional[int] = None,
        enqueued_ms: Optional[int] = None,
        deadline_ms: Optional[int] = None,
    ) -> None:
        if self._shutdown_evt.is_set():
            return
        enq_start_ms = self._clock.now_ms()
        await self._trace.emit(
            t_ms=self._clock.now_ms(),
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=self._epoch,
            epoch=self._epoch,
            ws_state=self._ws_state.value,
            conv_state=self._conv_state.value,
            event_type="timing_marker",
            payload_obj={
                "phase": "outbound_enqueue_start_ms",
                "response_type": str(getattr(msg, "response_type", "")),
                "response_id": int(getattr(msg, "response_id", 0)),
            },
        )

        rt = str(getattr(msg, "response_type", ""))
        if epoch is None and rt == "response":
            epoch = int(getattr(msg, "response_id", 0))
            speak_gen = int(self._gate_ref.speak_gen)
        elif epoch is None and rt in {"tool_call_invocation", "tool_call_result"}:
            epoch = int(self._epoch)
            speak_gen = int(self._gate_ref.speak_gen)

        if priority is None:
            priority = self._default_outbound_priority(msg)
        plane = self._outbound_plane(msg)
        if enqueued_ms is None:
            enqueued_ms = self._clock.now_ms()
        if (
            deadline_ms is None
            and str(getattr(msg, "response_type", "")) == "ping_pong"
            and int(self._config.keepalive_ping_write_deadline_ms) > 0
        ):
            deadline_ms = int(self._config.keepalive_ping_write_deadline_ms)

        env = OutboundEnvelope(
            msg=msg,
            epoch=epoch,
            speak_gen=speak_gen,
            priority=int(priority),
            plane=plane,  # type: ignore[arg-type]
            enqueued_ms=int(enqueued_ms),
            deadline_ms=(None if deadline_ms is None else int(deadline_ms)),
        )

        def evict(existing: OutboundEnvelope) -> bool:
            ex_msg = existing.msg

            # Never evict terminal response frames; those are our correctness boundary.
            if (
                str(getattr(ex_msg, "response_type", "")) == "response"
                and bool(getattr(ex_msg, "content_complete", False))
            ):
                return False

            # Prefer evicting stale gates (epoch/speak_gen) to prevent queue bloat.
            if existing.epoch is not None and existing.epoch != int(self._gate_ref.epoch):
                return True
            if existing.speak_gen is not None and existing.speak_gen != int(self._gate_ref.speak_gen):
                return True

            # Control-plane frames should never be evicted for speech.
            if existing.plane == "control" and env.plane != "control":
                return False
            if env.plane == "control" and existing.plane != "control":
                return True

            # Otherwise, evict older, lower-priority items first.
            return int(existing.priority) < int(env.priority)

        # Never block: if full, evict stale/low-priority items first.
        ok = await self._outbound_q.put(env, evict=evict)
        if not ok:
            self._metrics.inc("outbound_queue_dropped_total", 1)

        await self._trace.emit(
            t_ms=self._clock.now_ms(),
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=self._epoch,
            epoch=self._epoch,
            ws_state=self._ws_state.value,
            conv_state=self._conv_state.value,
            event_type="timing_marker",
            payload_obj={
                "phase": "outbound_enqueue_ms",
                "duration_ms": self._clock.now_ms() - int(enq_start_ms),
                "response_type": str(getattr(msg, "response_type", "")),
                "priority": int(priority),
                "response_id": int(getattr(msg, "response_id", 0)),
            },
        )

    async def _send_config(self) -> None:
        cfg = RetellConfig(
            auto_reconnect=self._config.retell_auto_reconnect,
            call_details=self._config.retell_call_details,
            transcript_with_tool_calls=self._config.retell_transcript_with_tool_calls,
        )
        await self._enqueue_outbound(OutboundConfig(response_type="config", config=cfg))

    async def _send_update_agent(self) -> None:
        if not self._config.retell_send_update_agent_on_connect:
            return
        agent_cfg = AgentConfig(
            responsiveness=float(self._config.retell_responsiveness),
            interruption_sensitivity=float(self._config.retell_interruption_sensitivity),
            reminder_trigger_ms=int(self._config.retell_reminder_trigger_ms),
            reminder_max_count=int(self._config.retell_reminder_max_count),
        )
        await self._enqueue_outbound(
            OutboundUpdateAgent(response_type="update_agent", agent_config=agent_cfg)
        )

    async def _send_begin_greeting(self) -> None:
        if self._config.conversation_profile == "b2b":
            greeting = (
                f"Hi, this is {self._config.b2b_agent_name} with {self._config.b2b_org_name}. "
                "Is now a bad time for a quick question?"
            )
            if self._config.eve_v7_enabled:
                try:
                    greeting = load_eve_v7_opener(
                        script_path=self._config.eve_v7_script_path,
                        placeholders={
                            "business_name": self._config.b2b_business_name,
                            "city": self._config.b2b_city,
                            "clinic_name": self._config.b2b_business_name,
                            "test_timestamp": self._config.b2b_test_timestamp,
                            "evidence_type": self._config.b2b_evidence_type,
                            "emr_system": self._config.b2b_emr_system,
                            "contact_number": self._config.b2b_contact_number,
                        },
                    )
                except Exception:
                    pass
            self._disclosure_sent = bool(self._config.b2b_auto_disclosure)
        else:
            greeting = (
                f"Hi! Thanks for calling {self._config.clinic_name}. "
                "This is Sarah, the clinic's virtual assistant. "
                "How can I help today?"
            )
            self._disclosure_sent = True
        plan = build_plan(
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=0,
            epoch=0,
            created_at_ms=self._clock.now_ms(),
            reason="CONTENT",
            segments=micro_chunk_text(
                text=greeting,
                max_expected_ms=self._config.vic_max_segment_expected_ms,
                pace_ms_per_char=self._config.pace_ms_per_char,
                purpose="CONTENT",
                interruptible=True,
                requires_tool_evidence=False,
                tool_evidence_ids=[],
                max_monologue_expected_ms=self._config.vic_max_monologue_expected_ms,
                markup_mode=self._config.speech_markup_mode,
                dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                dash_pause_scope=self._config.dash_pause_scope,
            ),
            source_refs=[],
            disclosure_included=True,
            metrics=self._metrics,
        )
        # Record as SpeechPlan/Segments for VIC determinism.
        self._speech_plans.append(plan)
        await self._set_conv_state(ConvState.SPEAKING, reason="begin_greeting")
        await self._trace.emit(
            t_ms=self._clock.now_ms(),
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=0,
            epoch=0,
            ws_state=self._ws_state.value,
            conv_state=self._conv_state.value,
            event_type="speech_plan",
            payload_obj={
                "plan_id": plan.plan_id,
                "reason": plan.reason,
                "segment_count": len(plan.segments),
            },
        )
        for seg in plan.segments:
            await self._trace.emit(
                t_ms=self._clock.now_ms(),
                session_id=self._session_id,
                call_id=self._call_id,
                turn_id=0,
                epoch=0,
                ws_state=self._ws_state.value,
                conv_state=self._conv_state.value,
                event_type="speech_segment",
                payload_obj={
                    "purpose": seg.purpose,
                    "segment_index": seg.segment_index,
                    "interruptible": seg.interruptible,
                    "safe_interrupt_point": seg.safe_interrupt_point,
                    "expected_duration_ms": seg.expected_duration_ms,
                    "requires_tool_evidence": seg.requires_tool_evidence,
                    "tool_evidence_ids": seg.tool_evidence_ids,
                },
                segment_hash=seg.segment_hash(epoch=0, turn_id=0),
            )
            await self._enqueue_outbound(
                OutboundResponse(
                    response_type="response",
                    response_id=0,
                    content=seg.ssml,
                    content_complete=False,
                ),
                priority=50,
            )
        await self._enqueue_outbound(
            OutboundResponse(
                response_type="response",
                response_id=0,
                content="",
                content_complete=True,
            ),
            priority=100,
        )
        await self._set_conv_state(ConvState.LISTENING, reason="begin_complete")

    # ---------------------------------------------------------------------
    # Keepalive / watchdog
    # ---------------------------------------------------------------------

    def _reset_idle_watchdog(self) -> None:
        if self._idle_task is not None:
            self._idle_task.cancel()
        self._idle_task = asyncio.create_task(self._idle_watchdog())

    async def _idle_watchdog(self) -> None:
        try:
            await self._clock.sleep_ms(self._config.idle_timeout_ms)
            await self.end_session(reason="idle_timeout")
        except asyncio.CancelledError:
            return

    async def _ping_loop(self) -> None:
        try:
            while not self._shutdown_evt.is_set():
                await self._clock.sleep_ms(self._config.ping_interval_ms)
                await self._enqueue_outbound(
                    OutboundPingPong(
                        response_type="ping_pong",
                        timestamp=self._clock.now_ms(),
                    )
                )
        except asyncio.CancelledError:
            return
