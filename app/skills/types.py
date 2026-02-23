from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Skill:
    id: str
    intent: str
    inputs: str
    outputs: str
    constraints: str
    commands: str
    tests: str
    body: str
    source_path: str


@dataclass(frozen=True, slots=True)
class RetrievedSkill:
    skill: Skill
    score: float
