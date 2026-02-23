# Retell WS Brain (Deterministic, Actor Model, VIC-Gated)

This repo contains a production-grade "Brain" server for Retell's Custom LLM WebSocket integration.

## Dumb-Simple Commands

```bash
make call
```

That calls `DOGFOOD_TO_NUMBER` from `.env.retell.local` with the B2B agent.

Other simple commands:

```bash
make call TO=+19859914360
make start
make call-status ID=call_xxx
make retell-fast
make learn
make leads INPUT=tests/fixtures/leads_seed.csv
make ops-loop
make money
make go
make test
make ci
make ci-local
make metrics
make dashboard
python3 scripts/dogfood_scorecard.py --metrics-url http://127.0.0.1:8080/metrics
```

Revenue-ops loop (objective function, real call artifacts):

- `make ops-loop` computes:
  - `email_capture_rate`
  - `time_to_email_capture`
  - `turns_to_capture`
  - `first_response_latency`
- `make money` runs:
  1. call sync + learning loop
  2. revenue-ops objective report
  3. scorecard snapshot
- Reports are written to `data/revenue_ops/latest.json` and `data/revenue_ops/latest.md`.
- Full details: `docs/revenue_ops_loop.md`

Learning loop (auto-pulls transcript + recording metadata and refines prompt at threshold):

- `make learn` runs one sync/analyze cycle.
- `scripts/call_b2b.sh` no longer auto-applies prompt updates.
- Current locked posture defaults:
  - `RETELL_AUTO_LEARN_ON_CALL=false`
  - `RETELL_LEARN_APPLY=false`
  - `B2B_CLOSE_PROMPT_LOCK=1`
- Defaults:
  - `RETELL_LEARN_THRESHOLD=250` (auto-refine once corpus reaches ~200-300 calls)
  - `RETELL_LEARN_LIMIT=100`

To intentionally update the locked Apex Medspa closer prompt:

1. Edit `scripts/prompts/b2b_fast_plain.prompt.txt` only.
2. Unlock once:
   ```bash
   export B2B_CLOSE_PROMPT_UNLOCK=1
   ```
3. Push the prompt:
   ```bash
   cd '<repo_root>'
   B2B_CLOSE_PROMPT_LOCK=1 ./scripts/retell_fast_recover.sh
   ```
4. Turn lock guard back on automatically (recommended):
   ```bash
   unset B2B_CLOSE_PROMPT_UNLOCK
   ```
5. Verify lock + websocket:
   ```bash
   ./scripts/verify_voice_agent_lock.sh
   ```
6. Resume calling:
   ```bash
   make call
   ```

Lead Factory (ICP scraping/enrichment scorer for outbound queues):

- Input any CSV/JSON lead dumps (Apify, Maps, ad-library exports, n8n outputs).
- Or pull JSON directly from an HTTP source with `--source-url`.
- Scores for:
  - ad-active businesses
  - high-ticket vertical fit
  - pain signal likelihood
  - ability to pay `5k-10k/mo`
- Outputs call-ready queue files under `data/leads/`.
- Optional n8n push:
  - set `N8N_LEAD_WEBHOOK_URL=https://...`
  - run `make leads INPUT=path/to/leads.csv`
  - or: `python3 scripts/lead_factory.py --source-url https://your-n8n-endpoint/leads`
- Full details: `docs/lead_factory.md`

Skills / shell / self-improve helpers:

```bash
# This installs `openclaw-*` helpers into your active virtualenv bin and ~/.local/bin.
# If command lookup still fails, add one of those dirs to PATH.
bash scripts/setup_shell_commands.sh
openclaw-skill-capture --id fix_timeout --intent "Recover from tool timeout" --tests tests/test_tool_grounding.py::test_tool_timeout_falls_back_without_numbers
openclaw-skill-validate skills/fix_timeout.md
openclaw-self-improve --mode propose
```

## Run

Install deps (recommended in a virtualenv):

```bash
python3 -m pip install -e ".[dev]"
```

Optional extras:

```bash
python3 -m pip install -e ".[gemini,ops]"
```

Run server:

```bash
python3 -m uvicorn app.server:app --host 0.0.0.0 --port 8080
```

## Production (Single Host Compose + Cloudflare) - canonical deploy path

Bootstrap config:

```bash
cp .env.example .env
```

