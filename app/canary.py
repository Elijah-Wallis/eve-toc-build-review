from __future__ import annotations

import hashlib


def _clamp_percent(percent: int) -> int:
    try:
        p = int(percent)
    except Exception:
        p = 0
    return max(0, min(100, p))


def rollout_enabled(subject: str, percent: int) -> bool:
    p = _clamp_percent(percent)
    if p <= 0:
        return False
    if p >= 100:
        return True
    s = (subject or "default").encode("utf-8", errors="replace")
    h = hashlib.sha256(s).hexdigest()
    bucket = int(h[:8], 16) % 100
    return bucket < p

