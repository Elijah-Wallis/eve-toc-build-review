from __future__ import annotations

import os
from dataclasses import dataclass


def _getenv_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _getenv_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _getenv_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw


def _getenv_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class BrainConfig:
    # Conversation profile
    conversation_profile: str = "clinic"  # clinic | b2b

    # Retell config response
    retell_auto_reconnect: bool = True
    retell_call_details: bool = True
    retell_transcript_with_tool_calls: bool = True

    # Websocket route policy
    websocket_canonical_route: str = "llm-websocket"
    websocket_enforce_canonical_route: bool = True
    ws_structured_logging: bool = False

    # Brain behavior
    speak_first: bool = True
    backchannel_enabled: bool = False
    inbound_queue_max: int = 256
    outbound_queue_max: int = 256
    turn_queue_max: int = 64
    idle_timeout_ms: int = 5000
    ping_interval_ms: int = 2000
    keepalive_ping_write_deadline_ms: int = 100
    ws_write_timeout_ms: int = 400
    ws_close_on_write_timeout: bool = True
    ws_max_consecutive_write_timeouts: int = 2
    ws_max_frame_bytes: int = 262_144
    transcript_max_utterances: int = 200
    transcript_max_chars: int = 50_000

    # Speech markup / pacing primitives (Retell-accurate defaults)
    # - DASH_PAUSE: spaced dashes (" - ") are the pause primitive for Retell.
    # - RAW_TEXT: no pauses inserted.
    # - SSML: experimental; inserts <break time="...ms"/> tags.
    speech_markup_mode: str = "DASH_PAUSE"  # DASH_PAUSE | RAW_TEXT | SSML
    dash_pause_scope: str = "PROTECTED_ONLY"  # PROTECTED_ONLY | SEGMENT_BOUNDARY
    dash_pause_unit_ms: int = 200
    digit_dash_pause_unit_ms: int = 150
    retell_normalize_for_speech: bool = False  # optional platform-side setting (doc surfaced)

    # LLM integration (provider-agnostic; tests default to deterministic fakes)
    llm_provider: str = "fake"  # fake | gemini | openai
    use_llm_nlg: bool = False
    llm_phrasing_for_facts_enabled: bool = False
    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"
    openai_reasoning_effort: str = "minimal"
    openai_timeout_ms: int = 8000
    openai_canary_enabled: bool = False
    openai_canary_percent: int = 0
    gemini_api_key: str = ""
    gemini_vertexai: bool = False
    gemini_project: str = ""
    gemini_location: str = "global"
    gemini_model: str = "gemini-3-flash-preview"
    gemini_thinking_level: str = "minimal"

    # WS security hardening (optional; prefer enforcing at reverse proxy)
    ws_allowlist_enabled: bool = False
    ws_allowlist_cidrs: str = ""  # comma-separated CIDRs; empty allows all
    ws_trusted_proxy_enabled: bool = False
    ws_trusted_proxy_cidrs: str = ""  # comma-separated CIDRs allowed to set X-Forwarded-For
    ws_shared_secret_enabled: bool = False
    ws_shared_secret: str = ""  # if set, require matching header
    ws_shared_secret_header: str = "X-RETELL-SIGNATURE"
    ws_query_token: str = ""  # optional query token for WS URL
    ws_query_token_param: str = "token"

    # Voice quality guardrails (plain-language deterministic mode)
    voice_plain_language_mode: bool = True
    voice_no_reasoning_leak: bool = True
    voice_jargon_blocklist_enabled: bool = True

    # Skills runtime (default OFF for stability)
    skills_enabled: bool = False
    skills_dir: str = "skills"
    skills_max_injected: int = 3

    # Shell runtime policy (default local-only; hosted OFF)
    shell_mode: str = "local"  # local | hosted | hybrid
    shell_enable_hosted: bool = False
    shell_allowed_commands: str = ""
    shell_tool_enabled: bool = False
    shell_tool_canary_enabled: bool = False
    shell_tool_canary_percent: int = 0

    # Self-improvement runtime (default OFF)
    self_improve_mode: str = "off"  # off | propose | apply

    # Speculative planning (uses update_only to compute early, emits only after response_required)
    speculative_planning_enabled: bool = True
    speculative_debounce_ms: int = 0
    speculative_tool_prefetch_enabled: bool = True
    speculative_tool_prefetch_timeout_ms: int = 100

    # Retell dynamic agent tuning on connect
    retell_send_update_agent_on_connect: bool = True
    retell_responsiveness: float = 0.8
    retell_interruption_sensitivity: float = 0.8
    retell_reminder_trigger_ms: int = 3000
    retell_reminder_max_count: int = 1
    # Pre-ACK behavior split:
    # - safe_pre_ack_on_response_required_enabled: emits a tiny response chunk only after response_required.
    # - interrupt_pre_ack_on_agent_turn_enabled: emits agent_interrupt on update_only.agent_turn (experimental).
    # Forced OFF (production hardening): avoid speculative ACK/interrupt frames that can race with
    # Retell-side interruption and create overlap/replay edge cases under backpressure.
    safe_pre_ack_on_response_required_enabled: bool = False
    interrupt_pre_ack_on_agent_turn_enabled: bool = False
    # Back-compat: legacy flag that enabled both.
    ultra_fast_pre_ack_enabled: bool = False

    # VIC timing thresholds
    vic_ack_deadline_ms: int = 250
    vic_tool_filler_threshold_ms: int = 800
    vic_tool_timeout_ms: int = 1500
    vic_model_filler_threshold_ms: int = 800
    vic_model_timeout_ms: int = 3800
    vic_max_fillers_per_tool: int = 1
    vic_max_segment_expected_ms: int = 650
    vic_max_monologue_expected_ms: int = 12000
    vic_max_reprompts: int = 2
    vic_barge_in_cancel_p95_ms: int = 250

    # Speech pacing estimator
    pace_ms_per_char: int = 12

    # Persona/runtime metadata
    clinic_name: str = "Clinic"
    clinic_city: str = "Plano"
    clinic_state: str = "Texas"
    b2b_agent_name: str = "Cassidy"
    b2b_org_name: str = "Eve"
    b2b_auto_disclosure: bool = False
    eve_v7_enabled: bool = True
    eve_v7_script_path: str = "/Users/elijah/Documents/New project/orchestration/eve-v7-orchestrator.yaml"
    b2b_business_name: str = "Clinic"
    b2b_city: str = "Plano"
    b2b_test_timestamp: str = "Saturday at 6:30 PM"
    b2b_evidence_type: str = "AUDIO"
    b2b_emr_system: str = "Zenoti, Boulevard, or MangoMint"
    b2b_contact_number: str = "+14695998571"

    @staticmethod
    def from_env() -> "BrainConfig":
        conversation_profile = _getenv_str("CONVERSATION_PROFILE", "clinic").strip().lower()
        if conversation_profile not in {"clinic", "b2b"}:
            conversation_profile = "clinic"
        clinic_name = _getenv_str("CLINIC_NAME", "Clinic")
        clinic_city = _getenv_str("CLINIC_CITY", "Plano")
        clinic_state = _getenv_str("CLINIC_STATE", "Texas")
        raw_ws_route = _getenv_str("WEBSOCKET_CANONICAL_ROUTE", "llm-websocket").strip().lower()
        raw_ws_route = raw_ws_route.strip().strip("/")
        if not raw_ws_route:
            raw_ws_route = "llm-websocket"
        raw_mode = _getenv_str("SPEECH_MARKUP_MODE", "DASH_PAUSE").strip().upper()
        if raw_mode not in {"DASH_PAUSE", "RAW_TEXT", "SSML"}:
            raw_mode = "DASH_PAUSE"
        raw_pause_scope = _getenv_str("DASH_PAUSE_SCOPE", "PROTECTED_ONLY").strip().upper()
        if raw_pause_scope not in {"PROTECTED_ONLY", "SEGMENT_BOUNDARY"}:
            raw_pause_scope = "PROTECTED_ONLY"
        llm_provider = _getenv_str("LLM_PROVIDER", "fake").strip().lower()
        if llm_provider not in {"fake", "gemini", "openai"}:
            llm_provider = "fake"
        shell_mode = _getenv_str("SHELL_MODE", "local").strip().lower()
        if shell_mode not in {"local", "hosted", "hybrid"}:
            shell_mode = "local"
        self_improve_mode = _getenv_str("SELF_IMPROVE_MODE", "off").strip().lower()
        if self_improve_mode not in {"off", "propose", "apply"}:
            self_improve_mode = "off"

        return BrainConfig(
            conversation_profile=conversation_profile,
            retell_auto_reconnect=_getenv_bool("RETELL_AUTO_RECONNECT", True),
            retell_call_details=_getenv_bool("RETELL_CALL_DETAILS", True),
            retell_transcript_with_tool_calls=_getenv_bool(
                "RETELL_TRANSCRIPT_WITH_TOOL_CALLS", True
            ),
            websocket_canonical_route=raw_ws_route,
            websocket_enforce_canonical_route=_getenv_bool(
                "WEBSOCKET_ENFORCE_CANONICAL_ROUTE", True
            ),
            ws_structured_logging=_getenv_bool("WEBSOCKET_STRUCTURED_LOGGING", False),
            speak_first=_getenv_bool("BRAIN_SPEAK_FIRST", True),
            backchannel_enabled=_getenv_bool("BRAIN_BACKCHANNEL_ENABLED", False),
            inbound_queue_max=_getenv_int("BRAIN_INBOUND_QUEUE_MAX", 256),
            outbound_queue_max=_getenv_int("BRAIN_OUTBOUND_QUEUE_MAX", 256),
            turn_queue_max=_getenv_int("BRAIN_TURN_QUEUE_MAX", 64),
            idle_timeout_ms=_getenv_int("BRAIN_IDLE_TIMEOUT_MS", 5000),
            ping_interval_ms=_getenv_int("BRAIN_PING_INTERVAL_MS", 2000),
            keepalive_ping_write_deadline_ms=_getenv_int("KEEPALIVE_PING_WRITE_DEADLINE_MS", 100),
            ws_write_timeout_ms=_getenv_int("WS_WRITE_TIMEOUT_MS", 400),
            ws_close_on_write_timeout=_getenv_bool("WS_CLOSE_ON_WRITE_TIMEOUT", True),
            ws_max_consecutive_write_timeouts=_getenv_int("WS_MAX_CONSECUTIVE_WRITE_TIMEOUTS", 2),
            ws_max_frame_bytes=_getenv_int("WS_MAX_FRAME_BYTES", 262_144),
            transcript_max_utterances=_getenv_int("TRANSCRIPT_MAX_UTTERANCES", 200),
            transcript_max_chars=_getenv_int("TRANSCRIPT_MAX_CHARS", 50_000),
            speech_markup_mode=raw_mode,
            dash_pause_scope=raw_pause_scope,
            dash_pause_unit_ms=_getenv_int("DASH_PAUSE_UNIT_MS", 200),
            digit_dash_pause_unit_ms=_getenv_int("DIGIT_DASH_PAUSE_UNIT_MS", 150),
            retell_normalize_for_speech=_getenv_bool("RETELL_NORMALIZE_FOR_SPEECH", False),
            llm_provider=llm_provider,
            use_llm_nlg=_getenv_bool("BRAIN_USE_LLM_NLG", False),
            llm_phrasing_for_facts_enabled=_getenv_bool("LLM_PHRASING_FOR_FACTS_ENABLED", False),
            openai_api_key=_getenv_str("OPENAI_API_KEY", ""),
            openai_model=_getenv_str("OPENAI_MODEL", "gpt-5-mini"),
            openai_reasoning_effort=_getenv_str("OPENAI_REASONING_EFFORT", "minimal"),
            openai_timeout_ms=_getenv_int("OPENAI_TIMEOUT_MS", 8000),
            openai_canary_enabled=_getenv_bool("OPENAI_CANARY_ENABLED", False),
            openai_canary_percent=max(0, min(100, _getenv_int("OPENAI_CANARY_PERCENT", 0))),
            gemini_api_key=_getenv_str("GEMINI_API_KEY", ""),
            gemini_vertexai=_getenv_bool("GEMINI_VERTEXAI", False),
            gemini_project=_getenv_str("GEMINI_PROJECT", ""),
            gemini_location=_getenv_str("GEMINI_LOCATION", "global"),
            gemini_model=_getenv_str("GEMINI_MODEL", "gemini-3-flash-preview"),
            gemini_thinking_level=_getenv_str("GEMINI_THINKING_LEVEL", "minimal"),
            ws_allowlist_enabled=_getenv_bool("WS_ALLOWLIST_ENABLED", False),
            ws_allowlist_cidrs=_getenv_str("WS_ALLOWLIST_CIDRS", ""),
            ws_trusted_proxy_enabled=_getenv_bool("WS_TRUSTED_PROXY_ENABLED", False),
            ws_trusted_proxy_cidrs=_getenv_str("WS_TRUSTED_PROXY_CIDRS", ""),
            ws_shared_secret_enabled=_getenv_bool("WS_SHARED_SECRET_ENABLED", False),
            ws_shared_secret=_getenv_str("WS_SHARED_SECRET", ""),
            ws_shared_secret_header=_getenv_str("WS_SHARED_SECRET_HEADER", "X-RETELL-SIGNATURE"),
            ws_query_token=_getenv_str("WS_QUERY_TOKEN", ""),
            ws_query_token_param=_getenv_str("WS_QUERY_TOKEN_PARAM", "token"),
            voice_plain_language_mode=_getenv_bool("VOICE_PLAIN_LANGUAGE_MODE", True),
            voice_no_reasoning_leak=_getenv_bool("VOICE_NO_REASONING_LEAK", True),
            voice_jargon_blocklist_enabled=_getenv_bool("VOICE_JARGON_BLOCKLIST_ENABLED", True),
            skills_enabled=_getenv_bool("SKILLS_ENABLED", False),
            skills_dir=_getenv_str("SKILLS_DIR", "skills"),
            skills_max_injected=_getenv_int("SKILLS_MAX_INJECTED", 3),
            shell_mode=shell_mode,
            shell_enable_hosted=_getenv_bool("SHELL_ENABLE_HOSTED", False),
            shell_allowed_commands=_getenv_str("SHELL_ALLOWED_COMMANDS", ""),
            shell_tool_enabled=_getenv_bool("SHELL_TOOL_ENABLED", False),
            shell_tool_canary_enabled=_getenv_bool("SHELL_TOOL_CANARY_ENABLED", False),
            shell_tool_canary_percent=max(0, min(100, _getenv_int("SHELL_TOOL_CANARY_PERCENT", 0))),
            self_improve_mode=self_improve_mode,
            speculative_planning_enabled=_getenv_bool("SPECULATIVE_PLANNING_ENABLED", True),
            speculative_debounce_ms=_getenv_int("SPECULATIVE_DEBOUNCE_MS", 0),
            speculative_tool_prefetch_enabled=_getenv_bool("SPECULATIVE_TOOL_PREFETCH_ENABLED", True),
            speculative_tool_prefetch_timeout_ms=_getenv_int(
                "SPECULATIVE_TOOL_PREFETCH_TIMEOUT_MS", 100
            ),
            retell_send_update_agent_on_connect=_getenv_bool(
                "RETELL_SEND_UPDATE_AGENT_ON_CONNECT", True
            ),
            retell_responsiveness=_getenv_float("RETELL_RESPONSIVENESS", 0.8),
            retell_interruption_sensitivity=_getenv_float(
                "RETELL_INTERRUPTION_SENSITIVITY", 0.8
            ),
            retell_reminder_trigger_ms=_getenv_int("RETELL_REMINDER_TRIGGER_MS", 3000),
            retell_reminder_max_count=_getenv_int("RETELL_REMINDER_MAX_COUNT", 1),
            # Forced OFF (production hardening): see dataclass defaults.
            safe_pre_ack_on_response_required_enabled=False,
            interrupt_pre_ack_on_agent_turn_enabled=False,
            ultra_fast_pre_ack_enabled=False,
            vic_ack_deadline_ms=_getenv_int("VIC_ACK_DEADLINE_MS", 250),
            vic_tool_filler_threshold_ms=_getenv_int("VIC_TOOL_FILLER_THRESHOLD_MS", 800),
            vic_tool_timeout_ms=_getenv_int("VIC_TOOL_TIMEOUT_MS", 1500),
            vic_model_filler_threshold_ms=_getenv_int("VIC_MODEL_FILLER_THRESHOLD_MS", 800),
            vic_model_timeout_ms=_getenv_int("VIC_MODEL_TIMEOUT_MS", 3800),
            vic_max_fillers_per_tool=_getenv_int("VIC_MAX_FILLERS_PER_TOOL", 1),
            vic_max_segment_expected_ms=_getenv_int("VIC_MAX_SEGMENT_EXPECTED_MS", 650),
            vic_max_monologue_expected_ms=_getenv_int(
                "VIC_MAX_MONOLOGUE_EXPECTED_MS", 12000
            ),
            vic_max_reprompts=_getenv_int("VIC_MAX_REPROMPTS", 2),
            vic_barge_in_cancel_p95_ms=_getenv_int("VIC_BARGE_IN_CANCEL_P95_MS", 250),
            pace_ms_per_char=_getenv_int("PACE_MS_PER_CHAR", 12),
            clinic_name=clinic_name,
            clinic_city=clinic_city,
            clinic_state=clinic_state,
            b2b_agent_name=_getenv_str("B2B_AGENT_NAME", "Cassidy"),
            b2b_org_name=_getenv_str("B2B_ORG_NAME", "Eve"),
            b2b_auto_disclosure=_getenv_bool("B2B_AUTO_DISCLOSURE", False),
            eve_v7_enabled=_getenv_bool("EVE_V7_ENABLED", True),
            eve_v7_script_path=_getenv_str(
                "EVE_V7_SCRIPT_PATH",
                "/Users/elijah/Documents/New project/orchestration/eve-v7-orchestrator.yaml",
            ),
            b2b_business_name=_getenv_str("B2B_BUSINESS_NAME", clinic_name),
            b2b_city=_getenv_str("B2B_CITY", clinic_city),
            b2b_test_timestamp=_getenv_str("B2B_TEST_TIMESTAMP", "Saturday at 6:30 PM"),
            b2b_evidence_type=_getenv_str("B2B_EVIDENCE_TYPE", "AUDIO"),
            b2b_emr_system=_getenv_str("B2B_EMR_SYSTEM", "Zenoti, Boulevard, or MangoMint"),
            b2b_contact_number=_getenv_str("B2B_CONTACT_NUMBER", "+14695998571"),
        )
