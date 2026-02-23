from __future__ import annotations

from app.dialogue_policy import SlotState, decide_action
from app.protocol import TranscriptUtterance


def _u(role: str, content: str) -> TranscriptUtterance:
    return TranscriptUtterance(role=role, content=content)


def test_b2b_open_no_means_not_a_bad_time_proceed() -> None:
    # Opener: "Is this a bad time?" User: "No." => proceed (not rejection).
    st = SlotState(b2b_funnel_stage="OPEN")
    tx = [
        _u("agent", "Hi, this is Cassidy from Eve. Is this a bad time for one quick question?"),
        _u("user", "No."),
    ]
    act = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert act.action_type == "Ask"
    msg = str(act.payload.get("message", ""))
    assert "close" not in msg.lower()
    # ROUTING-stage question should appear after permission.
    assert "manager" in msg.lower()
    assert "email" in msg.lower()


def test_b2b_open_yes_means_bad_time_offer_close_or_send() -> None:
    st = SlotState(b2b_funnel_stage="OPEN")
    tx = [
        _u("agent", "Hi, this is Cassidy from Eve. Is this a bad time for one quick question?"),
        _u("user", "Yes."),
    ]
    act = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert act.action_type == "Ask"
    msg = str(act.payload.get("message", ""))
    assert "close" in msg.lower()
    assert "email" in msg.lower()


def test_b2b_routing_no_is_admin_block_not_rejection() -> None:
    st = SlotState(b2b_funnel_stage="ROUTING")
    tx = [
        _u("agent", "Are you the person handling manager routing, or should I use a routing inbox?"),
        _u("user", "No."),
    ]
    act = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert act.action_type == "Ask"
    msg = str(act.payload.get("message", ""))
    assert "inbox" in msg.lower()
    assert "close" not in msg.lower()


def test_b2b_no_email_objection_routes_to_inbox() -> None:
    st = SlotState(b2b_funnel_stage="ROUTING")
    tx = [
        _u("agent", "Quick question: what's the best way to get a short email to the manager?"),
        _u("user", "We don't give out emails."),
    ]
    act = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    msg = str(act.payload.get("message", "")).lower()
    assert act.action_type == "Ask"
    assert "inbox" in msg
    assert "archive" not in msg


def test_b2b_open_ambient_noise_with_whitespace_no_signal_noop() -> None:
    st = SlotState(b2b_funnel_stage="OPEN")
    tx = [
        _u("agent", "Hi, this is Cassidy with Eve. Is now a bad time for a quick question?"),
        _u("user", "   "),
    ]
    act = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert act.action_type == "Noop"
    assert act.payload.get("no_progress") is True
    assert bool(act.payload.get("no_signal"))


def test_b2b_open_noise_fragment_noop() -> None:
    st = SlotState(b2b_funnel_stage="OPEN")
    tx = [
        _u("agent", "Hi, this is Cassidy with Eve. Is now a bad time for a quick question?"),
        _u("user", "um"),
    ]
    act = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert act.action_type == "Noop"
    assert act.payload.get("no_progress") is True
    assert bool(act.payload.get("no_signal"))
    assert act.payload.get("intent_signature") == "b2b:OPEN:noise_only"

    again = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert again.action_type == "Noop"
    assert again.payload.get("intent_signature") in {
        "b2b:OPEN:noise_only",
        "b2b:OPEN:repeated_noise",
    }


def test_b2b_open_got_it_acknowledgement_noop() -> None:
    st = SlotState(b2b_funnel_stage="OPEN")
    tx = [
        _u("agent", "Hi, this is Cassidy with Eve. Is now a bad time for a quick question?"),
        _u("user", "Yep, got it."),
    ]
    act = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert act.action_type == "Noop"
    assert act.payload.get("no_progress") is True
    assert bool(act.payload.get("no_signal"))
    assert act.payload.get("message", "") == ""


def test_b2b_repeated_opener_signal_stays_silent() -> None:
    st = SlotState(
        b2b_funnel_stage="OPEN",
        b2b_last_stage="OPEN",
        b2b_last_signal="NEW_CALL",
        b2b_no_signal_streak=1,
    )
    tx = [
        _u("agent", "Hi, this is Cassidy with Eve. Is now a bad time for a quick question?"),
        _u("user", "..."),
    ]
    first = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert first.action_type == "Noop"
    assert first.payload.get("no_progress") is True


def test_b2b_repeated_short_noise_without_signature_progress_does_not_replay() -> None:
    st = SlotState()
    opener = _u("agent", "Hi, this is Cassidy with Eve. Is now a bad time for a quick question?")
    tx = [opener, _u("user", "um")]
    first = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    second = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert first.action_type == "Noop"
    assert second.action_type == "Noop"
    assert first.payload.get("message", "") == ""
    assert second.payload.get("message", "") == ""
    assert first.payload.get("no_signal") is True
    assert second.payload.get("no_signal") is True


def test_b2b_empty_noise_turns_stay_noop_and_do_not_replay_opener() -> None:
    st = SlotState(b2b_funnel_stage="OPEN")
    tx = [
        _u("agent", "Hi, this is Cassidy with Eve. Is now a bad time for a quick question?"),
        _u("user", "  "),
    ]
    first = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    second = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert first.action_type == "Noop"
    assert second.action_type == "Noop"
    assert first.payload.get("no_progress") is True
    assert second.payload.get("no_progress") is True
    assert first.payload.get("message", "") == ""
    assert second.payload.get("message", "") == ""


def test_b2b_intro_phrase_with_got_it_is_treated_as_no_signal() -> None:
    st = SlotState(b2b_funnel_stage="OPEN")
    tx = [
        _u("agent", "Hi, this is Cassidy with Eve. Is now a bad time for a quick question?"),
        _u("user", "Hey, this is Cassidy from Eve, yep got it."),
    ]
    first = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    second = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    assert first.action_type == "Noop"
    assert second.action_type == "Noop"
    assert first.payload.get("message", "") == ""
    assert second.payload.get("message", "") == ""
    assert first.payload.get("no_signal") is True
    assert second.payload.get("no_signal") is True


def test_b2b_open_hello_does_not_repeat_opener() -> None:
    st = SlotState(b2b_funnel_stage="OPEN")
    tx = [
        _u("agent", "Hi, this is Cassidy with Eve. Is now a bad time for a quick question?"),
        _u("user", "Hello?"),
    ]
    act = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    msg = str(act.payload.get("message", ""))
    # Should move forward to routing, not re-ask "bad time" again.
    assert "bad time" not in msg.lower()
    assert "email" in msg.lower()


def test_b2b_open_not_a_bad_time_phrase_proceeds() -> None:
    st = SlotState(b2b_funnel_stage="OPEN")
    tx = [
        _u("agent", "Hi, this is Cassidy with Eve. Is now a bad time for a quick question?"),
        _u("user", "That is not a bad time, go ahead."),
    ]
    act = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    msg = str(act.payload.get("message", "")).lower()
    assert act.action_type == "Ask"
    assert "manager" in msg
    assert "email" in msg
    assert "close" not in msg


def test_b2b_no_email_and_soft_rejection_favors_inbox_request() -> None:
    st = SlotState(b2b_funnel_stage="OPEN")
    tx = [
        _u("agent", "Quick question: what's the best way to get a short email to the manager?"),
        _u("user", "We don't give out emails, not interested."),
    ]
    act = decide_action(
        state=st,
        transcript=tx,
        needs_apology=False,
        safety_kind="ok",
        safety_message="",
        profile="b2b",
    )
    msg = str(act.payload.get("message", "")).lower()
    assert act.action_type == "Ask"
    assert "inbox" in msg
