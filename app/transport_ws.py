from __future__ import annotations

import asyncio
import json
import json as _json
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Literal, Optional, Protocol

from .bounded_queue import BoundedDequeQueue
from .clock import Clock
from .metrics import Metrics, VIC
from .protocol import (
    InboundCallDetails,
    InboundClear,
    InboundEvent,
    InboundPingPong,
    InboundReminderRequired,
    InboundResponseRequired,
    InboundUpdateOnly,
    OutboundEvent,
    dumps_outbound,
    parse_inbound_obj,
)


class Transport(Protocol):
    async def recv_text(self) -> str: ...

    async def send_text(self, text: str) -> None: ...

    async def close(self, *, code: int = 1000, reason: str = "") -> None: ...


@dataclass(frozen=True, slots=True)
class TransportClosed:
    reason: str


InboundItem = InboundEvent | TransportClosed


@dataclass(frozen=True, slots=True)
class OutboundEnvelope:
    """
    Internal-only wrapper to enforce epoch + speak-generation gating in the single writer.

    This must never leak onto the wire: only `msg` is serialized and sent as JSON.
    """

    msg: OutboundEvent
    epoch: Optional[int] = None
    speak_gen: Optional[int] = None
    priority: int = 0
    plane: Literal["control", "speech"] = "speech"
    enqueued_ms: Optional[int] = None
    deadline_ms: Optional[int] = None


async def socket_reader(
    *,
    transport: Transport,
    inbound_q: BoundedDequeQueue[InboundItem],
    metrics: Metrics,
    shutdown_evt: asyncio.Event,
    max_frame_bytes: int = 262_144,
    structured_logs: bool = False,
    call_id: str | None = None,
) -> None:
    """
    Reads WS frames -> JSON decode -> protocol validation -> inbound bounded queue.
    Never blocks on a full inbound queue: it drops/evicts via inbound policy handled by orchestrator.
    """
    def _log(event: str, **payload: object) -> None:
        if not structured_logs:
            return
        base = {
            "component": "ws_inbound",
            "call_id": str(call_id or ""),
            "event": event,
        }
        base.update(payload)
        print(_json.dumps(base, sort_keys=True, separators=(",", ":")))

    try:
        while not shutdown_evt.is_set():
            raw = await transport.recv_text()
            if int(max_frame_bytes) > 0:
                raw_len = len(raw.encode("utf-8"))
                if raw_len > int(max_frame_bytes):
                    _log("frame_dropped", reason="frame_too_large", size_bytes=raw_len)
                    await inbound_q.put(TransportClosed(reason="FRAME_TOO_LARGE"))
                    return
            try:
                obj = json.loads(raw)
                _log(
                    "raw_frame",
                    interaction_type=str(
                        obj["interaction_type"] if isinstance(obj, dict) else ""
                    ),
                    size_bytes=len(raw.encode("utf-8")),
                )
            except JSONDecodeError:
                _log("frame_dropped", reason="BAD_JSON")
                await inbound_q.put(TransportClosed(reason="BAD_JSON"))
                return
            except Exception:
                _log("frame_dropped", reason="BAD_JSON")
                await inbound_q.put(TransportClosed(reason="BAD_JSON"))
                return

            try:
                ev = parse_inbound_obj(obj)
            except Exception:
                interaction_type = ""
                if isinstance(obj, dict):
                    interaction_type = str(obj.get("interaction_type", ""))
                _log("frame_dropped", reason="BAD_SCHEMA", interaction_type=interaction_type)
                metrics.inc("inbound.bad_schema_total", 1)
                # Future schema drift / unknown interaction_type must not tear down the session.
                continue

            _log(
                "frame_accepted",
                interaction_type=str(getattr(ev, "interaction_type", "")),
                has_transcript=hasattr(ev, "transcript"),
            )

            # Inbound overflow policy (bounded):
            # - update_only: keep only latest snapshot (drop older update_only first)
            # - response_required/reminder_required: evict update_only first, then ping/call_details
            if isinstance(ev, InboundUpdateOnly):
                await inbound_q.drop_where(
                    lambda x: isinstance(x, InboundUpdateOnly)
                    or (hasattr(x, "interaction_type") and getattr(x, "interaction_type") == "update_only")
                )
                ok = await inbound_q.put(ev)
            elif isinstance(ev, (InboundResponseRequired, InboundReminderRequired)):
                ok = await inbound_q.put(
                    ev,
                    evict=lambda x: isinstance(x, (InboundUpdateOnly, InboundPingPong, InboundCallDetails)),
                )
                if not ok:
                    # Extreme overload: drop an older response_required (stale) to keep the newest epoch.
                    ok = await inbound_q.put(
                        ev,
                        evict=lambda x: hasattr(x, "response_id")
                        and getattr(x, "response_id") < getattr(ev, "response_id"),
                    )
            elif isinstance(ev, InboundPingPong):
                # Keepalive control plane must not be starved by update-only floods.
                ok = await inbound_q.put(ev)
                if not ok:
                    evicted = await inbound_q.evict_one_where(lambda x: isinstance(x, InboundUpdateOnly))
                    if evicted:
                        metrics.inc(VIC["inbound_queue_evictions_total"], 1)
                        metrics.inc("inbound.queue_evictions.drop_update_only_for_ping_total", 1)
                        ok = await inbound_q.put(ev)
            elif isinstance(ev, InboundClear):
                # Clear must be delivered promptly to stop/rollback the current speak stream.
                ok = await inbound_q.put(ev)
                if not ok:
                    evicted = await inbound_q.evict_one_where(lambda x: isinstance(x, InboundUpdateOnly))
                    if evicted:
                        metrics.inc(VIC["inbound_queue_evictions_total"], 1)
                        metrics.inc("inbound.queue_evictions.drop_update_only_for_clear_total", 1)
                        ok = await inbound_q.put(ev)
            else:
                # call_details: drop if queue is full.
                ok = await inbound_q.put(ev, evict=lambda x: isinstance(x, InboundUpdateOnly))
            if not ok:
                # If inbound queue is full, drop this frame and count it.
                metrics.inc("inbound_queue_dropped_total", 1)
    except Exception:
        await inbound_q.put(TransportClosed(reason="transport_read_error"))


