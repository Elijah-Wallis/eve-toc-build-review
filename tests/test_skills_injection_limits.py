from __future__ import annotations

from app.skills.retriever import retrieve_skills
from app.skills.types import Skill


def _mk(i: int) -> Skill:
    return Skill(
        id=f"s{i}",
        intent="pricing timeout fallback",
        inputs="in",
        outputs="out",
        constraints="safe",
        commands="cmd",
        tests="tests/x",
        body="pricing timeout fallback",
        source_path=f"skills/s{i}.md",
    )


def test_retrieve_respects_max_items() -> None:
    skills = [_mk(i) for i in range(10)]
    got = retrieve_skills("pricing timeout fallback", skills, max_items=3)
    assert len(got) == 3
