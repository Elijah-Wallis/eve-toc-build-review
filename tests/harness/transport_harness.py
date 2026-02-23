from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from dataclasses import dataclass
from typing import Any, Optional

from app.bounded_queue import BoundedDequeQueue
from app.clock import FakeClock, RealClock
from app.config import BrainConfig
from app.metrics import Metrics
from app.orchestrator import Orchestrator
from app.protocol import OutboundEvent, parse_outbound_json
from app.llm_client import LLMClient
from app.tools import ToolRegistry
from app.trace import TraceSink
from app.transport_ws import GateRef, Transport, socket_reader, socket_writer


class InMemoryTransport(Transport):
    def __init__(self) -> None:
        self._in: asyncio.Queue[str] = asyncio.Queue()
        self._out: asyncio.Queue[str] = asyncio.Queue()
        self._closed = asyncio.Event()
        # Test-only latch to deterministically pause/resume writer output.
        self.send_allowed = asyncio.Event()
        self.send_allowed.set()

    async def recv_text(self) -> str:
        return await self._in.get()

    async def send_text(self, text: str) -> None:
        await self.send_allowed.wait()
        await self._out.put(text)

    async def close(self, *, code: int = 1000, reason: str = "") -> None:
        self._closed.set()
        self.send_allowed.set()
        # Unblock recv if needed.
        await self._in.put("")

    async def push_inbound(self, raw_text: str) -> None:
        await self._in.put(raw_text)

    async def pop_outbound(self) -> str:
        return await self._out.get()

    def outbound_qsize(self) -> int:
        return self._out.qsize()


@dataclass
class HarnessSession:
    cfg: BrainConfig
    clock: FakeClock
    metrics: Metrics
    trace: TraceSink
    tools: ToolRegistry
    transport: InMemoryTransport
    inbound_q: BoundedDequeQueue
    outbound_q: BoundedDequeQueue
    shutdown_evt: asyncio.Event
    gate: GateRef
    orch: Orchestrator
    tasks: list[asyncio.Task[Any]]

    @staticmethod
    async def start(
        *,
        session_id: str = "s1",
        cfg: Optional[BrainConfig] = None,
        tool_latencies: Optional[dict[str, int]] = None,
        llm: Optional[LLMClient] = None,
        include_update_agent_on_start: bool = False,
        use_real_clock: bool = False,
    ) -> "HarnessSession":
        clock = RealClock() if use_real_clock else FakeClock(start_ms=0)
        metrics = Metrics()
        trace = TraceSink()
        cfg = cfg or BrainConfig(speak_first=False, retell_send_update_agent_on_connect=False)
        # Keep harness startup stable for existing tests: config + begin only.
        if cfg.retell_send_update_agent_on_connect and not include_update_agent_on_start:
            cfg = replace(cfg, retell_send_update_agent_on_connect=False)
        transport = InMemoryTransport()
        inbound_q = BoundedDequeQueue(maxsize=cfg.inbound_queue_max)
        outbound_q = BoundedDequeQueue(maxsize=cfg.outbound_queue_max)
        shutdown_evt = asyncio.Event()
        gate = GateRef(epoch=0, speak_gen=0)
        tools = ToolRegistry(session_id=session_id, clock=clock, latency_ms_by_tool=tool_latencies)
        orch = Orchestrator(
            session_id=session_id,
            call_id=session_id,
            config=cfg,
            clock=clock,
            metrics=metrics,
            trace=trace,
            inbound_q=inbound_q,
            outbound_q=outbound_q,
            shutdown_evt=shutdown_evt,
            gate=gate,
            tools=tools,
            llm=llm,
        )

        tasks: list[asyncio.Task[Any]] = []
        tasks.append(
            asyncio.create_task(
                socket_reader(
                    transport=transport,
                    inbound_q=inbound_q,
                    metrics=metrics,
                    shutdown_evt=shutdown_evt,
                    max_frame_bytes=cfg.ws_max_frame_bytes,
                )
            )
        )
        tasks.append(
            asyncio.create_task(
                socket_writer(
                    transport=transport,
                    outbound_q=outbound_q,
                    metrics=metrics,
                    shutdown_evt=shutdown_evt,
                    gate=gate,
                    clock=clock,
                    inbound_q=inbound_q,
                    ws_write_timeout_ms=cfg.ws_write_timeout_ms,
                    ws_close_on_write_timeout=cfg.ws_close_on_write_timeout,
                    ws_max_consecutive_write_timeouts=cfg.ws_max_consecutive_write_timeouts,
                )
            )
        )
        tasks.append(asyncio.create_task(orch.run()))

        # Let orchestrator start and enqueue initial config/BEGIN.
        await asyncio.sleep(0)

        return HarnessSession(
            cfg=cfg,
            clock=clock,
            metrics=metrics,
            trace=trace,
            tools=tools,
            transport=transport,
            inbound_q=inbound_q,
            outbound_q=outbound_q,
            shutdown_evt=shutdown_evt,
            gate=gate,
            orch=orch,
            tasks=tasks,
        )

    async def stop(self) -> None:
        # Prefer graceful shutdown so orchestrator can clean up internal wait-tasks deterministically.
        await self.orch.end_session(reason="harness_stop")
        await self.transport.close(code=1000, reason="harness_stop")
        self.shutdown_evt.set()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def send_inbound_obj(self, obj: dict[str, Any], *, expect_ack: bool = True) -> None:
        await self.transport.push_inbound(json.dumps(obj, separators=(",", ":"), sort_keys=True))
        # Yield until the orchestrator has had a chance to observe/process the event.
        # This keeps FakeClock-based tests deterministic even when they advance time in large jumps.
        await asyncio.sleep(0)

        itype = str(obj.get("interaction_type", ""))
        if itype in {"response_required", "reminder_required"}:
            target = int(obj.get("response_id", 0))
            for _ in range(1000):
                if self.gate.epoch == target:
                    break
                await asyncio.sleep(0)

            if self.gate.epoch != target:
                raise AssertionError(
                    f"orchestrator did not observe epoch={target} in time (epoch={self.gate.epoch})"
                )

            # Also wait for the ACK SpeechPlan, which implies the TurnHandler started and reached
            # its post-finalization path (critical for deterministic FakeClock jumps).
            if expect_ack:
                for _ in range(2000):
                    if any(p.epoch == target and p.reason == "ACK" for p in self.orch.speech_plans):
                        return
                    await asyncio.sleep(0)
                raise AssertionError(f"no ACK SpeechPlan observed for epoch={target}")

    async def recv_outbound(self) -> OutboundEvent:
        raw = await self.transport.pop_outbound()
        return parse_outbound_json(raw)
