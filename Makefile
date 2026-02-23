.PHONY: help call call-status retell-fast ws-on ws-restore ws-dev cloudflare-verify learn leads ops-loop money test ci ci-local metrics dashboard go self-improve skill-capture skill-validate synth-generate synth-push-leads synth-call-batch synth-map-journeys synth-export-supabase live-build-queue live-call-batch live-map-journeys live-export-supabase omni-gate-start omni-gate-eod start-outbound-dialing start-outbound-dialing-eod start

LEARN_APPLY_FLAG := $(if $(filter true 1,$(RETELL_LEARN_APPLY)),--apply,--no-apply)

help:
	@echo "Simple commands:"
	@echo "  make call                  # call default DOGFOOD_TO_NUMBER"
	@echo "  make call TO=+19859914360  # call a specific number"
	@echo "  make retell-fast           # patch live Retell B2B prompt + fastest reply settings"
	@echo "  make ws-on                 # switch B2B agent to Custom LLM WebSocket brain (requires BRAIN_WSS_BASE_URL in .env)"
	@echo "  make ws-restore BACKUP=...  # restore agent response engine from backup JSON"
	@echo "  make ws-dev                # local dev: start server + cloudflared + switch agent"
	@echo "  make cloudflare-verify     # verify Cloudflare API token from .env.cloudflare.local"
	@echo "  make learn                 # sync calls + transcripts/recordings + auto-refine at threshold"
	@echo "  make leads INPUT=...       # score/sort lead lists and optionally push to n8n"
	@echo "  make ops-loop              # compute objective metrics from call corpus + next actions"
	@echo "  make money                 # run learn + ops-loop + scorecard in one command"
	@echo "  make go                    # start server in dogfood mode (loads .env.retell.local, B2B profile)"
	@echo "  make call-status ID=call_x # fetch call status"
	@echo "  make test                  # run pytest -q"
	@echo "  make ci                    # run hard gate suite (backend + expressive + web)"
	@echo "  make ci-local              # run local hard gates without dependency install"
	@echo "  make metrics               # print key metric summary from /metrics"
	@echo "  make dashboard             # open Eve dashboard URL"
	@echo "  make synth-generate        # generate synthetic MedSpa dataset in SYNTHETIC_OUTPUT_DIR"
	@echo "  make synth-push-leads      # build synthetic call queue jsonl"
	@echo "  make synth-call-batch      # run synthetic outbound batch from queue"
	@echo "  make synth-map-journeys    # normalize Retell calls into synthetic journey rows"
	@echo "  make synth-export-supabase # export synthetic journeys into Supabase tables"
	@echo "  make live-build-queue      # build live B2B queue from Apify/local file"
	@echo "  make live-call-batch       # run live outbound calls from live queue"
	@echo "  make live-map-journeys     # normalize Retell calls into live journey rows"
	@echo "  make live-export-supabase  # export live journey rows into Supabase"
	@echo "  make start                 # same as start-outbound-dialing"
	@echo "  make omni-gate-start       # daily start sequence for outbound system"
	@echo "  make omni-gate-eod         # end-of-day metrics and outcome summary"
	@echo "  make start-outbound-dialing # one-command start for live outbound dialing (with concurrency)"
	@echo "  make start-outbound-dialing-eod # one-command end-of-day processing for live outbound dialing"
	@echo "  make self-improve          # run safe self-improvement cycle (propose)"
	@echo "  make skill-capture ID=... INTENT=... [TESTS=...]"
	@echo "  make skill-validate PATH=skills/<file>.md"

call:
	@./scripts/call_b2b.sh "$(TO)"

call-status:
	@./scripts/call_status.sh "$(ID)"

retell-fast:
	@./scripts/retell_fast_recover.sh

ws-on:
	@./scripts/b2b_switch_to_ws_brain.sh

ws-restore:
	@./scripts/retell_restore_agent.sh "$(BACKUP)"

ws-dev:
	@./scripts/ws_brain_dev_on.sh

cloudflare-verify:
	@./scripts/cloudflare_verify.sh

