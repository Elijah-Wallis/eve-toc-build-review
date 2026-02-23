# Ontology Live Campaign Gate (Apify + Retell + n8n + Twilio + Supabase)

## 1) Lead ingest (Apify)

Command:

```bash
python3 scripts/build_live_campaign_queue.py \
  --campaign-id ont-live-001 \
  --campaign-name b2b_outbound_workflow \
  --out-dir data/leads \
  --top-k 500 \
  --states Texas,Florida,California \
  --query medspa
```

Required lead columns in `live_leads.csv`:

- `lead_id`
- `clinic_id`
- `clinic_name`
- `clinic_phone`
- `clinic_email` (best-effort)
- `clinic_website`
- `industry_vertical`
- `manager_name`
- `manager_email`
- `campaign_id`
- `campaign_tier`
- `notes`
- `state`, `city`, `website`, `call_days`, `call_hours`

Queue file:
- `live_call_queue.jsonl` at `--out-dir` path
- Each row includes `metadata` and call control fields.

Queue row contract:

```json
{
  "lead_id": "abc123",
  "clinic_id": "123",
  "to_number": "+15551230000",
  "clinic_name": "Acme MedSpa",
  "clinic_phone": "+15551230000",
  "clinic_email": "frontdesk@acme.example",
  "manager_name": "Alex Manager",
  "manager_email": "alex@acme.example",
  "campaign_id": "ont-live-001",
  "campaign_tier": "outbound",
  "notes": "live medspa outbound campaign",
  "metadata": {
    "tenant": "live_medspa",
    "campaign_id": "ont-live-001",
    "clinic_id": "123",
    "lead_id": "abc123"
  }
}
```

## 2) Retell caller

Command:

```bash
python3 scripts/run_live_campaign.py \
  --queue-file data/leads/live_call_queue.jsonl \
  --out-dir data/retell_calls \
  --campaign-id ont-live-001 \
  --tenant live_medspa \
  --max-calls 20 \
  --daily-call-cap 3 \
  --max-attempts 500 \
  --attempt-warning-threshold 200 \
  --concurrency 20 \
  --resume
```

Retell POST payload:

```json
{
  "from_number": "+14695998571",
  "to_number": "+15551230000",
  "override_agent_id": "agent_5d6f2744acfc79e26ddce13af2",
  "metadata": {
    "tenant": "live_medspa",
    "campaign_id": "ont-live-001",
    "clinic_id": "123",
    "lead_id": "abc123",
    "clinic_phone": "+15551230000",
    "clinic_name": "Acme MedSpa",
    "campaign_name": "b2b_outbound_workflow",
    "call_segment": "priority"
  }
}
```

Campaign controls:
- daily call cap (default `3`): `CAMPAIGN_DAILY_CALL_CAP`
- max attempts (default `500`): `CAMPAIGN_MAX_ATTEMPTS`
- attempts warning threshold (default `200`): `CAMPAIGN_ATTEMPT_WARNING_THRESHOLD`
- flagging: `attempts_exceeded_200=true` for records over warning threshold
- terminal stop states: `dnc,closed,invalid,contacted,booked`
- after-hours policy: one voicemail attempt per lead outside configured `call_hours`
- default lead window: `09:00-18:00`
- control outside-hours by adding `--allow-after-hours-calls` (default on) or `--no-after-hours-calls` (off)

## 3) Journey normalization

Command:

```bash
python3 scripts/synthetic_journey_mapper.py \
  --calls-dir data/retell_calls \
  --lead-file data/leads/live_leads.csv \
  --campaign-id ont-live-001 \
  --tenant live_medspa \
  --out data/retell_calls/live_customer_journeys.jsonl
```

Output row contract:

```json
{
  "tenant": "live_medspa",
  "campaign_id": "ont-live-001",
  "lead_id": "abc123",
  "clinic_id": "123",
  "call_id": "call_xxx",
  "to_number": "+15551230000",
  "call_outcome": "booked_demo",
  "conversion_stage": "booked_demo",
  "tool_calls": ["send_evidence_package","mark_dnc_compliant"],
  "tool_call_events": [
    {"name":"send_evidence_package","arguments":{"recipient_email":"manager@acme.example","delivery_method":"EMAIL_AND_SMS"}},
    {"name":"send_call_recording_followup","arguments":{"campaign_id":"ont-live-001","clinic_id":"123","lead_id":"L-001","call_id":"call_xxx","recording_url":"https://.../recording.wav","recipient_email":"manager@acme.example","channel":"twilio_sms","reason":"queued","next_step":"queued","timestamp_ms":1700000000123}}
  ],
  "captured_email": "manager@acme.example",
  "call_status": "ended",
  "sentiment": "neutral",
  "call_duration_ms": 12345,
  "attempt_number": 3,
  "attempt_warning_threshold": 200,
  "attempts_exceeded_200": false,
  "recording_followup_requested": true,
  "recording_followup_requests": [
    {"tool":"send_call_recording_followup","recording_url":"https://.../recording.wav","channel":"twilio_sms","recipient_email":"manager@acme.example"}
  ],
  "call_window": "09:00-18:00",
  "call_window_type": "business_hours",
  "after_hours_call_once_done": false,
  "transcript_hash": "sha256hex",
  "outcome_ts": 1700000000000
}
```