class GateRef:
    def __init__(self, *, epoch: int = 0, speak_gen: int = 0) -> None:
        self.epoch = int(epoch)
        self.speak_gen = int(speak_gen)
        self._version = 0
        self._changed_evt = asyncio.Event()

    def snapshot(self) -> tuple[int, int, int, asyncio.Event]:
        # (epoch, speak_gen, version, changed_evt)
        return (int(self.epoch), int(self.speak_gen), int(self._version), self._changed_evt)

    def set_epoch(self, epoch: int) -> None:
        self.epoch = int(epoch)
        self.speak_gen = 0
        self._pulse_changed()

    def bump_speak_gen(self) -> int:
        self.speak_gen = int(self.speak_gen) + 1
        self._pulse_changed()
        return int(self.speak_gen)

    def _pulse_changed(self) -> None:
        # Wake any writer send currently in-flight, then swap the event to make it edge-triggered.
        self._version += 1
        self._changed_evt.set()
        self._changed_evt = asyncio.Event()


async def socket_writer(
    *,
    transport: Transport,
    outbound_q: BoundedDequeQueue[OutboundEnvelope],
    metrics: Metrics,
    shutdown_evt: asyncio.Event,
    gate: GateRef,
    clock: Clock,
    inbound_q: Optional[BoundedDequeQueue[InboundItem]] = None,
    ws_write_timeout_ms: int = 400,
    ws_close_on_write_timeout: bool = True,
    ws_max_consecutive_write_timeouts: int = 2,
) -> None:
    """
    Single-writer rule: the only task that writes to the WS.
    Drops stale turn-bound messages if (epoch, speak_gen) doesn't match the current gate.
    """
    def _is_control_envelope(env: OutboundEnvelope) -> bool:
        if env.plane == "control":
            return True
        return False

    async def _signal_fatal_and_stop(reason: str) -> None:
        if inbound_q is not None:
            await inbound_q.put(TransportClosed(reason=reason))
        shutdown_evt.set()
        try:
            await transport.close(code=1011, reason=reason)
        except Exception:
            pass

    consecutive_write_timeouts = 0

    try:
        while not shutdown_evt.is_set():
            try:
                env = await outbound_q.get_prefer(_is_control_envelope)
            except Exception:
                return

            # Gate checks for turn-bound envelopes (response/tool weaving).
            gate_epoch, gate_speak_gen, _, changed_evt = gate.snapshot()
            if env.epoch is not None and env.epoch != gate_epoch:
                metrics.inc(VIC["stale_segment_dropped_total"], 1)
                continue
            if env.speak_gen is not None and env.speak_gen != gate_speak_gen:
                metrics.inc(VIC["stale_segment_dropped_total"], 1)
                continue

            msg = env.msg

            # Belt-and-suspenders: never send a response chunk for the wrong response_id.
            if (
                getattr(msg, "response_type", None) == "response"
                and getattr(msg, "response_id", None) != gate_epoch
            ):
                metrics.inc(VIC["stale_segment_dropped_total"], 1)
                continue

            payload = dumps_outbound(msg)

            async def _send_payload() -> bool:
                nonlocal consecutive_write_timeouts
                rt = str(getattr(msg, "response_type", ""))
                if rt == "ping_pong" and env.enqueued_ms is not None:
                    delay = max(0, clock.now_ms() - int(env.enqueued_ms))
                    metrics.observe(VIC["keepalive_ping_pong_queue_delay_ms"], delay)
                    deadline = int(env.deadline_ms or 0)
                    if deadline > 0 and delay > deadline:
                        metrics.inc(VIC["keepalive_ping_pong_missed_deadline_total"], 1)
                if rt == "ping_pong":
                    metrics.inc(VIC["keepalive_ping_pong_write_attempt_total"], 1)
                try:
                    await clock.run_with_timeout(
                        transport.send_text(payload),
                        timeout_ms=max(1, int(ws_write_timeout_ms)),
                    )
                    consecutive_write_timeouts = 0
                    return True
                except TimeoutError:
                    metrics.inc(VIC["ws_write_timeout_total"], 1)
                    if rt == "ping_pong":
                        metrics.inc(VIC["keepalive_ping_pong_write_timeout_total"], 1)
                    consecutive_write_timeouts += 1
                    if (
                        ws_close_on_write_timeout
                        and consecutive_write_timeouts
                        >= max(1, int(ws_max_consecutive_write_timeouts))
                    ):
                        await _signal_fatal_and_stop("WRITE_TIMEOUT_BACKPRESSURE")
                    return False

            # Control-plane frames are always sent immediately and never preempted by queued speech.
            if env.plane == "control":
                ok_send = await _send_payload()
                if not ok_send:
                    if shutdown_evt.is_set():
                        return
                    continue
                continue

            # Speech-plane writes are cancellable for two reasons:
            # 1) gate changes (epoch/speak_gen),
            # 2) a control-plane envelope arrives and must preempt.
            if env.epoch is not None or env.speak_gen is not None:
                send_task = asyncio.create_task(_send_payload())
                gate_task = asyncio.create_task(changed_evt.wait())
                control_wait_task = asyncio.create_task(outbound_q.wait_for_any(_is_control_envelope))
                done, pending = await asyncio.wait(
                    {send_task, gate_task, control_wait_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if gate_task in done and not send_task.done():
                    send_task.cancel()
                    control_wait_task.cancel()
                    await asyncio.gather(
                        send_task, gate_task, control_wait_task, return_exceptions=True
                    )
                    metrics.inc(VIC["stale_segment_dropped_total"], 1)
                elif control_wait_task in done and not send_task.done():
                    # A control frame is waiting; requeue speech deterministically and send control first.
                    send_task.cancel()
                    gate_task.cancel()
                    await asyncio.gather(
                        send_task, gate_task, control_wait_task, return_exceptions=True
                    )

                    ok = await outbound_q.put(
                        env,
                        evict=lambda existing: (
                            existing.plane == "speech"
                            and int(existing.priority) < int(env.priority)
                            and not (
                                getattr(existing.msg, "response_type", None) == "response"
                                and bool(getattr(existing.msg, "content_complete", False))
                            )
                        ),
                    )
                    if not ok:
                        metrics.inc("outbound_queue_dropped_total", 1)
                else:
                    gate_task.cancel()
                    control_wait_task.cancel()
                    await asyncio.gather(gate_task, control_wait_task, return_exceptions=True)
                    ok_send = await send_task
                    if not ok_send and shutdown_evt.is_set():
                        return
                continue

            ok_send = await _send_payload()
            if not ok_send and shutdown_evt.is_set():
                return
    except Exception:
        # Writer errors end the session by exiting; orchestrator watchdog should close.
        return
