from __future__ import annotations

import re


OBJECTION_RESPONSES: dict[str, str] = {
    "price_shock": "I hear you. I can keep this simple and help you pick the best value option.",
    "timing_conflict": "No problem. I can look for a time that fits your schedule.",
    "trust_hesitation": "Totally fair. I can answer basics and then connect you with the clinic team.",
    "urgency_pressure": "I understand this feels urgent. I'll help you get the soonest next step.",
}

_TIME_PAT = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(AM|PM)\b", re.I)

# Deterministic "historic" preference priors (higher is better).
_HOUR_WEIGHT = {
    9: 0.80,
    10: 0.76,
    11: 0.79,
    13: 0.73,
    14: 0.78,
    15: 0.72,
    16: 0.71,
}


def _slot_weight(slot: str) -> float:
    m = _TIME_PAT.search(slot or "")
    if not m:
        return 0.5
    h = int(m.group(1))
    ampm = (m.group(3) or "").upper()
    if ampm == "PM" and h != 12:
        h += 12
    if ampm == "AM" and h == 12:
        h = 0
    return float(_HOUR_WEIGHT.get(h, 0.6))


def sort_slots_by_acceptance(slots: list[str]) -> list[str]:
    return sorted(list(slots), key=lambda s: (-_slot_weight(s), s))
