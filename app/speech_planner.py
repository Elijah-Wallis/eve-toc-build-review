from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from .metrics import Metrics, VIC
from .trace import hash_segment


SpeechMarkupMode = Literal["DASH_PAUSE", "RAW_TEXT", "SSML"]
DashPauseScope = Literal["PROTECTED_ONLY", "SEGMENT_BOUNDARY"]

PlanReason = Literal[
    "ACK",
    "FILLER",
    "CONTENT",
    "BACKCHANNEL",
    "CLARIFY",
    "CONFIRM",
    "REPAIR",
    "ERROR",
    "CLOSING",
]


SegmentPurpose = Literal[
    "ACK",
    "FILLER",
    "CONTENT",
    "BACKCHANNEL",
    "CLARIFY",
    "CONFIRM",
    "REPAIR",
    "CONTROL",
    "CLOSING",
]


ProtectedSpanKind = Literal["PRICE", "TIME", "DATE", "PHONE", "DIGITS"]


@dataclass(frozen=True, slots=True)
class SourceRef:
    kind: str
    id: str


@dataclass(frozen=True, slots=True)
class ProtectedSpan:
    kind: ProtectedSpanKind
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class SpeechSegment:
    segment_index: int
    purpose: SegmentPurpose
    ssml: str
    plain_text: str
    interruptible: bool
    safe_interrupt_point: bool
    expected_duration_ms: int
    contains_protected_span: bool
    protected_spans: list[ProtectedSpan]
    requires_tool_evidence: bool
    tool_evidence_ids: list[str]

    def segment_hash(self, *, epoch: int, turn_id: int) -> str:
        return hash_segment(self.ssml, self.purpose, epoch, turn_id)


@dataclass(frozen=True, slots=True)
class SpeechPlan:
    session_id: str
    call_id: str
    turn_id: int
    epoch: int
    plan_id: str
    segments: list[SpeechSegment]
    created_at_ms: int
    reason: PlanReason
    source_refs: list[SourceRef] = field(default_factory=list)
    disclosure_included: bool = False


_MICRO_CHUNK_CACHE_MAX = 1024
_MICRO_CHUNK_CACHE: "OrderedDict[tuple[Any, ...], tuple[SpeechSegment, ...]]" = (
    OrderedDict()
)
_SCRIPT_TEXT_CACHE_MAX = 256
_SCRIPT_TEXT_CACHE: "OrderedDict[tuple[Any, ...], tuple[SpeechSegment, ...]]" = OrderedDict()


_PRICE_PAT = re.compile(r"(\$\s*\d+(?:\.\d+)?)")
_PHONE_PAT = re.compile(r"\b(\d{3})[\s\-\)]*(\d{3})[\s\-]*(\d{4})\b")
_TIME_PAT = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
_DIGITS_PAT = re.compile(r"\d+")


def _det_break_ms(segment_index: int) -> int:
    # Deterministic "random" in [150, 400].
    return 150 + ((segment_index * 77) % 251)


def dash_pause(*, units: int) -> str:
    """
    Retell pause primitive: spaced dashes.

    Each unit is exactly " - " (spaces around dash). Repeating units yields double spaces
    between dashes naturally (" -  -  - ").
    """
    if int(units) <= 0:
        return ""
    return " - " * int(units)


