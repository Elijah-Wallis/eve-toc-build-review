from __future__ import annotations

from pathlib import Path

from app.skills.loader import load_skill_file, load_skills, validate_skills


def test_load_skill_file(tmp_path: Path) -> None:
    p = tmp_path / "s.md"
    p.write_text(
        """---
id: s1
intent: do x
inputs: in
outputs: out
constraints: keep safe
commands: pytest -q
tests: tests/test_x.py
---
Body text here.
""",
        encoding="utf-8",
    )
    s = load_skill_file(p)
    assert s.id == "s1"
    assert "Body text" in s.body


def test_load_skills_and_validate(tmp_path: Path) -> None:
    d = tmp_path / "skills"
    d.mkdir()
    (d / "ok.md").write_text(
        """---
id: s_ok
intent: x
inputs: i
outputs: o
constraints: c
commands: c
tests: t
---
hello world body
""",
        encoding="utf-8",
    )
    (d / "bad.md").write_text("not valid", encoding="utf-8")
    skills = load_skills(d)
    assert [s.id for s in skills] == ["s_ok"]
    assert validate_skills(skills) == []
