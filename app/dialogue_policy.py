from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from .protocol import TranscriptUtterance


ActionType = Literal[
    "Ask",
    "Inform",
    "OfferSlots",
    "Confirm",
    "Repair",
    "Transfer",
    "EndCall",
    "EscalateSafety",
    "Noop",
]


@dataclass(frozen=True, slots=True)
class ToolRequest:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DialogueAction:
    action_type: ActionType
    payload: dict[str, Any] = field(default_factory=dict)
    tool_requests: list[ToolRequest] = field(default_factory=list)


@dataclass(slots=True)
class SlotState:
    intent: Optional[str] = None  # "booking" | None
    patient_name: Optional[str] = None
    phone: Optional[str] = None  # normalized digits
    phone_confirmed: bool = False
    requested_dt: Optional[str] = None  # user-provided date/time hint
    requested_dt_confirmed: bool = False
    b2b_funnel_stage: str = "OPEN"
    manager_email: Optional[str] = None

    b2b_last_stage: str = "OPEN"
    b2b_last_signal: str = ""
    b2b_no_signal_streak: int = 0
    b2b_last_user_signature: str = ""
    campaign_id: Optional[str] = None
    clinic_id: Optional[str] = None
    clinic_name: Optional[str] = None
    lead_id: Optional[str] = None
    to_number: Optional[str] = None
    tenant: Optional[str] = None

    reprompts: dict[str, int] = field(default_factory=dict)
    b2b_autonomy_mode: str = "baseline"
    question_depth: int = 1
    objection_pressure: int = 0


_PHONE_PAT = re.compile(r"(\d[\d\s\-\(\)]{8,}\d)")
_NAME_PAT = re.compile(r"\b(my name is|this is)\s+([A-Za-z][A-Za-z\-\s']{0,40})\b", re.I)
_BOOK_PAT = re.compile(r"\b(book|schedule|appointment|appt)\b", re.I)
_PRICE_PAT = re.compile(r"\b(price|cost|pricing|how much)\b", re.I)
_AVAIL_PAT = re.compile(r"\b(available|availability|openings|slot)\b", re.I)
_WEEKDAY_PAT = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)
_TIME_PAT = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)
_NEG_SENT_PAT = re.compile(r"\b(frustrated|upset|angry|mad|annoyed|disappointed|stressed)\b", re.I)
_SHELL_CMD_PAT = re.compile(r"^\s*(?:/shell|shell:)\s+(.+?)\s*$", re.I | re.S)
_EMAIL_PAT = re.compile(r"\b([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})\b", re.I)
_DNC_PAT = re.compile(r"\b(stop calling|remove me|do not call|don't call|take me off)\b", re.I)
_SOFT_REJECT_PAT = re.compile(
    r"\b(not interested|too busy|we are good|we're good|not right now|no thanks)\b", re.I
)
_ADMIN_BLOCK_PAT = re.compile(
    r"\b(receptionist|front desk|with a patient|in a meeting|call back later|busy|manager is not in|can\s*you\s*email\s*)\b", re.I
)
_NO_EMAIL_PAT = re.compile(r"\b(don't give out emails?|do not give out emails?|can't give.*email|not allowed to give.*email)\b", re.I)
_WHO_PAT = re.compile(r"\b(who is this|who are you|what is this|is this sales)\b", re.I)
_INTEREST_PAT = re.compile(r"\b(sure|yes|send it|okay send|go ahead|what's the email)\b", re.I)
_INFO_EMAIL_PAT = re.compile(r"\b(info|contact|admin|frontdesk)@", re.I)
_BAD_TIME_PAT = re.compile(r"\b(not a good time|bad time|not now|too busy|call me later|later|call back later|not right now)\b", re.I)
_NOT_DECISION_MAKER_PAT = re.compile(r"\b(not the decision maker|not the right person|not my decision|who can decide|not authorized|can't authorize)\b", re.I)
_NOT_INTERESTED_PAT = re.compile(r"\b(not interested|not looking|we are good|we're good|not right now)\b", re.I)
_PRICE_PUSH_PAT = re.compile(r"\b(price|cost|pricing|how much|too expensive|budget)\b", re.I)
_TOO_BUSY_PAT = re.compile(r"\b(too busy|too much going on|in a meeting|busy right now|can you call later|call me back later)\b", re.I)
_INTERNAL_ALIGNMENT_PAT = re.compile(r"\b(need approval|need to get approval|internal alignment|run it by|run this by|discuss with)\b", re.I)
_ALREADY_USING_VENDOR_PAT = re.compile(r"\b(we already have|we use|already using|already have|existing vendor|current vendor)\b", re.I)
_YES_PAT = re.compile(r"\b(yes|yeah|yep|sure|go on|go ahead|okay|ok|alright|all right|fine)\b", re.I)
_NO_PAT = re.compile(r"\b(no|not now|not today|not a bad time|nope|nah|pass|don't|do not)\b", re.I)
_HELLO_PAT = re.compile(r"\b(hello|hi|hey)\b", re.I)
_OPEN_NOT_BAD_TIME_PAT = re.compile(r"\bnot a bad time\b", re.I)
_CLOSE_PROGRESS_PAT = re.compile(
    r"\b(call me now|close this out|close this call|close the call|hang up|hang up now|end call|end this call)\b",
    re.I,
)
_NO_SIGNAL_CHAR_PAT = re.compile(r"^[\W_]+$", re.I)
_NO_SIGNAL_REPEAT_PUNCT = re.compile(r"^(.)\1+$")
_B2B_NOISE_TOKEN_PAT = re.compile(
    r"^(?:u{1,2}h|um{1,3}|mmm?|hmm|ah|eh|er|erm|huh|phew|meh)$",
    re.I,
)
_B2B_ACK_NOISE_PAT = re.compile(
    r"^(?:(?:hey|hi|hello)\s+)?(?:got\s*it|gotcha|i\s+got\s+it|yep\s+got\s+it|yup\s+got\s+it|ya\s+got\s+it|"
    r"understand\b|understood\b|"
    r"yep\b|yup\b|ok\b|okay\b|right\b|alright\b|all\s+right)$",
    re.I,
)
_B2B_ACK_NOISE_TOKENS = {
    "got",
    "it",
    "gotcha",
    "yep",
    "yup",
    "ya",
    "understand",
    "understood",
    "ok",
    "okay",
    "right",
    "alright",
    "hey",
    "hi",
    "hello",
    "this",
    "is",
    "from",
    "cassidy",
    "eve",
    "sarah",
    "agent",
    "with",
    "the",
    "a",
    "an",
    "and",
    "to",
    "all",
}
_B2B_NOISE_PREFIX_TOKENS = {
    "hey",
    "hi",
    "hello",
    "cassidy",
    "sarah",
    "agent",
    "eve",
    "this",
    "is",
    "from",
    "with",
}


