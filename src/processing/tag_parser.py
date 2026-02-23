from __future__ import annotations

import re
from dataclasses import dataclass

from src.interfaces.events import SpeechSegment, StyleModifier


_TAG_PATTERN = re.compile(r"\[(laughs|whispers|sighs|slow|excited)\]", re.I)
_WORD_PATTERN = re.compile(r"\b\w+\b")


@dataclass(frozen=True, slots=True)
class TagScope:
    scope_id: str
    style: StyleModifier
    speed: float
    words_left: int


_STYLE_MAP: dict[str, tuple[StyleModifier, float]] = {
    "laughs": (StyleModifier.LAUGHS, 1.05),
    "whispers": (StyleModifier.WHISPERS, 0.85),
    "sighs": (StyleModifier.SIGHS, 0.9),
    "slow": (StyleModifier.SLOW, 0.8),
    "excited": (StyleModifier.EXCITED, 1.15),
}


def parse_tagged_text(text: str, *, scope_words: int = 5, base_scope_id: str = "base") -> list[SpeechSegment]:
    scope_words = max(1, int(scope_words))
    tokens = _tokenize(text or "")
    out: list[SpeechSegment] = []

    active_style = StyleModifier.BASELINE
    active_speed = 1.0
    words_left = 0
    scope_idx = 0
    scope_id = base_scope_id

    chunk_words: list[str] = []

    def flush() -> None:
        nonlocal chunk_words
        if not chunk_words:
            return
        chunk_text = " ".join(chunk_words).strip()
        wc = len(_WORD_PATTERN.findall(chunk_text))
        if wc == 0:
            chunk_words = []
            return
        out.append(
            SpeechSegment(
                text=chunk_text,
                style_modifier=active_style,
                speed_multiplier=active_speed,
                scope_id=scope_id,
                word_count=wc,
            )
        )
        chunk_words = []

    for tok_type, tok_val in tokens:
        if tok_type == "tag":
            flush()
            tag = tok_val.lower()
            if tag in _STYLE_MAP:
                active_style, active_speed = _STYLE_MAP[tag]
                words_left = scope_words
                scope_idx += 1
                scope_id = f"scope_{scope_idx}_{tag}"
            continue

        # plain text token
        chunk_words.append(tok_val)
        if _WORD_PATTERN.fullmatch(tok_val):
            if words_left > 0:
                words_left -= 1
                if words_left == 0:
                    flush()
                    active_style = StyleModifier.BASELINE
                    active_speed = 1.0
                    scope_id = base_scope_id

    flush()
    return out


def _tokenize(text: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    i = 0
    while i < len(text):
        m = _TAG_PATTERN.search(text, i)
        if not m:
            tail = text[i:].strip()
            if tail:
                parts.extend(("word", w) for w in tail.split())
            break
        pre = text[i : m.start()].strip()
        if pre:
            parts.extend(("word", w) for w in pre.split())
        parts.append(("tag", m.group(1)))
        i = m.end()
    return parts