- Fill required values in `.env` before any live calls (Retell, Gemini, Supabase, n8n).
- Keep secrets out of git.
- `scripts/cloudflare_verify.sh` reads `.env.cloudflare.local` by default, so mirror your Cloudflare vars there (or export them) before verification.

Local hard gate (recommended before image build):

```bash
make ci
```

Container build + boot:

```bash
docker build -t eve-brain .
docker compose up --build -d
```

Smoke checks:

```bash
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8080/metrics | head
```

- Open dashboard: `http://127.0.0.1:8080/dashboard/`

Cloudflare verification:

```bash
./scripts/cloudflare_verify.sh
```

- Confirm `voice-agent.evesystems.org` resolves and tunnel ingress points to the same host port used by compose (`EVE_BRAIN_HOST_PORT`, default `8080`).

Retell production lock verification (before a live batch):

- Verify the B2B agent websocket URL is `wss://voice-agent.evesystems.org/llm-websocket`.
- Run `scripts/verify_voice_agent_lock.sh`.
- Run one controlled lab-number call before live outbound dialing.

Failure triage:

- `/health` works but `/metrics` or `/dashboard/` fails: wrong container entrypoint/runtime image drift.
- First call fails with Gemini import/runtime error: image built without `gemini` extra.
- Retell cannot connect to WS host: Cloudflare tunnel ingress/DNS/port mismatch.

## Public evaluation handoff (sanitized GitHub snapshot)

Use the canonical export script to publish a review-safe snapshot that preserves `.env.example` while excluding real local secrets and runtime artifacts:

```bash
bash scripts/export_public_handoff.sh --repo Elijah-Wallis/eve-toc-build-review --push
```

Handoff policy:

- `.env.example` is intentionally public and included.
- Local secret env files (for example `.env`, `.env.*.local`, `.env.cloudflare.local`) are excluded.
- Logs, call artifacts, and `artifacts/` are excluded.
- The export aborts if the built-in secret scan detects likely credentials.

Quick evaluator validation (fresh clone):

```bash
git clone https://github.com/Elijah-Wallis/eve-toc-build-review
cd eve-toc-build-review
test -f .env.example
cp .env.example .env
# fill placeholders, then:
docker build -t eve-brain .
docker compose up --build -d
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8080/metrics | head
```

Dashboard:

- One command: `make dashboard` (starts server + opens dashboard)
- One command shortcut: `make start` (build queue and start outbound calls)
- Full launcher: `/Users/elijah/Documents/New project/start_outbound_dialing`  
  (checks `RETELL_API_KEY`, `RETELL_FROM_NUMBER`, `B2B_AGENT_ID`, starts live outbound dialing, then opens the dashboard)
- Open directly: `http://127.0.0.1:8080/dashboard/`
- APIs: `/api/dashboard/summary`, `/api/dashboard/repo-map`, `/api/dashboard/sop`, `/api/dashboard/readme`

WebSocket endpoints:

- `ws://{host}/llm-websocket/{call_id}` (canonical default)
- `ws://{host}/ws/{call_id}` (legacy compatibility)

Retell pacing defaults:

- Speech pauses are represented by spaced dashes: `" - "` (not SSML by default).
- Digits are read slowly as: `2 - 1 - 3 - 4`.

Optional env flags:

- `BRAIN_BACKCHANNEL_ENABLED=true` (default false)
- `SPEECH_MARKUP_MODE=DASH_PAUSE|RAW_TEXT|SSML` (default DASH_PAUSE)
- `DASH_PAUSE_SCOPE=PROTECTED_ONLY|SEGMENT_BOUNDARY` (default PROTECTED_ONLY)
- `WS_WRITE_TIMEOUT_MS=400`
- `WS_CLOSE_ON_WRITE_TIMEOUT=true`
- `WS_MAX_CONSECUTIVE_WRITE_TIMEOUTS=2`
- `WS_MAX_FRAME_BYTES=262144`
- `TRANSCRIPT_MAX_UTTERANCES=200`
- `TRANSCRIPT_MAX_CHARS=50000`
- `LLM_PHRASING_FOR_FACTS_ENABLED=false`
- `VOICE_PLAIN_LANGUAGE_MODE=true`
- `VOICE_NO_REASONING_LEAK=true`
- `VOICE_JARGON_BLOCKLIST_ENABLED=true`
- `RETELL_SEND_UPDATE_AGENT_ON_CONNECT=true`
- `RETELL_RESPONSIVENESS=0.5`
- `RETELL_INTERRUPTION_SENSITIVITY=0.5`
- `SKILLS_ENABLED=false`
- `SKILLS_DIR=skills`
- `SKILLS_MAX_INJECTED=3`
- `SHELL_MODE=local|hosted|hybrid` (default local)
- `SHELL_ENABLE_HOSTED=false`
- `SHELL_ALLOWED_COMMANDS=` (optional comma-separated allowlist)
- `SHELL_TOOL_ENABLED=false` (explicit runtime gate for model/policy shell calls)
- `SHELL_TOOL_CANARY_ENABLED=false`
- `SHELL_TOOL_CANARY_PERCENT=0` (0..100)
- `SELF_IMPROVE_MODE=off|propose|apply` (default off)

