from __future__ import annotations

import re
from dataclasses import dataclass


_IDENTITY_ARE_YOU_PAT = re.compile(r"\bare you\b", re.I)
_IDENTITY_KEYWORDS_PAT = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|virtual assistant|human|robot|a person|real person)\b",
    re.I,
)
_IDENTITY_DIRECT_Q_PAT = re.compile(r"\b(ai|human|robot)\?\b", re.I)
_IDENTITY_REAL_PAT = re.compile(r"\bare you real\b", re.I)
_URGENT_PAT = re.compile(
    r"\b(chest pain|can't breathe|cannot breathe|suicid(e|al)|stroke|heart attack)\b",
    re.I,
)
_CLINICAL_PAT = re.compile(
    r"\b("
    r"dosage|dose|mg|milligram|prescription|prescribe|side effects?"
    r"|should i take|can i take|what should i take|how much should i take"
    r"|diagnos(e|is)|treat(ment)?|symptom(s)?|medicine|medication"
    r")\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class SafetyResult:
    kind: str  # "ok" | "identity" | "urgent" | "clinical"
    message: str = ""


def evaluate_user_text(
    text: str,
    *,
    clinic_name: str,
    profile: str = "clinic",
    b2b_org_name: str = "Eve",
) -> SafetyResult:
    t = text or ""

    if _URGENT_PAT.search(t):
        return SafetyResult(
            kind="urgent",
            message=(
                "If this is a medical emergency, please call 911 or your local emergency number right now. "
                "If you'd like, I can help connect you to the clinic for next steps once you're safe."
            ),
        )

    if (_IDENTITY_ARE_YOU_PAT.search(t) and _IDENTITY_KEYWORDS_PAT.search(t)) or _IDENTITY_DIRECT_Q_PAT.search(
        t
    ) or _IDENTITY_REAL_PAT.search(t):
        if profile == "b2b":
            msg = f"I'm Cassidy, the AI caller for {b2b_org_name}. I can share the report details quickly."
        else:
            msg = f"I'm Sarah, the AI assistant for {clinic_name}. I can help book visits and answer basic questions."
        return SafetyResult(
            kind="identity",
            message=msg,
        )

    if _CLINICAL_PAT.search(t):
        return SafetyResult(
            kind="clinical",
            message=(
                "I can't give medical advice, but I can connect you with a clinician or send a message to the clinic. "
                "Would you like to book a visit?"
            ),
        )

    return SafetyResult(kind="ok")
