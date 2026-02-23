from __future__ import annotations

from app.prom_export import PromExporter


def test_prom_export_renders_gauge_counter_histogram() -> None:
    exp = PromExporter(ms_buckets=(100, 500))
    exp.inc("ws.write_timeout_total", 2)
    exp.observe("keepalive.ping_pong_queue_delay_ms", 120)
    exp.observe("keepalive.ping_pong_queue_delay_ms", 700)
    exp.set("memory.transcript_chars_current", 4321)

    text = exp.render()
    assert "# TYPE ws_write_timeout_total counter" in text
    assert "ws_write_timeout_total 2" in text
    assert "# TYPE keepalive_ping_pong_queue_delay_ms histogram" in text
    assert 'keepalive_ping_pong_queue_delay_ms_bucket{le="100"} 0' in text
    assert 'keepalive_ping_pong_queue_delay_ms_bucket{le="500"} 1' in text
    assert 'keepalive_ping_pong_queue_delay_ms_bucket{le="+Inf"} 2' in text
    assert "# TYPE memory_transcript_chars_current gauge" in text
    assert "memory_transcript_chars_current 4321" in text
