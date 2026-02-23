from __future__ import annotations

import asyncio

from app.config import BrainConfig
from app.conversation_memory import ConversationMemory
from app.dialogue_policy import SlotState
from app.metrics import VIC
from app.protocol import TranscriptUtterance

from tests.harness.transport_harness import HarnessSession


def _long_transcript() -> list[TranscriptUtterance]:
    out: list[TranscriptUtterance] = []
    for i in range(30):
        out.append(TranscriptUtterance(role="user", content=f"I want to book an appointment {i}"))
        out.append(TranscriptUtterance(role="agent", content=f"Sure, what time works best {i}?"))
    out.append(TranscriptUtterance(role="user", content="My phone number is 972-555-1234 and afternoons are best."))
    return out


def test_conversation_memory_compaction_is_bounded_and_deterministic() -> None:
    transcript = _long_transcript()
    slot_state = SlotState(intent="booking", phone="9725551234")

    m1 = ConversationMemory(max_utterances=6, max_chars=220)
    v1 = m1.ingest_snapshot(transcript=transcript, slot_state=slot_state)

    m2 = ConversationMemory(max_utterances=6, max_chars=220)
    v2 = m2.ingest_snapshot(transcript=transcript, slot_state=slot_state)

    assert v1.compacted is True
    assert v1.summary_blob == v2.summary_blob
    assert v1.utterances_current <= 6
    assert v1.chars_current <= 220
    assert "9725551234" not in v1.summary_blob
    assert "phone_last4=1234" in v1.summary_blob


def test_compaction_keeps_replay_determinism() -> None:
    async def run_once() -> tuple[str, int]:
        session = await HarnessSession.start(
            session_id="compact-replay",
            cfg=BrainConfig(
                speak_first=False,
                retell_auto_reconnect=False,
                idle_timeout_ms=60000,
                transcript_max_utterances=6,
                transcript_max_chars=220,
            ),
        )
        try:
            await session.recv_outbound()
            await session.recv_outbound()
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [u.model_dump() for u in _long_transcript()],
                }
            )
            for _ in range(200):
                await asyncio.sleep(0)
                if any(p.epoch == 1 for p in session.orch.speech_plans):
                    break
            assert session.metrics.get(VIC["memory_transcript_compactions_total"]) >= 1
            return session.trace.replay_digest(), session.metrics.get(VIC["memory_transcript_compactions_total"])
        finally:
            await session.stop()

    d1, c1 = asyncio.run(run_once())
    d2, c2 = asyncio.run(run_once())
    assert d1 == d2
    assert c1 == c2
