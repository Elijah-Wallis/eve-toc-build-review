from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Literal, Optional


ObjectionKind = Literal["price_shock", "timing_conflict", "trust_hesitation", "urgency_pressure"]


_PRICE_OBJECTION = re.compile(r"\b(too expensive|pricey|costs too much|can't afford|out of budget)\b", re.I)
_TIME_OBJECTION = re.compile(r"\b(too busy|no time|not available|can't make that time|schedule conflict)\b", re.I)
_TRUST_OBJECTION = re.compile(r"\b(not sure|don't trust|skeptical|is this legit|is this real)\b", re.I)
_URGENCY_OBJECTION = re.compile(r"\b(right now|asap|urgent|immediately|today only)\b", re.I)


@dataclass(frozen=True, slots=True)
class CallOutcome:
    call_id: str
    turn_id: int
    epoch: int
    intent: str
    action_type: str
    objection: Optional[ObjectionKind]
    offered_slots_count: int
    accepted: bool
    escalated: bool
    drop_off_point: str
    t_ms: int

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


def detect_objection(user_text: str) -> Optional[ObjectionKind]:
    txt = user_text or ""
    if _PRICE_OBJECTION.search(txt):
        return "price_shock"
    if _TIME_OBJECTION.search(txt):
        return "timing_conflict"
    if _TRUST_OBJECTION.search(txt):
        return "trust_hesitation"
    if _URGENCY_OBJECTION.search(txt):
        return "urgency_pressure"
    return None
