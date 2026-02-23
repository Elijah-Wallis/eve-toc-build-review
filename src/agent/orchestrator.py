from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

from pydantic import ValidationError

from src.agent.turn_manager import TurnManager
from src.interfaces.adapters import ASRAdapter, LLMAdapter, MockASRAdapter, MockLLMAdapter, MockTTSAdapter, TTSAdapter
from src.interfaces.events import (
    FLAG_BARGE_FLUSH,
    FLAG_END_OF_TURN,
    ConfigUpdate,
    Ping,
    RuntimeConfig,
    SessionStart,
    SpeechSegment,
    TurnState,
    UserTurnEnd,
    build_audio_packet,
    parse_client_control,
)
from src.processing.chunking import SpeakableChunker
from src.processing.prosody import ProsodyTracker
from src.processing.tag_parser import parse_tagged_text
from src.processing.vad import VAD, VADConfig
from src.utils.clock import Clock, RealClock
from src.utils.metrics import METRIC_KEYS, MetricsStore


@dataclass(frozen=True, slots=True)
class OutboundEvent:
    kind: str  # "json" | "audio"
    payload: dict[str, Any] | bytes


class SessionOrchestrator:
    def __init__(
        self,
        *,
        session_id: str,
        clock: Clock | None = None,
        config: RuntimeConfig | None = None,
        asr: ASRAdapter | None = None,
        llm: LLMAdapter | None = None,
        tts: TTSAdapter | None = None,
        metrics: MetricsStore | None = None,
    ) -> None:
        self.session_id = session_id
        self.clock = clock or RealClock()
        self.config = config or RuntimeConfig()
        self.metrics = metrics or MetricsStore()

        self.asr = asr or MockASRAdapter()
        self.llm = llm or MockLLMAdapter()
        self.tts = tts or MockTTSAdapter()

        self.vad = VAD(VADConfig(interruptions_enabled=self.config.interruptions_enabled, interruption_sensitivity=self.config.interruption_sensitivity))
        self.prosody = ProsodyTracker()
        self.turn_manager = TurnManager(config=self.config)

        self._control_in_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._audio_in_q: deque[bytes] = deque(maxlen=200)

        self._control_out_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._audio_out_q: deque[bytes] = deque(maxlen=200)

        self._state = TurnState.IDLE
        self._running = False
        self._loop_task: Optional[asyncio.Task[None]] = None
        self._agent_task: Optional[asyncio.Task[None]] = None

        self._turn_id = 0
        self._audio_out_seq = 0
        self._cancel_agent = asyncio.Event()
        self._soft_filler_spoken_turn: Optional[int] = None
        self._pending_final_text: str = ""
        self._last_agent_audio_seq_at_barge = 0
        self._audio_out_drop_streak = 0

    @property
    def state(self) -> TurnState:
        return self._state

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._run())
        await self._emit_json(
            {
                "type": "session.started",
                "session_id": self.session_id,
                "effective_config": self.config.model_dump(),
            }
        )
        await self._set_state(TurnState.IDLE, reason="session_start")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._agent_task is not None:
            self._cancel_agent.set()
            self._agent_task.cancel()
        if self._loop_task is not None:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
        await self._set_state(TurnState.ENDED, reason="session_end")

    async def submit_control(self, msg: dict[str, Any]) -> None:
        if self._control_in_q.full():
            try:
                _ = self._control_in_q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        await self._control_in_q.put(msg)

    async def submit_audio(self, frame: bytes) -> None:
        if len(self._audio_in_q) >= self._audio_in_q.maxlen:
            self._audio_in_q.popleft()
            self.metrics.inc(METRIC_KEYS["audio_in_dropped_total"], 1)
        self._audio_in_q.append(bytes(frame))

    async def next_outbound(self) -> OutboundEvent:
        # Control plane always wins.
        if not self._control_out_q.empty():
            return OutboundEvent(kind="json", payload=await self._control_out_q.get())
        if self._audio_out_q:
            return OutboundEvent(kind="audio", payload=self._audio_out_q.popleft())

        # Wait for whichever queue gets data first, but prefer control if both.
        wait_task = asyncio.create_task(self._control_out_q.get())
        while True:
            if self._audio_out_q:
                wait_task.cancel()
                await asyncio.gather(wait_task, return_exceptions=True)
                return OutboundEvent(kind="audio", payload=self._audio_out_q.popleft())
            if wait_task.done():
                return OutboundEvent(kind="json", payload=wait_task.result())
            await asyncio.sleep(0)

    async def _run(self) -> None:
        try:
            while self._running:
                handled = False
                if not self._control_in_q.empty():
                    msg = await self._control_in_q.get()
                    await self._handle_control(msg)
                    handled = True

                if self._audio_in_q:
                    frame = self._audio_in_q.popleft()
                    await self._handle_audio(frame)
                    handled = True

                if not handled:
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            return

    async def _handle_control(self, msg: dict[str, Any]) -> None:
        try:
            ev = parse_client_control(msg)
        except (ValidationError, ValueError) as e:
            await self._emit_json({"type": "error", "code": "BAD_CONTROL", "message": str(e)})
            return

        if isinstance(ev, SessionStart):
            if ev.session_id != self.session_id:
                await self._emit_json(
                    {
                        "type": "error",
                        "code": "SESSION_ID_MISMATCH",
                        "message": "session_id does not match websocket route",
                    }
                )
                return
            self.config = ev.config
            self._apply_runtime_config()
            await self._emit_json(
                {
                    "type": "session.started",
                    "session_id": self.session_id,
                    "effective_config": self.config.model_dump(),
                }
            )
            return

        if isinstance(ev, ConfigUpdate):
            patch = dict(ev.config_patch)
            merged = self.config.model_dump()
            merged.update(patch)
            try:
                self.config = RuntimeConfig.model_validate(merged)
            except ValidationError as e:
                await self._emit_json({"type": "error", "code": "BAD_CONFIG", "message": str(e)})
                return
            self._apply_runtime_config()
            await self._emit_json(
                {
                    "type": "session.started",
                    "session_id": self.session_id,
                    "effective_config": self.config.model_dump(),
                }
            )
            return

        if isinstance(ev, UserTurnEnd):
            await self._finalize_user_turn(force=True)
            return

        if isinstance(ev, Ping):
            await self._emit_json({"type": "pong", "ts_ms": ev.ts_ms})
            return

    async def _handle_audio(self, frame: bytes) -> None:
        now = self.clock.now_ms()
        has_speech = self.vad.is_speech(frame)
        prosody = self.prosody.ingest(frame, has_speech=has_speech)

        # Always feed ASR first for transcript stream.
        asr_results = await self.asr.ingest_audio(frame=frame, has_speech=has_speech, now_ms=now)
        for res in asr_results:
            await self._emit_json(
                {
                    "type": "transcript.final" if res.is_final else "transcript.partial",
                    "text": res.text,
                    "stability": res.stability,
                    "ts_ms": now,
                }
            )
            if res.is_final:
                self._pending_final_text = res.text

        ev = self.turn_manager.on_audio(has_speech=has_speech, now_ms=now, prosody=prosody)
        if ev == "barge_in":
            await self._handle_barge_in()
            return
        if ev == "turn_timeout_prompt":
            await self._emit_timeout_prompt()
            return
        if ev == "eou_detected":
            await self._finalize_user_turn(force=False)
            self.metrics.observe(METRIC_KEYS["eou_detection_ms"], now)

    async def _emit_timeout_prompt(self) -> None:
        txt = "Are you still there?"
        await self._emit_json({"type": "soft_timeout.filler", "turn_id": self._turn_id, "text": txt})
        await self._speak_text(txt, turn_id=self._turn_id, reason="turn_timeout")

    async def _finalize_user_turn(self, *, force: bool) -> None:
        final = self._pending_final_text
        if not final:
            res = await self.asr.force_finalize()
            if res is not None:
                final = res.text
                await self._emit_json({"type": "transcript.final", "text": final, "ts_ms": self.clock.now_ms()})
        if not final:
            return

        self._pending_final_text = ""
        self._turn_id += 1
        await self._set_state(TurnState.AGENT_THINKING, reason="final_transcript")

        if self._agent_task is not None and not self._agent_task.done():
            self._cancel_agent.set()
            self._agent_task.cancel()
            await asyncio.gather(self._agent_task, return_exceptions=True)

        self._cancel_agent = asyncio.Event()
        self._soft_filler_spoken_turn = None
        self._agent_task = asyncio.create_task(self._run_agent_turn(final, self._turn_id))

    async def _run_agent_turn(self, user_text: str, turn_id: int) -> None:
        chunker = SpeakableChunker(min_words=4)
        first_token_ts: Optional[int] = None
        first_audio_ts: Optional[int] = None
        got_speakable = False

        async def maybe_soft_timeout_filler() -> None:
            if not self.config.soft_timeout_enabled:
                return
            await self.clock.sleep_ms(int(self.config.soft_timeout_sec * 1000))
            if self._cancel_agent.is_set():
                return
            if got_speakable:
                return
            if self._soft_filler_spoken_turn == turn_id:
                return
            self._soft_filler_spoken_turn = turn_id
            self.metrics.inc(METRIC_KEYS["soft_timeout_trigger_total"], 1)
            filler = "One sec while I pull that up."
            await self._emit_json({"type": "soft_timeout.filler", "turn_id": turn_id, "text": filler})
            await self._speak_text(filler, turn_id=turn_id, reason="soft_timeout")

        filler_task = asyncio.create_task(maybe_soft_timeout_filler())
        started_ms = self.clock.now_ms()
        try:
            async for delta in self.llm.stream_text(prompt=user_text):
                if self._cancel_agent.is_set():
                    break
                if first_token_ts is None and delta.strip():
                    first_token_ts = self.clock.now_ms()
                    self.metrics.observe(METRIC_KEYS["first_token_latency_ms"], first_token_ts - started_ms)

                await self._emit_json({"type": "agent.text.delta", "turn_id": turn_id, "text_delta": delta})
                chunks = chunker.push(delta)
                for chunk in chunks:
                    if self._cancel_agent.is_set():
                        break
                    got_speakable = True
                    if first_audio_ts is None:
                        first_audio_ts = self.clock.now_ms()
                        self.metrics.observe(
                            METRIC_KEYS["first_audio_latency_ms"],
                            first_audio_ts - started_ms,
                        )
                    await self._speak_text(chunk, turn_id=turn_id, reason="stream_chunk")

            tail = chunker.flush()
            if tail and not self._cancel_agent.is_set():
                await self._speak_text(tail, turn_id=turn_id, reason="stream_tail")

            if not self._cancel_agent.is_set():
                self._audio_out_seq += 1
                self._push_audio(
                    build_audio_packet(
                        stream_kind=2,
                        seq=self._audio_out_seq,
                        flags=FLAG_END_OF_TURN,
                        pcm=b"",
                    )
                )
                await self._set_state(TurnState.IDLE, reason="agent_turn_complete")
        except asyncio.CancelledError:
            return
        finally:
            filler_task.cancel()
            await asyncio.gather(filler_task, return_exceptions=True)

    async def _speak_text(self, text: str, *, turn_id: int, reason: str) -> None:
        segments = parse_tagged_text(text, scope_words=self.config.expressive_scope_words)
        if self._state != TurnState.AGENT_SPEAKING:
            await self._set_state(TurnState.AGENT_SPEAKING, reason=reason)

        for seg in segments:
            if self._cancel_agent.is_set():
                return
            await self._emit_json({"type": "agent.segment", "turn_id": turn_id, "segment": seg.model_dump()})
            async for pcm in self.tts.synthesize(segment=seg, sample_rate_hz=16000):
                if self._cancel_agent.is_set():
                    return
                self._audio_out_seq += 1
                self._push_audio(
                    build_audio_packet(
                        stream_kind=2,
                        seq=self._audio_out_seq,
                        flags=0,
                        pcm=pcm,
                    )
                )

    async def _handle_barge_in(self) -> None:
        t0 = self.clock.now_ms()
        await self._set_state(TurnState.BARGED_IN_RECOVERY, reason="barge_in")
        self._cancel_agent.set()
        if self._agent_task is not None:
            self._agent_task.cancel()
            await asyncio.gather(self._agent_task, return_exceptions=True)

        cancelled_seq = self._audio_out_seq
        self._audio_out_q.clear()
        self._audio_out_seq += 1
        self._push_audio(
            build_audio_packet(
                stream_kind=2,
                seq=self._audio_out_seq,
                flags=FLAG_BARGE_FLUSH,
                pcm=b"",
            )
        )
        await self._emit_json(
            {
                "type": "barge_in",
                "turn_id": self._turn_id,
                "cancelled_audio_seq": cancelled_seq,
                "ts_ms": self.clock.now_ms(),
            }
        )
        await self._set_state(TurnState.USER_SPEAKING, reason="barge_in_recovered")
        self.metrics.observe(METRIC_KEYS["barge_in_stop_latency_ms"], self.clock.now_ms() - t0)

    async def _set_state(self, state: TurnState, *, reason: str) -> None:
        self._state = state
        self.turn_manager.set_state(state, self.clock.now_ms())
        await self._emit_json(
            {
                "type": "turn.state",
                "state": state.value,
                "reason": reason,
                "ts_ms": self.clock.now_ms(),
            }
        )

    def _apply_runtime_config(self) -> None:
        self.vad.update_config(
            VADConfig(
                interruptions_enabled=self.config.interruptions_enabled,
                interruption_sensitivity=self.config.interruption_sensitivity,
            )
        )
        self.turn_manager.update_config(self.config)

    def _push_audio(self, blob: bytes) -> None:
        if len(self._audio_out_q) >= self._audio_out_q.maxlen:
            self._audio_out_q.popleft()
            self.metrics.inc(METRIC_KEYS["audio_out_dropped_total"], 1)
            self._audio_out_drop_streak += 1
            if self._audio_out_drop_streak >= 20:
                # hard-stop path to avoid wedging under persistent backpressure
                asyncio.create_task(self._emit_json({"type": "error", "code": "BACKPRESSURE_OVERFLOW", "message": "audio backpressure overflow"}))
        else:
            self._audio_out_drop_streak = 0
        self._audio_out_q.append(blob)

    async def _emit_json(self, payload: dict[str, Any]) -> None:
        if self._control_out_q.full():
            try:
                _ = self._control_out_q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        await self._control_out_q.put(payload)
        await self._emit_metrics_tick()

    async def _emit_metrics_tick(self) -> None:
        snap = self.metrics.snapshot()
        payload = {
            "type": "metrics.tick",
            "turn_id": self._turn_id,
            "metrics_snapshot": snap,
        }
        if self._control_out_q.full():
            return
        await self._control_out_q.put(payload)
