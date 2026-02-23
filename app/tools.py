from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from .canary import rollout_enabled
from .clock import Clock
from .shell.executor import ShellExecutor


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    tool_call_id: str
    name: str
    arguments: dict[str, Any]
    started_at_ms: int
    completed_at_ms: int
    ok: bool
    content: str


ToolFn = Callable[[dict[str, Any]], Awaitable[str]]
EmitFn = Callable[[str, str, str], Awaitable[None]]


async def _run_with_timeout(
    clock: Clock,
    *,
    coro: Awaitable[str],
    deadline_ms: int,
) -> tuple[bool, str]:
    """
    Deterministic timeout based on Clock.sleep_ms(), not wall clock.
    Returns (ok, content_or_error).
    """

    # Anchor timeouts to an absolute deadline so tests can safely advance FakeClock even if
    # the coroutine hasn't yet reached its first sleep point.
    timeout_task = asyncio.create_task(clock.sleep_ms(deadline_ms - clock.now_ms()))
    work_task = asyncio.create_task(coro)
    done, pending = await asyncio.wait({timeout_task, work_task}, return_when=asyncio.FIRST_COMPLETED)

    if work_task in done and not work_task.cancelled():
        # Work completed first; stop the timeout task and drain it.
        if timeout_task in pending:
            timeout_task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        try:
            return True, str(work_task.result())
        except Exception as e:  # pragma: no cover (defensive)
            return False, f"tool_error:{type(e).__name__}"

    # Timed out.
    if work_task in pending:
        work_task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return False, "tool_timeout"


