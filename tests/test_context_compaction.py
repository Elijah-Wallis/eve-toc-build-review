from __future__ import annotations

from app.agent.compaction import CompactionContext, build_compaction_summary
from app.conversation_memory import ConversationMemory
from app.dialogue_policy import SlotState
from app.protocol import TranscriptUtterance


def test_compaction_summary_fields() -> None:
    txt = build_compaction_summary(
        CompactionContext(
            open_objectives="book_or_answer",
            pending_failures="none",
            active_guardrails="tool_grounding",
            last_green_baseline="vic_green",
        )
    )
    assert "open_objectives=" in txt
    assert "pending_failures=" in txt
    assert "active_guardrails=" in txt
    assert "last_green_baseline=" in txt


def test_conversation_memory_adds_compaction_context() -> None:
    mem = ConversationMemory(max_utterances=1, max_chars=20)
    transcript = [
        TranscriptUtterance(role="user", content="I want booking tomorrow afternoon"),
        TranscriptUtterance(role="agent", content="Sure"),
        TranscriptUtterance(role="user", content="My number is 972 555 1212"),
    ]
    view = mem.ingest_snapshot(transcript=transcript, slot_state=SlotState(intent="booking"))
    assert view.compacted is True
    assert "Compaction context:" in view.summary_blob
    assert "phone_last4=1212" in view.summary_blob
