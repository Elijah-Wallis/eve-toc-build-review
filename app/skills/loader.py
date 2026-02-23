from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .types import Skill


_REQUIRED_KEYS = ("id", "intent", "inputs", "outputs", "constraints", "commands", "tests")


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, str] = {}
    end = -1
    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "---":
            end = i
            break
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip().lower()
        val = v.strip()
        if val.startswith('"') and val.endswith('"') and len(val) >= 2:
            val = val[1:-1]
        meta[key] = val
    if end < 0:
        return {}, text
    body = "\n".join(lines[end + 1 :]).strip()
    return meta, body


def load_skill_file(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    missing = [k for k in _REQUIRED_KEYS if not (meta.get(k) or "").strip()]
    if missing:
        raise ValueError(f"skill missing required keys: {', '.join(missing)} ({path})")
    return Skill(
        id=meta["id"].strip(),
        intent=meta["intent"].strip(),
        inputs=meta["inputs"].strip(),
        outputs=meta["outputs"].strip(),
        constraints=meta["constraints"].strip(),
        commands=meta["commands"].strip(),
        tests=meta["tests"].strip(),
        body=body,
        source_path=str(path),
    )


def load_skills(skills_dir: str | Path) -> list[Skill]:
    root = Path(skills_dir)
    if not root.exists() or not root.is_dir():
        return []
    out: list[Skill] = []
    for p in sorted(root.rglob("*.md")):
        try:
            out.append(load_skill_file(p))
        except Exception:
            continue
    return out


def validate_skills(skills: Iterable[Skill]) -> list[str]:
    errs: list[str] = []
    seen: set[str] = set()
    for s in skills:
        if s.id in seen:
            errs.append(f"duplicate skill id: {s.id}")
        seen.add(s.id)
        if len(s.body.strip()) < 10:
            errs.append(f"skill body too short: {s.id}")
    return errs
