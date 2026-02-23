from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional, Protocol

from .clock import Clock


class LLMClient(Protocol):
    async def stream_text(self, *, prompt: str) -> AsyncIterator[str]:
        ...

    async def aclose(self) -> None:
        ...


@dataclass(frozen=True, slots=True)
class FakeLLMClient:
    clock: Clock
    tokens: list[str]
    token_delay_ms: int = 0

    async def stream_text(self, *, prompt: str) -> AsyncIterator[str]:
        # prompt is ignored; deterministic token stream for tests.
        for tok in self.tokens:
            if self.token_delay_ms > 0:
                await self.clock.sleep_ms(self.token_delay_ms)
            yield tok

    async def aclose(self) -> None:
        return


class GeminiLLMClient:
    """
    Gemini streaming adapter using the official Google Gen AI SDK (google-genai).

    Lazily imports `google-genai` so unit/VIC tests do not require credentials or the dependency.
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        vertexai: bool = False,
        project: str = "",
        location: str = "global",
        model: str = "gemini-3-flash-preview",
        thinking_level: str = "minimal",
    ) -> None:
        self._api_key = api_key
        self._vertexai = bool(vertexai)
        self._project = project
        self._location = location
        self._model = model
        self._thinking_level = (thinking_level or "minimal").strip().lower()

        self._client: Any = None
        self._aclient: Any = None
        self._types: Any = None

    def _ensure_client(self) -> tuple[Any, Any, Any]:
        if self._aclient is not None:
            return (self._client, self._aclient, self._types)

        try:
            from google import genai  # type: ignore[import-not-found]
            from google.genai import types  # type: ignore[import-not-found]
        except Exception as e:
            raise RuntimeError(
                "GeminiLLMClient requires the optional dependency 'google-genai'. "
                "Install with: python3 -m pip install -e '.[gemini]'"
            ) from e

        if self._vertexai:
            self._client = genai.Client(
                vertexai=True,
                project=self._project,
                location=self._location,
            )
        else:
            self._client = genai.Client(api_key=self._api_key)

        # Async client (aio) owns HTTP session lifecycle.
        self._aclient = self._client.aio
        self._types = types
        return (self._client, self._aclient, self._types)

    def _thinking_config(self, types_mod: Any) -> Any | None:
        # Best-effort mapping (API names may differ across releases).
        try:
            ThinkingConfig = getattr(types_mod, "ThinkingConfig")
            ThinkingLevel = getattr(types_mod, "ThinkingLevel")
        except Exception:
            return None

        level_map = {
            "minimal": getattr(ThinkingLevel, "MINIMAL", None),
            "low": getattr(ThinkingLevel, "LOW", None),
            "medium": getattr(ThinkingLevel, "MEDIUM", None),
            "high": getattr(ThinkingLevel, "HIGH", None),
        }
        level = level_map.get(self._thinking_level)
        if level is None:
            level = level_map.get("minimal")
        try:
            return ThinkingConfig(thinking_level=level, include_thoughts=False)
        except Exception:
            return None

    async def stream_text(self, *, prompt: str) -> AsyncIterator[str]:
        _, aclient, types_mod = self._ensure_client()

        # Config: low-latency voice behavior. If the SDK's config API changes, we fall back to None.
        cfg = None
        try:
            GenerateContentConfig = getattr(types_mod, "GenerateContentConfig")
            cfg = GenerateContentConfig(
                thinking_config=self._thinking_config(types_mod),
            )
        except Exception:
            cfg = None

        # Streaming API: may yield a final empty chunk; we must drain the stream to completion.
        stream = await aclient.models.generate_content_stream(
            model=self._model,
            contents=prompt,
            config=cfg,
        )

        async for chunk in stream:
            # Preferred: chunk.text
            txt = getattr(chunk, "text", None)
            if txt:
                yield str(txt)
                continue

            # Fallback: walk candidates->content->parts.
            try:
                candidates = getattr(chunk, "candidates", None) or []
                if not candidates:
                    continue
                content = getattr(candidates[0], "content", None)
                parts = getattr(content, "parts", None) or []
                buf: list[str] = []
                for p in parts:
                    if getattr(p, "thought", False):
                        continue
                    pt = getattr(p, "text", None)
                    if pt:
                        buf.append(str(pt))
                if buf:
                    yield "".join(buf)
            except Exception:
                continue

    async def aclose(self) -> None:
        if self._aclient is not None:
            try:
                await self._aclient.aclose()
            finally:
                self._aclient = None
                self._client = None
                self._types = None


class OpenAILLMClient:
    """
    OpenAI Responses streaming adapter (dual-provider pilot).

    Notes:
    - Lazy-imports the `openai` package so deterministic tests can run without credentials.
    - Emits only output text deltas; internal reasoning streams are ignored.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = "gpt-5-mini",
        reasoning_effort: str = "minimal",
        timeout_ms: int = 8000,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.reasoning_effort = (reasoning_effort or "minimal").strip().lower()
        self.timeout_ms = int(timeout_ms)
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import AsyncOpenAI  # type: ignore[import-not-found]
        except Exception as e:
            raise RuntimeError(
                "OpenAILLMClient requires the optional dependency 'openai'. "
                "Install with: python3 -m pip install -e '.[openai]'"
            ) from e
        self._client = AsyncOpenAI(api_key=self.api_key)
        return self._client

    @staticmethod
    def _iter_deltas(event: Any) -> list[str]:
        """
        Best-effort extraction for multiple SDK event shapes.
        """
        out: list[str] = []
        et = str(getattr(event, "type", "") or "")
        if et in {"response.output_text.delta", "output_text.delta"}:
            d = getattr(event, "delta", None)
            if d:
                out.append(str(d))
        # Common fallback fields.
        for k in ("delta", "text", "output_text"):
            v = getattr(event, k, None)
            if isinstance(v, str) and v:
                out.append(v)
        # Dict-like payload fallback.
        if isinstance(event, dict):
            if isinstance(event.get("delta"), str) and event.get("delta"):
                out.append(str(event["delta"]))
            if isinstance(event.get("text"), str) and event.get("text"):
                out.append(str(event["text"]))
        # De-duplicate while preserving order.
        dedup: list[str] = []
        seen: set[str] = set()
        for s in out:
            if s in seen:
                continue
            seen.add(s)
            dedup.append(s)
        return dedup

    async def stream_text(self, *, prompt: str) -> AsyncIterator[str]:
        client = self._ensure_client()
        kwargs = {
            "model": self.model,
            "input": prompt,
            "stream": True,
            "reasoning": {"effort": self.reasoning_effort},
            "timeout": max(1.0, self.timeout_ms / 1000.0),
        }
        stream = await client.responses.create(**kwargs)
        async for event in stream:
            for delta in self._iter_deltas(event):
                if delta:
                    yield str(delta)

    async def aclose(self) -> None:
        if self._client is not None:
            close_fn = getattr(self._client, "close", None)
            if callable(close_fn):
                res = close_fn()
                if asyncio.iscoroutine(res):
                    await res
            self._client = None
        return
