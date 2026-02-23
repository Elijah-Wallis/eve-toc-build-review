# Public Evaluation Handoff Checklist

Use this checklist before giving a public GitHub URL to an external evaluator (Grok, Cursor, another engineer).

## What the public handoff should contain

- `.env.example` (intentionally public bootstrap template)
- `Dockerfile`
- `docker-compose.yml`
- `pyproject.toml`
- `README.md`
- `app/`
- `dashboard/`
- `scripts/` (including `scripts/cloudflare_verify.sh`)
- `docs/` (deployment and SOP docs)

## What the public handoff must not contain

- `.env`
- `.env.local`
- `.env.*.local`
- `.env.retell.local`
- `.env.cloudflare.local`
- `logs/` and `*.log`
- `data/retell_calls/`
- `artifacts/`
- Runtime dumps, recordings, transcripts, or any PII exports

## Canonical export command

```bash
bash scripts/export_public_handoff.sh --repo Elijah-Wallis/eve-toc-build-review --push
```

## Secret scan expectations

The export script runs a secret-pattern scan and aborts if it detects likely credentials (JWTs, Twilio keys, Retell keys, Supabase service credentials, or known pasted secrets).

If it aborts:

- Remove/redact the flagged file(s) in the source repo or add a precise export exclude.
- Re-run the export command.

## Evaluator quick validation (fresh clone)

```bash
git clone https://github.com/Elijah-Wallis/eve-toc-build-review
cd eve-toc-build-review
test -f .env.example
cp .env.example .env
# fill placeholders in .env
docker build -t eve-brain .
docker compose up --build -d
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8080/metrics | head
```

Optional (Cloudflare check, requires local Cloudflare env setup):

```bash
./scripts/cloudflare_verify.sh
```
