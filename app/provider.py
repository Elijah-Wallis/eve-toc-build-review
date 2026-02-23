from __future__ import annotations

import os

from .canary import rollout_enabled
from .config import BrainConfig
from .llm_client import GeminiLLMClient, LLMClient, OpenAILLMClient


def build_llm_client(cfg: BrainConfig, *, session_id: str = "") -> LLMClient | None:
    if not cfg.use_llm_nlg:
        return None
    if cfg.llm_provider == "gemini":
        return GeminiLLMClient(
            api_key=cfg.gemini_api_key or os.getenv("GEMINI_API_KEY", ""),
            vertexai=cfg.gemini_vertexai,
            project=cfg.gemini_project,
            location=cfg.gemini_location,
            model=cfg.gemini_model,
            thinking_level=cfg.gemini_thinking_level,
        )
    if cfg.llm_provider == "openai":
        if cfg.openai_canary_enabled and not rollout_enabled(session_id or "default", cfg.openai_canary_percent):
            return None
        return OpenAILLMClient(
            api_key=cfg.openai_api_key or os.getenv("OPENAI_API_KEY", ""),
            model=cfg.openai_model,
            reasoning_effort=cfg.openai_reasoning_effort,
            timeout_ms=cfg.openai_timeout_ms,
        )
    return None