def _is_short_ack_noise_phrase(text: str) -> bool:
    phrase = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not phrase:
        return False
    if _B2B_ACK_NOISE_PAT.fullmatch(phrase):
        return True
    compact_tokens = [w for w in re.sub(r"[^a-z0-9]+", " ", phrase).split(" ") if w]
    if not compact_tokens:
        return False
    if len(compact_tokens) > 10:
        return False
    if all(token in _B2B_ACK_NOISE_TOKENS for token in compact_tokens):
        return True
    if (
        compact_tokens[0] in {"hey", "hi", "hello"}
        and compact_tokens[-1] in {"got", "it", "yep", "yup", "okay", "ok", "gotcha"}
    ):
        return True
    return False


def _normalize_b2b_noise_tokens(text: str) -> list[str]:
    compact_with_spaces = re.sub(r"\s+", " ", (text or "").strip().lower())
    compact_alpha = re.sub(r"[^a-z0-9\s]", " ", compact_with_spaces)
    return [w for w in re.sub(r"\s+", " ", compact_alpha).split(" ") if w]

_B2B_ONTOLOGY: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EXPLICIT_REJECTION", _DNC_PAT),
    ("ADMIN_BLOCK", _NO_EMAIL_PAT),
    ("ADMIN_BLOCK", _ADMIN_BLOCK_PAT),
    ("NOT_DECISION_MAKER", _NOT_DECISION_MAKER_PAT),
    ("NOT_INTERESTED", _NOT_INTERESTED_PAT),
    ("PRICE_PUSH", _PRICE_PUSH_PAT),
    ("TOO_BUSY", _TOO_BUSY_PAT),
    ("INTERNAL_ALIGNMENT", _INTERNAL_ALIGNMENT_PAT),
    ("ALREADY_USING_VENDOR", _ALREADY_USING_VENDOR_PAT),
    ("BAD_TIME", _BAD_TIME_PAT),
    ("SOFT_REJECTION", _SOFT_REJECT_PAT),
    ("ACTIVE_INTEREST", _INTEREST_PAT),
)


def _normalize_mode(value: str) -> str:
    if value in {"conservative", "assertive"}:
        return value
    return "baseline"


def _update_b2b_adaptive_state(
    *,
    state: SlotState,
    classification: str,
    last_user: str,
    current_stage: str,
    next_stage: str,
) -> None:
    pressure = int(state.objection_pressure or 0)
    if classification in {
        "BAD_TIME",
        "SOFT_REJECTION",
        "ADMIN_BLOCK",
        "EXPLICIT_REJECTION",
        "NOT_DECISION_MAKER",
        "NOT_INTERESTED",
        "PRICE_PUSH",
        "TOO_BUSY",
        "INTERNAL_ALIGNMENT",
        "ALREADY_USING_VENDOR",
    }:
        pressure += 1
    elif classification == "ACTIVE_INTEREST":
        pressure = max(0, pressure - 1)
    if _NEG_SENT_PAT.search(last_user or ""):
        pressure += 1

    if pressure < 0:
        pressure = 0
    if pressure > 6:
        pressure = 6
    state.objection_pressure = pressure

    state.b2b_autonomy_mode = (
        "assertive" if pressure >= 3 else "conservative" if pressure == 0 else "baseline"
    )

    depth = int(state.question_depth or 1)
    if classification in {"SOFT_REJECTION", "ADMIN_BLOCK"}:
        depth = min(4, depth + 1)
    elif classification == "ACTIVE_INTEREST":
        depth = max(1, depth - 1)
    if current_stage == "OPEN" and next_stage == "ROUTING" and not _YES_PAT.search(last_user or ""):
        depth = min(4, max(1, depth + 1))
    if depth < 1:
        depth = 1
    state.question_depth = depth


def _adapt_b2b_message(message: str, *, state: SlotState, classification: str, stage: str) -> str:
    msg = (message or "").strip()
    mode = _normalize_mode(state.b2b_autonomy_mode)
    if not msg:
        return msg

    # Do not emit meta prefixes like "Quick." / "Direct." / "I get you." in spoken content.
    # Assertive/conservative modes should change which question we ask, not add robotic prefaces.
    if classification == "EXPLICIT_REJECTION":
        msg = f"{msg}"

    if classification in {
        "NOT_DECISION_MAKER",
        "NOT_INTERESTED",
        "PRICE_PUSH",
        "TOO_BUSY",
        "INTERNAL_ALIGNMENT",
        "ALREADY_USING_VENDOR",
        "BAD_TIME",
    }:
        return msg

    # Avoid stacking multiple questions in one turn. If we already end with a question,
    # do not append another "depth" question (it sounds robotic and increases overtalk).
    if len(msg.split()) > 0 and state.question_depth > 2 and not msg.endswith("?"):
        append = ""
        if stage == "OPEN":
            append = "Want this in under 60 seconds?"
        elif stage == "ROUTING":
            append = "Who should I route this to?"
        elif stage == "PROBLEM":
            append = "Is that common now?"
        elif stage == "VALUE":
            append = "Want the quick report now?"
        if append:
            msg = f"{msg} {append}"
    return msg


