from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .dialogue_policy import DialogueAction
from .objection_library import OBJECTION_RESPONSES
from .outcome_schema import ObjectionKind


@dataclass(frozen=True, slots=True)
class PlaybookResult:
    action: DialogueAction
    matched_pattern: Optional[ObjectionKind]
    applied: bool


def apply_playbook(
    *,
    action: DialogueAction,
    objection: Optional[ObjectionKind],
    prior_attempts: int,
    profile: str = "clinic",
) -> PlaybookResult:
    if objection is None:
        return PlaybookResult(action=action, matched_pattern=None, applied=False)
    if profile == "b2b":
        # Keep B2B objections deterministic in dialogue_policy to avoid extra
        # branching and delay on high-frequency cold-caller phrases.
        return PlaybookResult(action=action, matched_pattern=objection, applied=False)

    base = OBJECTION_RESPONSES.get(objection, "")
    if not base:
        return PlaybookResult(action=action, matched_pattern=objection, applied=False)

    payload = dict(action.payload)
    payload["playbook_objection"] = objection

    # Deterministic policy: when objections appear, keep one-question flow and narrow next step.
    if action.action_type in {"Ask", "Repair", "Confirm"}:
        if objection == "price_shock":
            payload["message"] = f"{base} Do you want the price first, or should I help with times first?"
        elif objection == "timing_conflict":
            payload["message"] = f"{base} Is morning or afternoon better for you?"
        elif objection == "trust_hesitation":
            payload["message"] = f"{base} Do you want me to connect you with the front desk now?"
        else:
            payload["message"] = f"{base} Do you want the soonest opening?"
        return PlaybookResult(
            action=DialogueAction(action_type="Ask", payload=payload, tool_requests=list(action.tool_requests)),
            matched_pattern=objection,
            applied=True,
        )

    if action.action_type == "OfferSlots" and prior_attempts >= 1:
        payload["message_prefix"] = base
        return PlaybookResult(
            action=DialogueAction(action_type=action.action_type, payload=payload, tool_requests=list(action.tool_requests)),
            matched_pattern=objection,
            applied=True,
        )

    return PlaybookResult(action=action, matched_pattern=objection, applied=False)