learn:
	@python3 scripts/retell_learning_loop.py --limit $${RETELL_LEARN_LIMIT:-100} --threshold $${RETELL_LEARN_THRESHOLD:-250} $(LEARN_APPLY_FLAG)

leads:
	@python3 scripts/lead_factory.py --input "$(INPUT)" --out-dir $${LEAD_OUT_DIR:-data/leads} --min-score $${LEAD_MIN_SCORE:-60} --top-k $${LEAD_TOP_K:-500}

ops-loop:
	@python3 scripts/revenue_ops_loop.py --calls-dir $${OPS_CALLS_DIR:-data/retell_calls} --out-dir $${OPS_OUT_DIR:-data/revenue_ops}

money:
	@python3 scripts/retell_learning_loop.py --limit $${RETELL_LEARN_LIMIT:-100} --threshold $${RETELL_LEARN_THRESHOLD:-250} $(LEARN_APPLY_FLAG)
	@python3 scripts/revenue_ops_loop.py --calls-dir $${OPS_CALLS_DIR:-data/retell_calls} --out-dir $${OPS_OUT_DIR:-data/revenue_ops}
	@URL=$${METRICS_URL:-http://127.0.0.1:8080/metrics}; \
	if curl -fsS "$$URL" >/dev/null 2>&1; then \
	  python3 scripts/dogfood_scorecard.py --metrics-url "$$URL"; \
	else \
	  echo "Skipping scorecard (metrics endpoint unreachable at $$URL)"; \
	fi

test:
	@python3 -m pytest -q

ci:
	@bash scripts/ci_hard_gates.sh

ci-local:
	@PY=".venv/bin/python"; \
	if [ ! -x "$$PY" ]; then PY="python3"; fi; \
	$$PY -m pytest -q tests tests_expressive; \
	$$PY -m pytest -q -k vic_contract; \
	$$PY -m pytest -q tests/acceptance/at_vic_100_sessions.py; \
	$$PY -m pytest -q tests/acceptance/at_no_leak_30min.py; \
	$$PY -m pytest -q tests/acceptance/at_ws_torture_5min.py; \
	if command -v npm >/dev/null 2>&1 && [ -f apps/web/package.json ]; then \
	  (cd apps/web && npm run test && npm run build); \
	else \
	  echo "Skipping web gates: npm/apps/web not available"; \
	fi

metrics:
	@python3 scripts/metrics_summary.py --metrics-url http://127.0.0.1:8080/metrics

dashboard:
	@bash scripts/run_dashboard.sh

go:
	@bash scripts/run_dashboard.sh

self-improve:
	@python3 scripts/self_improve_cycle.py --mode $${SELF_IMPROVE_MODE:-propose}

skill-capture:
	@python3 scripts/skills/capture_skill.py --id "$(ID)" --intent "$(INTENT)" --tests "$(TESTS)"

skill-validate:
	@python3 scripts/skills/validate_skill.py "$(PATH)"

SYNTHETIC_OUTPUT_DIR ?= /tmp/medspa_synthetic
SYNTHETIC_CAMPAIGN_ID ?= ont-smoke-001
SYNTHETIC_CALL_QUEUE ?= data/retell_calls/synthetic_campaign_call_queue.jsonl
SYNTHETIC_MAX_CALLS ?= 0

LIVE_OUTPUT_DIR ?= data/leads
LIVE_CAMPAIGN_ID ?= ont-live-001
LIVE_CAMPAIGN_NAME ?= b2b_outbound_workflow
LIVE_QUEUE_FILE ?= $(LIVE_OUTPUT_DIR)/live_call_queue.jsonl
LIVE_LEAD_FILE ?= $(LIVE_OUTPUT_DIR)/live_leads.csv
LIVE_MAX_CALLS ?= 5
LIVE_CONCURRENCY ?= 5
LIVE_TENANT ?= live_medspa
LIVE_DISPATCH_CONTROL_FILE ?= data/leads/.live_dispatch_controls.json

synth-generate:
	@python3 synthetic_data_for_training/generate_medspa_synthetic_data.py \
		--campaign-id "$(SYNTHETIC_CAMPAIGN_ID)" \
		--output-dir "$(SYNTHETIC_OUTPUT_DIR)"

synth-push-leads:
	@python3 scripts/synthetic_to_n8n_campaign.py \
		--input-dir "$(SYNTHETIC_OUTPUT_DIR)" \
		--out data/retell_calls \
		--campaign-id "$(SYNTHETIC_CAMPAIGN_ID)" \
		--write-summary

synth-call-batch:
	@python3 scripts/run_synthetic_campaign.py \
		--queue-file "$(SYNTHETIC_CALL_QUEUE)" \
		--out-dir data/retell_calls \
		--resume \
		--max-calls "$(SYNTHETIC_MAX_CALLS)"

synth-map-journeys:
	@python3 scripts/synthetic_journey_mapper.py \
		--calls-dir data/retell_calls \
		--campaign-id "$(SYNTHETIC_CAMPAIGN_ID)" \
		--lead-file "$(SYNTHETIC_OUTPUT_DIR)/medspa_leads.csv" \
		--out data/retell_calls/synthetic_customer_journeys.jsonl

synth-export-supabase:
	@python3 scripts/export_journey_to_supabase.py \
		--calls-dir data/retell_calls \
		--journey-path data/retell_calls/synthetic_customer_journeys.jsonl \
		--supabase-url "$${SUPABASE_URL}" \
		--supabase-key "$${SUPABASE_SERVICE_KEY}" \
		--schema "$${SUPABASE_SCHEMA:-public}"

live-build-queue:
	@python3 scripts/build_live_campaign_queue.py \
		--campaign-id "$(LIVE_CAMPAIGN_ID)" \
		--campaign-name "$(LIVE_CAMPAIGN_NAME)" \
		--out-dir "$(LIVE_OUTPUT_DIR)" \
		--top-k "$${CAMPAIGN_TOP_K:-500}" \
		--states "$${CAMPAIGN_STATES:-Texas,Florida,California}" \
		--query "$${CAMPAIGN_QUERY:-medspa}"

live-call-batch:
	@python3 scripts/run_live_campaign.py \
		--queue-file "$(LIVE_QUEUE_FILE)" \
		--out-dir data/retell_calls \
		--campaign-id "$(LIVE_CAMPAIGN_ID)" \
		--tenant "$(LIVE_TENANT)" \
		--max-calls "$(LIVE_MAX_CALLS)" \
		--concurrency "$(LIVE_CONCURRENCY)" \
		--controls-file "$(LIVE_DISPATCH_CONTROL_FILE)" \
		--resume \
		--limit-call-rate

live-map-journeys:
	@python3 scripts/synthetic_journey_mapper.py \
		--calls-dir data/retell_calls \
		--campaign-id "$(LIVE_CAMPAIGN_ID)" \
		--tenant "$(LIVE_TENANT)" \
		--lead-file "$(LIVE_LEAD_FILE)" \
		--out data/retell_calls/live_customer_journeys.jsonl \
		--push-webhook "$${N8N_OUTCOME_WEBHOOK_URL:-}"

live-export-supabase:
	@python3 scripts/export_journey_to_supabase.py \
		--calls-dir data/retell_calls \
		--journey-path data/retell_calls/live_customer_journeys.jsonl \
		--supabase-url "$${SUPABASE_URL}" \
		--supabase-key "$${SUPABASE_SERVICE_KEY}" \
		--schema "$${SUPABASE_SCHEMA:-public}"

omni-gate-start:
	@$(MAKE) live-build-queue
	@$(MAKE) live-call-batch

omni-gate-eod:
	@$(MAKE) live-map-journeys
	@python3 scripts/revenue_ops_loop.py \
		--calls-dir data/retell_calls \
		--push-webhook "$${N8N_OUTCOME_WEBHOOK_URL:-}"

start-outbound-dialing:
	@$(MAKE) omni-gate-start

start-outbound-dialing-eod:
	@$(MAKE) omni-gate-eod

start:
	@$(MAKE) start-outbound-dialing
