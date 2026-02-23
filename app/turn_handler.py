from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

from .clock import Clock
from .config import BrainConfig
from .dialogue_policy import DialogueAction, ToolRequest
from .fact_guard import FactTemplate, validate_rewrite
from .eve_prompt import load_eve_v7_system_prompt
from .llm_client import LLMClient
from .metrics import Metrics, VIC
from .objection_library import sort_slots_by_acceptance
from .persona_prompt import build_system_prompt
from .phrase_selector import select_phrase
from .trace import TraceSink
from .protocol import (
    OutboundEvent,
    OutboundResponse,
    OutboundToolCallInvocation,
    OutboundToolCallResult,
    TranscriptUtterance,
)
from .speech_planner import (
    SourceRef,
    SpeechPlan,
    StreamingChunker,
    build_plan,
    enforce_vic_tool_grounding_or_fallback,
    micro_chunk_text,
)
from .skills import load_skills, render_skills_for_prompt, retrieve_skills
from .tools import ToolCallRecord, ToolRegistry
from .voice_guard import guard_user_text


TurnOutputKind = Literal["speech_plan", "outbound_msg", "turn_complete"]


@dataclass(frozen=True, slots=True)
class TurnOutput:
    kind: TurnOutputKind
    epoch: int
    payload: Any


_ACK_STANDARD = [
    "Okay.",
]
_ACK_APOLOGY = [
    "Sorry about that.",
]
_ACK_APOLOGY_B2B = [
    "Okay.",
]
_FILLER_1 = [
    "Okay, one sec.",
    "Give me a second.",
    "Checking that now.",
    "One moment.",
    "Hang on one sec.",
    "Let me check that.",
    "All right, one sec.",
    "Thanks-one second.",
]
_FILLER_2 = [
    "Still pulling that up.",
    "Thanks for waiting-I am still checking.",
    "Almost there-I am still loading it.",
    "Just a bit longer-I am still checking.",
    "Still on it.",
    "Still working on that now.",
]


_SKILLS_CACHE: dict[str, tuple[int, list[Any]]] = {}


def _skills_tree_mtime(skills_dir: str) -> int:
    root = Path(skills_dir)
    if not root.exists() or not root.is_dir():
        return 0
    mt = 0
    for p in root.rglob("*.md"):
        try:
            v = int(p.stat().st_mtime)
        except Exception:
            v = 0
        if v > mt:
            mt = v
    return mt


def _b2b_eve_placeholders(config: BrainConfig) -> dict[str, str]:
    return {
        "business_name": config.b2b_business_name,
        "city": config.b2b_city,
        "clinic_name": config.b2b_business_name,
        "test_timestamp": config.b2b_test_timestamp,
        "evidence_type": config.b2b_evidence_type,
        "emr_system": config.b2b_emr_system,
        "contact_number": config.b2b_contact_number,
    }


def _load_skills_cached(skills_dir: str) -> list[Any]:
    key = str(Path(skills_dir))
    mt = _skills_tree_mtime(key)
    cached = _SKILLS_CACHE.get(key)
    if cached and cached[0] == mt:
        return cached[1]
    skills = load_skills(key)
    _SKILLS_CACHE[key] = (mt, skills)
    return skills


def _pick_phrase(
    *,
    options: list[str],
    call_id: str,
    turn_id: int,
    segment_kind: str,
    segment_index: int,
    used_phrases: set[str],
) -> str:
    chosen = select_phrase(
        options=options,
        call_id=call_id,
        turn_id=turn_id,
        segment_kind=segment_kind,
        segment_index=segment_index,
    )
    if chosen not in used_phrases:
        used_phrases.add(chosen)
        return chosen

    if len(options) <= 1:
        used_phrases.add(chosen)
        return chosen

    start = options.index(chosen)
    for off in range(1, len(options)):
        cand = options[(start + off) % len(options)]
        if cand not in used_phrases:
            used_phrases.add(cand)
            return cand
    used_phrases.add(chosen)
    return chosen


def _ack_text(
    *,
    call_id: str,
    turn_id: int,
    needs_apology: bool,
    disclosure_required: bool,
    conversation_profile: str,
    used_phrases: set[str],
) -> str:
    options = _ACK_STANDARD
    if needs_apology:
        options = _ACK_APOLOGY_B2B if conversation_profile == "b2b" else _ACK_APOLOGY
    base = _pick_phrase(
        options=options,
        call_id=call_id,
        turn_id=turn_id,
        segment_kind="ACK",
        segment_index=0,
        used_phrases=used_phrases,
    )
    if disclosure_required:
        return f"{base} I'm Sarah, the clinic's virtual assistant."
    return base


def _filler_text(*, call_id: str, turn_id: int, filler_index: int, used_phrases: set[str]) -> str:
    options = _FILLER_1 if int(filler_index) <= 0 else _FILLER_2
    return _pick_phrase(
        options=options,
        call_id=call_id,
        turn_id=turn_id,
        segment_kind="FILLER",
        segment_index=int(filler_index),
        used_phrases=used_phrases,
    )


