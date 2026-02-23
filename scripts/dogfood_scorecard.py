from __future__ import annotations

import argparse
import sys
from pathlib import Path

from metrics_summary import (
    _fetch_metrics_text,
    histogram_quantile_from_buckets,
    parse_prometheus_text,
)


def main() -> None:
    ap = argparse.ArgumentParser(description="Dogfood scorecard for Retell Brain quality gates.")
    ap.add_argument("--metrics-url", default="http://127.0.0.1:8080/metrics")
    ap.add_argument("--metrics-file", default="")
    args = ap.parse_args()

    text = _fetch_metrics_text(metrics_url=args.metrics_url, metrics_file=(args.metrics_file or None))
    counters, gauges, hists = parse_prometheus_text(text)

    ack_p95 = histogram_quantile_from_buckets(hists.get("vic_turn_final_to_ack_segment_ms", {}), 0.95)
    first_p95 = histogram_quantile_from_buckets(hists.get("vic_turn_final_to_first_segment_ms", {}), 0.95)
    cancel_p95 = histogram_quantile_from_buckets(hists.get("vic_barge_in_cancel_latency_ms", {}), 0.95)

    checks = [
        ("ACK p95 <= 300ms", (ack_p95 is not None and ack_p95 <= 300), ack_p95),
        ("First-content p95 <= 700ms", (first_p95 is not None and first_p95 <= 700), first_p95),
        ("Barge-in cancel p95 <= 250ms", (cancel_p95 is not None and cancel_p95 <= 250), cancel_p95),
        (
            "Reasoning leakage == 0",
            int(counters.get("voice_reasoning_leak_total", 0)) == 0,
            int(counters.get("voice_reasoning_leak_total", 0)),
        ),
        (
            "Jargon violations == 0",
            int(counters.get("voice_jargon_violation_total", 0)) == 0,
            int(counters.get("voice_jargon_violation_total", 0)),
        ),
        (
            "Replay mismatches == 0",
            int(counters.get("vic_replay_hash_mismatch_total", 0)) == 0,
            int(counters.get("vic_replay_hash_mismatch_total", 0)),
        ),
    ]

    print("Dogfood Scorecard")
    print("=================")
    failed = False
    for name, ok, value in checks:
        status = "PASS" if ok else "FAIL"
        if not ok:
            failed = True
        print(f"{status:4}  {name:32} value={value}")

    print("\nCurrent Memory")
    print("--------------")
    print(f"transcript_chars_current={int(gauges.get('memory_transcript_chars_current', 0))}")
    print(f"transcript_utterances_current={int(gauges.get('memory_transcript_utterances_current', 0))}")
    print("\nOpenClaw Runtime Expansion")
    print("--------------------------")
    skills_inv = int(counters.get("skills_invocations_total", 0))
    skills_hit = int(counters.get("skills_hit_total", 0))
    skills_err = int(counters.get("skills_error_total", 0))
    if skills_inv > 0:
        skills_hit_rate = (float(skills_hit) / float(skills_inv)) * 100.0
        skills_hit_rate_str = f"{skills_hit_rate:.1f}%"
    else:
        skills_hit_rate_str = "n/a"
    print(f"skills.invocations_total={skills_inv}")
    print(f"skills.hit_total={skills_hit}")
    print(f"skills.hit_rate_pct={skills_hit_rate_str}")
    print(f"skills.error_total={skills_err}")
    print(f"shell.exec_total={int(counters.get('shell_exec_total', 0))}")
    print(f"shell.exec_denied_total={int(counters.get('shell_exec_denied_total', 0))}")
    print(f"shell.exec_timeout_total={int(counters.get('shell_exec_timeout_total', 0))}")
    print(f"self_improve.cycles_total={int(counters.get('self_improve_cycles_total', 0))}")
    print(f"self_improve.proposals_total={int(counters.get('self_improve_proposals_total', 0))}")
    print(f"self_improve.applies_total={int(counters.get('self_improve_applies_total', 0))}")
    print(
        "self_improve.blocked_on_gates_total="
        + str(int(counters.get("self_improve_blocked_on_gates_total", 0)))
    )
    print(f"context.compactions_total={int(counters.get('context_compactions_total', 0))}")
    print(
        "context.compaction_tokens_saved_total="
        + str(int(counters.get("context_compaction_tokens_saved_total", 0)))
    )

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
