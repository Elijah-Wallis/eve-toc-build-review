from __future__ import annotations

import re
from typing import Mapping

from .metrics import Metrics, VIC


_REASONING_PATTERNS = [
    re.compile(r"\blet me think\b", re.I),
    re.compile(r"\bhere('?| i)s my reasoning\b", re.I),
    re.compile(r"\bstep by step\b", re.I),
    re.compile(r"\bi('?| a)m analyz(?:ing|e)\b", re.I),
    re.compile(r"\bmy thought process\b", re.I),
    re.compile(r"\bi(?:\s+will)?\s+reason\b", re.I),
]

_DEFAULT_JARGON_MAP: dict[str, str] = {
    "eligibility": "fit",
    "procedure": "treatment",
    "procedures": "treatments",
    "consult": "visit",
    "consultation": "visit",
    "clinician consult": "clinician visit",
    "optimize": "improve",
    "utilize": "use",
    "facilitate": "help",
    "initiate": "start",
    "escalate": "route",
    "intake": "front desk calls",
    "stress-test": "quick check",
    "stress test": "quick check",
    "capacity": "call volume",
    "artifact": "report",
    "diagnostic": "check",
    "operational": "day-to-day",
    "throughput": "flow",
    "bandwidth": "time",
}


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def sanitize_reasoning_leak(text: str) -> tuple[str, bool]:
    out = text or ""
    changed = False
    for pat in _REASONING_PATTERNS:
        new = pat.sub("", out)
        if new != out:
            changed = True
            out = new
    out = _normalize_spaces(out)
    if not out:
        out = "Got it."
        changed = True
    return out, changed


def _apply_word_replacements(text: str, replacements: Mapping[str, str]) -> tuple[str, bool]:
    changed = False
    out = text or ""
    for src, dst in replacements.items():
        pat = re.compile(rf"\b{re.escape(src)}\b", re.I)
        new = pat.sub(dst, out)
        if new != out:
            changed = True
            out = new
    return out, changed


def _enforce_sentence_shape(text: str, *, max_words_per_sentence: int = 18, max_clauses: int = 3) -> str:
    parts = re.split(r"([.!?])", text)
    rebuilt: list[str] = []
    for i in range(0, len(parts), 2):
        sent = (parts[i] or "").strip()
        punct = parts[i + 1] if i + 1 < len(parts) else ""
        if not sent:
            continue

        clauses = re.split(r"[,;]", sent)
        clauses = [c.strip() for c in clauses if c.strip()]
        if len(clauses) > max_clauses:
            clauses = clauses[:max_clauses]
        sent = ", ".join(clauses)

        words = sent.split()
        if len(words) > max_words_per_sentence:
            words = words[:max_words_per_sentence]
            sent = " ".join(words)
        rebuilt.append((sent + punct).strip())

    out = " ".join(s for s in rebuilt if s).strip()
    return out or "Got it."


def enforce_plain_language(text: str, *, jargon_map: Mapping[str, str] | None = None) -> tuple[str, bool]:
    map_to_use = dict(_DEFAULT_JARGON_MAP)
    if jargon_map:
        map_to_use.update(jargon_map)
    out, changed = _apply_word_replacements(text, map_to_use)
    shaped = _enforce_sentence_shape(out)
    if shaped != out:
        changed = True
    return _normalize_spaces(shaped), changed


def _count_syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", (word or "").lower())
    if not w:
        return 1
    groups = re.findall(r"[aeiouy]+", w)
    n = max(1, len(groups))
    if w.endswith("e") and n > 1:
        n -= 1
    return max(1, n)


def readability_grade(text: str) -> int:
    txt = _normalize_spaces(text)
    if not txt:
        return 1
    sentences = max(1, len([s for s in re.split(r"[.!?]+", txt) if s.strip()]))
    words = re.findall(r"\b[\w']+\b", txt)
    if not words:
        return 1
    word_count = len(words)
    syllables = sum(_count_syllables(w) for w in words)
    grade = 0.39 * (word_count / sentences) + 11.8 * (syllables / word_count) - 15.59
    if grade < 1:
        return 1
    return int(round(grade))


def guard_user_text(
    *,
    text: str,
    metrics: Metrics,
    plain_language_mode: bool,
    no_reasoning_leak: bool,
    jargon_blocklist_enabled: bool,
) -> str:
    out = text or ""

    if no_reasoning_leak:
        out, changed = sanitize_reasoning_leak(out)
        if changed:
            metrics.inc(VIC["voice_reasoning_leak_total"], 1)

    if plain_language_mode and jargon_blocklist_enabled:
        out, changed = enforce_plain_language(out)
        if changed:
            metrics.inc(VIC["voice_jargon_violation_total"], 1)

    grade = readability_grade(out)
    metrics.observe(VIC["voice_readability_grade"], grade)
    return _normalize_spaces(out)