class TurnHandler:
    """
    Cancellable worker that produces SpeechPlans for exactly one epoch.
    """

    def __init__(
        self,
        *,
        session_id: str,
        call_id: str,
        epoch: int,
        turn_id: int,
        action: DialogueAction,
        transcript: list[TranscriptUtterance],
        config: BrainConfig,
        clock: Clock,
        metrics: Metrics,
        tools: ToolRegistry,
        llm: Optional[LLMClient] = None,
        output_q: asyncio.Queue[TurnOutput],
        prefetched_tool_records: Optional[list[ToolCallRecord]] = None,
        trace: Optional[TraceSink] = None,
    ) -> None:
        self._session_id = session_id
        self._call_id = call_id
        self._epoch = int(epoch)
        self._turn_id = int(turn_id)
        self._action = action
        self._transcript = list(transcript or [])
        self._config = config
        self._clock = clock
        self._metrics = metrics
        self._tools = tools
        self._llm = llm
        self._output_q = output_q
        self._trace = trace
        self._used_phrases: set[str] = set()
        self._prefetched_tool_records = list(prefetched_tool_records or [])

    def _guard_text(self, text: str) -> str:
        return guard_user_text(
            text=text,
            metrics=self._metrics,
            plain_language_mode=self._config.voice_plain_language_mode,
            no_reasoning_leak=self._config.voice_no_reasoning_leak,
            jargon_blocklist_enabled=self._config.voice_jargon_blocklist_enabled,
        )

    async def _emit_plan(self, plan: SpeechPlan) -> None:
        await self._output_q.put(TurnOutput(kind="speech_plan", epoch=self._epoch, payload=plan))

    async def _trace_marker(self, *, phase: str, payload_obj: dict[str, Any]) -> None:
        if self._trace is None:
            return
        await self._trace.emit(
            t_ms=self._clock.now_ms(),
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=self._turn_id,
            epoch=self._epoch,
            ws_state="LISTENING",
            conv_state="PROCESSING",
            event_type="timing_marker",
            payload_obj={"phase": phase, **payload_obj},
        )

    async def _emit_outbound(self, msg: OutboundEvent) -> None:
        await self._output_q.put(TurnOutput(kind="outbound_msg", epoch=self._epoch, payload=msg))

    async def _emit_done(self) -> None:
        await self._output_q.put(TurnOutput(kind="turn_complete", epoch=self._epoch, payload=None))

    async def run(self) -> None:
        try:
            await self._run_impl()
        except asyncio.CancelledError:
            # Cancelled epochs must stop immediately; no terminal is required because epoch is stale
            # (or a barge-in hint will be handled by orchestrator).
            raise
        except Exception:
            # Deterministic fallback on unexpected errors.
            err_text = "Sorry-I hit a snag. Can you say that one more time?"
            segs = micro_chunk_text(
                text=self._guard_text(err_text),
                max_expected_ms=self._config.vic_max_segment_expected_ms,
                pace_ms_per_char=self._config.pace_ms_per_char,
                purpose="CONTENT",
                interruptible=True,
                requires_tool_evidence=False,
                tool_evidence_ids=[],
                markup_mode=self._config.speech_markup_mode,
                dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                dash_pause_scope=self._config.dash_pause_scope,
            )
            plan = build_plan(
                session_id=self._session_id,
                call_id=self._call_id,
                turn_id=self._turn_id,
                epoch=self._epoch,
                created_at_ms=self._clock.now_ms(),
                reason="ERROR",
                segments=segs,
                source_refs=[],
                metrics=self._metrics,
            )
            await self._emit_plan(plan)
            await self._emit_done()

    async def _run_impl(self) -> None:
        needs_apology = bool(self._action.payload.get("needs_apology", False))
        disclosure_required = bool(self._action.payload.get("disclosure_required", False))
        skip_ack = bool(self._action.payload.get("skip_ack", False))
        no_signal = bool(self._action.payload.get("no_signal", False))
        no_progress = bool(self._action.payload.get("no_progress", False))
        action_message = str(self._action.payload.get("message", "") or "")
        is_no_signal_no_speech = bool(no_signal) and not bool(action_message.strip())
        is_no_progress_with_no_message = (
            self._action.action_type == "Noop"
            and not bool(str(self._action.payload.get("message", "")).strip())
            and no_progress
        )
        if (
            self._action.action_type == "Noop"
            or is_no_signal_no_speech
            or (no_progress and not action_message.strip())
        ):
            if is_no_signal_no_speech or is_no_progress_with_no_message:
                # No-op branches used for ambient/noise turns. Preserve state transitions
                # without advancing audio (one-bandwidth no-speak path).
                await self._emit_done()
                return
            if no_progress:
                await self._emit_done()
                return
            await self._emit_done()
            return

        # VIC-B01: ACK segment quickly after response_required finalization.
        # If the orchestrator already emitted a pre-ACK chunk for this epoch (safe pre-ack),
        # suppress the TurnHandler ACK to avoid back-to-back boilerplate.
        if (
            not skip_ack
            and self._config.conversation_profile != "b2b"
            and not no_signal
            and not no_progress
            and not is_no_signal_no_speech
            and not is_no_progress_with_no_message
        ):
            ack_segs = micro_chunk_text(
                text=self._guard_text(
                    _ack_text(
                        call_id=self._call_id,
                        turn_id=self._turn_id,
                        needs_apology=needs_apology,
                        disclosure_required=disclosure_required,
                        conversation_profile=self._config.conversation_profile,
                        used_phrases=self._used_phrases,
                    )
                ),
                max_expected_ms=self._config.vic_max_segment_expected_ms,
                pace_ms_per_char=self._config.pace_ms_per_char,
                purpose="ACK",
                interruptible=True,
                requires_tool_evidence=False,
                tool_evidence_ids=[],
                markup_mode=self._config.speech_markup_mode,
                dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                dash_pause_scope=self._config.dash_pause_scope,
            )
            ack_plan = build_plan(
                session_id=self._session_id,
                call_id=self._call_id,
                turn_id=self._turn_id,
                epoch=self._epoch,
                created_at_ms=self._clock.now_ms(),
                reason="ACK",
                segments=ack_segs,
                source_refs=[],
                disclosure_included=bool(disclosure_required),
                metrics=self._metrics,
            )
            await self._trace_marker(
                phase="speech_plan_ack_ms",
                payload_obj={"purpose": "ACK", "plan_segments": len(ack_segs)},
            )
            await self._emit_plan(ack_plan)

        # If this is a pure ask/repair/identity/safety response, no tools required.
        tool_records: list[ToolCallRecord] = []
        if self._action.tool_requests:
            tool_records = await self._execute_tools_with_latency_masking(self._action.tool_requests)

        # Optional LLM NLG (provider-agnostic). Default is disabled to keep deterministic behavior.
        if (
            self._config.use_llm_nlg
            and self._llm is not None
            and self._action.action_type in {"Ask", "Repair"}
            and not self._action.tool_requests
        ):
            await self._emit_llm_nlg_content(tool_records=tool_records)
            await self._emit_done()
            return

        # Build content plan based on action + tool results.
        await self._trace_marker(
            phase="speech_plan_build_start_ms",
            payload_obj={"purpose": "CONTENT", "tool_records": len(tool_records)},
        )
        plan_start = self._clock.now_ms()
        plan = await self._plan_from_action(tool_records)
        await self._trace_marker(
            phase="speech_plan_build_ms",
            payload_obj={"purpose": plan.reason, "segments": len(plan.segments), "duration_ms": self._clock.now_ms() - plan_start},
        )
        plan = enforce_vic_tool_grounding_or_fallback(plan=plan, metrics=self._metrics)
        await self._emit_plan(plan)
        if self._action.action_type == "EndCall" and bool(self._action.payload.get("end_call", False)):
            await self._emit_outbound(
                OutboundResponse(
                    response_type="response",
                    response_id=self._epoch,
                    content="",
                    content_complete=True,
                    end_call=True,
                )
            )
        await self._emit_done()

    async def _maybe_rewrite_fact_template(self, *, ft: FactTemplate) -> str:
        """
        Optional factual phrasing rewrite with strict placeholder preservation.
        """
        if not self._config.llm_phrasing_for_facts_enabled:
            return ft.render()
        if self._llm is None:
            return ft.render()

        prompt = (
            "Rewrite this clinic assistant response with warmer phrasing.\n"
            "Hard constraints:\n"
            "- Keep all placeholder tokens exactly unchanged.\n"
            "- Do not add any numbers.\n"
            "- Keep it short (1-2 sentences).\n\n"
            f"TEXT: {ft.template}\n"
            "Return only rewritten text."
        )
        try:
            async def _collect() -> str:
                parts: list[str] = []
                async for d in self._llm.stream_text(prompt=prompt):
                    if d:
                        parts.append(str(d))
                return "".join(parts).strip()

            rewritten = await self._clock.run_with_timeout(
                _collect(),
                timeout_ms=max(200, int(self._config.vic_model_timeout_ms)),
            )
            if validate_rewrite(rewritten=rewritten, required_tokens=ft.required_tokens):
                return ft.render(rewritten)
        except Exception:
            pass

        self._metrics.inc(VIC["llm_fact_guard_fallback_total"], 1)
        return ft.render()

    def _build_llm_prompt(self, *, tool_records: list[ToolCallRecord]) -> str:
        if self._config.conversation_profile == "b2b" and self._config.eve_v7_enabled:
            try:
                system = load_eve_v7_system_prompt(
                    script_path=self._config.eve_v7_script_path,
                    placeholders=_b2b_eve_placeholders(self._config),
                )
            except Exception:
                system = build_system_prompt(
                    clinic_name=self._config.clinic_name,
                    clinic_city=self._config.clinic_city,
                    clinic_state=self._config.clinic_state,
                )
        else:
            system = build_system_prompt(
                clinic_name=self._config.clinic_name,
                clinic_city=self._config.clinic_city,
                clinic_state=self._config.clinic_state,
            )
        # Keep this prompt contract-driven and short; the LLM is only used to phrase non-factual turns
        # by default (Ask/Repair). Tool-grounded factual responses remain deterministic unless you
        # explicitly extend this integration.
        payload = json.dumps(self._action.payload or {}, separators=(",", ":"), sort_keys=True)
        transcript_json = json.dumps(
            [{"role": u.role, "content": u.content} for u in (self._transcript or [])],
            separators=(",", ":"),
            sort_keys=True,
        )
        tool_summary = json.dumps(
            [{"name": r.name, "ok": r.ok, "content": r.content} for r in tool_records],
            separators=(",", ":"),
            sort_keys=True,
        )
        skills_block = ""
        if self._config.skills_enabled:
            self._metrics.inc("skills.invocations_total", 1)
            try:
                skills = _load_skills_cached(self._config.skills_dir)
                query = " ".join(
                    [
                        str(self._action.action_type or ""),
                        payload,
                    ]
                )
                hits = retrieve_skills(query, skills, max_items=max(0, int(self._config.skills_max_injected)))
                if hits:
                    self._metrics.inc("skills.hit_total", 1)
                    rendered = render_skills_for_prompt(hits)
                    if rendered:
                        skills_block = (
                            "Relevant skills (advisory only; hard constraints still win):\n"
                            f"{rendered}\n\n"
                        )
            except Exception:
                # Skill lookup must never break a live turn.
                self._metrics.inc("skills.error_total", 1)
        return (
            f"{system}\n\n"
            "Task: write the single next utterance for the clinic assistant.\n"
            "Hard constraints:\n"
            "- Do not claim to be human.\n"
            "- Do not invent any numbers, prices, times, dates, or availability.\n"
            "- Use plain words an 8th grader can understand.\n"
            "- Never explain your internal reasoning.\n"
            "- Keep it short (1-2 sentences).\n"
            "- Use Retell dash pauses for pacing (spaced dashes: ' - ').\n\n"
            f"action_type={self._action.action_type}\n"
            f"action_payload={payload}\n"
            f"transcript={transcript_json}\n"
            f"tool_records={tool_summary}\n\n"
            f"{skills_block}"
            "Return only the text to say."
        )

    async def _emit_llm_nlg_content(self, *, tool_records: list[ToolCallRecord]) -> None:
        assert self._llm is not None

        prompt = self._build_llm_prompt(tool_records=tool_records)

        # Bounded token queue to avoid unbounded buffering if the model streams faster than we emit.
        token_q: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=64)

        async def produce() -> None:
            try:
                async for delta in self._llm.stream_text(prompt=prompt):
                    # Always drain to completion; consumer controls whether/when to forward.
                    await token_q.put(self._guard_text(str(delta)))
            finally:
                # Sentinel. If the turn is cancelled, don't block trying to deliver it.
                with contextlib.suppress(asyncio.CancelledError):
                    await token_q.put(None)

        chunker = StreamingChunker(
            max_expected_ms=self._config.vic_max_segment_expected_ms,
            pace_ms_per_char=self._config.pace_ms_per_char,
            purpose="CONTENT",
            interruptible=True,
            requires_tool_evidence=False,
            tool_evidence_ids=[],
            markup_mode=self._config.speech_markup_mode,
            dash_pause_unit_ms=self._config.dash_pause_unit_ms,
            digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
            dash_pause_scope=self._config.dash_pause_scope,
        )

        filler_sent = False
        content_emitted = False
        digit_violation = False
        timed_out = False

        async with asyncio.TaskGroup() as tg:
            producer_task = tg.create_task(produce())
            filler_task = tg.create_task(self._clock.sleep_ms(self._config.vic_model_filler_threshold_ms))
            timeout_task = tg.create_task(self._clock.sleep_ms(self._config.vic_model_timeout_ms))

            get_task: Optional[asyncio.Task[Optional[str]]] = None
            try:
                while True:
                    get_task = asyncio.create_task(token_q.get())
                    wait_set: set[asyncio.Task[Any]] = {get_task, timeout_task}
                    if not filler_sent and not content_emitted:
                        wait_set.add(filler_task)

                    done, _ = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)

                    # Prefer tokens over filler if both complete "at the same time".
                    if get_task in done:
                        delta = get_task.result()
                        if delta is None:
                            break
                        if not delta:
                            continue
                        if any(ch.isdigit() for ch in delta):
                            digit_violation = True
                            break
                        segs = chunker.push(delta=delta)
                        if segs:
                            content_emitted = True
                            plan = build_plan(
                                session_id=self._session_id,
                                call_id=self._call_id,
                                turn_id=self._turn_id,
                                epoch=self._epoch,
                                created_at_ms=self._clock.now_ms(),
                                reason="CONTENT",
                                segments=segs,
                                source_refs=[],
                                metrics=self._metrics,
                            )
                            await self._emit_plan(plan)
                    else:
                        # We didn't consume a token; avoid leaking this per-iteration task.
                        get_task.cancel()
                        await asyncio.gather(get_task, return_exceptions=True)
                        get_task = None

                    if timeout_task in done:
                        # Hard timeout: stop consuming and fall back.
                        self._metrics.inc(VIC["fallback_used_total"], 1)
                        timed_out = True
                        break

                    if filler_task in done and not filler_sent and not content_emitted:
                        filler_sent = True
                        filler_plan = build_plan(
                            session_id=self._session_id,
                            call_id=self._call_id,
                            turn_id=self._turn_id,
                            epoch=self._epoch,
                            created_at_ms=self._clock.now_ms(),
                            reason="FILLER",
                            segments=micro_chunk_text(
                                text=self._guard_text(
                                    _filler_text(
                                        call_id=self._call_id,
                                        turn_id=self._turn_id,
                                        filler_index=0,
                                        used_phrases=self._used_phrases,
                                    )
                                ),
                                max_expected_ms=self._config.vic_max_segment_expected_ms,
                                pace_ms_per_char=self._config.pace_ms_per_char,
                                purpose="FILLER",
                                interruptible=True,
                                requires_tool_evidence=False,
                                tool_evidence_ids=[],
                                markup_mode=self._config.speech_markup_mode,
                                dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                                digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                                dash_pause_scope=self._config.dash_pause_scope,
                            ),
                            source_refs=[],
                            metrics=self._metrics,
                        )
                        await self._emit_plan(filler_plan)

                # Final flush of any remaining buffered content.
                if not digit_violation and not timed_out:
                    final_segs = chunker.flush_final()
                    if final_segs:
                        content_emitted = True
                        plan = build_plan(
                            session_id=self._session_id,
                            call_id=self._call_id,
                            turn_id=self._turn_id,
                            epoch=self._epoch,
                            created_at_ms=self._clock.now_ms(),
                            reason="CONTENT",
                            segments=final_segs,
                            source_refs=[],
                            metrics=self._metrics,
                        )
                        await self._emit_plan(plan)

                if (digit_violation or timed_out) and not content_emitted:
                    # If we failed before emitting meaningful content, fall back deterministically.
                    self._metrics.inc(VIC["fallback_used_total"], 1)
                    msg = "Sorry-one moment. Could you say that again?"
                    segs = micro_chunk_text(
                        text=self._guard_text(msg),
                        max_expected_ms=self._config.vic_max_segment_expected_ms,
                        pace_ms_per_char=self._config.pace_ms_per_char,
                        purpose="CLARIFY",
                        interruptible=True,
                        requires_tool_evidence=False,
                        tool_evidence_ids=[],
                        markup_mode=self._config.speech_markup_mode,
                        dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                        digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                        dash_pause_scope=self._config.dash_pause_scope,
                    )
                    plan = build_plan(
                        session_id=self._session_id,
                        call_id=self._call_id,
                        turn_id=self._turn_id,
                        epoch=self._epoch,
                        created_at_ms=self._clock.now_ms(),
                        reason="CLARIFY",
                        segments=segs,
                        source_refs=[],
                        metrics=self._metrics,
                    )
                    await self._emit_plan(plan)
            finally:
                if get_task is not None and not get_task.done():
                    get_task.cancel()
                    await asyncio.gather(get_task, return_exceptions=True)
                for t in (producer_task, filler_task, timeout_task):
                    if not t.done():
                        t.cancel()

    async def _execute_tools_with_latency_masking(self, requests: list[ToolRequest]) -> list[ToolCallRecord]:
        records: list[ToolCallRecord] = []
        # Map prefetched records by (name, canonical_args_json).
        prefetched: dict[tuple[str, str], ToolCallRecord] = {}
        if self._prefetched_tool_records:
            for r in self._prefetched_tool_records:
                try:
                    args_json = json.dumps(r.arguments, separators=(",", ":"), sort_keys=True)
                except Exception:
                    args_json = "{}"
                prefetched[(str(r.name), args_json)] = r

        for req in requests:
            started = self._clock.now_ms()
            first_filler_sent = False
            fillers_sent = 0
            timeout_at = started + self._config.vic_tool_timeout_ms
            tool_call_id_val: Optional[str] = None
            tool_result_sent = False

            async def emit_invocation(tc_id: str, name: str, args_json: str) -> None:
                nonlocal tool_call_id_val
                tool_call_id_val = tc_id
                await self._emit_outbound(
                    OutboundToolCallInvocation(
                        response_type="tool_call_invocation",
                        tool_call_id=tc_id,
                        name=name,
                        arguments=args_json,
                    )
                )

            async def emit_result(tc_id: str, content: str) -> None:
                nonlocal tool_result_sent
                tool_result_sent = True
                await self._emit_outbound(
                    OutboundToolCallResult(
                        response_type="tool_call_result",
                        tool_call_id=tc_id,
                        content=str(content),
                    )
                )

            # Fast-path: reuse a prefetched tool result if it matches exactly and is OK.
            try:
                req_args_json = json.dumps(req.arguments, separators=(",", ":"), sort_keys=True)
            except Exception:
                req_args_json = "{}"
            pre = prefetched.get((str(req.name), req_args_json))
            if pre is not None and bool(pre.ok):
                # Emit tool weaving events now (optional but enabled in config) without re-running the tool.
                await emit_invocation(pre.tool_call_id, pre.name, req_args_json)
                await emit_result(pre.tool_call_id, pre.content)
                self._metrics.observe(VIC["tool_call_total_ms"], pre.completed_at_ms - pre.started_at_ms)
                records.append(pre)
                continue

            tool_task = asyncio.create_task(
                self._tools.invoke(
                    name=req.name,
                    arguments=req.arguments,
                    timeout_ms=self._config.vic_tool_timeout_ms,
                    started_at_ms=started,
                    emit_invocation=emit_invocation,
                    emit_result=emit_result,
                )
            )

            timer_task: Optional[asyncio.Task[None]] = None
            try:
                # Filler deadlines: first at threshold, second after a longer wait. Deterministic.
                filler_deadlines = [started + self._config.vic_tool_filler_threshold_ms]
                if self._config.vic_max_fillers_per_tool > 1:
                    second_filler_ms = max(
                        self._config.vic_tool_filler_threshold_ms,
                        200,
                    )
                    filler_deadlines.append(started + self._config.vic_tool_filler_threshold_ms + second_filler_ms)

                rec: Optional[ToolCallRecord] = None
                while rec is None:
                    if tool_task.done():
                        rec = await tool_task
                        break

                    now = self._clock.now_ms()
                    if now >= timeout_at:
                        # Enforce a hard stop independent of tool-task scheduling.
                        tool_task.cancel()
                        with contextlib.suppress(BaseException):
                            await tool_task
                        if tool_call_id_val is not None and not tool_result_sent:
                            tool_result_sent = True
                            await emit_result(tool_call_id_val, "tool_timeout")
                        rec = ToolCallRecord(
                            tool_call_id=tool_call_id_val or f"{self._session_id}:tool:timeout",
                            name=req.name,
                            arguments=dict(req.arguments),
                            started_at_ms=started,
                            completed_at_ms=timeout_at,
                            ok=False,
                            content="tool_timeout",
                        )
                        break

                    next_filler_deadline: Optional[int] = None
                    if fillers_sent < self._config.vic_max_fillers_per_tool:
                        for d in filler_deadlines:
                            if d > now:
                                next_filler_deadline = d
                                break

                    # Next timer is either a filler deadline or the hard timeout.
                    next_deadline = timeout_at
                    if next_filler_deadline is not None:
                        next_deadline = min(next_filler_deadline, timeout_at)

                    timer_task = asyncio.create_task(self._clock.sleep_ms(next_deadline - now))
                    done, pending = await asyncio.wait(
                        {tool_task, timer_task}, return_when=asyncio.FIRST_COMPLETED
                    )

                    if tool_task in done:
                        # Tool finished first; stop the timer without touching the tool task.
                        if timer_task in pending:
                            timer_task.cancel()
                            with contextlib.suppress(BaseException):
                                await timer_task
                        continue

                    # Timer fired.
                    if next_deadline >= timeout_at:
                        # Timeout path handled at top of loop.
                        continue

                    # Filler deadline fired and tool still running: emit a filler.
                    fillers_sent += 1
                    filler_plan = build_plan(
                        session_id=self._session_id,
                        call_id=self._call_id,
                        turn_id=self._turn_id,
                        epoch=self._epoch,
                        created_at_ms=self._clock.now_ms(),
                        reason="FILLER",
                        segments=micro_chunk_text(
                            text=self._guard_text(_filler_text(
                                call_id=self._call_id,
                                turn_id=self._turn_id,
                                filler_index=fillers_sent - 1,
                                used_phrases=self._used_phrases,
                            )),
                            max_expected_ms=self._config.vic_max_segment_expected_ms,
                            pace_ms_per_char=self._config.pace_ms_per_char,
                            purpose="FILLER",
                            interruptible=True,
                            requires_tool_evidence=False,
                            tool_evidence_ids=[],
                            markup_mode=self._config.speech_markup_mode,
                            dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                            digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                dash_pause_scope=self._config.dash_pause_scope,
                        ),
                        source_refs=[],
                        metrics=self._metrics,
                    )
                    await self._emit_plan(filler_plan)

                    if not first_filler_sent:
                        first_filler_sent = True
                        self._metrics.observe(
                            VIC["tool_call_to_first_filler_ms"], self._clock.now_ms() - started
                        )

                assert rec is not None
            finally:
                if timer_task is not None and not timer_task.done():
                    timer_task.cancel()
                    with contextlib.suppress(BaseException):
                        await timer_task
                if not tool_task.done():
                    tool_task.cancel()
                    with contextlib.suppress(BaseException):
                        await tool_task
            self._metrics.observe(VIC["tool_call_total_ms"], rec.completed_at_ms - rec.started_at_ms)
            if not rec.ok:
                self._metrics.inc(VIC["tool_failures_total"], 1)
            records.append(rec)
        return records

    async def _plan_from_action(self, tool_records: list[ToolCallRecord]) -> SpeechPlan:
        created_at = self._clock.now_ms()
        needs_apology = bool(self._action.payload.get("needs_apology", False))
        needs_empathy = bool(self._action.payload.get("needs_empathy", False))
        source_refs = [SourceRef(kind="tool_call", id=r.tool_call_id) for r in tool_records]

        # Helper: used for tool-grounded numeric/time statements.
        tool_ids = [r.tool_call_id for r in tool_records if r.ok]

        def with_empathy(msg: str) -> str:
            if not needs_empathy:
                return msg
            low = (msg or "").lower()
            if "sorry" in low:
                return msg
            if self._config.conversation_profile == "b2b":
                return f"I hear you. {msg}"
            return f"I'm sorry about that. {msg}"

        action = self._action.action_type

        if action == "EscalateSafety":
            msg = with_empathy(str(self._action.payload.get("message", "")))
            segs = micro_chunk_text(
                text=self._guard_text(msg),
                max_expected_ms=self._config.vic_max_segment_expected_ms,
                pace_ms_per_char=self._config.pace_ms_per_char,
                purpose="CONTENT",
                interruptible=True,
                requires_tool_evidence=False,
                tool_evidence_ids=[],
                markup_mode=self._config.speech_markup_mode,
                dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                dash_pause_scope=self._config.dash_pause_scope,
            )
            return build_plan(
                session_id=self._session_id,
                call_id=self._call_id,
                turn_id=self._turn_id,
                epoch=self._epoch,
                created_at_ms=created_at,
                reason="ERROR",
                segments=segs,
                source_refs=source_refs,
                metrics=self._metrics,
            )

        if action == "Ask":
            msg = with_empathy(str(self._action.payload.get("message", "")))
            segs = micro_chunk_text(
                text=self._guard_text(msg),
                max_expected_ms=self._config.vic_max_segment_expected_ms,
                pace_ms_per_char=self._config.pace_ms_per_char,
                purpose="CLARIFY",
                interruptible=True,
                requires_tool_evidence=False,
                tool_evidence_ids=[],
                markup_mode=self._config.speech_markup_mode,
                dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                dash_pause_scope=self._config.dash_pause_scope,
            )
            return build_plan(
                session_id=self._session_id,
                call_id=self._call_id,
                turn_id=self._turn_id,
                epoch=self._epoch,
                created_at_ms=created_at,
                reason="CLARIFY",
                segments=segs,
                source_refs=source_refs,
                metrics=self._metrics,
            )

        if action == "Repair":
            self._metrics.inc(VIC["repair_attempts_total"], 1)
            field = str(self._action.payload.get("field", ""))
            strategy = str(self._action.payload.get("strategy", "ask"))
            if field == "name" and strategy == "spell":
                msg = with_empathy("Could you spell your name for me?")
            else:
                msg = with_empathy("Sorry, can you say that again?")
            segs = micro_chunk_text(
                text=self._guard_text(msg),
                max_expected_ms=self._config.vic_max_segment_expected_ms,
                pace_ms_per_char=self._config.pace_ms_per_char,
                purpose="REPAIR",
                interruptible=True,
                requires_tool_evidence=False,
                tool_evidence_ids=[],
                markup_mode=self._config.speech_markup_mode,
                dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                dash_pause_scope=self._config.dash_pause_scope,
            )
            return build_plan(
                session_id=self._session_id,
                call_id=self._call_id,
                turn_id=self._turn_id,
                epoch=self._epoch,
                created_at_ms=created_at,
                reason="REPAIR",
                segments=segs,
                source_refs=source_refs,
                metrics=self._metrics,
            )

        if action == "Confirm":
            self._metrics.inc(VIC["confirmations_total"], 1)
            field = str(self._action.payload.get("field", ""))
            if field == "phone_last4":
                last4 = str(self._action.payload.get("phone_last4", ""))
                msg = with_empathy(f"Just to confirm, your last four are {last4}, right?")
            elif field == "requested_dt":
                dt = str(self._action.payload.get("requested_dt", ""))
                msg = with_empathy(f"Just to confirm, {dt}, right?")
            else:
                msg = with_empathy("Just to confirm, is that right?")

            segs = micro_chunk_text(
                text=self._guard_text(msg),
                max_expected_ms=self._config.vic_max_segment_expected_ms,
                pace_ms_per_char=self._config.pace_ms_per_char,
                purpose="CONFIRM",
                interruptible=True,
                requires_tool_evidence=False,
                tool_evidence_ids=[],
                markup_mode=self._config.speech_markup_mode,
                dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                dash_pause_scope=self._config.dash_pause_scope,
            )
            return build_plan(
                session_id=self._session_id,
                call_id=self._call_id,
                turn_id=self._turn_id,
                epoch=self._epoch,
                created_at_ms=created_at,
                reason="CONFIRM",
                segments=segs,
                source_refs=source_refs,
                metrics=self._metrics,
            )

        if action == "Inform":
            info_type = str(self._action.payload.get("info_type", ""))
            if info_type == "identity":
                msg = str(self._action.payload.get("message", ""))
                segs = micro_chunk_text(
                    text=self._guard_text(msg),
                    max_expected_ms=self._config.vic_max_segment_expected_ms,
                    pace_ms_per_char=self._config.pace_ms_per_char,
                    purpose="CONTENT",
                    interruptible=True,
                    requires_tool_evidence=False,
                    tool_evidence_ids=[],
                    markup_mode=self._config.speech_markup_mode,
                    dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                    digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                dash_pause_scope=self._config.dash_pause_scope,
                )
                return build_plan(
                    session_id=self._session_id,
                    call_id=self._call_id,
                    turn_id=self._turn_id,
                    epoch=self._epoch,
                    created_at_ms=created_at,
                    reason="CONTENT",
                    segments=segs,
                    source_refs=source_refs,
                    disclosure_included=True,
                    metrics=self._metrics,
                )

            if info_type == "b2b_identity":
                msg = with_empathy(str(self._action.payload.get("message", "")))
                segs = micro_chunk_text(
                    text=self._guard_text(msg),
                    max_expected_ms=self._config.vic_max_segment_expected_ms,
                    pace_ms_per_char=self._config.pace_ms_per_char,
                    purpose="CONTENT",
                    interruptible=True,
                    requires_tool_evidence=False,
                    tool_evidence_ids=[],
                    markup_mode=self._config.speech_markup_mode,
                    dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                    digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                    dash_pause_scope=self._config.dash_pause_scope,
                )
                return build_plan(
                    session_id=self._session_id,
                    call_id=self._call_id,
                    turn_id=self._turn_id,
                    epoch=self._epoch,
                    created_at_ms=created_at,
                    reason="CONTENT",
                    segments=segs,
                    source_refs=source_refs,
                    metrics=self._metrics,
                )

            if info_type == "shell_exec":
                rec = None
                for r in tool_records:
                    if r.name == "run_shell_command":
                        rec = r
                        break
                if rec is None:
                    msg = with_empathy("I couldn't execute that command in this turn.")
                else:
                    try:
                        p = json.loads(rec.content or "{}")
                    except Exception:
                        p = {}
                    ok = bool(p.get("ok", False))
                    reason = str(p.get("reason", "unknown"))
                    runtime = str(p.get("runtime", "local"))
                    rc = p.get("returncode", "n/a")
                    out = str(p.get("stdout", "") or "").strip()
                    err = str(p.get("stderr", "") or "").strip()
                    preview = out if out else err
                    preview = preview.replace("\n", " ").strip()
                    if len(preview) > 140:
                        preview = preview[:140].rstrip() + "..."
                    if ok:
                        msg = with_empathy(
                            f"Command executed in {runtime} with return code {rc}. "
                            + (f"Output: {preview}" if preview else "No output.")
                        )
                    else:
                        msg = with_empathy(
                            f"Command execution failed with reason {reason} and return code {rc}. "
                            + (f"Output: {preview}" if preview else "No output.")
                        )
                segs = micro_chunk_text(
                    text=self._guard_text(msg),
                    max_expected_ms=self._config.vic_max_segment_expected_ms,
                    pace_ms_per_char=self._config.pace_ms_per_char,
                    purpose="CONTENT",
                    interruptible=True,
                    requires_tool_evidence=False,
                    tool_evidence_ids=[],
                    markup_mode=self._config.speech_markup_mode,
                    dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                    digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                    dash_pause_scope=self._config.dash_pause_scope,
                )
                return build_plan(
                    session_id=self._session_id,
                    call_id=self._call_id,
                    turn_id=self._turn_id,
                    epoch=self._epoch,
                    created_at_ms=created_at,
                    reason="CONTENT",
                    segments=segs,
                    source_refs=source_refs,
                    metrics=self._metrics,
                )

            if info_type == "pricing":
                # Use tool result if available.
                price_usd: Optional[int] = None
                for r in tool_records:
                    if r.name == "get_pricing" and r.ok:
                        try:
                            price_usd = int(json.loads(r.content).get("price_usd"))
                        except Exception:
                            price_usd = None
                if price_usd is None:
                    self._metrics.inc(VIC["fallback_used_total"], 1)
                    msg = with_empathy(
                        "I can check pricing for you, but I don't want to guess. What service are you asking about?"
                    )
                    segs = micro_chunk_text(
                        text=self._guard_text(msg),
                        max_expected_ms=self._config.vic_max_segment_expected_ms,
                        pace_ms_per_char=self._config.pace_ms_per_char,
                        purpose="CLARIFY",
                        interruptible=True,
                        requires_tool_evidence=False,
                        tool_evidence_ids=[],
                        markup_mode=self._config.speech_markup_mode,
                        dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                        digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                dash_pause_scope=self._config.dash_pause_scope,
                    )
                    return build_plan(
                        session_id=self._session_id,
                        call_id=self._call_id,
                        turn_id=self._turn_id,
                        epoch=self._epoch,
                        created_at_ms=created_at,
                        reason="ERROR",
                        segments=segs,
                        source_refs=source_refs,
                        metrics=self._metrics,
                    )

                ft = FactTemplate(
                    template=with_empathy("For a general visit, it's [[PRICE]]."),
                    placeholders={"PRICE": f"${price_usd}"},
                )
                msg = await self._maybe_rewrite_fact_template(ft=ft)
                segs = micro_chunk_text(
                    text=self._guard_text(msg),
                    max_expected_ms=self._config.vic_max_segment_expected_ms,
                    pace_ms_per_char=self._config.pace_ms_per_char,
                    purpose="CONTENT",
                    interruptible=True,
                    requires_tool_evidence=True,
                    tool_evidence_ids=tool_ids,
                    max_monologue_expected_ms=self._config.vic_max_monologue_expected_ms,
                    markup_mode=self._config.speech_markup_mode,
                    dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                    digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                dash_pause_scope=self._config.dash_pause_scope,
                )
                return build_plan(
                    session_id=self._session_id,
                    call_id=self._call_id,
                    turn_id=self._turn_id,
                    epoch=self._epoch,
                    created_at_ms=created_at,
                    reason="CONTENT",
                    segments=segs,
                    source_refs=source_refs,
                    metrics=self._metrics,
                )

        if action == "OfferSlots":
            # Parse slots.
            slots: list[str] = []
            for r in tool_records:
                if r.name == "check_availability" and r.ok:
                    try:
                        slots = list(json.loads(r.content).get("slots", []))
                    except Exception:
                        slots = []
            if not slots:
                self._metrics.inc(VIC["fallback_used_total"], 1)
                msg = with_empathy(
                    "I'm not seeing openings right now. Do you want to try a different day, or should I have someone call you back?"
                )
                segs = micro_chunk_text(
                    text=self._guard_text(msg),
                    max_expected_ms=self._config.vic_max_segment_expected_ms,
                    pace_ms_per_char=self._config.pace_ms_per_char,
                    purpose="CLARIFY",
                    interruptible=True,
                    requires_tool_evidence=False,
                    tool_evidence_ids=[],
                    markup_mode=self._config.speech_markup_mode,
                    dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                    digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                dash_pause_scope=self._config.dash_pause_scope,
                )
                return build_plan(
                    session_id=self._session_id,
                    call_id=self._call_id,
                    turn_id=self._turn_id,
                    epoch=self._epoch,
                    created_at_ms=created_at,
                    reason="ERROR",
                    segments=segs,
                    source_refs=source_refs,
                    metrics=self._metrics,
                )

            ranked_slots = sort_slots_by_acceptance(slots)
            offer = ranked_slots[:3]  # VIC-G01
            self._metrics.observe(VIC["offered_slots_count"], len(offer))
            prefix = str(self._action.payload.get("message_prefix", "")).strip()
            lead = f"{prefix} " if prefix else ""
            ft = FactTemplate(
                template=with_empathy(
                    f"{lead}I have [[SLOT_1]], [[SLOT_2]], or [[SLOT_3]]. Which works best?"
                ),
                placeholders={
                    "SLOT_1": str(offer[0]),
                    "SLOT_2": str(offer[1]),
                    "SLOT_3": str(offer[2]),
                },
            )
            msg = await self._maybe_rewrite_fact_template(ft=ft)
            segs = micro_chunk_text(
                text=self._guard_text(msg),
                max_expected_ms=self._config.vic_max_segment_expected_ms,
                pace_ms_per_char=self._config.pace_ms_per_char,
                purpose="CONTENT",
                interruptible=True,
                requires_tool_evidence=True,
                tool_evidence_ids=tool_ids,
                max_monologue_expected_ms=self._config.vic_max_monologue_expected_ms,
                markup_mode=self._config.speech_markup_mode,
                dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                dash_pause_scope=self._config.dash_pause_scope,
            )
            return build_plan(
                session_id=self._session_id,
                call_id=self._call_id,
                turn_id=self._turn_id,
                epoch=self._epoch,
                created_at_ms=created_at,
                reason="CONTENT",
                segments=segs,
                source_refs=source_refs,
                metrics=self._metrics,
            )

        if action == "EndCall":
            msg = with_empathy(str(self._action.payload.get("message", "Thanks for your time. Goodbye.")))
            segs = micro_chunk_text(
                text=self._guard_text(msg),
                max_expected_ms=self._config.vic_max_segment_expected_ms,
                pace_ms_per_char=self._config.pace_ms_per_char,
                purpose="CLOSING",
                interruptible=True,
                requires_tool_evidence=False,
                tool_evidence_ids=[],
                markup_mode=self._config.speech_markup_mode,
                dash_pause_unit_ms=self._config.dash_pause_unit_ms,
                digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                dash_pause_scope=self._config.dash_pause_scope,
            )
            return build_plan(
                session_id=self._session_id,
                call_id=self._call_id,
                turn_id=self._turn_id,
                epoch=self._epoch,
                created_at_ms=created_at,
                reason="CLOSING",
                segments=segs,
                source_refs=source_refs,
                metrics=self._metrics,
            )

        # Default.
        msg = with_empathy("How can I help?")
        segs = micro_chunk_text(
            text=self._guard_text(msg),
            max_expected_ms=self._config.vic_max_segment_expected_ms,
            pace_ms_per_char=self._config.pace_ms_per_char,
            purpose="CLARIFY",
            interruptible=True,
            requires_tool_evidence=False,
            tool_evidence_ids=[],
            markup_mode=self._config.speech_markup_mode,
            dash_pause_unit_ms=self._config.dash_pause_unit_ms,
            digit_dash_pause_unit_ms=self._config.digit_dash_pause_unit_ms,
                dash_pause_scope=self._config.dash_pause_scope,
        )
        return build_plan(
            session_id=self._session_id,
            call_id=self._call_id,
            turn_id=self._turn_id,
            epoch=self._epoch,
            created_at_ms=created_at,
            reason="CLARIFY",
            segments=segs,
            source_refs=source_refs,
            metrics=self._metrics,
        )