def _last_user_text(transcript: list[TranscriptUtterance]) -> str:
    for utt in reversed(transcript):
        if utt.role == "user":
            return utt.content or ""
    return ""


def _normalized_user_signature(text: str) -> str:
    compact = re.sub(r"\s+", "", (text or "").strip().lower())
    if not compact:
        return ""
    if re.fullmatch(_NO_SIGNAL_REPEAT_PUNCT, compact) and len(compact) >= 2 and not compact[0].isalnum():
        return compact
    compact_alpha = re.sub(r"[^a-z0-9]", "", compact)
    if not compact_alpha:
        return compact
    if compact_alpha in {"u", "uh", "um", "hmm", "hm", "ah", "uhm"}:
        return compact_alpha
    return compact_alpha[:80]


def _last_agent_text(transcript: list[TranscriptUtterance]) -> str:
    for utt in reversed(transcript):
        if utt.role == "agent":
            return utt.content or ""
    return ""


def _is_short_yes(text: str) -> bool:
    t = re.sub(r"[^a-z\s]", "", (text or "").strip().lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t in {"yes", "yeah", "yep", "sure", "ok", "okay", "alright", "all right", "fine", "go ahead"}


def _is_short_no(text: str) -> bool:
    t = re.sub(r"[^a-z\s]", "", (text or "").strip().lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t in {"no", "nope", "nah"}


def _extract_phone_digits(text: str) -> Optional[str]:
    m = _PHONE_PAT.search(text or "")
    if not m:
        return None
    digits = re.sub(r"\D+", "", m.group(1))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return digits


def _extract_name(text: str) -> Optional[str]:
    m = _NAME_PAT.search(text or "")
    if not m:
        return None
    name = (m.group(2) or "").strip()
    # Normalize multiple spaces.
    name = re.sub(r"\s+", " ", name)
    return name if name else None


def _name_confidence_high(name: str) -> bool:
    parts = [p for p in (name or "").split(" ") if p]
    if len(parts) >= 2 and all(len(p) >= 2 for p in parts):
        return True
    return False


def _extract_requested_dt(text: str) -> Optional[str]:
    wd = _WEEKDAY_PAT.search(text or "")
    if not wd:
        return None
    tm = _TIME_PAT.search(text or "")
    if not tm:
        return None
    weekday = (wd.group(1) or "").strip().capitalize()
    hour = tm.group(1) or ""
    minute = tm.group(2)
    ampm = (tm.group(3) or "").strip().upper()
    time_part = hour
    if minute:
        time_part += f":{minute}"
    if ampm:
        time_part += f" {ampm}"
    return f"{weekday} at {time_part}".strip()


def _inc_reprompt(state: SlotState, field: str) -> int:
    state.reprompts[field] = state.reprompts.get(field, 0) + 1
    return state.reprompts[field]


def _extract_email(text: str) -> Optional[str]:
    m = _EMAIL_PAT.search(text or "")
    if not m:
        return None
    return (m.group(1) or "").strip().lower()


def _classify_b2b_state(
    text: str,
    *,
    stage: str = "OPEN",
    last_agent: str = "",
) -> str:
    t = (text or "").strip()
    if not t:
        return "NO_SIGNAL"

    if re.fullmatch(r"(?:hello|hi|hey)[.!?]*", t.lower()):
        agent = (last_agent or "").lower()
        # If the agent just asked the OPEN permission question, "hello" is a soft proceed signal.
        if stage == "OPEN" and "bad time" in agent:
            return "ACTIVE_INTEREST"
        # Otherwise treat as a new-call greeting and deliver the permission opener next.
        return "NEW_CALL"

    compact = re.sub(r"\s+", "", t)
    if not compact:
        return "NO_SIGNAL"

    compact_alpha = re.sub(r"[^a-z0-9\s]", "", t.lower()).strip()
    compact_tokens = [w for w in re.sub(r"\s+", " ", compact_alpha).split(" ") if w]
    compact_noise_tokens = _normalize_b2b_noise_tokens(compact_alpha)
    compact_phrase = " ".join(compact_tokens)
    if compact_noise_tokens and any(
        token in _B2B_NOISE_PREFIX_TOKENS for token in compact_noise_tokens
    ) and any(token in {"got", "gotcha"} for token in compact_noise_tokens):
        return "NO_SIGNAL"
    if _is_short_ack_noise_phrase(compact_phrase):
        return "NO_SIGNAL"
    if compact_noise_tokens and len(compact_noise_tokens) <= 8 and all(
        token in _B2B_ACK_NOISE_TOKENS for token in compact_noise_tokens
    ):
        return "NO_SIGNAL"
    if compact_tokens:
        if _B2B_ACK_NOISE_PAT.fullmatch(compact_phrase):
            return "NO_SIGNAL"
        if len(compact_tokens) <= 3 and re.fullmatch(r".*got\s*it$", compact_phrase):
            return "NO_SIGNAL"
    # Very short/ambient responses should never re-open stage transition logic.
    if re.fullmatch(_NO_SIGNAL_CHAR_PAT, compact):
        return "NO_SIGNAL"

    if re.fullmatch(_NO_SIGNAL_REPEAT_PUNCT, compact) and len(compact) >= 2 and not compact[0].isalnum():
        return "NO_SIGNAL"

    if re.fullmatch(_NO_SIGNAL_REPEAT_PUNCT, compact.lower()) and compact.lower() in {
        "??",
        "!!",
        "~~",
        "--",
        "__",
        "...",
    }:
        return "NO_SIGNAL"

    if _HELLO_PAT.search(t):
        return "ACTIVE_INTEREST"

    agent = (last_agent or "").lower()
    if stage == "OPEN" and "bad time" in agent:
        # Bad-time opener disambiguation: short "No." usually means permission to continue.
        if _is_short_no(t):
            return "ACTIVE_INTEREST"
        if _is_short_yes(t):
            return "BAD_TIME"
        if _OPEN_NOT_BAD_TIME_PAT.search(t):
            return "ACTIVE_INTEREST"
        if _HELLO_PAT.search(t):
            return "ACTIVE_INTEREST"

    if stage == "ROUTING" and ("routing" in agent or "person handling" in agent):
        # "Are you the person handling routing?" -> "No" is an admin-block, not rejection.
        if _is_short_no(t):
            return "ADMIN_BLOCK"

    if _extract_email(t):
        return "ACTIVE_INTEREST"

    for classification, pat in _B2B_ONTOLOGY:
        if pat.search(t):
            return classification

    if _WHO_PAT.search(t):
        return "SOFT_REJECTION"

    if _YES_PAT.search(t):
        return "ACTIVE_INTEREST"

    if _NO_PAT.search(t):
        return "SOFT_REJECTION"

    return "NEW_CALL"


def _is_b2b_noise_only_input(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    compact = re.sub(r"\s+", "", t)
    compact_with_spaces = re.sub(r"\s+", " ", t.lower())
    if not compact_with_spaces:
        return True
    compact_lower = compact_with_spaces
    # A pure greeting is a valid start/continuation signal; do not treat it as noise-only.
    if re.fullmatch(r"(?:hello|hi|hey)[.!?]*", compact_lower):
        return False
    compact_noise_tokens = _normalize_b2b_noise_tokens(compact_with_spaces)
    if compact_noise_tokens and len(compact_noise_tokens) <= 8 and all(
        token in _B2B_ACK_NOISE_TOKENS for token in compact_noise_tokens
    ):
        return True
    if compact_noise_tokens and any(
        token in _B2B_NOISE_PREFIX_TOKENS for token in compact_noise_tokens
    ) and any(token in {"got", "gotcha", "it", "yep", "yup"} for token in compact_noise_tokens):
        return True
    if _B2B_NOISE_TOKEN_PAT.fullmatch(compact_lower):
        return True
    # Preserve short ambient backchannel tokens that can arrive from ASR instability.
    # These are commonly heard as tiny sound-like fragments and should not advance dialogue.
    compact_alpha = re.sub(r"[^a-z]", "", compact_lower)
    if compact_alpha and compact_alpha in {"u", "uh", "um", "huh", "hmm", "hm", "ah"}:
        return True
    if _is_short_ack_noise_phrase(compact_lower):
        return True
    compact_words = [w for w in re.sub(r"[^a-z0-9\s]", " ", compact_lower).split(" ") if w]
    if compact_words and len(compact_words) <= 4:
        compact_phrase = " ".join(compact_words)
        if _B2B_ACK_NOISE_PAT.fullmatch(compact_phrase):
            return True
    if re.fullmatch(_NO_SIGNAL_CHAR_PAT, compact):
        return True
    if re.fullmatch(_NO_SIGNAL_REPEAT_PUNCT, compact) and len(compact) >= 2 and not compact[0].isalnum():
        return True
    return False


def _is_repeated_no_progress_state(
    *,
    state: SlotState,
    current_stage: str,
    detected_state: str,
    previous_stage: str | None = None,
    previous_signal: str | None = None,
    previous_no_signal_streak: int | None = None,
    previous_user_signature: str | None = None,
    current_user_signature: str | None = None,
) -> bool:
    """Return True when a detected input should not reopen the same prompt."""

    prev_stage = str(previous_stage if previous_stage is not None else getattr(state, "b2b_last_stage", current_stage))
    prev_signal = str(previous_signal if previous_signal is not None else getattr(state, "b2b_last_signal", ""))
    prev_streak = int(
        getattr(state, "b2b_no_signal_streak", 0)
        if previous_no_signal_streak is None
        else previous_no_signal_streak
    )

    if prev_stage != current_stage:
        return False

    if detected_state in {"NEW_CALL", "NO_SIGNAL"}:
        if current_user_signature is not None and previous_user_signature is not None:
            if current_user_signature != (previous_user_signature or "").strip():
                return False
        if prev_signal not in {"NO_SIGNAL", "NEW_CALL"}:
            return False
        if prev_streak <= 0:
            return False
        return True

    return False


_B2B_OBJECTION_MESSAGES: dict[str, str] = {
    "NOT_DECISION_MAKER": "Who is the decision maker I should speak to?",
    "NOT_INTERESTED": "Who should I send this to at your place?",
    "PRICE_PUSH": "Want me to send one quick pricing summary to the manager?",
    "TOO_BUSY": "I can keep this under 30 seconds. Email the manager?",
    "INTERNAL_ALIGNMENT": "Who else must approve this before I hand it to the manager?",
    "ALREADY_USING_VENDOR": "Who owns this decision on your side?",
    "BAD_TIME": "Should we close this now or send one short manager email?",
    "SOFT_REJECTION": "Should we close this now or send one short manager email?",
    "ADMIN_BLOCK": "Which inbox should I send that to?",
}

_B2B_OPEN_OPENER = "Is now a bad time for a quick question?"

_B2B_FAST_PATH_TAG = "b2b"


def _b2b_fast_path_signature(*, stage: str, next_stage: str, classification: str, signal: str) -> str:
    return f"{_B2B_FAST_PATH_TAG}:{stage}:{next_stage}:{classification}:{signal}"


def _objection_message(*, classification: str, last_user: str, needs_empathy: bool, stage: str) -> str:
    if classification == "SOFT_REJECTION":
        msg = _B2B_OBJECTION_MESSAGES["SOFT_REJECTION"]
    elif classification == "ADMIN_BLOCK":
        msg = _B2B_OBJECTION_MESSAGES["ADMIN_BLOCK"]
    elif classification in _B2B_OBJECTION_MESSAGES:
        msg = _B2B_OBJECTION_MESSAGES[classification]
    else:
        msg = ""

    if not msg:
        if stage == "OPEN":
            msg = "Not a pitch. Who can help confirm this for the manager?"
        elif stage == "ROUTING":
            msg = "What is the best way to get this to the manager?"
        elif stage == "PROBLEM":
            msg = "Would a short manager email be useful now?"
        elif stage == "VALUE":
            msg = "Would you like me to send a short manager summary email?"
        else:
            msg = "What is the best email for the manager?"

    if needs_empathy and _NEG_SENT_PAT.search(last_user or "") and not msg.startswith("I hear you"):
        msg = f"I hear you. {msg}"

    return msg


def _noop_signal_payload(*, intent_signature: str, needs_empathy: bool) -> dict[str, Any]:
    return {
        "message": "",
        "needs_empathy": bool(needs_empathy),
        "no_progress": True,
        "no_signal": True,
        "fast_path": True,
        "intent_signature": intent_signature,
        "skip_ack": True,
    }


def _next_b2b_stage(current: str, classification: str, last_user: str) -> str:
    if current == "EMAIL":
        return "EMAIL"
    if classification == "EXPLICIT_REJECTION":
        return "END"

    if current == "OPEN":
        if classification == "ACTIVE_INTEREST" or _YES_PAT.search(last_user or ""):
            return "ROUTING"
        return "OPEN"

    if current == "ROUTING":
        if classification == "ACTIVE_INTEREST" or _YES_PAT.search(last_user or ""):
            return "PROBLEM"
        if classification == "SOFT_REJECTION":
            return "VALUE"
        if _YES_PAT.search(last_user or "") or _INTEREST_PAT.search(last_user or ""):
            return "PROBLEM"
        return "ROUTING"

    if current == "PROBLEM":
        if _YES_PAT.search(last_user or ""):
            return "VALUE"
        if _SOFT_REJECT_PAT.search(last_user or ""):
            return "VALUE"
        return "PROBLEM"

    if current == "VALUE":
        if _YES_PAT.search(last_user or ""):
            return "EMAIL"
        if _SOFT_REJECT_PAT.search(last_user or ""):
            return "VALUE"
        if classification == "ACTIVE_INTEREST":
            return "EMAIL"
        return "VALUE"

    return current


def _advance_b2b_state_and_payload(
    *, state: SlotState, classification: str, last_user: str, needs_empathy: bool
) -> tuple[str, dict[str, Any]]:
    current = str(state.b2b_funnel_stage or "OPEN")
    next_stage = _next_b2b_stage(current, classification, last_user)

    # Persist stage for this turn.
    state.b2b_funnel_stage = next_stage

    if next_stage == "OPEN":
        if classification == "NEW_CALL":
            msg = _B2B_OPEN_OPENER
        elif classification == "BAD_TIME":
            msg = "Do you want to close now or send a short manager email?"
        elif _BAD_TIME_PAT.search(last_user or ""):
            msg = "Do you want to close now or send a short manager email?"
        elif _WHO_PAT.search(last_user or ""):
            msg = "Not a pitch. Who handles manager follow-up today?"
        elif _SOFT_REJECT_PAT.search(last_user or ""):
            msg = "Do you want to close this call or send a short manager email?"
        elif _ADMIN_BLOCK_PAT.search(last_user or ""):
            msg = "Which inbox should I send this to?"
        else:
            msg = "Is now a bad time for a quick question?"
    elif next_stage == "ROUTING":
        if _SOFT_REJECT_PAT.search(last_user or ""):
            msg = "Close this call or send one short manager email?"
        else:
            msg = "What is the best way to get a short email to the manager?"
    elif next_stage == "PROBLEM":
        msg = "What happens after hours when someone calls and leaves a voicemail?"
    elif next_stage == "VALUE":
        msg = "Would it help if new leads got a reply in under a minute, even after hours?"
    else:  # EMAIL or unknown fallback
        msg = "What is the best email for the manager?"
    if classification in _B2B_OBJECTION_MESSAGES:
        msg = _objection_message(
            classification=classification,
            last_user=last_user,
            needs_empathy=needs_empathy,
            stage=current,
        )

    # Keep transitions single-purpose and direct in high-volume branches.

    _update_b2b_adaptive_state(
        state=state,
        classification=classification,
        last_user=last_user,
        current_stage=current,
        next_stage=next_stage,
    )
    msg = _adapt_b2b_message(
        msg,
        state=state,
        classification=classification,
        stage=next_stage,
    )

    return next_stage, {
        "slots_needed": ["manager_email"],
        "message": msg,
        "fast_path": True,
        "intent_signature": _b2b_fast_path_signature(
            stage=current,
            next_stage=next_stage,
            classification=classification,
            signal="fast_path",
        ),
    }


def _build_recording_followup_tool_request(
    state: SlotState, *, call_id: str, reason: str = "call_progress"
) -> ToolRequest:
    return ToolRequest(
        name="send_call_recording_followup",
        arguments={
            "tenant": str(state.tenant or "synthetic_medspa").strip(),
            "campaign_id": str(state.campaign_id or "").strip(),
            "clinic_id": str(state.clinic_id or "").strip(),
            "lead_id": str(state.lead_id or "").strip(),
            "call_id": str(call_id or "").strip(),
            "to_number": str(state.to_number or state.phone or "").strip(),
            "recipient_email": str(state.manager_email or "").strip(),
            "recipient_phone": str(state.phone or "").strip(),
            "channel": "twilio_sms",
            "next_step": "recording_followup",
            "reason": reason,
        },
    )


def decide_action(
    *,
    state: SlotState,
    transcript: list[TranscriptUtterance],
    needs_apology: bool,
    safety_kind: str,
    safety_message: str,
    call_id: str = "",
    profile: str = "clinic",
) -> DialogueAction:
    """
    Pure(ish) policy: uses and mutates SlotState for reprompt counts and captured slots.
    No tools are executed here; tool requests are returned for TurnHandler to run.
    """

    last_user = _last_user_text(transcript)
    needs_empathy = bool(_NEG_SENT_PAT.search(last_user))

    def _p(d: dict[str, Any]) -> dict[str, Any]:
        out = dict(d)
        out["needs_empathy"] = needs_empathy
        return out

    if safety_kind == "urgent":
        return DialogueAction(
            action_type="EscalateSafety",
            payload=_p({"reason": "urgent", "message": safety_message, "needs_apology": needs_apology}),
        )
    if safety_kind == "identity":
        return DialogueAction(
            action_type="Inform",
            payload=_p({"info_type": "identity", "message": safety_message, "needs_apology": needs_apology}),
        )
    if safety_kind == "clinical":
        return DialogueAction(
            action_type="EscalateSafety",
            payload=_p({"reason": "clinical", "message": safety_message, "needs_apology": needs_apology}),
        )

    if _CLOSE_PROGRESS_PAT.search(last_user):
        c = _inc_reprompt(state, "b2b_close_request")
        if c > 1:
            return DialogueAction(
                action_type="Ask",
                payload=_p(
                    {
                        "slots_needed": ["manager_email"],
                        "message": "What is the best manager email to send this to?",
                        "needs_empathy": False,
                        "fast_path": True,
                        "intent_signature": "b2b:close_progress:ask",
                    }
                ),
            )
        return DialogueAction(
            action_type="Ask",
            payload=_p(
                {
                    "slots_needed": ["manager_email"],
                    "message": "What manager email should I send this to?",
                    "fast_path": True,
                    "intent_signature": "b2b:close_progress:ask",
                }
            ),
        )

    if profile == "b2b":
        current_signal = _normalized_user_signature(last_user)
        stage = str(state.b2b_funnel_stage or "OPEN")
        previous_stage = str(getattr(state, "b2b_last_stage", stage))
        previous_signal = str(getattr(state, "b2b_last_signal", ""))
        previous_no_signal_streak = int(getattr(state, "b2b_no_signal_streak", 0))
        previous_user_signature = str(getattr(state, "b2b_last_user_signature", ""))

        email = _extract_email(last_user)
        if _is_b2b_noise_only_input(last_user):
            repeated = _is_repeated_no_progress_state(
                state=state,
                current_stage=stage,
                detected_state="NO_SIGNAL",
                previous_stage=previous_stage,
                previous_signal=previous_signal,
                previous_no_signal_streak=previous_no_signal_streak,
                previous_user_signature=previous_user_signature,
                current_user_signature=current_signal,
            )
            intent_signature = (
                f"{_B2B_FAST_PATH_TAG}:{str(state.b2b_funnel_stage)}:repeated_noise"
                if repeated
                else f"{_B2B_FAST_PATH_TAG}:{str(state.b2b_funnel_stage)}:noise_only"
            )
            state.b2b_last_stage = str(state.b2b_funnel_stage or "OPEN")
            state.b2b_last_signal = "NO_SIGNAL"
            state.b2b_no_signal_streak = int(state.b2b_no_signal_streak) + 1
            state.b2b_last_user_signature = current_signal
            return DialogueAction(
                action_type="Noop",
                payload=_p(_noop_signal_payload(intent_signature=intent_signature, needs_empathy=False)),
            )
        if email:
            state.manager_email = email
            if _INFO_EMAIL_PAT.search(email):
                c = _inc_reprompt(state, "direct_email")
                if c <= 1:
                    return DialogueAction(
                        action_type="Ask",
                        payload=_p(
                    {
                                "slots_needed": ["direct_email"],
                                "message": "I can send there, but those inboxes often miss fast items. Do you have a direct manager email?",
                                "needs_apology": needs_apology,
                                "reprompt_count": c,
                                "fast_path": True,
                                "intent_signature": "b2b:generic_email:ask",
                            }
                        ),
                    )
            return DialogueAction(
                action_type="EndCall",
                payload=_p(
                    {
                        "message": f"I can send to {email} now, then send a follow-up if needed.",
                        "end_call": True,
                        "email": email,
                        "needs_apology": needs_apology,
                        "accepted": True,
                        "fast_path": True,
                        "intent_signature": f"b2b:{stage}:generic_email:accept_generic",
                    }
                ),
            )
        stage = str(state.b2b_funnel_stage or "OPEN")
        current_user_signature = current_signal
        last_agent = _last_agent_text(transcript).lower()
        state.b2b_last_user_signature = current_user_signature
        b2b_state = _classify_b2b_state(last_user, stage=stage, last_agent=last_agent)
        state.b2b_last_stage = stage
        state.b2b_last_signal = str(b2b_state)

        if b2b_state in {"NO_SIGNAL", "NEW_CALL"}:
            state.b2b_no_signal_streak = previous_no_signal_streak + 1
        else:
            state.b2b_no_signal_streak = 0

        if b2b_state in {"NO_SIGNAL", "NEW_CALL"}:
            repeated_no_progress = _is_repeated_no_progress_state(
                state=state,
                current_stage=stage,
                detected_state=b2b_state,
                previous_stage=previous_stage,
                previous_signal=previous_signal,
                previous_no_signal_streak=previous_no_signal_streak,
                previous_user_signature=previous_user_signature,
                current_user_signature=current_user_signature,
            )
            if repeated_no_progress:
                intent_signature = _b2b_fast_path_signature(
                    stage=stage,
                    next_stage=stage,
                    classification="repeated_no_signal",
                    signal=b2b_state,
                )
                # Explicit no-progress repeat suppression for noise and no-intent loops in-place.
                return DialogueAction(
                    action_type="Noop",
                    payload=_p(
                        _noop_signal_payload(
                            intent_signature=intent_signature,
                            needs_empathy=False,
                        )
                    ),
                )
            if b2b_state == "NO_SIGNAL":
                intent_signature = _b2b_fast_path_signature(
                    stage=stage,
                    next_stage=stage,
                    classification="no_signal",
                    signal=b2b_state,
                )
                return DialogueAction(
                    action_type="Noop",
                    payload=_p(_noop_signal_payload(intent_signature=intent_signature, needs_empathy=False)),
                )
            # First "new-call" event in a stage can be a valid opener, but repeated opener-with-no-intent
            # turns are suppressed to avoid re-stating the same stage question.
            if b2b_state == "NEW_CALL" and previous_signal in {"NO_SIGNAL", "NEW_CALL"} and previous_stage == stage:
                intent_signature = _b2b_fast_path_signature(
                    stage=stage,
                    next_stage=stage,
                    classification="repeated_new_call",
                    signal=b2b_state,
                )
                return DialogueAction(
                    action_type="Noop",
                    payload=_p(_noop_signal_payload(intent_signature=intent_signature, needs_empathy=False)),
                )

        if b2b_state == "EXPLICIT_REJECTION":
            return DialogueAction(
                action_type="EndCall",
                payload=_p(
                    {
                        "message": "Thanks, I won't call again. Goodbye.",
                        "end_call": True,
                        "dnc": True,
                        "fast_path": True,
                        "intent_signature": _b2b_fast_path_signature(
                            stage=stage,
                            next_stage="END",
                            classification="EXPLICIT_REJECTION",
                            signal="state",
                        ),
                        "needs_apology": needs_apology,
                    }
                ),
                tool_requests=[
                    ToolRequest(name="mark_dnc_compliant", arguments={"reason": "USER_REQUEST"}),
                    _build_recording_followup_tool_request(
                        state,
                        call_id=call_id,
                        reason="explicit_rejection",
                    ),
                ],
            )

        if b2b_state == "BAD_TIME":
            # Bad time is not a DNC signal. Offer a single close-or-send choice and then accept.
            c = _inc_reprompt(state, "b2b_bad_time")
            if c > 1:
                return DialogueAction(
                    action_type="Ask",
                    payload=_p(
                        {
                            "slots_needed": ["manager_email"],
                            "message": "What is the best manager email to send this to?",
                            "needs_empathy": True,
                            "needs_apology": needs_apology,
                            "fast_path": True,
                            "intent_signature": f"b2b:{stage}:bad_time_reprompt",
                        }
                    ),
                )
            return DialogueAction(
                action_type="Ask",
                payload=_p(
                    {
                        "slots_needed": ["manager_email"],
                        "message": "Do you want to close this or send one short manager email?",
                        "needs_empathy": True,
                        "needs_apology": needs_apology,
                        "fast_path": True,
                        "intent_signature": f"b2b:{stage}:bad_time_init",
                    }
                ),
            )

        next_stage, payload = _advance_b2b_state_and_payload(
            state=state, classification=b2b_state, last_user=last_user, needs_empathy=needs_empathy
        )

        if _WHO_PAT.search(last_user):
            # Preserve state while answering identity checks without reopening the funnel.
            return DialogueAction(
                action_type="Inform",
                payload=_p(
                    {
                        "info_type": "b2b_identity",
                        "message": "Not a sales pitch. I can send a short summary to the manager.",
                        "fast_path": True,
                        "intent_signature": _b2b_fast_path_signature(
                            stage=stage,
                            next_stage=stage,
                            classification="identity_followup",
                            signal="IDENTITY",
                        ),
                    }
                ),
            )

        if next_stage == "END":
            return DialogueAction(
                action_type="EndCall",
                payload=_p(
                    {
                        "message": "Thanks, I won't call again. Goodbye.",
                        "end_call": True,
                        "dnc": True,
                        "fast_path": True,
                        "intent_signature": _b2b_fast_path_signature(
                            stage=stage,
                            next_stage="END",
                            classification="EXPLICIT_REJECTION",
                            signal="transition",
                        ),
                        "needs_apology": needs_apology,
                    }
                ),
                tool_requests=[
                    ToolRequest(name="mark_dnc_compliant", arguments={"reason": "USER_REQUEST"}),
                    _build_recording_followup_tool_request(
                        state,
                        call_id=call_id,
                        reason="journey_end",
                    ),
                ],
            )

        if next_stage == "EMAIL":
            return DialogueAction(
                action_type="Ask",
                payload=_p(payload),
            )

        # Ask-first funnel step to mimic NEPQ-style permission/situation flow.
        return DialogueAction(
            action_type="Ask",
            payload=_p(payload),
        )

    # Update slot captures from the last user turn.
    phone = _extract_phone_digits(last_user)
    if phone:
        if state.phone and phone != state.phone:
            # Correction detected.
            state.phone_confirmed = False
        state.phone = phone
    name = _extract_name(last_user)
    if name:
        state.patient_name = name
    requested_dt = _extract_requested_dt(last_user)
    if requested_dt:
        if state.requested_dt and requested_dt != state.requested_dt:
            state.requested_dt_confirmed = False
        state.requested_dt = requested_dt

    wants_booking = bool(_BOOK_PAT.search(last_user))
    asks_price = bool(_PRICE_PAT.search(last_user))
    asks_avail = wants_booking or bool(_AVAIL_PAT.search(last_user))
    shell_m = _SHELL_CMD_PAT.match(last_user or "")
    if shell_m:
        cmd = str(shell_m.group(1) or "").strip()
        return DialogueAction(
            action_type="Inform",
            payload=_p({"info_type": "shell_exec", "needs_apology": needs_apology}),
            tool_requests=[ToolRequest(name="run_shell_command", arguments={"command": cmd, "timeout_s": 20})],
        )

    if wants_booking:
        state.intent = "booking"

    # Booking intake flow.
    if state.intent == "booking":
        if not state.patient_name:
            c = _inc_reprompt(state, "name")
            if c > 2:
                return DialogueAction(
                    action_type="Ask",
                    payload=_p(
                        {
                        "slots_needed": ["callback_name"],
                        "message": "What name should I use?",
                        "needs_apology": needs_apology,
                        "reprompt_count": c,
                        }
                    ),
                )
            return DialogueAction(
                action_type="Repair",
                payload=_p(
                    {
                    "field": "name",
                    "strategy": "spell" if c >= 1 else "ask",
                    "needs_apology": needs_apology,
                    "reprompt_count": c,
                    }
                ),
            )

        if not _name_confidence_high(state.patient_name):
            c = _inc_reprompt(state, "name_confidence")
            if c > 2:
                return DialogueAction(
                    action_type="Ask",
                    payload=_p(
                        {
                        "slots_needed": ["callback_name"],
                        "message": "Can you spell your name for me?",
                        "needs_apology": needs_apology,
                        "reprompt_count": c,
                        }
                    ),
                )
            return DialogueAction(
                action_type="Repair",
                payload=_p(
                    {
                    "field": "name",
                    "strategy": "spell",
                    "needs_apology": needs_apology,
                    "reprompt_count": c,
                    }
                ),
            )

        if not state.phone:
            c = _inc_reprompt(state, "phone")
            if c > 2:
                return DialogueAction(
                    action_type="Ask",
                    payload=_p(
                        {
                        "slots_needed": ["callback_phone"],
                        "message": "What number should we call you back on?",
                        "needs_apology": needs_apology,
                        "reprompt_count": c,
                        }
                    ),
                )
            return DialogueAction(
                action_type="Ask",
                payload=_p(
                    {
                    "slots_needed": ["phone"],
                    "message": "What's your phone number?",
                    "needs_apology": needs_apology,
                    "reprompt_count": c,
                    }
                ),
            )

        if not state.phone_confirmed:
            # Confirm last 4 digits (avoid repeating full phone).
            state.phone_confirmed = True
            last4 = state.phone[-4:]
            return DialogueAction(
                action_type="Confirm",
                payload=_p(
                    {
                    "field": "phone_last4",
                    "phone_last4": last4,
                    "needs_apology": needs_apology,
                    }
                ),
            )

        if not state.requested_dt:
            c = _inc_reprompt(state, "dt")
            return DialogueAction(
                action_type="Ask",
                payload=_p(
                    {
                    "slots_needed": ["preferred_day_time"],
                    "message": "What day works best for you?",
                    "needs_apology": needs_apology,
                    "reprompt_count": c,
                    }
                ),
            )

        if not state.requested_dt_confirmed:
            state.requested_dt_confirmed = True
            return DialogueAction(
                action_type="Confirm",
                payload=_p(
                    {
                    "field": "requested_dt",
                    "requested_dt": state.requested_dt,
                    "needs_apology": needs_apology,
                    }
                ),
            )

        # We have enough to check availability.
        return DialogueAction(
            action_type="OfferSlots",
            payload=_p(
                {
                "requested_dt": state.requested_dt,
                "patient_name": state.patient_name,
                "phone": state.phone,
                "needs_apology": needs_apology,
                }
            ),
            tool_requests=[ToolRequest(name="check_availability", arguments={"requested_dt": state.requested_dt})],
        )

    if asks_price:
        # Tool-first pricing.
        return DialogueAction(
            action_type="Inform",
            payload=_p({"info_type": "pricing", "needs_apology": needs_apology}),
            tool_requests=[ToolRequest(name="get_pricing", arguments={"service_id": "general"})],
        )

    if asks_avail:
        if not state.requested_dt:
            return DialogueAction(
                action_type="Ask",
                payload=_p(
                    {
                    "slots_needed": ["preferred_day_time"],
                    "message": "Sure. What day are you aiming for?",
                    "needs_apology": needs_apology,
                    }
                ),
            )
        return DialogueAction(
            action_type="OfferSlots",
            payload=_p({"requested_dt": state.requested_dt, "needs_apology": needs_apology}),
            tool_requests=[ToolRequest(name="check_availability", arguments={"requested_dt": state.requested_dt})],
        )

    return DialogueAction(
        action_type="Ask",
        payload=_p({"slots_needed": ["request"], "message": "How can I help today?", "needs_apology": needs_apology}),
    )
