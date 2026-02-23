from __future__ import annotations

import asyncio

from app.config import BrainConfig
from app.protocol import OutboundResponse

from tests.harness.transport_harness import HarnessSession


class EmptyTerminalChunkLLM:
    async def stream_text(self, *, prompt: str):
        yield "Hello"
        yield " there."
        yield ""  # provider may send a final empty delta

    async def aclose(self) -> None:
        return


def test_llm_stream_ignores_empty_terminal_delta_and_completes_turn() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(
            cfg=BrainConfig(
                speak_first=False,
                retell_auto_reconnect=False,
                use_llm_nlg=True,
                llm_provider="fake",
                idle_timeout_ms=60000,
            ),
            llm=EmptyTerminalChunkLLM(),
        )
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "Hi"}],
                }
            )

            saw_nonempty_nonterminal = False
            saw_empty_nonterminal = False
            saw_terminal = False
            for _ in range(200):
                m = await session.recv_outbound()
                if isinstance(m, OutboundResponse) and m.response_id == 1:
                    if m.content_complete:
                        saw_terminal = True
                        break
                    if (m.content or "") == "":
                        saw_empty_nonterminal = True
                    else:
                        saw_nonempty_nonterminal = True

            assert saw_terminal is True
            assert saw_nonempty_nonterminal is True
            assert saw_empty_nonterminal is False
        finally:
            await session.stop()

    asyncio.run(_run())

