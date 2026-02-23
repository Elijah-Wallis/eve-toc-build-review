from __future__ import annotations

from app.outcome_schema import CallOutcome, detect_objection


def test_detect_objection_patterns() -> None:
    assert detect_objection("This is too expensive") == "price_shock"
    assert detect_objection("I am too busy for that time") == "timing_conflict"
    assert detect_objection("I am not sure this is real") == "trust_hesitation"
    assert detect_objection("I need this asap") == "urgency_pressure"
    assert detect_objection("hello") is None


def test_call_outcome_payload_stable() -> None:
    outcome = CallOutcome(
        call_id="c1",
        turn_id=1,
        epoch=1,
        intent="booking",
        action_type="OfferSlots",
        objection="timing_conflict",
        offered_slots_count=3,
        accepted=False,
        escalated=False,
        drop_off_point="",
        t_ms=123,
    )
    payload = outcome.to_payload()
    assert payload["call_id"] == "c1"
    assert payload["offered_slots_count"] == 3
    assert payload["objection"] == "timing_conflict"
