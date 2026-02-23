# Revenue Ops Loop

This loop is designed to stay simple and executable every day.

## Objective Function

- Maximize: `email_capture_rate`
- Minimize: `time_to_email_capture`, `turns_to_capture`, `first_response_latency`

## One-command run

```bash
make money
```

That runs:
1. `scripts/retell_learning_loop.py`
2. `scripts/revenue_ops_loop.py`
3. `scripts/dogfood_scorecard.py`

## Metrics source

`/data/retell_calls/call_*/call.json`

The loop reads `transcript_object` and latency fields to compute:

- `email_capture_rate`
- `direct_email_capture_rate`
- `time_to_email_capture_p50/p95`
- `turns_to_capture_p50/p95`
- `first_response_latency_p50/p95`
- objection counts
- `first_response_latency_band` where:
  - `<700ms` => `excellent`
  - `700-999ms` => `good`
  - `1000-1499ms` => `warning`
  - `1500ms+` => `poor`

## Output

- `data/revenue_ops/latest.json`
- `data/revenue_ops/latest.md`

## Optional n8n push

Set:

- `N8N_OUTCOME_WEBHOOK_URL=https://...`

Then run:

```bash
python3 scripts/revenue_ops_loop.py --push-webhook "$N8N_OUTCOME_WEBHOOK_URL"
```
