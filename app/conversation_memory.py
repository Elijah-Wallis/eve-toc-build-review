from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from .agent.compaction import CompactionContext, build_compaction_summary
from .dialogue_policy import SlotState
from .protocol import TranscriptUtterance


_PHONE_PAT = re.compile(r"(\d[\d\s\-\(\)]{8,}\d)")
_TOPIC_PATTERNS = {
    "booking": re.compile(r"\b(book|schedule|appointment|appt)\b", re.I),
    "pricing": re.compile(r"\b(price|pricing|cost|how much)\b", re.I),
    "availability": re.compile(r"\b(available|availability|opening|slot)\b", re.I),
    "eligibility": re.compile(r"\b(eligible|eligibility|qualify)\b", re.I),
    "policy": re.compile(r"\b(policy|policies|hours|location|insurance)\b", re.I),
}
_PREFERENCE_PATTERNS = {
    "afternoon": re.compile(r"\b(afternoon|after 12|after noon)\b", re.I),
    "morning": re.compile(r"\b(morning|before 12|before noon)\b", re.I),
    "evening": re.compile(r"\b(evening|after work)\b", re.I),
}


@dataclass(frozen=True, slots=True)
class MemoryView:
    recent_transcript: list[TranscriptUtterance]
    summary_blob: str
    utterances_current: int
    chars_current: int
    compacted: bool


class ConversationMemory:
    def __init__(self, *, max_utterances: int, max_chars: int) -> None:
        self._max_utterances = max(1, int(max_utterances))
        self._max_chars = max(1, int(max_chars))
        self.recent_transcript: list[TranscriptUtterance] = []
        self.summary_blob: str = ""

    def ingest_snapshot(self, *, transcript: list[Any], slot_state: Optional[SlotState]) -> MemoryView:
        normalized = self._normalize_transcript(transcript)
        older: list[TranscriptUtterance] = []
        recent = list(normalized)
        compacted = False

        if len(recent) > self._max_utterances:
            cut = len(recent) - self._max_utterances
            older.extend(recent[:cut])
            recent = recent[cut:]
            compacted = True

        while self._chars_of(recent) > self._max_chars and recent:
            older.append(recent.pop(0))
            compacted = True

        summary = self._build_summary(older=older, slot_state=slot_state) if compacted else ""
        chars_current = self._chars_of(recent)

        self.recent_transcript = recent
        self.summary_blob = summary
        return MemoryView(
            recent_transcript=list(recent),
            summary_blob=summary,
            utterances_current=len(recent),
            chars_current=chars_current,
            compacted=compacted,
        )

    def _normalize_transcript(self, transcript: list[Any]) -> list[TranscriptUtterance]:
        out: list[TranscriptUtterance] = []
        for u in transcript or []:
            if isinstance(u, TranscriptUtterance):
                out.append(TranscriptUtterance(role=u.role, content=u.content))
                continue
            role = str(getattr(u, "role", "") or (u.get("role") if isinstance(u, dict) else "")).strip()
            content = str(getattr(u, "content", "") or (u.get("content") if isinstance(u, dict) else "")).strip()
            if role not in {"user", "agent"}:
                continue
            out.append(TranscriptUtterance(role=role, content=content))
        return out

    def _chars_of(self, transcript: list[TranscriptUtterance]) -> int:
        return sum(len(u.content or "") for u in transcript)

    def _extract_phone_last4(self, older: list[TranscriptUtterance], slot_state: Optional[SlotState]) -> str:
        if slot_state is not None and getattr(slot_state, "phone", None):
            digits = re.sub(r"\D+", "", str(slot_state.phone))
            if len(digits) >= 4:
                return digits[-4:]
        for utt in reversed(older):
            m = _PHONE_PAT.search(utt.content or "")
            if not m:
                continue
            digits = re.sub(r"\D+", "", m.group(1))
            if len(digits) >= 4:
                return digits[-4:]
        return ""

    def _build_summary(self, *, older: list[TranscriptUtterance], slot_state: Optional[SlotState]) -> str:
        texts = [u.content or "" for u in older]
        joined = " ".join(texts)

        topics: list[str] = []
        for name, pat in _TOPIC_PATTERNS.items():
            if pat.search(joined):
                topics.append(name)
        topics = sorted(set(topics))

        prefs: list[str] = []
        for name, pat in _PREFERENCE_PATTERNS.items():
            if pat.search(joined):
                prefs.append(name)
        prefs = sorted(set(prefs))

        parts: list[str] = []
        if slot_state is not None and getattr(slot_state, "intent", None):
            parts.append(f"intent={slot_state.intent}")
        if topics:
            parts.append("topics=" + ",".join(topics))

        phone_last4 = self._extract_phone_last4(older, slot_state)
        if phone_last4:
            parts.append(f"phone_last4={phone_last4}")
        if prefs:
            parts.append("preference=" + ",".join(prefs))

        if not parts:
            base = "Earlier context compacted."
        else:
            base = "Earlier context: " + "; ".join(parts) + "."

        context = CompactionContext(
            open_objectives="book_or_answer" if (slot_state and getattr(slot_state, "intent", None)) else "clarify_intent",
            pending_failures="none",
            active_guardrails="tool_grounding,plain_language,no_reasoning_leak",
            last_green_baseline="vic_contracts_green",
        )
        return base + " " + build_compaction_summary(context)
