# Outbound Dialing SOP (Live Omnichannel Workflow)

## Purpose

Local reference for the end-to-end live outbound loop in this repo:

- lead acquisition → campaign queue → Retell batch calls → journey normalization → n8n/Twilio follow-ups → Supabase upserts

## Source and campaign setup

- Default lead source: `compass/crawler-google-places` (Apify)
- Default campaign: `ont-live-001`
- Campaign name used in metadata: `b2b_outbound_workflow`
- Default states: `Texas,Florida,California`
- Default segment signals:
  - review score
  - review count
  - employee count
  - location / state
  - website presence
  - manager/decision-maker contact

## Campaign caps and stop logic

- Daily call cap: `CAMPAIGN_DAILY_CALL_CAP` (default `3`)
- Max attempts per lead: `CAMPAIGN_MAX_ATTEMPTS` (default `500`)
- Attempt warning threshold: `CAMPAIGN_ATTEMPT_WARNING_THRESHOLD` (default `200`)
  - leads above threshold are flagged with `attempts_exceeded_200=true`
- Stop reasons (hard skip):
  - `dnc,closed,invalid,contacted,booked`
- N8N workflow contract (canonical only):
  - Canonical outbound workflow: `B2B outbound calling workflow`
  - Canonical outcome workflow: `B2B Outbound Outcome Hub`
  - Legacy helper workflow: `openclaw_retell_fn_b2c_quote` (deprecated; do not send leads here)
  - Dispatch webhook: `N8N_B2B_DISPATCH_WEBHOOK` (`/webhook/openclaw-retell-dispatch`)
  - Outcome webhook: `N8N_B2B_OUTCOME_WEBHOOK` (`/webhook/openclaw-retell-fn-outcome-hub`)
- Deterministic workflow contract:
  - One input source: `live_call_queue.jsonl` records from `scripts/build_live_campaign_queue.py`
  - One output schema: `scripts/run_live_campaign.py` dispatch payload metadata + final journey events from `scripts/synthetic_journey_mapper.py`
  - One terminal outcome path: `booked`, `dnc`, `rejected`, `contacted_closed` (all route to terminal outcome branches only)
- Resume behavior:
  - `--resume` skips leads already present in dispatch state

## Runtime commands

```bash
make live-build-queue
make live-call-batch
make live-map-journeys
make live-export-supabase
```

## Deployment-ready daily automation

Morning start (Mon–Sat, local time):

```bash
make live-build-queue CAMPAIGN_STATES=Texas,Florida,California
make live-call-batch LIVE_MAX_CALLS=0 LIVE_CONCURRENCY=20
```

End-of-day update (Mon–Sat):

```bash
make live-map-journeys LIVE_CAMPAIGN_ID=ont-live-001
python3 scripts/revenue_ops_loop.py --calls-dir data/retell_calls --push-webhook "$N8N_OUTCOME_WEBHOOK_URL"
python3 scripts/export_journey_to_supabase.py --calls-dir data/retell_calls --journey-path data/retell_calls/live_customer_journeys.jsonl --supabase-url "$SUPABASE_URL" --supabase-key "$SUPABASE_SERVICE_KEY" --schema "$SUPABASE_SCHEMA"
```

Common direct calls:

```bash
python3 scripts/build_live_campaign_queue.py \
  --campaign-id ont-live-001 \
  --campaign-name b2b_outbound_workflow \
  --states Texas,Florida,California \
  --out-dir data/leads \
  --top-k 500 \
  --query medspa

python3 scripts/run_live_campaign.py \
  --queue-file data/leads/live_call_queue.jsonl \
  --out-dir data/retell_calls \
  --campaign-id ont-live-001 \
  --tenant live_medspa \
  --max-attempts 500 \
  --attempt-warning-threshold 200 \
  --daily-call-cap 3 \
  --resume \
  --limit-call-rate

python3 scripts/synthetic_journey_mapper.py \
  --calls-dir data/retell_calls \
  --lead-file data/leads/live_leads.csv \
  --campaign-id ont-live-001 \
  --tenant live_medspa \
  --out data/retell_calls/live_customer_journeys.jsonl

python3 scripts/export_journey_to_supabase.py \
  --calls-dir data/retell_calls \
  --journey-path data/retell_calls/live_customer_journeys.jsonl \
  --supabase-url "$SUPABASE_URL" \
  --supabase-key "$SUPABASE_SERVICE_KEY" \
  --schema "$SUPABASE_SCHEMA"
```

## Environment contract (.env)

Use `.env.retell.example` as the source of truth and copy values into `.env.retell.local`:

- Retell
  - `RETELL_API_KEY`
  - `B2B_AGENT_ID`
  - `RETELL_FROM_NUMBER`
- Apify
  - `APIFY_ACTOR_ID`
  - `APIFY_API_TOKEN`
- n8n
  - `N8N_LEAD_WEBHOOK_URL`
- `N8N_B2B_DISPATCH_WORKFLOW` (set to `B2B outbound calling workflow`)
  - `N8N_B2B_DISPATCH_WEBHOOK`
  - `N8N_B2B_OUTCOME_WORKFLOW`
  - `N8N_B2B_OUTCOME_WEBHOOK`
  - `N8N_OUTCOME_WEBHOOK_URL`
- Supabase
  - `SUPABASE_URL`
  - `SUPABASE_SERVICE_KEY`
  - `SUPABASE_SCHEMA`
- Campaign policy
  - `CAMPAIGN_DAILY_CALL_CAP=3`
  - `CAMPAIGN_MAX_ATTEMPTS=500`
  - `CAMPAIGN_ATTEMPT_WARNING_THRESHOLD=200`
- Twilio credentials (used by n8n actions):
  - `TWILIO_ACCOUNT_SID`
  - `TWILIO_AUTH_TOKEN`
  - `TWILIO_API_KEY_SID`
  - `TWILIO_API_KEY_SECRET`

## Notes for future rollout

- Keep `attempts_exceeded_200=true` as a first-class journey attribute and route high-attempt leads into a separate nurture sequence.
- Keep existing call scripts and n8n integrations additive, but keep workflow control simple: `B2B outbound calling workflow` is the only active outbound workflow.

## n8n wiring for recording + Twilio/SMS/email follow-up

Use `recording_followup_requested` as the canonical branch key from mapped outcomes.

- Expected event inputs:
  - `recording_followup_requested` (`true|false`)
  - `recording_followup_requests` (array)
  - `recording_url`
  - `campaign_id`, `tenant`, `lead_id`, `clinic_id`, `call_id`, `to_number`

- Minimal branch contract:
  - If `recording_followup_requested == true`
    - branch to follow-up path
    - send one SMS/WhatsApp message with `recording_url` + short text
    - send email with same content and link (or signed recording asset)
    - emit `tool=send_call_recording_followup` follow-up state in your downstream CRM/nurture state
  - Else continue normal outcome flow (`booked_demo`, `dnc`, `email_captured`, `rejected`, `voicemail`, `unknown`).

- Required node inputs (Twilio + SMTP/Email action):
  - `to_number` for SMS/WhatsApp
  - `recipient_email` (fallbacks from `captured_email` + `recording_followup_requests[*].recipient_email`)
  - `recording_url` (from `call recording_url` or `recording_followup_requests[].recording_url`)
  - `tenant`, `campaign_id`, `lead_id`, `clinic_id`, `call_id` for idempotent tracking

## After-hours call policy

- Default call window is `09:00-18:00` from each lead row (`call_hours`).
- Outside of that window, a lead is called only once for first-after-hours voicemail attempts (`after_hours_call_once_done=true`), then skipped until lead status changes from terminal.
