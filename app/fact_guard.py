from __future__ import annotations

import re
from dataclasses import dataclass


_PH_RE = re.compile(r"\[\[([A-Z0-9_]+)\]\]")


@dataclass(frozen=True, slots=True)
class FactTemplate:
    template: str
    placeholders: dict[str, str]

    def render(self, text: str | None = None) -> str:
        out = str(self.template if text is None else text)
        for k, v in self.placeholders.items():
            out = out.replace(f"[[{k}]]", str(v))
        return out

    @property
    def required_tokens(self) -> list[str]:
        return [f"[[{k}]]" for k in self.placeholders.keys()]


def validate_rewrite(*, rewritten: str, required_tokens: list[str]) -> bool:
    text = str((rewritten or "")).strip()
    if not text:
        return False

    # All placeholders must remain exactly present.
    for token in required_tokens:
        if token not in text:
            return False

    # No numeric literals outside placeholders.
    scrubbed = str(text)
    for token in required_tokens:
        scrubbed = scrubbed.replace(token, " ")
    if re.search(r"\d", scrubbed):
        return False
    return True
