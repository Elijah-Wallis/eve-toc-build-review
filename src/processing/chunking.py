from __future__ import annotations

import re

_BOUNDARY = re.compile(r"[,.!?;:]")


class SpeakableChunker:
    def __init__(self, *, min_words: int = 4) -> None:
        self._buf = ""
        self._min_words = int(min_words)

    def push(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        while True:
            words = len(re.findall(r"\b\w+\b", self._buf))
            if words < self._min_words:
                break
            m = _BOUNDARY.search(self._buf)
            if not m:
                break
            idx = m.end()
            chunk = self._buf[:idx].strip()
            self._buf = self._buf[idx:].lstrip()
            if chunk:
                out.append(chunk)
        return out

    def flush(self) -> str:
        out = self._buf.strip()
        self._buf = ""
        return out