Cloudflare production WebSocket checklist (for calling from Retell):

- DNS and tunnel alignment:
  - `BRAIN_WSS_BASE_URL` should point at a stable, resolvable host (example: `wss://voice-agent.evesystems.org/llm-websocket`), not a temporary `*.trycloudflare.com` name.
  - The host must resolve to a configured Cloudflare tunnel ingress and route to the local port where the brain is actually running.
- For the current workspace:
  - Canonical compose deployment exposes the brain on host port `8080` by default (`EVE_BRAIN_HOST_PORT=8080`).
  - If your tunnel ingress still points to `http://127.0.0.1:8099`, either update the tunnel ingress to `8080` or set `EVE_BRAIN_HOST_PORT=8099` before `docker compose up`.
  - `scripts/cloudflare_verify.sh` validates token + tunnel ingress + DNS.
- Practical zero-downtime checks before a call:
  1. Run `./scripts/cloudflare_verify.sh` and confirm `dns_ok=True` for `voice-agent.evesystems.org`.
  2. Confirm brain is reachable on the host port used by compose (default `8080`): `nc -vz 127.0.0.1 ${EVE_BRAIN_HOST_PORT:-8080}`.
 3. Confirm agent websocket URL matches env:
     ```bash
     python3 - <<'PY'
     import json
     import os
     import urllib.request

     api = os.environ["RETELL_API_KEY"]
     agent = os.environ["B2B_AGENT_ID"]
     req = urllib.request.Request(
         f"https://api.retellai.com/get-agent/{agent}",
         headers={"Authorization": f"Bearer {api}"},
     )
     payload = json.load(urllib.request.urlopen(req))
     print(payload.get("response_engine"))
     PY
     ```
  4. Run `scripts/verify_voice_agent_lock.sh` to hard-fail if the rollout drifts off the approved websocket agent.
  5. Use `scripts/call_b2b.sh` (it now auto-resolves stable Cloudflare websocket host if `BRAIN_WSS_BASE_URL` is empty).

Gemini (voice profile - locked for this repo):

- `BRAIN_USE_LLM_NLG=true`
- `LLM_PROVIDER=gemini`
- `GEMINI_API_KEY=...` (Developer API) OR `GEMINI_VERTEXAI=true` + `GEMINI_PROJECT=...` + `GEMINI_LOCATION=global`
- `GEMINI_MODEL=gemini-3-flash-preview`
- `GEMINI_THINKING_LEVEL=minimal` (recommended for low-latency voice)

Shell trigger (explicit operator intent):

- Send user text as `/shell <command>` or `shell: <command>`.
- Command still routes through shell policy/allowlist and timeout controls.

Security hardening (optional):

- `WS_ALLOWLIST_ENABLED=true`
- `WS_ALLOWLIST_CIDRS="10.0.0.0/8,192.168.1.0/24"`
- `WS_TRUSTED_PROXY_ENABLED=true`
- `WS_TRUSTED_PROXY_CIDRS="10.0.0.0/8"`
- `WS_SHARED_SECRET_ENABLED=true`
- `WS_SHARED_SECRET="..."`
- `WS_SHARED_SECRET_HEADER="X-RETELL-SIGNATURE"`
- `WS_QUERY_TOKEN="..."`
- `WS_QUERY_TOKEN_PARAM="token"`

## Test

```bash
python3 -m pytest
```

Acceptance/load (in-memory, deterministic):

```bash
python3 -m pytest -q tests/acceptance/at_vic_100_sessions.py
python3 -m pytest -q tests/acceptance/at_no_leak_30min.py
python3 scripts/load_test.py --sessions 100
```

