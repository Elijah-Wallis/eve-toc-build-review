from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_metrics_summary_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "metrics_summary.py"
    spec = importlib.util.spec_from_file_location("metrics_summary", str(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_metrics_summary_parses_and_formats_required_fields() -> None:
    mod = _load_metrics_summary_module()
    prom = """
# TYPE keepalive_ping_pong_queue_delay_ms histogram
keepalive_ping_pong_queue_delay_ms_bucket{le="100"} 5
keepalive_ping_pong_queue_delay_ms_bucket{le="200"} 8
keepalive_ping_pong_queue_delay_ms_bucket{le="+Inf"} 10
keepalive_ping_pong_queue_delay_ms_sum 1400
keepalive_ping_pong_queue_delay_ms_count 10
# TYPE vic_barge_in_cancel_latency_ms histogram
vic_barge_in_cancel_latency_ms_bucket{le="150"} 6
vic_barge_in_cancel_latency_ms_bucket{le="250"} 9
vic_barge_in_cancel_latency_ms_bucket{le="+Inf"} 10
vic_barge_in_cancel_latency_ms_sum 1700
vic_barge_in_cancel_latency_ms_count 10
# TYPE keepalive_ping_pong_write_timeout_total counter
keepalive_ping_pong_write_timeout_total 3
# TYPE ws_write_timeout_total counter
ws_write_timeout_total 4
# TYPE memory_transcript_chars_current gauge
memory_transcript_chars_current 123
# TYPE memory_transcript_utterances_current gauge
memory_transcript_utterances_current 12
# TYPE skills_invocations_total counter
skills_invocations_total 4
# TYPE skills_hit_total counter
skills_hit_total 3
# TYPE skills_error_total counter
skills_error_total 1
# TYPE shell_exec_total counter
shell_exec_total 8
# TYPE shell_exec_denied_total counter
shell_exec_denied_total 2
# TYPE shell_exec_timeout_total counter
shell_exec_timeout_total 1
# TYPE self_improve_cycles_total counter
self_improve_cycles_total 5
# TYPE self_improve_proposals_total counter
self_improve_proposals_total 4
# TYPE self_improve_applies_total counter
self_improve_applies_total 1
# TYPE self_improve_blocked_on_gates_total counter
self_improve_blocked_on_gates_total 2
# TYPE context_compactions_total counter
context_compactions_total 9
# TYPE context_compaction_tokens_saved_total counter
context_compaction_tokens_saved_total 8800
""".strip()
    counters, gauges, hist = mod.parse_prometheus_text(prom)
    out = mod.summarize(counters=counters, gauges=gauges, hist=hist)

    assert "keepalive.ping_pong_queue_delay_ms p95=200 p99=200" in out
    assert "keepalive.ping_pong_write_timeout_total=3" in out
    assert "ws.write_timeout_total=4" in out
    assert "vic.barge_in_cancel_latency_ms p95=250 p99=250" in out
    assert "memory.transcript_chars_current=123" in out
    assert "memory.transcript_utterances_current=12" in out
    assert "skills.invocations_total=4" in out
    assert "skills.hit_total=3" in out
    assert "skills.hit_rate_pct=75" in out
    assert "skills.error_total=1" in out
    assert "shell.exec_total=8" in out
    assert "shell.exec_denied_total=2" in out
    assert "shell.exec_timeout_total=1" in out
    assert "self_improve.cycles_total=5" in out
    assert "self_improve.proposals_total=4" in out
    assert "self_improve.applies_total=1" in out
    assert "self_improve.blocked_on_gates_total=2" in out
    assert "context.compactions_total=9" in out
    assert "context.compaction_tokens_saved_total=8800" in out
