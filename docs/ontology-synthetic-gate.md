# Ontology Synthetic Gate Contracts

This document defines the synthetic acceptance path for synthetic MedSpa campaign data, calls, journey mapping, and persistence.

## 1) Generator artifacts

`python3 synthetic_data_for_training/generate_medspa_synthetic_data.py`

Outputs in `--output-dir`:

- `medspa_clinics.csv`
- `medspa_patients.csv`
- `medspa_appointments.csv`
- `medspa_conversations.csv`
- `medspa_leads.csv`
- `_manifest.json`

`_manifest.json` schema:

- `schema_version`
- `campaign_id`
- `generator_seed`
- `generated_at_utc`
- `counts`:
  - `medspa_clinics`
  - `medspa_patients`
  - `medspa_appointments`
  - `medspa_conversations`
  - `medspa_leads`
- `source_profile`

## 2) Synthetic lead queue (n8n intake contract)

`python3 scripts/synthetic_to_n8n_campaign.py --input-dir <generator_output> --out data/retell_calls`

Writes:
- `data/retell_calls/synthetic_campaign_call_queue.jsonl`
- `data/retell_calls/synthetic_campaign_summary.json` (optional with `--write-summary`)

Each queue row MUST include:

```json
{
  "lead_id": "L-0000001",
  "clinic_id": 12,
  "to_number": "+15551230000",
  "clinic_name": "Acme MedSpa",
  "clinic_phone": "+15551230000",
  "clinic_email": "frontdesk@acme.example",
  "manager_name": "Alex Agent",
  "manager_email": "alex_agent@acme.example",
  "campaign_id": "ont-smoke-001",
  "campaign_tier": "synthetic",
  "notes": "synthetic medspa outbound campaign",
  "metadata": {
    "tenant": "synthetic_medspa",
    "campaign_id": "ont-smoke-001",
    "clinic_id": 12,
    "lead_id": "L-0000001"
  }
}
```

## 3) Synthetic caller

`python3 scripts/run_synthetic_campaign.py --queue-file data/retell_calls/synthetic_campaign_call_queue.jsonl`

Calls Retell:

```
POST https://api.retellai.com/v2/create-phone-call
{
  "from_number": "+1...
  ",
  "to_number": "+15551230000",
  "override_agent_id": "<B2B_AGENT_ID>",
  "metadata": {
    "tenant": "synthetic_medspa",
    "clinic_id": 12,
    "lead_id": "L-0000001",
    "campaign_id": "ont-smoke-001",
    "clinic_phone": "+15551230000",
    "clinic_name": "Acme MedSpa"
  }
}
```

### Required env for live runs

- `RETELL_API_KEY`
- `B2B_AGENT_ID`
- `RETELL_FROM_NUMBER`
- `N8N_OUTCOME_WEBHOOK_URL`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `SUPABASE_SCHEMA`
- `SYNTHETIC_CAMPAIGN_ID` (optional override for synthetic metadata)

## 4) Journey mapper output

`python3 scripts/synthetic_journey_mapper.py --calls-dir data/retell_calls ...`

Writes:
- `data/retell_calls/synthetic_customer_journeys.jsonl`

Normalized record schema:

- `tenant`
- `campaign_id`
- `lead_id`
- `clinic_id`
- `call_id`
- `to_number`
- `call_outcome`
- `conversion_stage`
- `tool_calls`
- `tool_call_events`
- `captured_email`
- `call_status`
- `sentiment`
- `call_duration_ms`
- `transcript_hash`
- `outcome_ts`
- `recording_url`
- `recording_followup_requested`
- `recording_followup_requests`

Example:

```json
{
  "tenant": "synthetic_medspa",
  "campaign_id": "ont-smoke-001",
  "lead_id": "L-0000001",
  "clinic_id": "12",
  "call_id": "call_xxx",
  "to_number": "+15551230000",
  "call_outcome": "booked_demo",
  "conversion_stage": "booked_demo",
  "tool_calls": ["send_evidence_package", "set_follow_up_plan"],
  "tool_call_events": [{"name":"send_call_recording_followup","arguments":{"campaign_id":"ont-smoke-001","recording_url":"https://.../recording.wav","channel":"twilio_sms"}}],
  "captured_email": "manager@acme.example",
  "recording_url": "https://.../recording.wav",
  "recording_followup_requested": true,
  "recording_followup_requests": [
    {"tool":"send_call_recording_followup","recording_url":"https://.../recording.wav","channel":"twilio_sms"}
  ],
  "call_status": "ended",
  "sentiment": "neutral",
  "call_duration_ms": 12000,
  "transcript_hash": "f84d..."
}
```

Outcome priority in mapper:
1. `call_analysis.custom_analysis_data.call_outcome`
2. tool call markers (`send_evidence_package`, `mark_dnc_compliant`, `send_call_recording_followup`)
3. transcript heuristic

## 5) n8n outcome webhook

If `N8N_OUTCOME_WEBHOOK_URL` is set, mapper posts:

```json
{
  "tenant": "synthetic_medspa",
  "campaign_id": "ont-smoke-001",
  "count": 5,
  "generated_at_utc": "2026-02-15T00:00:00Z",
  "records": [
    {"tenant": "..."}
  ]
}
```

## 6) Supabase export

`python3 scripts/export_journey_to_supabase.py`

Writes to:
- `ont_leads` (upsert key: `tenant,campaign_id,lead_id`)
- `ont_calls` (upsert key: `tenant,call_id`)
- `ont_call_outcomes` (upsert key: `tenant,call_id,outcome_ts`)

Default endpoint:

`POST {SUPABASE_URL}/rest/v1/{table}?on_conflict=<key>`

Headers:
- `apikey: SUPABASE_SERVICE_KEY`
- `Authorization: Bearer SUPABASE_SERVICE_KEY`
- `Accept-Profile: SUPABASE_SCHEMA`
- `Content-Profile: SUPABASE_SCHEMA`
- `Prefer: resolution=merge-duplicates` when `upsert` mode is enabled

## 7) End-to-end command chain

```bash
python3 synthetic_data_for_training/generate_medspa_synthetic_data.py \
  --campaign-id ont-smoke-001 \
  --output-dir /tmp/medspa_synthetic \
  --clinics 20 --patients 200 --sessions 100

python3 scripts/synthetic_to_n8n_campaign.py \
  --input-dir /tmp/medspa_synthetic \
  --out data/retell_calls

# cost-aware dry run
python3 scripts/run_synthetic_campaign.py --max-calls 1 --resume

python3 scripts/synthetic_journey_mapper.py \
  --calls-dir data/retell_calls \
  --campaign-id ont-smoke-001 \
  --out data/retell_calls/synthetic_customer_journeys.jsonl

python3 scripts/export_journey_to_supabase.py \
  --calls-dir data/retell_calls \
  --journey-path data/retell_calls/synthetic_customer_journeys.jsonl
```
