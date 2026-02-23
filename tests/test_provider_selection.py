from __future__ import annotations

from app.config import BrainConfig
from app.provider import build_llm_client


def test_provider_selection_disabled_returns_none() -> None:
    cfg = BrainConfig(use_llm_nlg=False, llm_provider="openai")
    assert build_llm_client(cfg) is None


def test_provider_selection_openai() -> None:
    cfg = BrainConfig(use_llm_nlg=True, llm_provider="openai", openai_model="gpt-5-mini")
    client = build_llm_client(cfg)
    assert client is not None
    assert client.__class__.__name__ == "OpenAILLMClient"


def test_provider_selection_openai_canary_disabled_for_subject() -> None:
    cfg = BrainConfig(
        use_llm_nlg=True,
        llm_provider="openai",
        openai_model="gpt-5-mini",
        openai_canary_enabled=True,
        openai_canary_percent=0,
    )
    assert build_llm_client(cfg, session_id="canary-0") is None


def test_provider_selection_openai_canary_enabled_for_subject() -> None:
    cfg = BrainConfig(
        use_llm_nlg=True,
        llm_provider="openai",
        openai_model="gpt-5-mini",
        openai_canary_enabled=True,
        openai_canary_percent=100,
    )
    client = build_llm_client(cfg, session_id="canary-100")
    assert client is not None
    assert client.__class__.__name__ == "OpenAILLMClient"


def test_provider_selection_gemini() -> None:
    cfg = BrainConfig(use_llm_nlg=True, llm_provider="gemini", gemini_model="gemini-3-flash-preview")
    client = build_llm_client(cfg)
    assert client is not None
    assert client.__class__.__name__ == "GeminiLLMClient"


def test_provider_selection_fake_returns_none() -> None:
    cfg = BrainConfig(use_llm_nlg=True, llm_provider="fake")
    assert build_llm_client(cfg) is None
