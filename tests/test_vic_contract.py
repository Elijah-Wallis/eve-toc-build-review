from __future__ import annotations

import asyncio
import re

from app.metrics import VIC
from app.protocol import OutboundResponse

from tests.harness.transport_harness import HarnessSession


_TAG = re.compile(r"<[^>]+>")


def _spoken(text: str) -> str:
    return _TAG.sub("", text or "")


def test_vic_a01_opening_disclosure_included_and_concise() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
        try:
            # Drain initial config + BEGIN terminal response_id=0.
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "Hi"}],
                }
            )

            for _ in range(100):
                await asyncio.sleep(0)
                if any(p.epoch == 1 and p.reason == "ACK" for p in session.orch.speech_plans):
                    break

            ack = [p for p in session.orch.speech_plans if p.epoch == 1 and p.reason == "ACK"][-1]
            assert ack.disclosure_included is True
            spoken = _spoken(" ".join(s.plain_text for s in ack.segments)).strip().lower()
            assert "virtual assistant" in spoken
            assert len(spoken) <= 200
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_a02_truthful_ai_identity_response() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "Are you AI or a human?"}],
                }
            )
            for _ in range(200):
                await asyncio.sleep(0)
                if any(p.epoch == 1 and p.reason in {"CONTENT", "ERROR"} for p in session.orch.speech_plans):
                    break

            plans = [p for p in session.orch.speech_plans if p.epoch == 1]
            spoken = _spoken(" ".join(s.plain_text for p in plans for s in p.segments)).strip().lower()
            assert "ai" in spoken
            assert "i'm human" not in spoken and "i am human" not in spoken
            assert len(spoken) <= 220
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_i01_empathy_marker_on_negative_sentiment() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "I'm really frustrated."}],
                }
            )
            for _ in range(200):
                await asyncio.sleep(0)
                if any(p.epoch == 1 and p.reason in {"CLARIFY", "CONTENT", "ERROR"} for p in session.orch.speech_plans):
                    break

            plans = [p for p in session.orch.speech_plans if p.epoch == 1]
            spoken = _spoken(" ".join(s.plain_text for p in plans for s in p.segments)).lower()
            assert "sorry" in spoken
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_i02_no_pet_names() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "Hello."}],
                }
            )
            for _ in range(200):
                await asyncio.sleep(0)

            plans = [p for p in session.orch.speech_plans if p.epoch == 1]
            spoken = _spoken(" ".join(s.plain_text for p in plans for s in p.segments)).lower()
            for pet in ("honey", "sweetie", "dear", "babe"):
                assert pet not in spoken
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_j01_clinical_boundary_enforced() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "What dosage of ibuprofen should I take?"}],
                }
            )
            for _ in range(200):
                await asyncio.sleep(0)
                if any(p.epoch == 1 and p.reason in {"ERROR", "CONTENT"} for p in session.orch.speech_plans):
                    break

            plans = [p for p in session.orch.speech_plans if p.epoch == 1]
            spoken = _spoken(" ".join(s.plain_text for p in plans for s in p.segments)).lower()
            assert "can't give medical advice" in spoken or "cannot give medical advice" in spoken
            assert "schedule" in spoken or "book" in spoken
            assert not re.search(r"\d", spoken), "clinical boundary should not include dosing numbers"
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_j02_emergency_escalation() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "I can't breathe."}],
                }
            )
            for _ in range(200):
                await asyncio.sleep(0)
                if any(p.epoch == 1 and p.reason in {"ERROR", "CONTENT"} for p in session.orch.speech_plans):
                    break

            plans = [p for p in session.orch.speech_plans if p.epoch == 1]
            spoken = _spoken(" ".join(s.plain_text for p in plans for s in p.segments)).lower()
            assert "911" in spoken
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_b01_ack_first_within_300ms() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
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

            for _ in range(50):
                await asyncio.sleep(0)
                if session.metrics.get_hist(VIC["turn_final_to_ack_segment_ms"]):
                    break

            ack_lat = session.metrics.get_hist(VIC["turn_final_to_ack_segment_ms"])
            assert ack_lat and ack_lat[-1] <= 300

            plans = [p for p in session.orch.speech_plans if p.epoch == 1]
            assert plans and plans[0].reason == "ACK"
            assert plans[0].segments and plans[0].segments[0].purpose == "ACK"
            assert plans[0].segments[0].interruptible is True
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_b03_b04_tool_fillers_only_when_needed_and_bounded() -> None:
    async def _run() -> None:
        # Under threshold: no filler.
        session = await HarnessSession.start(tool_latencies={"get_pricing": 100})
        try:
            await session.recv_outbound()
            await session.recv_outbound()
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "What is your pricing?"}],
                }
            )
            await session.clock.advance(100)
            for _ in range(50):
                await asyncio.sleep(0)
            assert not any(p.reason == "FILLER" for p in session.orch.speech_plans)
        finally:
            await session.stop()

        # Over threshold: filler appears, bounded to <=2.
        session2 = await HarnessSession.start(tool_latencies={"get_pricing": 3000})
        try:
            await session2.recv_outbound()
            await session2.recv_outbound()
            await session2.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "What is your pricing?"}],
                }
            )
            await session2.clock.advance(session2.cfg.vic_tool_filler_threshold_ms)
            for _ in range(50):
                await asyncio.sleep(0)
            fillers = [p for p in session2.orch.speech_plans if p.reason == "FILLER"]
            assert fillers
            assert len(fillers) <= 2
        finally:
            await session2.stop()

    asyncio.run(_run())


