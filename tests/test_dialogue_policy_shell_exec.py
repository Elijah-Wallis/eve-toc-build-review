from __future__ import annotations

from app.dialogue_policy import SlotState, decide_action
from app.protocol import TranscriptUtterance


def test_shell_command_routed_to_tool_request() -> None:
    state = SlotState()
    action = decide_action(
        state=state,
        transcript=[TranscriptUtterance(role="user", content="/shell python3 -V")],
        needs_apology=False,
        safety_kind="none",
        safety_message="",
    )
    assert action.action_type == "Inform"
    assert action.payload.get("info_type") == "shell_exec"
    assert len(action.tool_requests) == 1
    req = action.tool_requests[0]
    assert req.name == "run_shell_command"
    assert req.arguments.get("command") == "python3 -V"


def test_shell_command_not_triggered_for_normal_message() -> None:
    state = SlotState()
    action = decide_action(
        state=state,
        transcript=[TranscriptUtterance(role="user", content="Can I get Tuesday availability?")],
        needs_apology=False,
        safety_kind="none",
        safety_message="",
    )
    assert not any(r.name == "run_shell_command" for r in action.tool_requests)
