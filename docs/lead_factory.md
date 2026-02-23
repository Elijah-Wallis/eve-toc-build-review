# Lead Factory (Speed + Ease)

Purpose: build large call-ready lead queues for OpenClaw/Retell from scraped data.

## One-command run

```bash
make leads INPUT=tests/fixtures/leads_seed.csv
```

Outputs:

- `data/leads/all_scored.csv`
- `data/leads/qualified.csv`
- `data/leads/call_queue.jsonl`
- `data/leads/summary.json`

## Direct source pull (n8n/HTTP)

```bash
python3 scripts/lead_factory.py \
  --source-url https://your-n8n-endpoint/leads \
  --out-dir data/leads \
  --min-score 60 \
  --top-k 500
```

Expected JSON shape from source URL:

- List of lead objects, or
- Object with one of: `data`, `items`, `leads`, `records` containing a list.

## Optional push to n8n after scoring

Set:

- `N8N_LEAD_WEBHOOK_URL=https://your-n8n-endpoint/intake`

Then run:

```bash
make leads INPUT=path/to/your_leads.csv
```

The script POSTs batches:

```json
{
  "batch_size": 25,
  "leads": [ ...qualified_leads... ]
}
```

## ICP filter logic

Qualified leads must satisfy all:

- ad-active signal
- high-ticket vertical signal (dental/plastic/medspa/etc.)
- ability-to-pay signal (`5k-10k/mo` fit)
- score >= `--min-score` (default 60)

