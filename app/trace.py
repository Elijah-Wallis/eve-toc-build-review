from __future__ import annotations

import asyncio
from collections import deque
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_payload(obj: Any) -> str:
    # Canonical JSON to make hashing stable for replay.
    blob = json.dumps(obj, separators=(",", ":"), sort_keys=True, ensure_ascii=True).encode(
        "utf-8"
    )
    return _sha256_hex(blob)


def hash_segment(ssml: str, purpose: str, epoch: int, turn_id: int) -> str:
    blob = f"{epoch}|{turn_id}|{purpose}|{ssml}".encode("utf-8")
    return _sha256_hex(blob)


@dataclass(frozen=True, slots=True)
class TraceEvent:
    seq: int
    t_ms: int
    session_id: str
    call_id: str
    turn_id: int
    epoch: int
    ws_state: str
    conv_state: str
    event_type: str
    payload_hash: str
    segment_hash: Optional[str] = None


class TraceSink:
    def __init__(self, *, max_events: int = 20000) -> None:
        self._seq = 0
        self._events = deque(maxlen=int(max_events))
        self._cv = asyncio.Condition()
        self.schema_violations_total = 0

    @property
    def events(self) -> list[TraceEvent]:
        return list(self._events)

    async def emit(
        self,
        *,
        t_ms: int,
        session_id: str,
        call_id: str,
        turn_id: int,
        epoch: int,
        ws_state: str,
        conv_state: str,
        event_type: str,
        payload_obj: Any,
        segment_hash: Optional[str] = None,
    ) -> None:
        payload_hash = hash_payload(payload_obj)

        self._seq += 1
        ev = TraceEvent(
            seq=self._seq,
            t_ms=int(t_ms),
            session_id=session_id,
            call_id=call_id,
            turn_id=int(turn_id),
            epoch=int(epoch),
            ws_state=ws_state,
            conv_state=conv_state,
            event_type=event_type,
            payload_hash=payload_hash,
            segment_hash=segment_hash,
        )
        if not self._validate(ev):
            self.schema_violations_total += 1

        async with self._cv:
            self._events.append(ev)
            self._cv.notify_all()

    async def wait_for_len(self, n: int) -> None:
        async with self._cv:
            while len(self._events) < n:
                await self._cv.wait()

    async def wait_for_event_type(self, event_type: str) -> TraceEvent:
        async with self._cv:
            while True:
                for ev in self._events:
                    if ev.event_type == event_type:
                        return ev
                await self._cv.wait()

    def replay_digest(self) -> str:
        blob = "|".join(
            f"{e.seq}:{e.t_ms}:{e.session_id}:{e.call_id}:{e.turn_id}:{e.epoch}:{e.ws_state}:{e.conv_state}:{e.event_type}:{e.payload_hash}:{e.segment_hash or ''}"
            for e in self._events
        ).encode("utf-8")
        return _sha256_hex(blob)

    def _validate(self, ev: TraceEvent) -> bool:
        if ev.seq <= 0:
            return False
        if ev.t_ms < 0:
            return False
        if not ev.session_id:
            return False
        if not ev.call_id:
            return False
        if ev.turn_id < 0:
            return False
        if ev.epoch < 0:
            return False
        if not ev.ws_state:
            return False
        if not ev.conv_state:
            return False
        if not ev.event_type:
            return False
        if not ev.payload_hash:
            return False
        if ev.segment_hash is not None and not ev.segment_hash:
            return False
        return True