Replay determinism helper:

```bash
python3 scripts/replay_session.py
```

Real WebSocket load test (run server first):

```bash
python3 scripts/ws_load_test.py --sessions 25 --turns 2 --assert-keepalive
python3 scripts/ws_load_test.py --sessions 10 --turns 2 --torture-pause-reads-ms 1500 --assert-keepalive
```

## Keepalive SLOs

- `keepalive.ping_pong_queue_delay_ms`: target p99 < 100ms in non-stalled conditions.
- `keepalive.ping_pong_missed_deadline_total`: target 0 in normal/torture runs.
- `vic.barge_in_cancel_latency_ms`: target p95 <= 250ms.
- `voice.reasoning_leak_total`: target 0.
- `voice.jargon_violation_total`: target 0.
- `voice.readability_grade`: target max <= 8.
- `moat.playbook_hit_total`: track upward trend as playbooks mature.
- `moat.objection_pattern_total`: track top objection volume over time.

Keepalive behavior:
- ping/control traffic is prioritized over speech in the outbound writer.
- inbound ping events are prioritized over update-only floods.
- every socket write has a deadline (`WS_WRITE_TIMEOUT_MS`); repeated write stalls trigger clean close (`WRITE_TIMEOUT_BACKPRESSURE`) so Retell can reconnect.
- Retell auto-reconnect behavior expects ping/pong cadence around every 2s and may close/reconnect after ~5s without keepalive traffic.

