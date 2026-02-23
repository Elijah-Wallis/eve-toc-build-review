from __future__ import annotations

import re

from app.speech_planner import dash_pause, micro_chunk_text


def test_retell_dash_pause_helper_format() -> None:
    assert dash_pause(units=0) == ""
    assert dash_pause(units=1) == " - "
    assert dash_pause(units=3) == " -  -  - "


def test_retell_read_slow_digits_formatting_spacing() -> None:
    segs = micro_chunk_text(
        text="Just to confirm-last four are 4567, right?",
        max_expected_ms=1200,
        pace_ms_per_char=20,
        purpose="CONFIRM",
        interruptible=True,
        requires_tool_evidence=False,
        tool_evidence_ids=[],
    )
    ssml = " ".join(s.ssml for s in segs)
    assert "<break" not in ssml
    assert "4 - 5 - 6 - 7" in ssml

    # Spacing correctness: always space-dash-space between digits.
    assert re.search(r"4\s-\s5\s-\s6\s-\s7", ssml) is not None
    assert "--" not in ssml
    assert re.search(r"\d-\d", ssml) is None, "dash separators must be spaced"
