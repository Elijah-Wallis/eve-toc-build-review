from __future__ import annotations

import re

from app.speech_planner import micro_chunk_text


_TRAILING_DASH_PAUSE = re.compile(r"(?:\s-\s)+\s*$")


def test_micro_chunking_max_duration_and_interrupt_points() -> None:
    text = (
        "Okay. We can help with scheduling, pricing questions, and basic policies. "
        "Tell me what you're looking for, and I'll point you in the right direction."
    )
    segs = micro_chunk_text(
        text=text,
        max_expected_ms=1200,
        pace_ms_per_char=20,
        purpose="CONTENT",
        interruptible=True,
        requires_tool_evidence=False,
        tool_evidence_ids=[],
        max_monologue_expected_ms=12000,
    )
    assert segs
    assert all(s.expected_duration_ms <= 1200 for s in segs)
    assert all(s.safe_interrupt_point for s in segs)

    # Retell pacing: default output should not contain SSML breaks.
    ssml = " ".join(s.ssml for s in segs)
    assert "<break" not in ssml

    # Default pause scope is PROTECTED_ONLY: no segment-boundary dash suffixes for generic content.
    trailing_pause_segments = [s for s in segs if _TRAILING_DASH_PAUSE.search(s.ssml or "")]
    assert len(trailing_pause_segments) == 0


def test_no_monologue_over_12s_without_checkin() -> None:
    # 800 chars at 20ms/char => ~16s expected duration. Requires check-in insertion.
    long = ("Here is some detailed information. " * 40).strip()
    segs = micro_chunk_text(
        text=long,
        max_expected_ms=1200,
        pace_ms_per_char=20,
        purpose="CONTENT",
        interruptible=True,
        requires_tool_evidence=False,
        tool_evidence_ids=[],
        max_monologue_expected_ms=12000,
    )
    assert any(s.purpose == "CLARIFY" for s in segs), "expected a check-in/clarifier segment"
    assert all(s.expected_duration_ms <= 1200 for s in segs)


def test_micro_chunking_preserves_word_boundaries_across_segments() -> None:
    # Regression guard: Retell concatenates streaming chunks exactly as sent. If we split on word
    # boundaries without preserving spaces between segments, the transcript/audio becomes run-on
    # (e.g. "thisor", "Eve.Is"). We enforce deterministic stitching in micro_chunk_text().
    text = "Should I archive this or send a short report to your manager inbox now?"
    segs = micro_chunk_text(
        text=text,
        max_expected_ms=200,  # force many small segments deterministically
        pace_ms_per_char=30,
        purpose="CONTENT",
        interruptible=True,
        requires_tool_evidence=False,
        tool_evidence_ids=[],
        markup_mode="RAW_TEXT",
        dash_pause_scope="PROTECTED_ONLY",
    )
    assert len(segs) > 5
    stitched = "".join(s.ssml for s in segs)
    norm = re.sub(r"\s+", " ", stitched).strip()
    assert norm == text
    assert "thisor" not in stitched
