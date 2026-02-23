# Retell WebSocket Hardening: Lessons Learned

This document captures the root causes, fixes, and guardrails from the Retell websocket production-hardening pass.

## Root Causes (By Layer)

### Protocol Layer (`app/protocol.py`)
- **Schema drift closed sessions**: inbound frames that were valid JSON but not recognized by the inbound union caused validation errors that previously cascaded into session termination.
- **Missing `clear` event in the inbound union**: Retell’s explicit interruption signal (`interaction_type="clear"`) was not represented in the inbound parse union, forcing validation errors.

### Transport Layer (`app/transport_ws.py`)
- **Over-eager fatal error policy**: schema validation errors were treated as fatal (`TransportClosed(BAD_SCHEMA)`), causing unnecessary websocket closure on forward-compatible/unknown frames.
- **Need to keep hard-fails hard**: oversized frames and invalid JSON should still terminate to avoid runaway memory usage and undefined state.

### Orchestration Layer (`app/orchestrator.py`)
- **Waiter lifecycle hazards**: per-turn queue/task churn can strand a waiter on an orphaned queue. Without an explicit “active turn output queue” swap rule, the run loop can hang.
- **Interruption correctness**: barge-in/clear must cancel turn work, drop stale chunks, and avoid replay/overlap.
- **State rollback needs to be interruption-aware**: rolling back state on every new epoch can break confirmation flows when a user responds quickly before the terminal frame.

### Turn Execution Layer (`app/turn_handler.py`)
- **Streaming zombie risk**: shielded streaming patterns can keep producers alive after cancellation and leak tasks.
- **Prompt completeness**: the TurnHandler needs full transcript history injected to build consistent prompts (and to support EVE v7 constraints when enabled).

### Server Layer (`app/server.py`)
- **Route drift**: multiple websocket routes (`/ws/{call_id}` vs canonical) can create production inconsistency and client misconfiguration.

### Tooling & Policy (`app/tools.py`, `app/dialogue_policy.py`, `orchestration/eve-v7-orchestrator.yaml`)
- **Tool name drift**: legacy `mark_dnc` references needed normalization to canonical `mark_dnc_compliant`.
- **DNC compliance must be explicit**: explicit “stop calling me” style signals must trigger a tool invocation and an end-call terminal response.

## Fixes (Concrete Changes)

### 1) Protocol: add `clear` and tolerate future schemas
- Added `InboundClear` with `interaction_type="clear"` so inbound validation succeeds.
- Kept outbound models unchanged.
- Files:
  - `app/protocol.py`

### 2) Transport: schema errors are non-fatal, JSON/size remain fatal
- Replaced `socket_reader` behavior so:
  - **Schema errors** increment `inbound.bad_schema_total` and continue reading (no websocket close).
  - **Frame too large** and **invalid JSON** remain hard-fail and close.
- Preserved the drop/evict strategy for `update_only`, `response_required`, `reminder_required`, `ping_pong`.
- Files:
  - `app/transport_ws.py`

### 3) Orchestrator: persistent waiters + required caveat + barge-in safety
- Replaced the run-loop with a **persistent waiter** model:
  - persistent inbound waiter
  - persistent speculative waiter
  - bounded turn-output consumer waiter
- Implemented the REQUIRED caveat:
  - track `active_turn_q`
  - if `self._turn_output_q is not active_turn_q`, cancel the existing consumer task and recreate it against the new queue
- Added explicit `InboundClear` handling that routes to the barge-in cancel path.
- Kept stale-chunk suppression with epoch + speak-generation gating.
- Adjusted SlotState rollback semantics to avoid breaking confirmation flows when the old epoch has already emitted chunks:
  - rollback-on-new-epoch only when the old epoch had not emitted a segment yet
- Added B2B stage sync for canonical opener to keep fast-path cache stable under duplicate/reordered transcript snapshots.
- Files:
  - `app/orchestrator.py`

### 4) Config defaults updated (cloud-safe and latency-targeted)
- Updated defaults:
  - `retell_responsiveness=0.8`
  - `retell_interruption_sensitivity=0.8`
  - `retell_reminder_trigger_ms=3000`
  - `vic_tool_filler_threshold_ms=800`
  - `vic_model_filler_threshold_ms=800`