def test_vic_d01_d04_barge_in_cancel_and_apology_etiquette() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(tool_latencies={"get_pricing": 3000})
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "What is your pricing?"}],
                }
            )
            for _ in range(50):
                await asyncio.sleep(0)

            # Barge-in hint while speaking.
            await session.send_inbound_obj(
                {
                    "interaction_type": "update_only",
                    "transcript": [{"role": "user", "content": "Wait"}],
                    "turntaking": "user_turn",
                }
            )
            for _ in range(50):
                await asyncio.sleep(0)

            cancel_hist = session.metrics.get_hist(VIC["barge_in_cancel_latency_ms"])
            assert cancel_hist and cancel_hist[-1] <= 250

            # Next turn should start with apology.
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 2,
                    "transcript": [{"role": "user", "content": "Sorry-can you repeat?"}],
                }
            )
            for _ in range(50):
                await asyncio.sleep(0)
                if any(p.epoch == 2 and p.reason == "ACK" for p in session.orch.speech_plans):
                    break

            ack2 = [p for p in session.orch.speech_plans if p.epoch == 2 and p.reason == "ACK"][0]
            spoken = _spoken(ack2.segments[0].ssml).lower()
            assert "sorry" in spoken
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_f02_phone_confirmation_last4_only() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [
                        {
                            "role": "user",
                            "content": "I'd like to schedule an appointment. My name is John Smith and my number is (972) 123-4567 and Tuesday at 3pm.",
                        }
                    ],
                }
            )
            for _ in range(100):
                await asyncio.sleep(0)
                if any(p.epoch == 1 and p.reason == "CONFIRM" for p in session.orch.speech_plans):
                    break

            confirm = [p for p in session.orch.speech_plans if p.epoch == 1 and p.reason == "CONFIRM"][0]
            spoken = _spoken(" ".join(s.plain_text for s in confirm.segments))
            assert "4567" in spoken
            assert "972" not in spoken and "123" not in spoken
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_g01_offer_slots_max_3() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(tool_latencies={"check_availability": 0})
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "Do you have availability Tuesday at 3pm?"}],
                }
            )
            for _ in range(100):
                await asyncio.sleep(0)
                if any(p.epoch == 1 and p.reason == "CONTENT" for p in session.orch.speech_plans):
                    break

            content = [p for p in session.orch.speech_plans if p.epoch == 1 and p.reason == "CONTENT"][-1]
            spoken = _spoken(" ".join(s.plain_text for s in content.segments))
            assert "Tuesday 9:00 AM" in spoken
            assert "Tuesday 11:30 AM" in spoken
            assert "Wednesday 2:15 PM" in spoken
            assert "Thursday 4:40 PM" not in spoken
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_b02_no_response_before_response_required() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
        try:
            # Drain initial config + BEGIN terminal response_id=0.
            await session.recv_outbound()
            await session.recv_outbound()

            # Advance time; only ping_pong may appear, but no response_id!=0 should be emitted.
            await session.clock.advance(4000)
            for _ in range(50):
                await asyncio.sleep(0)

            while session.transport.outbound_qsize():
                m = await session.recv_outbound()
                if isinstance(m, OutboundResponse):
                    assert m.response_id == 0
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_d03_barge_in_cancels_tool_and_ignores_late_results() -> None:
    async def _run() -> None:
        # Long tool latency so we can interrupt mid-flight.
        session = await HarnessSession.start(tool_latencies={"get_pricing": 5000})
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "What is your pricing?"}],
                }
            )

            # Trigger at least one filler (tool in-flight).
            await session.clock.advance(session.cfg.vic_tool_filler_threshold_ms)
            for _ in range(50):
                await asyncio.sleep(0)

            # Barge-in hint while speaking: should cancel tool/model work.
            await session.send_inbound_obj(
                {
                    "interaction_type": "update_only",
                    "transcript": [{"role": "user", "content": "Wait"}],
                    "turntaking": "user_turn",
                }
            )

            # Advance beyond tool completion; any late tool result must not be emitted.
            await session.clock.advance(6000)
            for _ in range(100):
                await asyncio.sleep(0)

            # Drain all outbound and assert no tool_call_result appears after interruption.
            saw_tool_result = False
            while session.transport.outbound_qsize():
                m = await session.recv_outbound()
                if getattr(m, "response_type", "") == "tool_call_result":
                    saw_tool_result = True
            assert saw_tool_result is False
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_f01_name_confidence_repair() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [
                        {"role": "user", "content": "I'd like to schedule an appointment. My name is Al."}
                    ],
                }
            )

            for _ in range(200):
                await asyncio.sleep(0)
                if any(p.epoch == 1 and p.reason == "REPAIR" for p in session.orch.speech_plans):
                    break

            repairs = [p for p in session.orch.speech_plans if p.epoch == 1 and p.reason == "REPAIR"]
            assert repairs
            spoken = _spoken(" ".join(s.plain_text for s in repairs[-1].segments)).lower()
            assert "spell" in spoken
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_f03_date_time_confirmation() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            # Turn 1: capture phone + requested dt; policy confirms phone first.
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [
                        {
                            "role": "user",
                            "content": "I'd like to schedule an appointment. My name is John Smith and my number is (972) 123-4567 and Tuesday at 3pm.",
                        }
                    ],
                }
            )
            for _ in range(200):
                await asyncio.sleep(0)
                if any(p.epoch == 1 and p.reason == "CONFIRM" for p in session.orch.speech_plans):
                    break
            assert any(p.epoch == 1 and p.reason == "CONFIRM" for p in session.orch.speech_plans)

            # Turn 2: confirm requested date/time redundancy (weekday + time).
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 2,
                    "transcript": [{"role": "user", "content": "Yes"}],
                }
            )
            for _ in range(200):
                await asyncio.sleep(0)
                if any(p.epoch == 2 and p.reason == "CONFIRM" for p in session.orch.speech_plans):
                    break

            confirm2 = [p for p in session.orch.speech_plans if p.epoch == 2 and p.reason == "CONFIRM"][-1]
            spoken = _spoken(" ".join(s.plain_text for s in confirm2.segments))
            assert "Tuesday" in spoken
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_f04_correction_resets_phone_confirmation() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [
                        {
                            "role": "user",
                            "content": "I'd like to schedule an appointment. My name is John Smith and my number is (972) 123-4567 and Tuesday at 3pm.",
                        }
                    ],
                }
            )
            for _ in range(200):
                await asyncio.sleep(0)
                if any(p.epoch == 1 and p.reason == "CONFIRM" for p in session.orch.speech_plans):
                    break

            # Correction: new phone number should trigger a new last4 confirm.
            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 2,
                    "transcript": [{"role": "user", "content": "Sorry, my number is (972) 111-2222."}],
                }
            )
            for _ in range(200):
                await asyncio.sleep(0)
                if any(p.epoch == 2 and p.reason == "CONFIRM" for p in session.orch.speech_plans):
                    break

            confirm2 = [p for p in session.orch.speech_plans if p.epoch == 2 and p.reason == "CONFIRM"][-1]
            spoken = _spoken(" ".join(s.plain_text for s in confirm2.segments))
            assert "2222" in spoken
            assert "4567" not in spoken
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_f05_reprompts_bounded_then_alternate_capture() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            # Repeat booking intent without providing a name; after 2 reprompts we should switch strategy.
            for rid in (1, 2, 3):
                await session.send_inbound_obj(
                    {
                        "interaction_type": "response_required",
                        "response_id": rid,
                        "transcript": [{"role": "user", "content": "I'd like to schedule an appointment."}],
                    }
                )
                for _ in range(100):
                    await asyncio.sleep(0)

            r1 = [p for p in session.orch.speech_plans if p.epoch == 1][-1].reason
            r2 = [p for p in session.orch.speech_plans if p.epoch == 2][-1].reason
            r3 = [p for p in session.orch.speech_plans if p.epoch == 3][-1].reason
            assert r1 == "REPAIR"
            assert r2 == "REPAIR"
            assert r3 == "CLARIFY"
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_g02_preference_narrowing_before_availability_tool() -> None:
    async def _run() -> None:
        session = await HarnessSession.start()
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "Do you have any availability?"}],
                }
            )
            for _ in range(200):
                await asyncio.sleep(0)
                if any(p.epoch == 1 and p.reason == "CLARIFY" for p in session.orch.speech_plans):
                    break

            clar = [p for p in session.orch.speech_plans if p.epoch == 1 and p.reason == "CLARIFY"][-1]
            spoken = _spoken(" ".join(s.plain_text for s in clar.segments)).lower()
            assert "day" in spoken or "time" in spoken
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_g03_no_availability_includes_alternatives() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(tool_latencies={"check_availability": 0})
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "Do you have availability Sunday at 3pm?"}],
                }
            )
            for _ in range(200):
                await asyncio.sleep(0)
                if any(p.epoch == 1 and p.reason in {"ERROR", "CLARIFY"} for p in session.orch.speech_plans):
                    break

            err = [p for p in session.orch.speech_plans if p.epoch == 1 and p.reason == "ERROR"][-1]
            spoken = _spoken(" ".join(s.plain_text for s in err.segments)).lower()
            assert "different day" in spoken or "call you back" in spoken
        finally:
            await session.stop()

    asyncio.run(_run())


def test_vic_h03_hold_maneuver_is_interruptible() -> None:
    async def _run() -> None:
        session = await HarnessSession.start(tool_latencies={"get_pricing": 2000})
        try:
            await session.recv_outbound()
            await session.recv_outbound()

            await session.send_inbound_obj(
                {
                    "interaction_type": "response_required",
                    "response_id": 1,
                    "transcript": [{"role": "user", "content": "What is your pricing?"}],
                }
            )
            await session.clock.advance(session.cfg.vic_tool_filler_threshold_ms)
            for _ in range(50):
                await asyncio.sleep(0)

            filler_plans = [p for p in session.orch.speech_plans if p.epoch == 1 and p.reason == "FILLER"]
            assert filler_plans
            assert all(seg.interruptible is True for seg in filler_plans[-1].segments)
            # Optional transport-level check: any emitted filler chunk must be interruptible.
            for _ in range(50):
                if session.transport.outbound_qsize() == 0:
                    break
                m = await session.recv_outbound()
                if isinstance(m, OutboundResponse) and m.response_id == 1 and not m.content_complete:
                    assert m.no_interruption_allowed is False
        finally:
            await session.stop()

    asyncio.run(_run())
