from __future__ import annotations

from dataclasses import dataclass


_DENY_PATTERNS = (
    "rm -rf /",
    "rm -rf ~",
    "mkfs",
    "shutdown",
    "reboot",
    "git reset --hard",
    "git clean -fd",
    "dd if=",
    "chmod -r 777 /",
)


@dataclass(frozen=True, slots=True)
class ShellPolicyDecision:
    allowed: bool
    reason: str


def parse_allowed_commands(raw: str) -> set[str]:
    out: set[str] = set()
    for item in str(raw or "").split(","):
        v = item.strip()
        if v:
            out.add(v)
    return out


def command_name(command: str) -> str:
    chunks = [x for x in str(command or "").strip().split() if x]
    return chunks[0] if chunks else ""


def validate_command(command: str, *, allowed_commands: set[str] | None = None) -> ShellPolicyDecision:
    cmd = str(command or "").strip()
    if not cmd:
        return ShellPolicyDecision(False, "empty_command")

    low = cmd.lower()
    for pat in _DENY_PATTERNS:
        if pat in low:
            return ShellPolicyDecision(False, f"denied_pattern:{pat}")

    name = command_name(cmd)
    if allowed_commands and name not in allowed_commands:
        return ShellPolicyDecision(False, f"not_in_allowlist:{name}")

    # Avoid interactive shells/tools by default in automation lanes.
    interactive_like = {"vim", "vi", "nano", "less", "more", "top", "htop"}
    if name in interactive_like:
        return ShellPolicyDecision(False, f"interactive_command:{name}")

    return ShellPolicyDecision(True, "ok")