Retell references:
- LLM WebSocket ping/pong + reconnect behavior: [Retell LLM WebSocket](https://docs.retellai.com/api-references/llm-websocket)
- WebSocket server setup / IP allowlist guidance: [Retell Setup WebSocket Server](https://docs.retellai.com/integrate-llm/setup-websocket-server)
- Secure webhook/IP guidance: [Retell Secure Webhook](https://docs.retellai.com/features/secure-webhook)
- Dash pause formatting (`" - "` with spaces): [Retell Add Pause](https://docs.retellai.com/build/add-pause)

## Production Defaults

| Area | Setting | Default |
|---|---|---|
| Outbound queue | `BRAIN_OUTBOUND_QUEUE_MAX` | `256` |
| Inbound queue | `BRAIN_INBOUND_QUEUE_MAX` | `256` |
| Ping interval | `BRAIN_PING_INTERVAL_MS` | `2000` |
| Idle watchdog | `BRAIN_IDLE_TIMEOUT_MS` | `60000` |
| Write timeout | `WS_WRITE_TIMEOUT_MS` | `400` |
| Max consecutive write timeouts | `WS_MAX_CONSECUTIVE_WRITE_TIMEOUTS` | `2` |
| Close on write timeout | `WS_CLOSE_ON_WRITE_TIMEOUT` | `true` |
| Max inbound frame size | `WS_MAX_FRAME_BYTES` | `262144` |
| Transcript utterance cap | `TRANSCRIPT_MAX_UTTERANCES` | `200` |
| Transcript char cap | `TRANSCRIPT_MAX_CHARS` | `50000` |
| Factual phrasing guard | `LLM_PHRASING_FOR_FACTS_ENABLED` | `false` |

Backpressure policy:
- Queue priority prevents control-plane starvation under normal pressure.
- Write deadlines prevent deadlocks when kernel/socket buffers stall.
- On repeated write timeout, session closes intentionally so Retell reconnect logic can recover.

## Production Verification Automation

- CI hard gates (backend + expressive + acceptance + web typecheck/build): `bash scripts/ci_hard_gates.sh`
- New 5-minute torture acceptance:
  - `python3 -m pytest -q tests/acceptance/at_ws_torture_5min.py`
  - This runs real sockets with pause-reads pressure and asserts keepalive misses stay at zero.
- Metrics summary:
  - `python3 scripts/metrics_summary.py --metrics-url http://127.0.0.1:8080/metrics`
- Self-improve SOP:
  - `docs/self_improve_sop.md`

## Real Retell Call Validation Checklist

1. Configure Retell to connect to `wss://.../llm-websocket/{call_id}` (or `/ws/{call_id}`).
2. On connect, confirm server sends:
   - `config`
   - BEGIN `response` stream for `response_id=0` (greeting or empty terminal)
3. Keepalive:
   - Retell sends inbound `ping_pong`
   - server echoes outbound `ping_pong` promptly (timestamp echoed)
4. Epoch correctness:
   - `response_required` id=N then id=N+1 mid-stream must hard-cancel id=N (no stale chunks)
5. Barge-in within epoch:
   - `update_only.turntaking=user_turn` while speech pending must stop immediately (speak-gen gate)
6. Pause formatting (audible):
   - phone/code digits read as `4 - 5 - 6 - 7`
   - default output contains no SSML `<break>` tags
7. Backchanneling:
   - recommended via Retell agent config (`enable_backchannel`, `backchannel_frequency`, `backchannel_words`)
   - server does not emit `agent_interrupt` backchannels by default
8. Security posture:
   - preferred: IP/CIDR allowlist
   - optional: shared-secret header (OFF by default)
   - optional: query token mode (OFF by default)
   - if behind a proxy, trust `X-Forwarded-For` only when trusted-proxy mode is enabled and proxy CIDRs are configured
```

## Ontology Synthetic Gate (Retell + n8n + Supabase)

Use this flow to validate the synthetic acceptance harness end-to-end:

```bash
make synth-generate
make synth-push-leads
make synth-call-batch
make synth-map-journeys
make synth-export-supabase
```

Recommended direct commands (as documented for acceptance gates):

```bash
python3 synthetic_data_for_training/generate_medspa_synthetic_data.py --campaign-id ont-smoke-001 --output-dir /tmp/medspa_synthetic --clinics 20 --patients 200 --sessions 100
python3 scripts/synthetic_to_n8n_campaign.py --input-dir /tmp/medspa_synthetic --out data/retell_calls --dry-run
python3 scripts/run_synthetic_campaign.py --max-calls 1 --limit-call-rate 0 --resume
python3 scripts/synthetic_journey_mapper.py --calls-dir data/retell_calls --campaign-id ont-smoke-001 --out data/retell_calls/synthetic_customer_journeys.jsonl
```

For precise payload contracts for n8n and Supabase, use `docs/ontology-synthetic-gate.md`.

## Live Omnichannel Campaign (Apify → Retell → n8n → Twilio → Supabase)

See `docs/Outbound Dialing SOP.md` for the full outbound dialing policy, call/attempt thresholds, and deployment-ready command sequence.

Use these targets for a local deployment dry-run and then live batch calls:

```bash
make live-build-queue    # writes data/leads/live_leads.csv + live_call_queue.jsonl
make live-call-batch      # dispatchs live calls respecting daily cap and stop states
make live-map-journeys    # normalizes call artifacts into live_customer_journeys.jsonl
make live-export-supabase # writes ont_leads/ont_calls/ont_call_outcomes in Supabase
```

Minimum required environment (for local run):

- `APIFY_API_TOKEN`
- `APIFY_ACTOR_ID=compass/crawler-google-places`
- `RETELL_API_KEY`
- `B2B_OUTBOUND_AGENT_ID=agent_5d6f2744acfc79e26ddce13af2` (canonical outbound agent for dialing)
- `B2B_AGENT_ID=agent_5d6f2744acfc79e26ddce13af2` (kept for compatibility while scripts resolve outbound target from B2B_OUTBOUND_AGENT_ID if set)
- `RETELL_FROM_NUMBER=+14695998571` (inbound/outbound channel number format in E.164)
- `N8N_LEAD_WEBHOOK_URL` (for lead queue intake)
- `N8N_OUTCOME_WEBHOOK_URL` (for journey push)
- `N8N_B2B_OUTCOME_WORKFLOW=B2B Outbound Outcome Hub`
- `N8N_B2B_OUTCOME_WEBHOOK=https://elijah-wallis.app.n8n.cloud/webhook/openclaw-retell-fn-outcome-hub`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `SUPABASE_SCHEMA`

Campaign behavior defaults used by the local pipeline:

- States: `Texas,Florida,California`
- Weekdays: `Mon-Sat`
- Daily cap: `3` calls
- Per-lead max attempts: `500` attempts
- Attempt warning threshold: leads with `attempts > 200` are flagged for review (`attempts_exceeded_200=true`)
- Graceful escalation: leads with terminal status in `dnc,closed,invalid,contacted,booked` are skipped

Twilio personalization and nurture execution are expected to be done in n8n workflows (email + SMS follow-ups). This repo emits deterministic tool intents (`log_call_outcome`, `set_follow_up_plan`) from `app/tools.py` so downstream workflows can choose actions safely.
