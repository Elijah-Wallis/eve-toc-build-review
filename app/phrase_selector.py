from __future__ import annotations

import hashlib
from typing import Sequence


def select_phrase(
    *,
    options: Sequence[str],
    call_id: str,
    turn_id: int,
    segment_kind: str,
    segment_index: int = 0,
) -> str:
    """
    Deterministic phrase selection for realism without randomness.
    """
    if not options:
        raise ValueError("options must be non-empty")
    seed = f"{call_id}|{int(turn_id)}|{segment_kind}|{int(segment_index)}".encode("utf-8")
    idx = int.from_bytes(hashlib.sha256(seed).digest()[:8], "big") % len(options)
    return str(options[idx])
