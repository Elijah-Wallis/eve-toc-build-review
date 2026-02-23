from __future__ import annotations

import asyncio

from app.dialogue_policy import DialogueAction
from app.metrics import VIC
from app.objection_library import sort_slots_by_acceptance
from app.playbook_policy import apply_playbook
from tests.harness.transport_harness import HarnessSession


def test_playbook_applies_on_price_objection_for_ask() -> None:
    action = DialogueAction(action_type="Ask", payload={"message": "How can I help?"}, tool_requests=[])
    result = apply_playbook(action=action, objection="price_shock", prior_attempts=0)
    assert result.applied is True
    assert result.matched_pattern == "price_shock"
    assert result.action.action_type == "Ask"
    msg = str(result.action.payload.get("message", "")).lower()
    assert "price" in msg


def test_playbook_noop_without_objection() -> None:
    action = DialogueAction(action_type="OfferSlots", payload={}, tool_requests=[])
    result = apply_playbook(action=action, objection=None, prior_attempts=2)
    assert result.applied is False
    assert result.action == action


def test_playbook_metrics_increment_in_orchestrator() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
        try:
            await session.recv_outbound()
            await session.recv_outbound()
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [
                        {"role": "user", "content": "This is too expensive. I need an appointment."}
                    ],
                }
            )
            for _ in range(100):
                await asyncio.sleep(0)
                if session.metrics.get(VIC["moat_objection_pattern_total"]) > 0:
                    break
            assert session.metrics.get(VIC["moat_objection_pattern_total"]) >= 1
            assert session.metrics.get(VIC["moat_playbook_hit_total"]) >= 1
            assert session.orch.outcomes and session.orch.outcomes[-1].objection == "price_shock"
        finally:
            await session.stop()

    asyncio.run(_run())


def test_slot_sorting_is_deterministic() -> None:
    slots = [
        "Thursday 4:40 PM",
        "Wednesday 2:15 PM",
        "Tuesday 11:30 AM",
        "Tuesday 9:00 AM",
    ]
    ranked = sort_slots_by_acceptance(slots)
    assert ranked[:3] == ["Tuesday 9:00 AM", "Tuesday 11:30 AM", "Wednesday 2:15 PM"]
