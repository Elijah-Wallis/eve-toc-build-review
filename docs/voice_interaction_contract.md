# Voice Interaction Contract (VIC) v1.0

See `tests/test_vic_contract.py` for deterministic invariants enforced in CI.

Intermediate artifacts:
- `SpeechPlan` / `SpeechSegment` in `app/speech_planner.py`
- `TraceEvent` in `app/trace.py`

## Retell-Accurate Output Semantics

Default speech markup mode is **DASH_PAUSE**:
- pause token is spaced dashes: `" - "`
- longer pauses repeat the unit (double spaces appear between dashes)
- protected digit spans (phone / codes) are rendered read-slowly as: `2 - 1 - 3 - 4`
- default pause scope is `PROTECTED_ONLY` to avoid choppy generic speech
- optional `SEGMENT_BOUNDARY` scope can re-enable explicit boundary pauses

Reference: [Retell Add Pause](https://docs.retellai.com/build/add-pause).  
Dash pause behavior requires spaces around `-` (`" - "`).

SSML `<break>` tags are supported only as an experimental mode and are not used by default.

Deterministic variation:
- ACK/filler phrase selection is hash-based (`call_id`, `turn_id`, segment kind/index)
- this adds conversational variation without randomness and preserves replay determinism

Fact-preserving phrasing guard (default off):
- `LLM_PHRASING_FOR_FACTS_ENABLED=false` keeps factual turns deterministic/tool-rendered.
- When enabled, factual phrasing uses placeholder-locked templates and validation:
  - placeholders must survive unchanged
  - numeric literals outside placeholders are rejected
  - violations fall back to deterministic templates and increment `llm.fact_guard_fallback_total`

Memory compaction contract:
- transcript memory is bounded by `TRANSCRIPT_MAX_UTTERANCES` and `TRANSCRIPT_MAX_CHARS`
- older history compacts into a deterministic summary blob
- summary keeps minimal PII only (for phone data, only last4)

Optional Retell normalization:
- `RETELL_NORMALIZE_FOR_SPEECH=true` can improve consistency for numbers/currency/dates
- tradeoff: may add latency (typically around ~100ms)

## Backchanneling Policy

Server-generated backchannels via `agent_interrupt` are OFF by default because `agent_interrupt`
is an interruption primitive. Recommended backchannels are configured in the Retell agent itself.
