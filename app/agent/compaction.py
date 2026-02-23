from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompactionContext:
    open_objectives: str
    pending_failures: str
    active_guardrails: str
    last_green_baseline: str


def build_compaction_summary(context: CompactionContext) -> str:
    parts = [
        f"open_objectives={context.open_objectives or 'unknown'}",
        f"pending_failures={context.pending_failures or 'none'}",
        f"active_guardrails={context.active_guardrails or 'default'}",
        f"last_green_baseline={context.last_green_baseline or 'unknown'}",
    ]
    return "Compaction context: " + "; ".join(parts) + "."
