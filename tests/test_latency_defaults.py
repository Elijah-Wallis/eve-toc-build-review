from __future__ import annotations

import asyncio

from app.config import BrainConfig
from app.metrics import VIC
from app.protocol import OutboundUpdateAgent
from tests.harness.transport_harness import HarnessSession


def test_latency_defaults_and_update_agent_defaults() -> None:
    cfg = BrainConfig()
    assert cfg.use_llm_nlg is False
    assert cfg.vic_tool_filler_threshold_ms == 800
    assert cfg.vic_model_filler_threshold_ms == 800
    assert cfg.retell_send_update_agent_on_connect is True
    assert cfg.retell_responsiveness == 0.8
    assert cfg.retell_interruption_sensitivity == 0.8
    assert cfg.voice_plain_language_mode is True
    assert cfg.voice_no_reasoning_leak is True
    assert cfg.voice_jargon_blocklist_enabled is True


def test_update_agent_is_sent_on_connect_when_enabled() -> None:
    async def _run() -> None:
        cfg = BrainConfig(speak_first=False, retell_send_update_agent_on_connect=True)
        session = await HarnessSession.start(cfg=cfg, include_update_agent_on_start=True)
        try:
            first = await session.recv_outbound()
            second = await session.recv_outbound()
            third = await session.recv_outbound()
            assert getattr(first, "response_type", "") == "config"
            assert isinstance(second, OutboundUpdateAgent)
            assert getattr(third, "response_type", "") == "response"
        finally:
            await session.stop()

    asyncio.run(_run())


def test_b2b_repeated_low_signal_response_required_has_no_speech_plan() -> None:
    async def _run() -> None:
        cfg = BrainConfig(speak_first=False, conversation_profile="b2b")
        session = await HarnessSession.start(cfg=cfg)
        try:
            # Start emits config and an initial empty response when speak_first is false.
            _ = await session.recv_outbound()
            _ = await session.recv_outbound()

            opener = (
                "Hi, this is Cassidy with Eve. Is now a bad time for a quick question?"
            )
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [
                        {"role": "agent", "content": opener},
                        {"role": "user", "content": "   "},
                    ],
                },
                expect_ack=False,
            )

            first = await session.recv_outbound()
            assert getattr(first, "response_type", "") == "response"
            assert getattr(first, "content", "") == ""
            assert getattr(first, "content_complete", False) is True
            assert len(session.orch.speech_plans) == 0

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 2,
                    "transcript": [
                        {"role": "agent", "content": opener},
                        {"role": "user", "content": "..."},
                    ],
                },
                expect_ack=False,
            )

            second = await session.recv_outbound()
            assert getattr(second, "response_type", "") == "response"
            assert getattr(second, "content", "") == ""
            assert getattr(second, "content_complete", False) is True
            assert len(session.orch.speech_plans) == 0
        finally:
            await session.stop()


def test_b2b_no_progress_noise_does_not_change_first_response_latency_histogram() -> None:
    async def _run() -> None:
        cfg = BrainConfig(speak_first=False, conversation_profile="b2b")
        session = await HarnessSession.start(cfg=cfg)
        try:
            _ = await session.recv_outbound()
            _ = await session.recv_outbound()

            opener = (
                "Hi, this is Cassidy with Eve. Is now a bad time for a quick question?"
            )
            before = len(session.metrics.get_hist(VIC["turn_final_to_first_segment_ms"]))

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [
                        {"role": "agent", "content": opener},
                        {"role": "user", "content": "..."}, 
                    ],
                },
                expect_ack=False,
            )

            _ = await session.recv_outbound()
            assert len(session.orch.speech_plans) == 0
            after = len(session.metrics.get_hist(VIC["turn_final_to_first_segment_ms"]))
            assert after == before
        finally:
            await session.stop()

    asyncio.run(_run())


def test_b2b_fast_path_cached_branch_keeps_cache_stable() -> None:
    async def _run() -> None:
        cfg = BrainConfig(speak_first=False, conversation_profile="b2b")
        session = await HarnessSession.start(cfg=cfg)
        try:
            _ = await session.recv_outbound()
            _ = await session.recv_outbound()
            opener = (
                "Hi, this is Cassidy with Eve. Is now a bad time for a quick question?"
            )
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [
                        {"role": "agent", "content": opener},
                        {"role": "user", "content": "Not a bad time right now."},
                    ],
                },
                expect_ack=False,
            )
            _ = await session.recv_outbound()
            cache_size_after_first = len(session.orch._fast_plan_cache)

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 2,
                    "transcript": [
                        {"role": "agent", "content": opener},
                        {"role": "user", "content": "Not a bad time right now."},
                    ],
                },
                expect_ack=False,
            )
            _ = await session.recv_outbound()
            assert len(session.orch._fast_plan_cache) == cache_size_after_first
        finally:
            await session.stop()

    asyncio.run(_run())


def test_b2b_got_it_ack_noop_has_no_speech_plan_and_no_ack() -> None:
    async def _run() -> None:
        cfg = BrainConfig(speak_first=False, conversation_profile="b2b")
        session = await HarnessSession.start(cfg=cfg)
        try:
            _ = await session.recv_outbound()
            _ = await session.recv_outbound()
            opener = (
                "Hi, this is Cassidy with Eve. Is now a bad time for a quick question?"
            )

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [
                        {"role": "agent", "content": opener},
                        {"role": "user", "content": "Yep, got it."},
                    ],
                },
                expect_ack=False,
            )

            first = await session.recv_outbound()
            assert getattr(first, "response_type", "") == "response"
            assert getattr(first, "content", "") == ""
            assert getattr(first, "content_complete", False) is True
            assert len(session.orch.speech_plans) == 0

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 2,
                    "transcript": [
                        {"role": "agent", "content": opener},
                        {"role": "user", "content": "yep got it."},
                    ],
                },
                expect_ack=False,
            )
            second = await session.recv_outbound()
            assert getattr(second, "response_type", "") == "response"
            assert getattr(second, "content", "") == ""
            assert getattr(second, "content_complete", False) is True
            assert len(session.orch.speech_plans) == 0
        finally:
            await session.stop()

    asyncio.run(_run())