def _dash_pause_units_for_break(*, break_ms: int, dash_pause_unit_ms: int) -> int:
    u = int(dash_pause_unit_ms)
    if u <= 0:
        return 0
    b = max(0, int(break_ms))
    # Round to nearest pause unit, but always emit at least one unit for non-last segments.
    return max(1, int((b + (u // 2)) // u))


def _find_protected_spans(text: str) -> list[ProtectedSpan]:
    spans: list[ProtectedSpan] = []

    for m in _PHONE_PAT.finditer(text):
        spans.append(ProtectedSpan(kind="PHONE", start=m.start(), end=m.end()))

    for m in _PRICE_PAT.finditer(text):
        spans.append(ProtectedSpan(kind="PRICE", start=m.start(), end=m.end()))

    for m in _TIME_PAT.finditer(text):
        spans.append(ProtectedSpan(kind="TIME", start=m.start(), end=m.end()))

    # Generic digits (avoid double-marking ones inside phone/price/time spans).
    covered = [False] * (len(text) + 1)
    for s in spans:
        for i in range(s.start, s.end):
            if 0 <= i < len(covered):
                covered[i] = True

    for m in _DIGITS_PAT.finditer(text):
        if any(covered[i] for i in range(m.start(), m.end())):
            continue
        spans.append(ProtectedSpan(kind="DIGITS", start=m.start(), end=m.end()))

    spans.sort(key=lambda s: (s.start, s.end))
    return spans


def _digit_pause_ms_for_spans(
    *,
    text: str,
    spans: list[ProtectedSpan],
    purpose: SegmentPurpose,
    digit_dash_pause_unit_ms: int,
) -> int:
    extra = 0
    unit = int(digit_dash_pause_unit_ms)
    if unit <= 0:
        unit = 0
    for sp in spans:
        if sp.kind == "PHONE" or (sp.kind == "DIGITS" and purpose in {"CONFIRM", "REPAIR"}):
            digits = re.sub(r"\D+", "", text[sp.start : sp.end])
            if digits:
                extra += max(0, len(digits) - 1) * unit
    return int(extra)


def _apply_protected_span_formatting(
    *,
    text: str,
    spans: list[ProtectedSpan],
    purpose: SegmentPurpose,
) -> str:
    """
    Render protected spans into a Retell-friendly "read slowly" format for digits/phone.

    - PHONE spans are always rendered as digits separated by spaced dashes.
    - DIGITS spans are rendered that way only for CONFIRM/REPAIR purposes (avoid spacing normal numbers).
    """
    if not spans:
        return text

    out: list[str] = []
    cur = 0
    for sp in spans:
        out.append(text[cur : sp.start])
        chunk = text[sp.start : sp.end]
        if sp.kind == "PHONE" or (sp.kind == "DIGITS" and purpose in {"CONFIRM", "REPAIR"}):
            digits = re.sub(r"\D+", "", chunk)
            if digits:
                out.append(" - ".join(list(digits)))
            else:
                out.append(chunk)
        else:
            out.append(chunk)
        cur = sp.end
    out.append(text[cur:])
    return "".join(out)


def _boundary_pause(
    *,
    mode: SpeechMarkupMode,
    break_ms: int,
    dash_pause_unit_ms: int,
) -> tuple[str, int]:
    """
    Returns (suffix_text, pause_ms).
    """
    if mode == "RAW_TEXT":
        return ("", 0)
    if mode == "SSML":
        return (f'<break time="{int(break_ms)}ms"/>', int(break_ms))
    # DASH_PAUSE
    units = _dash_pause_units_for_break(break_ms=int(break_ms), dash_pause_unit_ms=int(dash_pause_unit_ms))
    return (dash_pause(units=units), units * int(dash_pause_unit_ms))


def _estimate_expected_ms(
    *,
    plain_text: str,
    purpose: SegmentPurpose,
    pace_ms_per_char: int,
    spans: list[ProtectedSpan],
    mode: SpeechMarkupMode,
    break_ms: int,
    include_boundary_pause: bool,
    dash_pause_unit_ms: int,
    digit_dash_pause_unit_ms: int,
    dash_pause_scope: DashPauseScope,
) -> int:
    base = len(plain_text) * int(pace_ms_per_char)
    digit_extra = _digit_pause_ms_for_spans(
        text=plain_text,
        spans=spans,
        purpose=purpose,
        digit_dash_pause_unit_ms=int(digit_dash_pause_unit_ms),
    )
    boundary_ms = 0
    if include_boundary_pause and (
        mode == "SSML" or (mode == "DASH_PAUSE" and dash_pause_scope == "SEGMENT_BOUNDARY")
    ):
        _, boundary_ms = _boundary_pause(
            mode=mode,
            break_ms=int(break_ms),
            dash_pause_unit_ms=int(dash_pause_unit_ms),
        )
    return max(0, int(base + digit_extra + boundary_ms))


def _canonical_plan_id(
    *,
    session_id: str,
    call_id: str,
    turn_id: int,
    epoch: int,
    reason: PlanReason,
    segments: list[SpeechSegment],
    disclosure_included: bool,
) -> str:
    payload = {
        "session_id": session_id,
        "call_id": call_id,
        "turn_id": turn_id,
        "epoch": epoch,
        "reason": reason,
        "disclosure_included": bool(disclosure_included),
        "segments": [
            {"purpose": s.purpose, "ssml": s.ssml, "interruptible": s.interruptible}
            for s in segments
        ],
    }
    blob = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True, slots=True)
class _SegmentDraft:
    purpose: SegmentPurpose
    plain_text: str
    interruptible: bool
    requires_tool_evidence: bool
    tool_evidence_ids: list[str]


def micro_chunk_text(
    *,
    text: str,
    max_expected_ms: int,
    pace_ms_per_char: int,
    purpose: SegmentPurpose,
    interruptible: bool,
    requires_tool_evidence: bool,
    tool_evidence_ids: list[str],
    max_monologue_expected_ms: Optional[int] = None,
    markup_mode: SpeechMarkupMode = "DASH_PAUSE",
    dash_pause_unit_ms: int = 200,
    digit_dash_pause_unit_ms: int = 150,
    dash_pause_scope: DashPauseScope = "PROTECTED_ONLY",
    include_trailing_pause: bool = False,
) -> list[SpeechSegment]:
    """
    Split text into breath-group segments under max_expected_ms (deterministic).
    """
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return []

    cache_key = (
        cleaned,
        int(max_expected_ms),
        int(pace_ms_per_char),
        purpose,
        bool(interruptible),
        bool(requires_tool_evidence),
        tuple(sorted(set(tool_evidence_ids))),
        int(max_monologue_expected_ms or 0),
        str(markup_mode),
        int(dash_pause_unit_ms),
        int(digit_dash_pause_unit_ms),
        str(dash_pause_scope),
        bool(include_trailing_pause),
    )
    cached = _MICRO_CHUNK_CACHE.get(cache_key)
    if cached is not None:
        _MICRO_CHUNK_CACHE.move_to_end(cache_key)
        return list(cached)

    # Clause boundary splitter.
    parts = re.split(r"(?<=[\.!\?;])\s+|,\s+|\s+(?:and|but|so)\s+", cleaned)
    parts = [p.strip() for p in parts if p and p.strip()]

    drafts: list[_SegmentDraft] = []
    buf: list[str] = []

    def est_candidate(plain: str, *, next_index: int) -> int:
        spans = _find_protected_spans(plain)
        return _estimate_expected_ms(
            plain_text=plain,
            purpose=purpose,
            pace_ms_per_char=int(pace_ms_per_char),
            spans=spans,
            mode=markup_mode,
            break_ms=_det_break_ms(next_index),
            include_boundary_pause=True,
            dash_pause_unit_ms=int(dash_pause_unit_ms),
            digit_dash_pause_unit_ms=int(digit_dash_pause_unit_ms),
            dash_pause_scope=dash_pause_scope,
        )

    def flush_buf() -> None:
        nonlocal buf, drafts
        if not buf:
            return
        plain = " ".join(buf).strip()
        if plain:
            drafts.append(
                _SegmentDraft(
                    purpose=purpose,
                    plain_text=plain,
                    interruptible=bool(interruptible),
                    requires_tool_evidence=bool(requires_tool_evidence),
                    tool_evidence_ids=list(tool_evidence_ids),
                )
            )
        buf = []

    def add_part(part_text: str) -> None:
        nonlocal buf, drafts
        part_text = part_text.strip()
        if not part_text:
            return
        if not buf:
            # If a single part is too long, split by words deterministically.
            if est_candidate(part_text, next_index=len(drafts)) > int(max_expected_ms):
                words = part_text.split(" ")
                wbuf: list[str] = []
                for w in words:
                    if not w:
                        continue
                    cand = " ".join(wbuf + [w]).strip()
                    if wbuf and est_candidate(cand, next_index=len(drafts)) > int(max_expected_ms):
                        buf = wbuf
                        flush_buf()
                        wbuf = [w]
                    else:
                        wbuf.append(w)
                if wbuf:
                    buf = wbuf
                    flush_buf()
                return

            buf.append(part_text)
            return

        cand = (" ".join(buf + [part_text])).strip()
        if est_candidate(cand, next_index=len(drafts)) > int(max_expected_ms):
            flush_buf()
            buf.append(part_text)
        else:
            buf.append(part_text)

    for part in parts:
        add_part(part)
    flush_buf()

    if max_monologue_expected_ms is not None and purpose == "CONTENT":
        drafts = _insert_checkins_drafts(
            drafts=drafts,
            max_monologue_expected_ms=int(max_monologue_expected_ms),
            pace_ms_per_char=int(pace_ms_per_char),
            digit_dash_pause_unit_ms=int(digit_dash_pause_unit_ms),
        )

    # Render drafts to final SpeechSegments with stable indices and appropriate pause suffixes.
    segments: list[SpeechSegment] = []
    last_index = len(drafts) - 1
    for i, d in enumerate(drafts):
        plain = d.plain_text
        spans = _find_protected_spans(plain)
        body = _apply_protected_span_formatting(text=plain, spans=spans, purpose=d.purpose)
        break_ms = _det_break_ms(i)
        include_pause = bool(include_trailing_pause) or (i < last_index)
        if markup_mode == "RAW_TEXT":
            include_pause = False
        elif markup_mode == "DASH_PAUSE" and dash_pause_scope != "SEGMENT_BOUNDARY":
            include_pause = False
        suffix, boundary_ms = ("", 0)
        if include_pause:
            suffix, boundary_ms = _boundary_pause(
                mode=markup_mode,
                break_ms=int(break_ms),
                dash_pause_unit_ms=int(dash_pause_unit_ms),
            )
        # Important: Retell concatenates streaming chunks exactly as sent. If we emit multiple
        # segments for the same response_id, we must preserve word boundaries across chunk
        # boundaries (otherwise you get "thisor" / "Eve.Is"). We do this deterministically
        # by appending a single space to non-final segments when the next segment begins with
        # an alphanumeric character and the current chunk does not already end in whitespace.
        #
        # We intentionally avoid doing this in SSML mode to minimize surprises for the
        # experimental path.
        out_text = body + suffix
        if markup_mode != "SSML" and i < last_index:
            nxt = drafts[i + 1].plain_text.lstrip()
            if nxt:
                nxt0 = nxt[0]
                if (
                    out_text
                    and not out_text[-1].isspace()
                    and not nxt0.isspace()
                    and (nxt0.isalnum() or nxt0 in {"$", "(", "[", "\"", "'"})
                ):
                    out_text += " "
        digit_extra = _digit_pause_ms_for_spans(
            text=plain,
            spans=spans,
            purpose=d.purpose,
            digit_dash_pause_unit_ms=int(digit_dash_pause_unit_ms),
        )
        expected = max(
            0,
            int(len(plain) * int(pace_ms_per_char) + digit_extra + int(boundary_ms)),
        )
        segments.append(
            SpeechSegment(
                segment_index=i,
                purpose=d.purpose,
                ssml=out_text,
                plain_text=plain,
                interruptible=bool(d.interruptible),
                safe_interrupt_point=True,
                expected_duration_ms=int(expected),
                contains_protected_span=bool(spans),
                protected_spans=spans,
                requires_tool_evidence=bool(d.requires_tool_evidence),
                tool_evidence_ids=list(d.tool_evidence_ids),
            )
        )

    _MICRO_CHUNK_CACHE[cache_key] = tuple(segments)
    while len(_MICRO_CHUNK_CACHE) > _MICRO_CHUNK_CACHE_MAX:
        _MICRO_CHUNK_CACHE.popitem(last=False)
    return segments


def micro_chunk_text_cached(
    *,
    text: str,
    max_expected_ms: int,
    pace_ms_per_char: int,
    purpose: SegmentPurpose,
    interruptible: bool,
    requires_tool_evidence: bool,
    tool_evidence_ids: list[str],
    max_monologue_expected_ms: Optional[int] = None,
    markup_mode: SpeechMarkupMode = "DASH_PAUSE",
    dash_pause_unit_ms: int = 200,
    digit_dash_pause_unit_ms: int = 150,
    dash_pause_scope: DashPauseScope = "PROTECTED_ONLY",
    include_trailing_pause: bool = False,
    slot_snapshot_signature: str = "",
    intent_signature: str = "",
) -> list[SpeechSegment]:
    """Memoized wrapper used by deterministic fast paths."""
    cache_key = (
        slot_snapshot_signature,
        intent_signature,
        re.sub(r"\s+", " ", (text or "").strip()),
        int(max_expected_ms),
        int(pace_ms_per_char),
        purpose,
        bool(interruptible),
        bool(requires_tool_evidence),
        tuple(sorted(set(tool_evidence_ids))),
        int(max_monologue_expected_ms or 0),
        str(markup_mode),
        int(dash_pause_unit_ms),
        int(digit_dash_pause_unit_ms),
        str(dash_pause_scope),
        bool(include_trailing_pause),
    )
    cached = _SCRIPT_TEXT_CACHE.get(cache_key)
    if cached is not None:
        _SCRIPT_TEXT_CACHE.move_to_end(cache_key)
        return list(cached)

    chunks = micro_chunk_text(
        text=text,
        max_expected_ms=max_expected_ms,
        pace_ms_per_char=pace_ms_per_char,
        purpose=purpose,
        interruptible=interruptible,
        requires_tool_evidence=requires_tool_evidence,
        tool_evidence_ids=tool_evidence_ids,
        max_monologue_expected_ms=max_monologue_expected_ms,
        markup_mode=markup_mode,
        dash_pause_unit_ms=dash_pause_unit_ms,
        digit_dash_pause_unit_ms=digit_dash_pause_unit_ms,
        dash_pause_scope=dash_pause_scope,
        include_trailing_pause=include_trailing_pause,
    )
    _SCRIPT_TEXT_CACHE[cache_key] = tuple(chunks)
    while len(_SCRIPT_TEXT_CACHE) > _SCRIPT_TEXT_CACHE_MAX:
        _SCRIPT_TEXT_CACHE.popitem(last=False)
    return chunks


@dataclass(slots=True)
class StreamingChunker:
    """
    Helper for streaming text sources (LLM token deltas).

    The chunker accumulates deltas and periodically flushes them into SpeechSegments using the
    same deterministic micro-chunking and Retell markup rules as non-streaming paths.
    """

    max_expected_ms: int
    pace_ms_per_char: int
    purpose: SegmentPurpose
    interruptible: bool
    requires_tool_evidence: bool
    tool_evidence_ids: list[str]
    markup_mode: SpeechMarkupMode = "DASH_PAUSE"
    dash_pause_unit_ms: int = 200
    digit_dash_pause_unit_ms: int = 150
    dash_pause_scope: DashPauseScope = "PROTECTED_ONLY"
    _buf: str = ""

    def push(self, *, delta: str) -> list[SpeechSegment]:
        if not delta:
            return []
        self._buf += str(delta)
        if not self._should_flush():
            return []
        return self._flush(include_trailing_pause=True)

    def flush_final(self) -> list[SpeechSegment]:
        return self._flush(include_trailing_pause=False)

    def _buf_expected_ms(self) -> int:
        plain = re.sub(r"\s+", " ", (self._buf or "").strip())
        if not plain:
            return 0
        spans = _find_protected_spans(plain)
        digit_extra = _digit_pause_ms_for_spans(
            text=plain,
            spans=spans,
            purpose=self.purpose,
            digit_dash_pause_unit_ms=int(self.digit_dash_pause_unit_ms),
        )
        return max(0, int(len(plain) * int(self.pace_ms_per_char) + digit_extra))

    def _should_flush(self) -> bool:
        plain = (self._buf or "").strip()
        if not plain:
            return False
        if plain.endswith((".", "!", "?", ";")):
            return True
        return self._buf_expected_ms() >= int(self.max_expected_ms)

    def _flush(self, *, include_trailing_pause: bool) -> list[SpeechSegment]:
        plain = re.sub(r"\s+", " ", (self._buf or "").strip())
        self._buf = ""
        if not plain:
            return []
        return micro_chunk_text(
            text=plain,
            max_expected_ms=int(self.max_expected_ms),
            pace_ms_per_char=int(self.pace_ms_per_char),
            purpose=self.purpose,
            interruptible=bool(self.interruptible),
            requires_tool_evidence=bool(self.requires_tool_evidence),
            tool_evidence_ids=list(self.tool_evidence_ids),
            markup_mode=self.markup_mode,
            dash_pause_unit_ms=int(self.dash_pause_unit_ms),
            digit_dash_pause_unit_ms=int(self.digit_dash_pause_unit_ms),
            dash_pause_scope=self.dash_pause_scope,
            include_trailing_pause=bool(include_trailing_pause),
        )


def _insert_checkins_drafts(
    *,
    drafts: list[_SegmentDraft],
    max_monologue_expected_ms: int,
    pace_ms_per_char: int,
    digit_dash_pause_unit_ms: int,
) -> list[_SegmentDraft]:
    if max_monologue_expected_ms <= 0:
        return drafts

    out: list[_SegmentDraft] = []
    since_checkin = 0
    for d in drafts:
        spans = _find_protected_spans(d.plain_text)
        expected_wo_boundary = max(
            0,
            int(
                len(d.plain_text) * int(pace_ms_per_char)
                + _digit_pause_ms_for_spans(
                    text=d.plain_text,
                    spans=spans,
                    purpose=d.purpose,
                    digit_dash_pause_unit_ms=int(digit_dash_pause_unit_ms),
                )
            ),
        )
        if out and since_checkin + expected_wo_boundary > int(max_monologue_expected_ms):
            out.append(
                _SegmentDraft(
                    purpose="CLARIFY",
                    plain_text="Want me to keep going?",
                    interruptible=True,
                    requires_tool_evidence=False,
                    tool_evidence_ids=[],
                )
            )
            since_checkin = 0

        out.append(d)
        since_checkin += expected_wo_boundary

    return out


def build_plan(
    *,
    session_id: str,
    call_id: str,
    turn_id: int,
    epoch: int,
    created_at_ms: int,
    reason: PlanReason,
    segments: list[SpeechSegment],
    source_refs: Optional[list[SourceRef]] = None,
    disclosure_included: bool = False,
    metrics: Optional[Metrics] = None,
) -> SpeechPlan:
    plan_id = _canonical_plan_id(
        session_id=session_id,
        call_id=call_id,
        turn_id=turn_id,
        epoch=epoch,
        reason=reason,
        segments=segments,
        disclosure_included=bool(disclosure_included),
    )
    plan = SpeechPlan(
        session_id=session_id,
        call_id=call_id,
        turn_id=turn_id,
        epoch=epoch,
        plan_id=plan_id,
        segments=list(segments),
        created_at_ms=int(created_at_ms),
        reason=reason,
        source_refs=list(source_refs or []),
        disclosure_included=bool(disclosure_included),
    )

    if metrics is not None:
        metrics.observe(VIC["segment_count_per_turn"], len(segments))
        for seg in segments:
            metrics.observe(VIC["segment_expected_duration_ms"], seg.expected_duration_ms)

    return plan


def enforce_vic_tool_grounding_or_fallback(
    *,
    plan: SpeechPlan,
    metrics: Metrics,
) -> SpeechPlan:
    """
    VIC-H01/H02: If a segment requires tool evidence, it must have tool_evidence_ids.
    If violated, hard-fallback into an ERROR plan without numbers.
    """

    for seg in plan.segments:
        if seg.requires_tool_evidence and not seg.tool_evidence_ids:
            metrics.inc(VIC["factual_segment_without_tool_evidence_total"], 1)
            metrics.inc(VIC["fallback_used_total"], 1)
            fallback_text = "I can check that for you, but I don't want to guess. Could I get a little more detail?"
            fb_segs = micro_chunk_text(
                text=fallback_text,
                max_expected_ms=1200,
                pace_ms_per_char=20,
                purpose="CONTENT",
                interruptible=True,
                requires_tool_evidence=False,
                tool_evidence_ids=[],
            )
            return build_plan(
                session_id=plan.session_id,
                call_id=plan.call_id,
                turn_id=plan.turn_id,
                epoch=plan.epoch,
                created_at_ms=plan.created_at_ms,
                reason="ERROR",
                segments=fb_segs,
                source_refs=plan.source_refs,
                disclosure_included=plan.disclosure_included,
                metrics=metrics,
            )

    return plan
