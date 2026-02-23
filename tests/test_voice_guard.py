from __future__ import annotations

from app.metrics import Metrics, VIC
from app.voice_guard import enforce_plain_language, guard_user_text, readability_grade, sanitize_reasoning_leak


def test_sanitize_reasoning_leak_blocks_patterns() -> None:
    txt = "Let me think step by step. Here is my reasoning."
    out, changed = sanitize_reasoning_leak(txt)
    assert changed is True
    assert "reasoning" not in out.lower()
    assert "step by step" not in out.lower()


def test_plain_language_rewrites_jargon() -> None:
    txt = "We will facilitate a consult to optimize eligibility."
    out, changed = enforce_plain_language(txt)
    assert changed is True
    low = out.lower()
    assert "facilitate" not in low
    assert "consult" not in low
    assert "eligibility" not in low


def test_plain_language_blocks_intake_and_stress_test_terms() -> None:
    txt = "This is an intake stress-test of your capacity with an artifact."
    out, changed = enforce_plain_language(txt)
    assert changed is True
    low = out.lower()
    assert "intake" not in low
    assert "stress-test" not in low
    assert "stress test" not in low
    assert "capacity" not in low
    assert "artifact" not in low


def test_guard_metrics_and_readability() -> None:
    m = Metrics()
    out = guard_user_text(
        text="I am analyzing this. We will initiate a consult procedure.",
        metrics=m,
        plain_language_mode=True,
        no_reasoning_leak=True,
        jargon_blocklist_enabled=True,
    )
    assert out
    assert m.get_hist(VIC["voice_readability_grade"])
    assert readability_grade(out) <= 8
