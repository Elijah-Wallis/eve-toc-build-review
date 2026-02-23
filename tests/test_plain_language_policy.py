from __future__ import annotations

from app.dialogue_policy import SlotState, TranscriptUtterance, decide_action
from app.safety_policy import evaluate_user_text
from app.voice_guard import readability_grade


def test_dialogue_policy_messages_are_plain() -> None:
    st = SlotState(intent="booking")
    action = decide_action(
        state=st,
        transcript=[TranscriptUtterance(role="user", content="I need an appointment")],
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
    )
    if "message" in action.payload:
        msg = str(action.payload.get("message", ""))
        assert msg
        assert readability_grade(msg) <= 8
        assert len(msg.split()) <= 18
    else:
        # Repair/confirm-first flows are acceptable as long as they avoid jargon.
        assert action.action_type in {"Repair", "Confirm", "Ask"}


def test_safety_policy_clinical_boundary_is_plain() -> None:
    res = evaluate_user_text("What dosage should I take?", clinic_name="Clinic")
    assert res.kind == "clinical"
    assert readability_grade(res.message) <= 8
    low = res.message.lower()
    assert "consult" not in low
    assert "book" in low or "visit" in low
