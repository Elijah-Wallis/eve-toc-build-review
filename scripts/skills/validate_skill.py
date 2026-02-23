#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow direct script execution from repo root without editable install.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.skills.loader import load_skill_file  # noqa: E402


FORBIDDEN_IN_COMMANDS = ("rm -rf /", "git reset --hard", "mkfs", "shutdown", "reboot")


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate a skill markdown file.")
    ap.add_argument("path", help="Path to skill markdown file")
    args = ap.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"missing file: {path}", file=sys.stderr)
        return 2

    try:
        s = load_skill_file(path)
    except Exception as e:
        print(f"invalid skill: {e}", file=sys.stderr)
        return 2

    low_cmd = (s.commands or "").lower()
    for pat in FORBIDDEN_IN_COMMANDS:
        if pat in low_cmd:
            print(f"unsafe commands field contains forbidden pattern: {pat}", file=sys.stderr)
            return 3

    print(f"OK {s.id} ({path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
