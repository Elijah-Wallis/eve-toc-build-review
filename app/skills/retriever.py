from __future__ import annotations

import re
from typing import Iterable

from .types import RetrievedSkill, Skill


_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{2,}")


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")}


def _score(skill: Skill, q_tokens: set[str]) -> float:
    if not q_tokens:
        return 0.0
    hay = _tokens(" ".join([skill.id, skill.intent, skill.inputs, skill.outputs, skill.constraints, skill.body]))
    if not hay:
        return 0.0
    overlap = q_tokens.intersection(hay)
    if not overlap:
        return 0.0
    # Simple overlap score with mild boost for id/intent hits.
    base = len(overlap) / max(1, len(q_tokens))
    boosted = base
    sid = _tokens(skill.id)
    sintent = _tokens(skill.intent)
    if q_tokens.intersection(sid):
        boosted += 0.15
    if q_tokens.intersection(sintent):
        boosted += 0.10
    return boosted


def retrieve_skills(query: str, skills: Iterable[Skill], *, max_items: int = 3) -> list[RetrievedSkill]:
    q_tokens = _tokens(query)
    ranked: list[RetrievedSkill] = []
    for s in skills:
        sc = _score(s, q_tokens)
        if sc <= 0:
            continue
        ranked.append(RetrievedSkill(skill=s, score=sc))
    ranked.sort(key=lambda x: (-x.score, x.skill.id))
    return ranked[: max(0, int(max_items))]


def render_skills_for_prompt(items: Iterable[RetrievedSkill]) -> str:
    rows: list[str] = []
    for i, r in enumerate(items, start=1):
        s = r.skill
        rows.append(
            f"Skill {i}: {s.id}\n"
            f"Intent: {s.intent}\n"
            f"Inputs: {s.inputs}\n"
            f"Outputs: {s.outputs}\n"
            f"Constraints: {s.constraints}\n"
            f"Commands: {s.commands}\n"
            f"Tests: {s.tests}\n"
        )
    return "\n".join(rows).strip()
