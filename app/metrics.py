from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class Metrics:
    counters: dict[str, int] = field(default_factory=dict)
    histograms: dict[str, list[int]] = field(default_factory=dict)
    gauges: dict[str, int] = field(default_factory=dict)

    def inc(self, name: str, value: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + value

    def observe(self, name: str, value: int) -> None:
        self.histograms.setdefault(name, []).append(int(value))

    def set(self, name: str, value: int) -> None:
        self.gauges[name] = int(value)

    def get(self, name: str) -> int:
        return int(self.counters.get(name, 0))

    def get_hist(self, name: str) -> list[int]:
        return list(self.histograms.get(name, []))

    def get_gauge(self, name: str) -> int:
        return int(self.gauges.get(name, 0))

    def percentile(self, name: str, p: float) -> int | None:
        values = sorted(self.histograms.get(name, []))
        if not values:
            return None
        if p <= 0:
            return values[0]
        if p >= 100:
            return values[-1]
        k = int(round((p / 100.0) * (len(values) - 1)))
        return values[k]

    def snapshot(self) -> dict[str, Any]:
        return {
            "counters": dict(self.counters),
            "histograms": {k: list(v) for k, v in self.histograms.items()},
            "gauges": dict(self.gauges),
        }


class CompositeMetrics:
    """
    Write-only metrics fanout.

    Used in production server to feed both per-session Metrics and a process-level exporter
    without changing existing unit/VIC tests (which use Metrics directly).
    """

    def __init__(self, *sinks: Any) -> None:
        self._sinks = [s for s in sinks if s is not None]

    def inc(self, name: str, value: int = 1) -> None:
        for s in self._sinks:
            s.inc(name, value)

    def observe(self, name: str, value: int) -> None:
        for s in self._sinks:
            s.observe(name, value)

    def set(self, name: str, value: int) -> None:
        for s in self._sinks:
            if hasattr(s, "set"):
                s.set(name, value)


VIC = {
    # Latency & pacing
    "turn_final_to_first_segment_ms": "vic.turn_final_to_first_segment_ms",
    "turn_final_to_ack_segment_ms": "vic.turn_final_to_ack_segment_ms",
    "tool_call_to_first_filler_ms": "vic.tool_call_to_first_filler_ms",
    "tool_call_total_ms": "vic.tool_call_total_ms",
    "segment_expected_duration_ms": "vic.segment_expected_duration_ms",
    "segment_count_per_turn": "vic.segment_count_per_turn",
    # Turn-taking / overlap
    "barge_in_cancel_latency_ms": "vic.barge_in_cancel_latency_ms",
    "overtalk_incidents_total": "vic.overtalk_incidents_total",
    "backchannel_detected_total": "vic.backchannel_detected_total",
    "backchannel_misclassified_total": "vic.backchannel_misclassified_total",
    "stale_segment_dropped_total": "vic.stale_segment_dropped_total",
    # Dialogue quality
    "repair_attempts_total": "vic.repair_attempts_total",
    "confirmations_total": "vic.confirmations_total",
    "reprompts_total": "vic.reprompts_total",
    "offered_slots_count": "vic.offered_slots_count",
    "user_requested_repeat_total": "vic.user_requested_repeat_total",
    # Truth/tool grounding
    "factual_segment_without_tool_evidence_total": "vic.factual_segment_without_tool_evidence_total",
    "tool_failures_total": "vic.tool_failures_total",
    "fallback_used_total": "vic.fallback_used_total",
    # Replayability
    "replay_hash_mismatch_total": "vic.replay_hash_mismatch_total",
    # Keepalive / control plane
    "keepalive_ping_pong_queue_delay_ms": "keepalive.ping_pong_queue_delay_ms",
    "keepalive_ping_pong_missed_deadline_total": "keepalive.ping_pong_missed_deadline_total",
    # Inbound queue management
    "inbound_queue_evictions_total": "inbound.queue_evictions_total",
    # WS backpressure / close control
    "ws_write_timeout_total": "ws.write_timeout_total",
    "ws_close_reason_total": "ws.close_reason_total",
    "keepalive_ping_pong_write_attempt_total": "keepalive.ping_pong_write_attempt_total",
    "keepalive_ping_pong_write_timeout_total": "keepalive.ping_pong_write_timeout_total",
    # Memory compaction
    "memory_transcript_compactions_total": "memory.transcript_compactions_total",
    "memory_transcript_chars_current": "memory.transcript_chars_current",
    "memory_transcript_utterances_current": "memory.transcript_utterances_current",
    # LLM factual phrasing guard
    "llm_fact_guard_fallback_total": "llm.fact_guard_fallback_total",
    # Skills runtime
    "skills_invocations_total": "skills.invocations_total",
    "skills_hit_total": "skills.hit_total",
    "skills_error_total": "skills.error_total",
    # Shell runtime
    "shell_exec_total": "shell.exec_total",
    "shell_exec_denied_total": "shell.exec_denied_total",
    "shell_exec_timeout_total": "shell.exec_timeout_total",
    # Self-improve loop
    "self_improve_cycles_total": "self_improve.cycles_total",
    "self_improve_proposals_total": "self_improve.proposals_total",
    "self_improve_applies_total": "self_improve.applies_total",
    "self_improve_blocked_on_gates_total": "self_improve.blocked_on_gates_total",
    # Context compaction
    "context_compactions_total": "context.compactions_total",
    "context_compaction_tokens_saved_total": "context.compaction_tokens_saved_total",
    # Voice quality guardrails
    "voice_reasoning_leak_total": "voice.reasoning_leak_total",
    "voice_jargon_violation_total": "voice.jargon_violation_total",
    "voice_readability_grade": "voice.readability_grade",
    # Moat telemetry
    "moat_playbook_hit_total": "moat.playbook_hit_total",
    "moat_objection_pattern_total": "moat.objection_pattern_total",
}
