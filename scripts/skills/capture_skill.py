#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def slugify(s: str) -> str:
    x = (s or "").strip().lower().replace("-", "_").replace(" ", "_")
    x = _SLUG_RE.sub("_", x)
    x = re.sub(r"_+", "_", x).strip("_")
    return x or "skill"


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture a new skill template from an incident/resolution.")
    ap.add_argument("--id", required=True, help="skill id")
    ap.add_argument("--intent", required=True, help="skill intent")
    ap.add_argument("--inputs", default="user request, context, constraints")
    ap.add_argument("--outputs", default="short answer or action plan")
    ap.add_argument("--constraints", default="must preserve safety/tool grounding")
    ap.add_argument("--commands", default="")
    ap.add_argument("--tests", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--out", default="skills")
    args = ap.parse_args()

    sid = slugify(args.id)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{sid}.md"

    body = (args.notes or "").strip() or (
        "Context:\n"
        "- what failed\n\n"
        "Resolution:\n"
        "- what fixed it\n\n"
        "Runbook:\n"
        "1. detect signal\n"
        "2. apply minimal fix\n"
        "3. verify with tests\n"
    )

    text = (
        "---\n"
        f"id: {sid}\n"
        f"intent: {args.intent.strip()}\n"
        f"inputs: {args.inputs.strip()}\n"
        f"outputs: {args.outputs.strip()}\n"
        f"constraints: {args.constraints.strip()}\n"
        f"commands: {args.commands.strip()}\n"
        f"tests: {args.tests.strip()}\n"
        "---\n"
        f"{body.rstrip()}\n"
    )

    out_path.write_text(text, encoding="utf-8")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