Outcome priority:
1. `call_analysis.custom_analysis_data.call_outcome`
2. Tool call markers (`send_evidence_package`, `mark_dnc_compliant`, `set_follow_up_plan`, `log_call_outcome`, `send_call_recording_followup`)
3. transcript heuristic

## 4) n8n workflow mapping (deployment)

Current live workflow in the connected n8n environment:

- `B2B outbound calling workflow` (webhook `/webhook/openclaw-retell-dispatch`, production URL:
  `https://elijah-wallis.app.n8n.cloud/webhook/openclaw-retell-dispatch`)
- `openclaw_retell_fn_b2c_quote` is deprecated as an outbound workflow; keep only if explicitly needed for non-core pricing tooling, otherwise do not invoke for lead dispatch.
- Recommended env for this contract:
  - `N8N_B2B_DISPATCH_WORKFLOW=B2B outbound calling workflow`
  - `N8N_B2B_DISPATCH_WEBHOOK=https://elijah-wallis.app.n8n.cloud/webhook/openclaw-retell-dispatch`
  - `N8N_B2B_OUTCOME_WORKFLOW` / `N8N_B2B_OUTCOME_WEBHOOK` for conversion pushes.

Workflow control for this version:
- One input source: Retell call-queue payload from `scripts/run_live_campaign.py`
- One output schema: one standardized outcome envelope from mapper (`tenant, campaign_id, lead_id, clinic_id, call_id, call_outcome, conversion_stage, ...`)
- One terminal outcome path: `booked`, `dnc`, `rejected`, `contacted_closed`, `voicemail`, `unknown`

Lead flow:
- Use `N8N_LEAD_WEBHOOK_URL`/`N8N_B2B_DISPATCH_WEBHOOK` to invoke dispatch workflow.
- Use `N8N_OUTCOME_WEBHOOK_URL` for outcome webhooks produced by mapper (or map to a dedicated outcome workflow).

High-attempt routing:
- If `attempts_exceeded_200=true`, branch to a separate nurture sequence in n8n after outcome mapping.

Mapper output (`data/retell_calls/live_customer_journeys.jsonl`) now includes:
- `attempt_number`
- `attempt_warning_threshold`
- `attempts_exceeded_200`

## 5) n8n integration payload shape

Mapper can POST:

```json
{
  "tenant": "live_medspa",
  "campaign_id": "ont-live-001",
  "count": 10,
  "generated_at_utc": "2026-02-16T00:00:00Z",
  "records": [ ...normalized rows... ]
}
```

### Recording follow-up branch in n8n

- Trigger on mapped outcome events from `data/retell_calls/live_customer_journeys.jsonl` webhook.
- Canonical branch key:
  - `recording_followup_requested == true`
  - `recording_followup_requests` array contains one or more requests.
- Minimum contract to consume:
  - `tenant`, `campaign_id`, `lead_id`, `clinic_id`, `call_id`, `to_number`
  - `recording_url` (from Retell `call.json` artifact) or `recording_followup_requests[].recording_url`
  - `recording_followup_requests[].channel` (`twilio_sms`, `EMAIL`, `EMAIL_AND_SMS`)
- Recommended action mapping:
  1. Send WhatsApp/SMS via Twilio node with short message + link to `recording_url`.
  2. Send email (Twilio SendGrid/Sendinblue) with personal line + `recording_url`.
  3. Optionally call/update CRM/nurture queue.

## 6) Supabase export

Command:

```bash
python3 scripts/export_journey_to_supabase.py \
  --calls-dir data/retell_calls \
  --journey-path data/retell_calls/live_customer_journeys.jsonl \
  --supabase-url "$SUPABASE_URL" \
  --supabase-key "$SUPABASE_SERVICE_KEY" \
  --schema "$SUPABASE_SCHEMA"
```

Target upsert keys:
- `ont_leads`: `tenant,campaign_id,lead_id`
- `ont_calls`: `tenant,call_id`
- `ont_call_outcomes`: `tenant,call_id,outcome_ts`

## 7) Next steps to complete n8n + Twilio + website

The repo provides local orchestration and contracts. You still need:

- `B2B outbound calling workflow` as the sole active lead intake/dispatch workflow
- nurture branch for booked outcomes (higher-touch sequence)
- Twilio tool node config for SMS + email personalization
- optional email content prompts/safe templates

Once those are in place, run `make live-map-journeys` and `make live-export-supabase` after each call batch.
