from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path


def _load_module():
    p = Path(__file__).resolve().parents[1] / "scripts" / "revenue_ops_loop.py"
    spec = importlib.util.spec_from_file_location("revenue_ops_loop", p)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)  # type: ignore[attr-defined]
    return m


def _mk_call(*, call_id: str, user_line: str, user_t: float, latency_p50: float = 700.0) -> dict:
    return {
        "call_id": call_id,
        "call_status": "ended",
        "latency": {"llm": {"p50": latency_p50}},
        "transcript_object": [
            {
                "role": "agent",
                "content": "Hi, this is Cassidy from Eve Systems.",
                "words": [{"start": 0.1, "end": 0.8, "word": "Hi"}],
            },
            {
                "role": "user",
                "content": user_line,
                "words": [{"start": user_t - 0.2, "end": user_t, "word": "ok"}],
            },
        ],
    }


def test_build_summary_core_objective_metrics() -> None:
    m = _load_module()
    calls = [
        _mk_call(call_id="c1", user_line="send to info@clinic.com", user_t=9.0, latency_p50=600),
        _mk_call(call_id="c2", user_line="use manager@clinic.com", user_t=12.0, latency_p50=800),
        _mk_call(call_id="c3", user_line="is this sales?", user_t=7.0, latency_p50=1000),
    ]
    s = m.build_summary(calls)

    assert s.corpus_total_calls == 3
    assert s.answered_calls == 3
    assert s.email_captures == 2
    assert s.direct_email_captures == 1
    assert s.generic_email_captures == 1
    assert abs(s.email_capture_rate - (2 / 3)) < 1e-4
    assert s.first_response_latency_p95_ms is not None
    assert s.objection_counts["is_sales"] >= 1


def test_spoken_email_detection_is_counted_as_capture() -> None:
    m = _load_module()
    calls = [
        _mk_call(call_id="c1", user_line="it is sara at gmail dot com", user_t=10.5, latency_p50=550),
    ]
    s = m.build_summary(calls)
    assert s.email_captures == 1
    assert s.direct_email_captures == 1


def test_main_writes_report_files() -> None:
    m = _load_module()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        calls_dir = root / "calls"
        out_dir = root / "out"
        (calls_dir / "call_x").mkdir(parents=True)
        call = _mk_call(call_id="call_x", user_line="manager@clinic.com", user_t=8.0, latency_p50=500)
        (calls_dir / "call_x" / "call.json").write_text(json.dumps(call), encoding="utf-8")

        # exercise real CLI entry through argv mutation
        orig_argv = sys.argv
        try:
            sys.argv = [
                "revenue_ops_loop.py",
                "--calls-dir",
                str(calls_dir),
                "--out-dir",
                str(out_dir),
            ]
            rc2 = m.main()
        finally:
            sys.argv = orig_argv

        assert rc2 == 0
        latest = json.loads((out_dir / "latest.json").read_text(encoding="utf-8"))
        assert latest["summary"]["email_captures"] == 1
        assert latest["summary"]["first_response_latency_band"] == "excellent"
        assert "recommended_actions" in latest
