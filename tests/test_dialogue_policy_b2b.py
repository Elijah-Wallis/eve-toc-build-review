from __future__ import annotations

from app.dialogue_policy import SlotState, decide_action
from app.protocol import TranscriptUtterance


def _tx(user_text: str) -> list[TranscriptUtterance]:
    return [TranscriptUtterance(role="user", content=user_text)]


def test_b2b_policy_starts_with_b2b_permission_question() -> None:
    state = SlotState()
    action = decide_action(
        state=state,
        transcript=_tx("hello"),
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert action.action_type == "Ask"
    msg = str(action.payload.get("message", "")).lower()
    assert "bad time" in msg or "question" in msg


def test_b2b_policy_tiny_got_it_fragment_stays_silent() -> None:
    state = SlotState()
    first = decide_action(
        state=state,
        transcript=_tx("yep got it."),
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    second = decide_action(
        state=state,
        transcript=_tx("yep got it."),
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert first.action_type == "Noop"
    assert second.action_type == "Noop"
    assert first.payload.get("message", "") == ""
    assert first.payload.get("no_progress") is True
    assert first.payload.get("no_signal") is True
    assert second.payload.get("message", "") == ""
    assert second.payload.get("no_progress") is True
    assert second.payload.get("no_signal") is True


def test_b2b_policy_tiny_noise_token_stays_silent() -> None:
    state = SlotState()
    first = decide_action(
        state=state,
        transcript=_tx("um"),
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    second = decide_action(
        state=state,
        transcript=_tx("um"),
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert first.action_type == "Noop"
    assert second.action_type == "Noop"
    assert first.payload.get("message", "") == ""
    assert second.payload.get("message", "") == ""


def test_b2b_profile_close_now_moves_to_manager_email_capture() -> None:
    state = SlotState()
    action = decide_action(
        state=state,
        transcript=_tx("call me now"),
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert action.action_type == "Ask"
    msg = str(action.payload.get("message", "")).lower()
    assert "manager" in msg
    assert "email" in msg
    assert action.payload.get("slots_needed") == ["manager_email"]


def test_b2b_policy_handles_explicit_dnc() -> None:
    state = SlotState()
    action = decide_action(
        state=state,
        transcript=_tx("stop calling me"),
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert action.action_type == "EndCall"
    assert action.payload.get("end_call") is True
    assert action.payload.get("fast_path") is True
    assert str(action.payload.get("intent_signature", "")).startswith("b2b:OPEN:")


def test_b2b_policy_closes_when_email_collected() -> None:
    state = SlotState()
    action = decide_action(
        state=state,
        transcript=_tx("send it to manager@example.com"),
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert action.action_type == "EndCall"
    assert action.payload.get("email") == "manager@example.com"


def test_b2b_generic_email_gets_one_pushback_then_accepts() -> None:
    state = SlotState()
    first = decide_action(
        state=state,
        transcript=_tx("send it to info@clinic.com"),
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert first.action_type == "Ask"
    assert "direct manager email" in str(first.payload.get("message", "")).lower()
    assert first.payload.get("fast_path") is True
    assert str(first.payload.get("intent_signature", "")) == "b2b:generic_email:ask"

    second = decide_action(
        state=state,
        transcript=_tx("send it to info@clinic.com"),
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert second.action_type == "EndCall"
    assert second.payload.get("fast_path") is True
    assert str(second.payload.get("intent_signature", "")).startswith("b2b:OPEN:generic_email")
    assert second.payload.get("fast_path") is True
    assert str(second.payload.get("intent_signature", "")).startswith("b2b:OPEN:generic_email")


def test_b2b_progression_remains_question_heavy() -> None:
    state = SlotState()

    def act(msg: str) -> str:
        action = decide_action(
            state=state,
            transcript=_tx(msg),
            needs_apology=False,
            safety_kind="ok",
            safety_message="",
            profile="b2b",
        )
        return str(action.payload.get("message", "")).strip()

    messages = [
        act("hello"),
        act("yes"),
        act("yes"),
        act("yes"),
        act("manager@example.com"),
    ]

    question_msgs = [m for m in messages if m.endswith("?")]
    # 4/5 are questions in this core funnel path (>= 80%).
    assert len(question_msgs) >= 4
    assert len(messages) == 5


def test_b2b_policy_relaxes_when_conversation_is_positive() -> None:
    state = SlotState()
    _ = decide_action(
        state=state,
        transcript=_tx("who are you?"),
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    # Repeated positive answers should relax into conservative mode.
    for _ in range(3):
        _ = decide_action(
            state=state,
            transcript=_tx("yes, send it"),
            needs_apology=False,
            safety_kind="ok",
            safety_message="",
            profile="b2b",
        )
    assert state.b2b_autonomy_mode == "conservative"


def test_b2b_policy_increases_assertiveness_on_repeated_objections() -> None:
    state = SlotState()
    _ = decide_action(
        state=state,
        transcript=_tx("hello"),
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )

    action = None
    for _ in range(4):
        action = decide_action(
            state=state,
            transcript=_tx("not interested"),
            needs_apology=False,
            safety_kind="ok",
            safety_message="",
            profile="b2b",
        )
    assert action is not None
    assert state.b2b_autonomy_mode in {"baseline", "assertive"}
    assert state.objection_pressure >= 2
    if state.b2b_autonomy_mode == "assertive":
        text = str(action.payload.get("message", "")).strip()
        # Assertive mode should not add robotic meta-prefixes ("Quick.", "Direct.") anymore.
        # Instead, enforce that the message stays short and ends as a single question.
        assert text.endswith("?")
        assert len(text.split()) <= 18


def test_b2b_policy_noop_noise_does_not_replay_on_repeated_inputs() -> None:
    state = SlotState(
        b2b_funnel_stage="OPEN",
        b2b_last_stage="OPEN",
        b2b_last_signal="NEW_CALL",
        b2b_last_user_signature="hello",
        b2b_no_signal_streak=1,
    )

    first = decide_action(
        state=state,
        transcript=_tx("..."),
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    second = decide_action(
        state=state,
        transcript=_tx("..."),
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )

    assert first.action_type == "Noop"
    assert second.action_type == "Noop"
    assert first.payload.get("message", "") == ""
    assert second.payload.get("message", "") == ""
    assert first.payload.get("no_progress") is True
    assert second.payload.get("no_progress") is True
    assert first.payload.get("no_signal") is True
    assert second.payload.get("no_signal") is True
