# Retell WS Brain Contract

Production-grade “Brain” server implementing Retell’s Custom LLM WebSocket contract with deterministic, VIC-gated behavior.

## Endpoints

- WebSocket: `/ws/{call_id}`
- WebSocket (alias): `/llm-websocket/{call_id}`
- Health: `GET /healthz`
- Metrics: `GET /metrics` (Prometheus text format; dots are exported as underscores)

## Wire Protocol (Authoritative)

All WebSocket frames are JSON text. Inbound messages are discriminated by `interaction_type` and outbound by `response_type`.

Source of truth:
- `app/protocol.py`

No invented message types are allowed.

## Connection Flow

On connection open, the server sends:
1. `config` (optional but enabled by default)
2. A BEGIN `response` stream for `response_id=0`:
   - greeting chunks (if `BRAIN_SPEAK_FIRST=true`), then terminal `content_complete=true`, OR
   - an empty terminal `content_complete=true` if waiting for the user

## Keepalive

- Retell may send inbound `ping_pong`. If `RETELL_AUTO_RECONNECT=true`, we respond with outbound `ping_pong` (echo timestamp).
- The server also sends periodic outbound `ping_pong` on `BRAIN_PING_INTERVAL_MS` when `RETELL_AUTO_RECONNECT=true`.
- Keepalive is treated as control-plane traffic:
  - inbound `ping_pong` is prioritized over update-only backlog
  - outbound `ping_pong` is dequeued ahead of speech traffic
  - speech send operations can be preempted when control frames are pending
  - writes use per-frame deadlines; repeated blocked sends trigger session close with reason `WRITE_TIMEOUT_BACKPRESSURE`

Reference: [Retell LLM WebSocket](https://docs.retellai.com/api-references/llm-websocket).  
Operational contract: auto-reconnect keepalive cadence is ~2s and Retell may close/reconnect after ~5s without keepalive traffic.

### Write-Timeout Contract

- Each outbound frame send is bounded by `WS_WRITE_TIMEOUT_MS`.
- On timeout:
  - `ws.write_timeout_total` increments
  - `keepalive.ping_pong_write_timeout_total` increments for ping writes
- On repeated timeouts (`WS_MAX_CONSECUTIVE_WRITE_TIMEOUTS`) and if `WS_CLOSE_ON_WRITE_TIMEOUT=true`:
  - writer emits `TransportClosed(reason=\"WRITE_TIMEOUT_BACKPRESSURE\")`
  - orchestrator ends session and closes websocket
  - Retell can reconnect cleanly

### Inbound Parse/Frame Contract

- Reader rejects oversized frames (`WS_MAX_FRAME_BYTES`) with `TransportClosed(reason=\"FRAME_TOO_LARGE\")`.
- JSON parse failure produces `TransportClosed(reason=\"BAD_JSON\")`.
- Schema validation failure produces `TransportClosed(reason=\"BAD_SCHEMA\")`.
- Unknown fields remain forward-compatible via permissive model extras.

## Epoch Cancellation Rule (Hard)

`response_id` is the **epoch**.

When a new `response_required` / `reminder_required` arrives with `response_id=N`:
- Orchestrator atomically sets current epoch to `N`
- Cancels any in-flight TurnHandler for older epochs
- Drops queued outbound messages for stale epochs
- Writer drops any stale in-flight/queued messages for old epochs

## Same-Epoch Barge-In (Speak-Gen Gate)

When `update_only.turntaking == "user_turn"` arrives while the agent has pending speech:
- Orchestrator bumps an internal **speak-generation** gate (`speak_gen`)
- Writer drops/cancels queued/in-flight outbound chunks from the old `speak_gen`
- Orchestrator immediately emits a terminal empty `response` for the current epoch

This is internal-only; it does **not** change the Retell wire schema.

## Retell Pacing Semantics (Dash Pauses)

Default speech markup mode is **DASH_PAUSE**:
- pauses are represented by spaced dashes: `" - "`
- longer pauses use repeated units: `" - " * N` (produces double spaces between dashes)
- protected digit spans (phone / codes) are rendered read-slowly as: `2 - 1 - 3 - 4`

Reference: [Retell Add Pause](https://docs.retellai.com/build/add-pause).  
Pause token must keep spaces around `-` (`" - "`).

SSML `<break>` tags are **not** used by default; SSML mode exists only as experimental config.

## Backchanneling

Server-generated backchannels via `agent_interrupt` are considered **experimental** and OFF by default.

Recommended: configure backchanneling in the Retell agent settings (`enable_backchannel`, `backchannel_frequency`, `backchannel_words`).

## Security Contract

- Primary supported hardening: IP/CIDR allowlist.
- Shared-secret header and query-token checks are optional and OFF by default.
- Proxy-aware client IP resolution honors `X-Forwarded-For` only when trusted-proxy mode is explicitly enabled and the direct peer is in trusted proxy CIDRs.

References:
- [Retell Setup WebSocket Server](https://docs.retellai.com/integrate-llm/setup-websocket-server)
- [Retell Secure Webhook](https://docs.retellai.com/features/secure-webhook)
