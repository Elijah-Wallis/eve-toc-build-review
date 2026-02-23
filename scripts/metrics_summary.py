from __future__ import annotations

import argparse
import math
import re
import urllib.request
from pathlib import Path


_TYPE_RE = re.compile(r"^#\s*TYPE\s+([a-zA-Z_:][a-zA-Z0-9_:]*)\s+(counter|gauge|histogram)\s*$")
_SAMPLE_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([-+]?[0-9]+(?:\.[0-9]+)?)$")
_LE_RE = re.compile(r'le="([^"]+)"')


def _fetch_metrics_text(*, metrics_url: str, metrics_file: str | None) -> str:
    if metrics_file:
        return Path(metrics_file).read_text(encoding="utf-8")
    with urllib.request.urlopen(metrics_url, timeout=5) as resp:
        return resp.read().decode("utf-8")


def parse_prometheus_text(text: str) -> tuple[dict[str, float], dict[str, float], dict[str, dict[str, float]]]:
    types: dict[str, str] = {}
    counters: dict[str, float] = {}
    gauges: dict[str, float] = {}
    hist_buckets: dict[str, dict[str, float]] = {}

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m_type = _TYPE_RE.match(line)
        if m_type:
            types[m_type.group(1)] = m_type.group(2)
            continue
        if line.startswith("#"):
            continue

        m_sample = _SAMPLE_RE.match(line)
        if not m_sample:
            continue
        name = m_sample.group(1)
        labels = m_sample.group(2) or ""
        value = float(m_sample.group(3))

        if name.endswith("_bucket"):
            base = name[: -len("_bucket")]
            m_le = _LE_RE.search(labels)
            if m_le is None:
                continue
            le = m_le.group(1)
            hist_buckets.setdefault(base, {})[le] = value
            continue

        t = types.get(name, "")
        if t == "counter":
            counters[name] = value
        elif t == "gauge":
            gauges[name] = value

    return counters, gauges, hist_buckets


def histogram_quantile_from_buckets(buckets: dict[str, float], q: float) -> float | None:
    if not buckets:
        return None
    items: list[tuple[float, float]] = []
    inf_count: float | None = None
    for le_str, count in buckets.items():
        if le_str == "+Inf":
            inf_count = float(count)
            continue
        try:
            items.append((float(le_str), float(count)))
        except Exception:
            continue
    items.sort(key=lambda x: x[0])
    if inf_count is None:
        if not items:
            return None
        inf_count = items[-1][1]
    if inf_count <= 0:
        return None

    target = max(1.0, math.ceil(float(q) * float(inf_count)))
    for le, cumulative in items:
        if cumulative >= target:
            return le
    # If only +Inf satisfies the quantile target, clamp to the highest finite bucket.
    if items:
        return items[-1][0]
    return None


def _fmt(v: float | int | None) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return f"{v:.3f}"
    return str(v)


def summarize(*, counters: dict[str, float], gauges: dict[str, float], hist: dict[str, dict[str, float]]) -> str:
    k_ping = "keepalive_ping_pong_queue_delay_ms"
    k_cancel = "vic_barge_in_cancel_latency_ms"

    ping_p95 = histogram_quantile_from_buckets(hist.get(k_ping, {}), 0.95)
    ping_p99 = histogram_quantile_from_buckets(hist.get(k_ping, {}), 0.99)
    cancel_p95 = histogram_quantile_from_buckets(hist.get(k_cancel, {}), 0.95)
    cancel_p99 = histogram_quantile_from_buckets(hist.get(k_cancel, {}), 0.99)

    skills_invocations = float(counters.get("skills_invocations_total", 0))
    skills_hits = float(counters.get("skills_hit_total", 0))
    skills_hit_rate = None
    if skills_invocations > 0:
        skills_hit_rate = (skills_hits / skills_invocations) * 100.0

    lines = [
        f"keepalive.ping_pong_queue_delay_ms p95={_fmt(ping_p95)} p99={_fmt(ping_p99)}",
        "keepalive.ping_pong_write_timeout_total="
        + _fmt(counters.get("keepalive_ping_pong_write_timeout_total", 0)),
        "ws.write_timeout_total=" + _fmt(counters.get("ws_write_timeout_total", 0)),
        f"vic.barge_in_cancel_latency_ms p95={_fmt(cancel_p95)} p99={_fmt(cancel_p99)}",
        "memory.transcript_chars_current="
        + _fmt(gauges.get("memory_transcript_chars_current", 0)),
        "memory.transcript_utterances_current="
        + _fmt(gauges.get("memory_transcript_utterances_current", 0)),
        "skills.invocations_total=" + _fmt(counters.get("skills_invocations_total", 0)),
        "skills.hit_total=" + _fmt(counters.get("skills_hit_total", 0)),
        "skills.hit_rate_pct=" + _fmt(skills_hit_rate),
        "skills.error_total=" + _fmt(counters.get("skills_error_total", 0)),
        "shell.exec_total=" + _fmt(counters.get("shell_exec_total", 0)),
        "shell.exec_denied_total=" + _fmt(counters.get("shell_exec_denied_total", 0)),
        "shell.exec_timeout_total=" + _fmt(counters.get("shell_exec_timeout_total", 0)),
        "self_improve.cycles_total=" + _fmt(counters.get("self_improve_cycles_total", 0)),
        "self_improve.proposals_total=" + _fmt(counters.get("self_improve_proposals_total", 0)),
        "self_improve.applies_total=" + _fmt(counters.get("self_improve_applies_total", 0)),
        "self_improve.blocked_on_gates_total="
        + _fmt(counters.get("self_improve_blocked_on_gates_total", 0)),
        "context.compactions_total=" + _fmt(counters.get("context_compactions_total", 0)),
        "context.compaction_tokens_saved_total="
        + _fmt(counters.get("context_compaction_tokens_saved_total", 0)),
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Print key Retell WS Brain SLO metrics summary from /metrics.")
    ap.add_argument(
        "--metrics-url",
        type=str,
        default="http://127.0.0.1:8080/metrics",
        help="Prometheus metrics URL.",
    )
    ap.add_argument(
        "--metrics-file",
        type=str,
        default="",
        help="Optional local metrics text file; if set, metrics-url is ignored.",
    )
    args = ap.parse_args()

    text = _fetch_metrics_text(metrics_url=args.metrics_url, metrics_file=(args.metrics_file or None))
    counters, gauges, hists = parse_prometheus_text(text)
    print(summarize(counters=counters, gauges=gauges, hist=hists))


if __name__ == "__main__":
    main()
