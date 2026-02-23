# Retell WS Brain Playbook

## Install

Recommended (virtualenv):

```bash
python3 -m pip install -e ".[dev]"
```

Gemini + ops tooling (optional):

```bash
python3 -m pip install -e ".[gemini,ops]"
```

## Run

```bash
python3 -m uvicorn app.server:app --host 0.0.0.0 --port 8080
```

## WebSocket Endpoints

- `ws://{host}/ws/{call_id}`
- `ws://{host}/llm-websocket/{call_id}` (alias)

## Retell Platform Semantics (Important)

Pacing/pauses are **dash-based** by default (Retell style), not SSML:
- pause unit: `" - "`
- digits read slowly: `2 - 1 - 3 - 4`

Configure with:
- `SPEECH_MARKUP_MODE=DASH_PAUSE|RAW_TEXT|SSML`
- `DASH_PAUSE_SCOPE=PROTECTED_ONLY|SEGMENT_BOUNDARY` (default `PROTECTED_ONLY`)

Reference: [Retell Add Pause](https://docs.retellai.com/build/add-pause).  
Use spaced dash tokens (`" - "`), not compact dashes.

## Gemini (P1)

Enable Gemini streaming NLG (optional):
- `BRAIN_USE_LLM_NLG=true`
- `LLM_PROVIDER=gemini`

Gemini Developer API:
- `GEMINI_API_KEY=...`

Vertex AI:
- `GEMINI_VERTEXAI=true`
- `GEMINI_PROJECT=...`
- `GEMINI_LOCATION=global` (often required for preview models)

Model/tuning:
- `GEMINI_MODEL=gemini-3-flash-preview`
- `GEMINI_THINKING_LEVEL=minimal|low|medium|high` (voice default is `minimal`)

## Metrics

- `GET /metrics` returns Prometheus text.
- Exported names replace dots with underscores (example: `vic.turn_final_to_ack_segment_ms` -> `vic_turn_final_to_ack_segment_ms`).

Key VIC metrics to watch:
- `vic.turn_final_to_ack_segment_ms` (target <= 300ms in harness)
- `vic.turn_final_to_first_segment_ms`
- `vic.barge_in_cancel_latency_ms` (p95 target <= 250ms in harness)
- `vic.stale_segment_dropped_total` (must increase under preemption tests)
- `vic.factual_segment_without_tool_evidence_total` (must stay 0)
- `vic.replay_hash_mismatch_total` (must stay 0)

Keepalive/control-plane metrics:
- `keepalive.ping_pong_queue_delay_ms` (target p99 < 100ms in non-stalled conditions)
- `keepalive.ping_pong_missed_deadline_total` (target 0)
- `keepalive.ping_pong_write_attempt_total` (should track expected ping volume)
- `keepalive.ping_pong_write_timeout_total` (target 0 in healthy operation)
- `ws.write_timeout_total` (alert on sustained increase)
- `ws.close_reason_total.WRITE_TIMEOUT_BACKPRESSURE` (near-zero normal; expected in torture tests)
- `inbound.queue_evictions_total` (watch for persistent growth under input floods)
- `memory.transcript_chars_current` / `memory.transcript_utterances_current` (must remain under configured caps)
- `memory.transcript_compactions_total` (expected to rise in very long calls)

Keepalive reference: [Retell LLM WebSocket](https://docs.retellai.com/api-references/llm-websocket).
Retell keepalive expectation in production: ping/pong around every 2s, reconnect/close behavior after roughly 5s without traffic.

## Security Hardening (Optional)

Prefer enforcing allowlists/secrets at the reverse proxy. This server supports optional gating:
- `WS_ALLOWLIST_ENABLED=true`
- `WS_ALLOWLIST_CIDRS="10.0.0.0/8,192.168.1.0/24"`
- `WS_TRUSTED_PROXY_ENABLED=true`
- `WS_TRUSTED_PROXY_CIDRS="10.0.0.0/8"`
- `WS_SHARED_SECRET_ENABLED=true`
- `WS_SHARED_SECRET="..."`
- `WS_SHARED_SECRET_HEADER="X-RETELL-SIGNATURE"`
- `WS_QUERY_TOKEN="..."`
- `WS_QUERY_TOKEN_PARAM="token"`

Recommended posture for Retell:
- use IP allowlisting first (Retell-compatible)
- keep shared-secret optional/off unless your client can send custom headers
- trust `X-Forwarded-For` only with trusted-proxy mode and explicit proxy CIDRs

References:
- [Retell Setup WebSocket Server](https://docs.retellai.com/integrate-llm/setup-websocket-server)
- [Retell Secure Webhook](https://docs.retellai.com/features/secure-webhook)

## Production Default Baseline

- `BRAIN_INBOUND_QUEUE_MAX=256`
- `BRAIN_OUTBOUND_QUEUE_MAX=256`
- `BRAIN_IDLE_TIMEOUT_MS=5000`
- `BRAIN_PING_INTERVAL_MS=2000`
- `WS_WRITE_TIMEOUT_MS=400`
- `WS_CLOSE_ON_WRITE_TIMEOUT=true`
- `WS_MAX_CONSECUTIVE_WRITE_TIMEOUTS=2`
- `WS_MAX_FRAME_BYTES=262144`
- `TRANSCRIPT_MAX_UTTERANCES=200`
- `TRANSCRIPT_MAX_CHARS=50000`
- `LLM_PHRASING_FOR_FACTS_ENABLED=false`

Operational note:
- Priority queues handle ordering under load, but they cannot unblock a stalled kernel send buffer.
- Write deadlines are the hard escape hatch. When exceeded repeatedly, we close intentionally and rely on Retell reconnect.
- This close-on-timeout behavior is correct for real backpressure: without it, a blocked writer can deadlock keepalive and cause prolonged wedge behavior.

## Load Testing

Deterministic in-memory acceptance:

```bash
python3 -m pytest -q tests/acceptance/at_vic_100_sessions.py
python3 -m pytest -q tests/acceptance/at_no_leak_30min.py
python3 scripts/load_test.py --sessions 100
```

Real-socket WebSocket load test (run server first):

```bash
python3 scripts/ws_load_test.py --sessions 25 --turns 2
python3 scripts/ws_load_test.py --sessions 10 --turns 2 --torture-pause-reads-ms 1500 --assert-keepalive
python3 scripts/ws_load_test.py --sessions 10 --duration-sec 300 --turn-interval-ms 250 --torture-pause-reads-ms 1500 --torture-pause-reads-every-turn --assert-keepalive
python3 scripts/metrics_summary.py --metrics-url http://127.0.0.1:8080/metrics
bash scripts/ci_hard_gates.sh
```

## Real Retell Call Validation Checklist

1. Configure Retell to connect to `wss://.../llm-websocket/{call_id}` (or `/ws/{call_id}`).
2. On connect, confirm:
   - server sends `config`
   - server sends BEGIN `response` stream for `response_id=0` (greeting or empty terminal)
3. Confirm keepalive:
   - Retell sends inbound `ping_pong`
   - server echoes outbound `ping_pong` promptly (timestamp echoed)
4. Confirm epoch cancellation:
   - send `response_required` id=N then id=N+1 mid-stream
   - no further chunks for id=N should be spoken after id=N+1 starts
5. Confirm barge-in within epoch:
   - while agent speaking, user interrupts -> Retell sends `update_only.turntaking=user_turn`
   - server stops immediately (speak-gen gate)
6. Confirm pacing audibly:
   - digits are read slowly: `4 - 5 - 6 - 7`
   - default output contains no SSML `<break>` tags
7. Backchanneling:
   - enable backchannels in Retell agent config (recommended)
   - server does not emit `agent_interrupt` backchannels by default
