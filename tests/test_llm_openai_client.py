from __future__ import annotations

import asyncio
import importlib.util
import sys
import types

from app.llm_client import OpenAILLMClient


class _FakeStream:
    def __init__(self, events):
        self._events = list(events)

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._events):
            raise StopAsyncIteration
        v = self._events[self._i]
        self._i += 1
        return v


class _FakeResponses:
    def __init__(self, events):
        self._events = events

    async def create(self, **kwargs):
        _ = kwargs
        return _FakeStream(self._events)


class _FakeAsyncOpenAI:
    def __init__(self, api_key=None):
        _ = api_key
        self.closed = False
        self.responses = _FakeResponses(
            [
                {"type": "response.output_text.delta", "delta": "Hello"},
                {"type": "response.output_text.delta", "delta": " there"},
                {"type": "response.output_text.delta", "delta": ""},
            ]
        )

    async def close(self):
        self.closed = True


def test_openai_client_stream_parses_deltas(monkeypatch) -> None:
    fake_mod = types.ModuleType("openai")
    fake_mod.AsyncOpenAI = _FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_mod)

    async def _run() -> None:
        client = OpenAILLMClient(api_key="k", model="gpt-5-mini")
        parts = []
        async for d in client.stream_text(prompt="hi"):
            parts.append(d)
        assert "".join(parts) == "Hello there"
        await client.aclose()

    asyncio.run(_run())


def test_openai_client_missing_dependency_raises(monkeypatch) -> None:
    if importlib.util.find_spec("openai") is not None:
        # Environment has real package; this contract only applies when dependency is absent.
        return
    monkeypatch.delitem(sys.modules, "openai", raising=False)

    async def _run() -> None:
        client = OpenAILLMClient(api_key="k")
        try:
            async for _ in client.stream_text(prompt="x"):
                pass
        except RuntimeError as e:
            assert "optional dependency 'openai'" in str(e)
            return
        raise AssertionError("expected RuntimeError")

    asyncio.run(_run())