- Forced OFF in `from_env()`:
  - `safe_pre_ack_on_response_required_enabled=False`
  - `interrupt_pre_ack_on_agent_turn_enabled=False`
  - `ultra_fast_pre_ack_enabled=False`
- Files:
  - `app/config.py`

### 5) TurnHandler: transcript injection + streaming stability
- Injected full transcript history into `TurnHandler` and `_build_llm_prompt`.
- Removed shield-based cleanup; streaming now uses task/queue lifecycle that exits cleanly on cancel.
- Ensured ACK emission is deterministic even when `action.payload["message"]` is empty (tool-first turns).
- Files:
  - `app/turn_handler.py`
  - (signature compatibility validated) `app/llm_client.py`, `app/conversation_memory.py`, `app/eve_prompt.py`

### 6) Policy: stronger B2B objections + DNC tool path
- Updated objection messages and opener framing.
- Added DNC tool invocation path (`mark_dnc_compliant`) for explicit rejection.
- Fixed greeting/noise classification edge cases and ontology precedence.
- Files:
  - `app/dialogue_policy.py`

### 7) Server route: canonical enforcement
- Enforced canonical websocket route: `/llm-websocket/{call_id}`
- Removed/neutralized alias drift for `/ws/{call_id}` (default no drift).
- Files:
  - `app/server.py`

### 8) Tools + Orchestration YAML: EVE v7 names and compatibility
- Verified tool map includes:
  - `send_evidence_package`
  - `mark_dnc_compliant`
- Kept compatibility normalization for legacy `mark_dnc` -> `mark_dnc_compliant` where strictly needed.
- Normalized EVE v7 YAML references to `mark_dnc_compliant`.
- Files:
  - `app/tools.py`
  - `orchestration/eve-v7-orchestrator.yaml`

### 9) Tests: coverage for protocol robustness and interruption safety
- Updated latency default assertions for new thresholds.
- Added/updated coverage for:
  - unknown inbound schema does not close websocket
  - `clear` enters barge-in cancellation path
  - no overlap/replay on epoch interruption
  - `mark_dnc_compliant` invocation path
  - connect upgrade + heartbeat ping/pong
- Files:
  - `tests/test_inbound_limits.py`
  - `tests/test_epoch_barge_in.py`
  - `tests/test_keepalive_ping_pong.py`
  - `tests/test_protocol_parsing.py`
  - `tests/test_dnc_tool_invocation.py`
  - `tests/test_latency_defaults.py`

## Guardrails

### Pre-merge checklist
1. `python3 -m pytest -q`
2. Verify websocket route is **only** `/llm-websocket/{call_id}`.
3. Send an unknown-but-valid JSON inbound frame and confirm:
   - session stays open
   - `inbound.bad_schema_total` increments
4. Confirm `clear`:
   - cancels active speaking
   - drops queued non-terminal chunks for the epoch
   - emits an empty terminal chunk to close the epoch cleanly
5. Confirm epoch preemption:
   - no epoch N chunks appear after epoch N+1 begins
6. Confirm tool naming:
   - no active policy path references legacy `mark_dnc`
   - EVE v7 yaml uses `mark_dnc_compliant`

### Metrics to watch in production
- `inbound.bad_schema_total` (should be non-zero over time; should not correlate with ws closes)
- `ws_close_reason_total.*` (watch for spikes, especially schema-related)
- `stale_segment_dropped_total` (should exist but not explode)
- `barge_in_cancel_latency_ms`

## If The Symptom Reappears, Inspect This First

- **Websocket closes after a new inbound event type appears**:
  - `app/protocol.py` inbound union coverage
  - `app/transport_ws.py` schema-error handling path
- **Agent overlaps/replays speech after interruption**:
  - `app/orchestrator.py` `_barge_in_cancel()` + speak-generation gate + stale-drop logic
  - `tests/test_epoch_barge_in.py`
- **Orchestrator hangs with no outbound after interruptions**:
  - `app/orchestrator.py` `active_turn_q` swap logic in `run()`
- **DNC path not firing**:
  - `app/dialogue_policy.py` explicit rejection classifier + tool request
  - `app/tools.py` tool registry + legacy alias normalization

## Metadata
- Date: 2026-02-14
- Branch: `codex/websocket-voice-agent`
- Branch URL: https://github.com/Elijah-Wallis/eve-legal-policies/tree/codex/websocket-voice-agent
- Commit: d025059a9ddef1e8b768f40af22b688a5f82b79f
