# EVE V7.0 Runtime Harness

This package provides a deterministic runtime binding layer for Retell/Vapi while keeping the orchestrator source-of-truth in:

- `orchestration/eve-v7-orchestrator.yaml`

## 1) Retell binding (reference payload)

Use `orchestration/eve-v7-orchestrator.yaml` as the policy source and generate tool schemas from `contracts`.

```json
{
  "agent": {
    "name": "eve_medspa_apex_predator_v7",
    "model": "gpt-4o-mini",
    "system_prompt_source": "orchestration/eve-v7-orchestrator.yaml#flow",
    "voice": {
      "model": "soft_authoritative_female",
      "speed": 0.92,
      "post_processing": {
        "post_turn_silence_ms": 1200
      }
    }
  },
  "functions": [
    {
      "name": "send_evidence_package",
      "description": "Triggers the Double-Tap delivery (Email + optional SMS) to the clinic.
",
      "parameters": {
        "recipient_email": { "type": "string", "format": "email" },
        "delivery_method": { "type": "string", "enum": ["EMAIL_ONLY", "EMAIL_AND_SMS"] },
        "artifact_type": { "type": "string", "enum": ["AUDIO_LINK", "FAILURE_LOG_PDF"] }
      },
      "required": ["recipient_email", "delivery_method", "artifact_type"]
    },
    {
      "name": "mark_dnc_compliant",
      "description": "Immediate Do-Not-Call add.",
      "parameters": {
        "reason": {
          "type": "string",
          "enum": ["USER_REQUEST", "WRONG_NUMBER", "HOSTILE"]
        }
      },
      "required": ["reason"]
    }
  ],
  "state_machine": "orchestration/eve-v7-orchestrator.yaml"
}
```

## 2) Vapi binding (reference payload)

Use the same state machine JSON and function schema for Vapi tool calls.

```json
{
  "assistant": {
    "name": "Cassidy",
    "voice": "soft_authoritative_female",
    "temperature": 0.2,
    "max_delay_ms": 1200,
    "system": "orchestration/eve-v7-orchestrator.yaml#flow",
    "tools": "orchestration/eve-v7-orchestrator.yaml#contracts"
  },
  "dialer": {
    "initial_state": "opener"
  }
}
```

## 3) Runtime contract guards

1. Bind these as non-LLM hard checks before any tool invocation:
   - `recipient_email` is required for `send_evidence_package`
   - `delivery_method` must be enum `EMAIL_ONLY` or `EMAIL_AND_SMS`
   - `artifact_type` must be enum `AUDIO_LINK` or `FAILURE_LOG_PDF`

2. Sentiment hooks:
   - `hostile` -> route to `hostility_handler` and set `latency_ms` to 2000
   - `ai_disclosure` -> route to `disclose_ai`
   - `dnc` -> route to `dnc`

## 4) Local deterministic execution harness

Use `orchestration/eve-v7-harness-run.rb` to simulate test cases without the platform runtime.

```bash
ruby orchestration/eve-v7-harness-run.rb orchestration/eve-v7-orchestrator.yaml orchestration/eve-v7-test-cases.yaml
```

This prints per-case transition path and contract/tool expectation checks.
