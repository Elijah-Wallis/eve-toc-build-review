from __future__ import annotations

from app.skills.retriever import render_skills_for_prompt, retrieve_skills
from app.skills.types import Skill


def _skill(sid: str, intent: str, body: str) -> Skill:
    return Skill(
        id=sid,
        intent=intent,
        inputs="in",
        outputs="out",
        constraints="safe",
        commands="cmd",
        tests="tests/test_x.py",
        body=body,
        source_path=f"skills/{sid}.md",
    )


def test_retrieve_skills_by_overlap() -> None:
    items = [
        _skill("pricing_timeout", "Handle pricing timeout", "tool timeout fallback no numbers"),
        _skill("booking_slots", "Offer booking slots", "offer slots and ask preference"),
    ]
    got = retrieve_skills("pricing tool timeout", items, max_items=3)
    assert got
    assert got[0].skill.id == "pricing_timeout"


def test_render_skills_for_prompt() -> None:
    items = [_skill("x", "intent x", "body")]
    got = retrieve_skills("intent", items, max_items=1)
    txt = render_skills_for_prompt(got)
    assert "Skill 1: x" in txt
    assert "Intent: intent x" in txt
