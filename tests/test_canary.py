from __future__ import annotations

from app.canary import rollout_enabled


def test_rollout_enabled_bounds() -> None:
    assert rollout_enabled("any", -1) is False
    assert rollout_enabled("any", 0) is False
    assert rollout_enabled("any", 100) is True
    assert rollout_enabled("any", 150) is True


def test_rollout_enabled_is_deterministic() -> None:
    a = rollout_enabled("session-123", 17)
    b = rollout_enabled("session-123", 17)
    assert a is b