class ToolRegistry:
    def __init__(
        self,
        *,
        session_id: str,
        clock: Clock,
        latency_ms_by_tool: Optional[dict[str, int]] = None,
        metrics: Any | None = None,
        shell_executor: ShellExecutor | None = None,
        shell_tool_enabled: bool = False,
        shell_tool_canary_enabled: bool = False,
        shell_tool_canary_percent: int = 0,
    ) -> None:
        self._session_id = session_id
        self._clock = clock
        self._latency_ms_by_tool = dict(latency_ms_by_tool or {})
        self._tool_seq = 0
        self._metrics = metrics
        self._shell_executor = shell_executor
        self._shell_tool_enabled = bool(shell_tool_enabled)
        self._shell_tool_canary_enabled = bool(shell_tool_canary_enabled)
        self._shell_tool_canary_percent = int(shell_tool_canary_percent)
        self._tools: dict[str, ToolFn] = {
            "check_availability": self._check_availability,
            "get_pricing": self._get_pricing,
            "check_eligibility": self._check_eligibility,
            "clinic_policies": self._clinic_policies,
            "send_evidence_package": self._send_evidence_package,
            "mark_dnc_compliant": self._mark_dnc_compliant,
            "send_call_recording_followup": self._send_call_recording_followup,
            "log_call_outcome": self._log_call_outcome,
            "set_follow_up_plan": self._set_follow_up_plan,
            "run_shell_command": self._run_shell_command,
        }

    def _new_tool_call_id(self) -> str:
        # Deterministic, globally unique within a call/session.
        self._tool_seq += 1
        return f"{self._session_id}:tool:{self._tool_seq}"

    def set_latency_ms(self, name: str, ms: int) -> None:
        self._latency_ms_by_tool[name] = int(ms)

    def get_latency_ms(self, name: str) -> int:
        return int(self._latency_ms_by_tool.get(name, 0))

    def _normalize_tool_name(self, name: str) -> str:
        key = str(name or "").strip()
        if key.lower() == "mark_dnc":
            return "mark_dnc_compliant"
        return key.lower()

    async def invoke(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        timeout_ms: int,
        started_at_ms: Optional[int] = None,
        emit_invocation: Optional[Callable[[str, str, str], Awaitable[None]]] = None,
        emit_result: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ) -> ToolCallRecord:
        canonical_name = self._normalize_tool_name(name)
        if canonical_name not in self._tools:
            raise ValueError(f"unknown tool: {name}")

        tool_call_id = self._new_tool_call_id()
        started = int(started_at_ms) if started_at_ms is not None else self._clock.now_ms()

        args_json = json.dumps(arguments, separators=(",", ":"), sort_keys=True)
        if emit_invocation is not None:
            await emit_invocation(tool_call_id, canonical_name, args_json)

        ok, content = await self._invoke_impl(
            name=canonical_name,
            arguments=arguments,
            timeout_ms=timeout_ms,
            started_at_ms=started,
        )
        completed = self._clock.now_ms()

        if emit_result is not None:
            await emit_result(tool_call_id, content)

        return ToolCallRecord(
            tool_call_id=tool_call_id,
            name=canonical_name,
            arguments=dict(arguments),
            started_at_ms=started,
            completed_at_ms=completed,
            ok=ok,
            content=content,
        )

    async def _invoke_impl(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        timeout_ms: int,
        started_at_ms: int,
    ) -> tuple[bool, str]:
        async def work() -> str:
            latency = self.get_latency_ms(name)
            if latency > 0:
                # Anchor latency to the declared start time for determinism under FakeClock jumps.
                await self._clock.sleep_ms((started_at_ms + latency) - self._clock.now_ms())
            return await self._tools[name](arguments)

        return await _run_with_timeout(
            self._clock,
            coro=work(),
            deadline_ms=int(started_at_ms) + int(timeout_ms),
        )

    # ---------------------------------------------------------------------
    # Mock tools (deterministic)
    # ---------------------------------------------------------------------

    async def _check_availability(self, arguments: dict[str, Any]) -> str:
        requested_dt = str(arguments.get("requested_dt", "")).strip().lower()
        # Deterministic slot generation.
        if "sunday" in requested_dt:
            slots: list[str] = []
            return json.dumps({"slots": slots}, separators=(",", ":"), sort_keys=True)
        if "tomorrow" in requested_dt:
            slots = [
                "Tomorrow 9:00 AM",
                "Tomorrow 11:30 AM",
                "Tomorrow 3:15 PM",
                "Tomorrow 4:40 PM",
            ]
        else:
            slots = [
                "Tuesday 9:00 AM",
                "Tuesday 11:30 AM",
                "Wednesday 2:15 PM",
                "Thursday 4:40 PM",
                "Friday 10:10 AM",
            ]
        return json.dumps({"slots": slots}, separators=(",", ":"), sort_keys=True)

    async def _get_pricing(self, arguments: dict[str, Any]) -> str:
        service_id = str(arguments.get("service_id", "general"))
        # Deterministic pricing; must be treated as tool-grounded.
        if service_id == "general":
            return json.dumps({"service_id": service_id, "price_usd": 120}, separators=(",", ":"), sort_keys=True)
        return json.dumps({"service_id": service_id, "price_usd": 0}, separators=(",", ":"), sort_keys=True)

    async def _check_eligibility(self, arguments: dict[str, Any]) -> str:
        return json.dumps({"eligible": True}, separators=(",", ":"), sort_keys=True)

    async def _clinic_policies(self, arguments: dict[str, Any]) -> str:
        return json.dumps({"policies": "We can help schedule appointments and answer basic questions."}, separators=(",", ":"), sort_keys=True)

    async def _run_shell_command(self, arguments: dict[str, Any]) -> str:
        if self._metrics is not None:
            self._metrics.inc("shell.exec_total", 1)

        if not self._shell_tool_enabled:
            if self._metrics is not None:
                self._metrics.inc("shell.exec_denied_total", 1)
            return json.dumps({"ok": False, "error": "shell_tool_disabled"}, separators=(",", ":"), sort_keys=True)

        if self._shell_tool_canary_enabled and not rollout_enabled(self._session_id, self._shell_tool_canary_percent):
            if self._metrics is not None:
                self._metrics.inc("shell.exec_denied_total", 1)
            return json.dumps({"ok": False, "error": "shell_tool_not_in_canary"}, separators=(",", ":"), sort_keys=True)

        if self._shell_executor is None:
            if self._metrics is not None:
                self._metrics.inc("shell.exec_denied_total", 1)
            return json.dumps({"ok": False, "error": "shell_executor_missing"}, separators=(",", ":"), sort_keys=True)

        command = str(arguments.get("command", "")).strip()
        timeout_s = int(arguments.get("timeout_s", 20) or 20)
        prefer_hosted = bool(arguments.get("prefer_hosted", False))
        if not command:
            if self._metrics is not None:
                self._metrics.inc("shell.exec_denied_total", 1)
            return json.dumps({"ok": False, "error": "missing_command"}, separators=(",", ":"), sort_keys=True)

        result = await self._shell_executor.execute(command, timeout_s=max(1, timeout_s), prefer_hosted=prefer_hosted)
        if (result.reason or "") in {"timeout"}:
            if self._metrics is not None:
                self._metrics.inc("shell.exec_timeout_total", 1)
        if (result.reason or "").startswith("denied_") or (result.reason or "").startswith("not_in_allowlist") or (result.reason or "").startswith("interactive_"):
            if self._metrics is not None:
                self._metrics.inc("shell.exec_denied_total", 1)

        payload = {
            "ok": bool(result.ok),
            "runtime": result.runtime,
            "returncode": int(result.returncode),
            "reason": result.reason,
            "duration_ms": int(result.duration_ms),
            "stdout": (result.stdout or "")[:1200],
            "stderr": (result.stderr or "")[:1200],
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    async def _send_call_recording_followup(self, arguments: dict[str, Any]) -> str:
        tenant = str(arguments.get("tenant", "synthetic_medspa")).strip()
        campaign_id = str(arguments.get("campaign_id", "")).strip()
        clinic_id = str(arguments.get("clinic_id", "")).strip()
        lead_id = str(arguments.get("lead_id", "")).strip()
        call_id = str(arguments.get("call_id", "")).strip()
        to_number = str(arguments.get("to_number", "")).strip()
        call_recording_url = str(arguments.get("recording_url", arguments.get("call_recording_url", "") )).strip()
        recipient_email = str(arguments.get("recipient_email", "")).strip()
        recipient_phone = str(arguments.get("recipient_phone", "")).strip()
        reason = str(arguments.get("reason", "queued")).strip().lower() or "queued"
        channel = str(arguments.get("channel", arguments.get("channels", ""))).strip().lower() or "twilio_sms"
        next_step = str(arguments.get("next_step", "queued")).strip() or "queued"
        timestamp_ms = int(arguments.get("timestamp_ms", 0) or 0)
        if not timestamp_ms:
            timestamp_ms = int(time.time() * 1000)

        return json.dumps(
            {
                "ok": True,
                "tool": "send_call_recording_followup",
                "status": "acknowledged",
                "reason": reason,
                "tenant": tenant,
                "campaign_id": campaign_id,
                "clinic_id": clinic_id,
                "lead_id": lead_id,
                "call_id": call_id,
                "to_number": to_number,
                "recipient_phone": recipient_phone,
                "recipient_email": recipient_email,
                "channel": channel,
                "recording_url": call_recording_url,
                "next_step": next_step,
                "timestamp_ms": timestamp_ms,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    async def _send_evidence_package(self, arguments: dict[str, Any]) -> str:
        recipient_email = str(arguments.get("recipient_email", "")).strip()
        delivery_method = str(arguments.get("delivery_method", "EMAIL_ONLY")).strip()
        artifact_type = str(arguments.get("artifact_type", "FAILURE_LOG_PDF")).strip()

        if delivery_method not in {"EMAIL_ONLY", "EMAIL_AND_SMS"}:
            return json.dumps(
                {
                    "ok": False,
                    "tool": "send_evidence_package",
                    "error": "invalid_delivery_method",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        if artifact_type not in {"AUDIO_LINK", "FAILURE_LOG_PDF"}:
            return json.dumps(
                {
                    "ok": False,
                    "tool": "send_evidence_package",
                    "error": "invalid_artifact_type",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        if not recipient_email:
            return json.dumps(
                {
                    "ok": False,
                    "tool": "send_evidence_package",
                    "error": "missing_recipient_email",
                },
                sort_keys=True,
                separators=(",", ":"),
            )

        return json.dumps(
            {
                "ok": True,
                "tool": "send_evidence_package",
                "recipient_email": recipient_email,
                "delivery_method": delivery_method,
                "artifact_type": artifact_type,
                "status": "queued",
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    async def _mark_dnc_compliant(self, arguments: dict[str, Any]) -> str:
        reason = str(arguments.get("reason", "USER_REQUEST")).strip().upper()
        if reason not in {"USER_REQUEST", "WRONG_NUMBER", "HOSTILE"}:
            return json.dumps(
                {
                    "ok": False,
                    "tool": "mark_dnc_compliant",
                    "error": "invalid_reason",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        return json.dumps(
            {
                "ok": True,
                "tool": "mark_dnc_compliant",
                "reason": reason,
                "status": "dnc_recorded",
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    async def _log_call_outcome(self, arguments: dict[str, Any]) -> str:
        tenant = str(arguments.get("tenant", "synthetic_medspa")).strip()
        campaign_id = str(arguments.get("campaign_id", "")).strip()
        clinic_id = str(arguments.get("clinic_id", "")).strip()
        lead_id = str(arguments.get("lead_id", "")).strip()
        call_id = str(arguments.get("call_id", "")).strip()
        reason = str(arguments.get("reason", "queued")).strip().lower()
        next_step = str(arguments.get("next_step", "queued")).strip()
        if not next_step:
            next_step = "queued"
        timestamp_ms = int(arguments.get("timestamp_ms", 0) or 0)
        if not timestamp_ms:
            timestamp_ms = int(time.time() * 1000)

        return json.dumps(
            {
                "ok": True,
                "tool": "log_call_outcome",
                "status": "acknowledged",
                "reason": reason,
                "tenant": tenant,
                "campaign_id": campaign_id,
                "clinic_id": clinic_id,
                "lead_id": lead_id,
                "call_id": call_id,
                "next_step": next_step,
                "timestamp_ms": timestamp_ms,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    async def _set_follow_up_plan(self, arguments: dict[str, Any]) -> str:
        tenant = str(arguments.get("tenant", "synthetic_medspa")).strip()
        campaign_id = str(arguments.get("campaign_id", "")).strip()
        clinic_id = str(arguments.get("clinic_id", "")).strip()
        lead_id = str(arguments.get("lead_id", "")).strip()
        call_id = str(arguments.get("call_id", "")).strip()
        reason = str(arguments.get("reason", "queued")).strip().lower()
        next_step = str(arguments.get("next_step", "queued")).strip()
        if not next_step:
            next_step = "queued"
        timestamp_ms = int(arguments.get("timestamp_ms", 0) or 0)
        if not timestamp_ms:
            timestamp_ms = int(time.time() * 1000)

        return json.dumps(
            {
                "ok": True,
                "tool": "set_follow_up_plan",
                "status": "acknowledged",
                "reason": reason,
                "tenant": tenant,
                "campaign_id": campaign_id,
                "clinic_id": clinic_id,
                "lead_id": lead_id,
                "call_id": call_id,
                "next_step": next_step,
                "timestamp_ms": timestamp_ms,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
