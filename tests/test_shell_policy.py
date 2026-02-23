from __future__ import annotations

from app.shell.policy import command_name, parse_allowed_commands, validate_command


def test_parse_allowed_commands() -> None:
    s = parse_allowed_commands("python3,pytest , bash")
    assert s == {"python3", "pytest", "bash"}


def test_validate_denies_dangerous_patterns() -> None:
    d = validate_command("rm -rf /")
    assert d.allowed is False
    assert d.reason.startswith("denied_pattern")


def test_validate_allowlist() -> None:
    d1 = validate_command("python3 -V", allowed_commands={"python3"})
    d2 = validate_command("bash -lc 'echo x'", allowed_commands={"python3"})
    assert d1.allowed is True
    assert d2.allowed is False


def test_command_name() -> None:
    assert command_name("python3 -m pytest") == "python3"
