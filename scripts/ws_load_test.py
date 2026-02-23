from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from typing import Iterable


def _now_ms() -> int:
    return int(time.time() * 1000)


def _mono_ms() -> int:
    return int(time.monotonic() * 1000)


def _percentile(values: Iterable[int], p: float) -> int | None:
    v = sorted(int(x) for x in values)
    if not v:
        return None
    if p <= 0:
        return v[0]
    if p >= 100:
        return v[-1]
    k = int(round((p / 100.0) * (len(v) - 1)))
    return v[k]


@dataclass(slots=True)
class SessionStats:
    ack_ms: list[int]
    cancel_ms: list[int]
    ping_echo_ms: list[int]
    keepalive_misses: int
    protocol_errors: int = 0
    close_reason: str = ""
    closed_early: bool = False
    hung: bool = False


async def _recv_until_begin_complete(ws) -> None:
    # Drain config + BEGIN response_id=0 stream (greeting or empty terminal).
    for _ in range(200):
        raw = await ws.recv()
        try:
            msg = json.loads(raw)
        except Exception:
            continue

        if msg.get("response_type") == "response" and int(msg.get("response_id", -1)) == 0:
            if bool(msg.get("content_complete")):
                return


async def _run_one(
    *,
    idx: int,
    base_url: str,
    turns: int,
    duration_sec: int,
    turn_interval_ms: int,
    torture_pause_reads_ms: int,
    torture_pause_reads_every_turn: bool,
    keepalive_deadline_ms: int,
) -> SessionStats:
    try:
        import websockets  # type: ignore[import-not-found]
    except Exception as e:
        raise RuntimeError(
            "scripts/ws_load_test.py requires the optional dependency 'websockets'. "
            "Install with: python3 -m pip install websockets"
        ) from e

    call_id = f"wslt{idx}"
    uri = f"{base_url.rstrip('/')}/{call_id}"
    ack_ms: list[int] = []
    cancel_ms: list[int] = []
    ping_echo_ms: list[int] = []
    keepalive_misses = 0
    protocol_errors = 0
    pending_pings: dict[int, int] = {}
    close_reason = ""
    closed_early = False
    start_ms = _mono_ms()

    def _record_close(exc: Exception) -> None:
        nonlocal close_reason, closed_early
        if close_reason:
            return
        reason = str(getattr(exc, "reason", "") or "").strip()
        code = getattr(exc, "code", None)
        if reason:
            close_reason = reason
        elif code is not None:
            close_reason = f"code={code}"
        else:
            close_reason = type(exc).__name__
        closed_early = True

    async with websockets.connect(uri, open_timeout=5, close_timeout=2) as ws:
        try:
            await _recv_until_begin_complete(ws)
        except websockets.exceptions.ConnectionClosed as e:  # type: ignore[attr-defined]
            _record_close(e)
            return SessionStats(
                ack_ms=ack_ms,
                cancel_ms=cancel_ms,
                ping_echo_ms=ping_echo_ms,
                keepalive_misses=keepalive_misses,
                protocol_errors=protocol_errors + 1,
                close_reason=close_reason,
                closed_early=closed_early,
            )

        # Keepalive: send ping_pong periodically (Retell -> server direction).
        async def ping_loop() -> None:
            try:
                while True:
                    await asyncio.sleep(2.0)
                    ts = _now_ms()
                    pending_pings[ts] = _mono_ms()
                    await ws.send(
                        json.dumps(
                            {"interaction_type": "ping_pong", "timestamp": ts},
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                    )
            except asyncio.CancelledError:
                return
            except websockets.exceptions.ConnectionClosed as e:  # type: ignore[attr-defined]
                _record_close(e)
                return
            except Exception:
                return

        ping_task = asyncio.create_task(ping_loop())
        try:
            rid = 1
            while True:
                if int(duration_sec) > 0:
                    if (_mono_ms() - start_ms) >= int(duration_sec) * 1000:
                        break
                else:
                    if rid > int(turns):
                        break

                # Send response_required.
                t0 = _mono_ms()
                expected_rid = int(rid)
                t_barge: int | None = None
                try:
                    await ws.send(
                        json.dumps(
                            {
                                "interaction_type": "response_required",
                                "response_id": int(rid),
                                "transcript": [{"role": "user", "content": "Hi"}],
                            },
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                    )
                except websockets.exceptions.ConnectionClosed as e:  # type: ignore[attr-defined]
                    _record_close(e)
                    break

                do_torture = bool(
                    int(torture_pause_reads_ms) > 0
                    and (rid == 1 or bool(torture_pause_reads_every_turn))
                )
                if do_torture:
                    # Pause reads to pressure server writes, then barge-in and advance epoch.
                    await asyncio.sleep(int(torture_pause_reads_ms) / 1000.0)
                    try:
                        t_barge = _mono_ms()
                        await ws.send(
                            json.dumps(
                                {
                                    "interaction_type": "update_only",
                                    "transcript": [{"role": "user", "content": "Wait"}],
                                    "turntaking": "user_turn",
                                },
                                separators=(",", ":"),
                                sort_keys=True,
                            )
                        )
                        expected_rid = int(rid) + 1
                        await ws.send(
                            json.dumps(
                                {
                                    "interaction_type": "response_required",
                                    "response_id": int(expected_rid),
                                    "transcript": [{"role": "user", "content": "Actually, can you repeat?"}],
                                },
                                separators=(",", ":"),
                                sort_keys=True,
                            )
                        )
                        rid = int(expected_rid)
                    except websockets.exceptions.ConnectionClosed as e:  # type: ignore[attr-defined]
                        _record_close(e)
                        break

                # Wait for first chunk + terminal for expected_rid.
                saw_first = False
                saw_terminal = False
                for _ in range(2000):
                    try:
                        raw = await ws.recv()
                    except websockets.exceptions.ConnectionClosed as e:  # type: ignore[attr-defined]
                        _record_close(e)
                        break
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        protocol_errors += 1
                        continue

                    if msg.get("response_type") == "ping_pong":
                        ts = int(msg.get("timestamp", -1))
                        sent_at = pending_pings.pop(ts, None)
                        if sent_at is not None:
                            delay = max(0, _mono_ms() - int(sent_at))
                            ping_echo_ms.append(delay)
                            if delay > int(keepalive_deadline_ms):
                                keepalive_misses += 1
                        continue

                    if msg.get("response_type") != "response":
                        continue
                    if int(msg.get("response_id", -1)) != int(expected_rid):
                        continue

                    if not saw_first and not bool(msg.get("content_complete")):
                        saw_first = True
                        ack_ms.append(_mono_ms() - t0)
                    if bool(msg.get("content_complete")):
                        saw_terminal = True
                        if t_barge is not None:
                            cancel_ms.append(max(0, _mono_ms() - int(t_barge)))
                        break

                if closed_early:
                    break
                if not saw_first:
                    protocol_errors += 1
                if not saw_terminal:
                    protocol_errors += 1

                rid += 1
                if int(turn_interval_ms) > 0:
                    await asyncio.sleep(int(turn_interval_ms) / 1000.0)

        finally:
            ping_task.cancel()
            await asyncio.gather(ping_task, return_exceptions=True)

    now = _mono_ms()
    for _, sent_at in list(pending_pings.items()):
        if (now - int(sent_at)) > int(keepalive_deadline_ms):
            keepalive_misses += 1

    return SessionStats(
        ack_ms=ack_ms,
        cancel_ms=cancel_ms,
        ping_echo_ms=ping_echo_ms,
        keepalive_misses=keepalive_misses,
        protocol_errors=protocol_errors,
        close_reason=close_reason,
        closed_early=closed_early,
    )


async def _main_async(args) -> None:
    timeout_sec: float | None = None
    if int(args.duration_sec) > 0:
        timeout_sec = float(args.duration_sec) + max(30.0, float(args.keepalive_deadline_ms) / 1000.0 + 10.0)

    async def _run_with_watchdog(i: int) -> SessionStats:
        try:
            coro = _run_one(
                idx=i,
                base_url=args.url,
                turns=int(args.turns),
                duration_sec=int(args.duration_sec),
                turn_interval_ms=int(args.turn_interval_ms),
                torture_pause_reads_ms=int(args.torture_pause_reads_ms),
                torture_pause_reads_every_turn=bool(args.torture_pause_reads_every_turn),
                keepalive_deadline_ms=int(args.keepalive_deadline_ms),
            )
            if timeout_sec is None:
                return await coro
            return await asyncio.wait_for(coro, timeout=timeout_sec)
        except asyncio.TimeoutError:
            return SessionStats(
                ack_ms=[],
                cancel_ms=[],
                ping_echo_ms=[],
                keepalive_misses=1,
                protocol_errors=1,
                close_reason="WATCHDOG_TIMEOUT",
                closed_early=True,
                hung=True,
            )

    stats = await asyncio.gather(*[_run_with_watchdog(i) for i in range(int(args.sessions))])

    ack_all: list[int] = []
    cancel_all: list[int] = []
    ping_all: list[int] = []
    keepalive_misses = 0
    errs = 0
    write_timeout_backpressure_closes_total = 0
    unexpected_closes_total = 0
    hung_sessions_total = 0
    for s in stats:
        ack_all.extend(s.ack_ms)
        cancel_all.extend(s.cancel_ms)
        ping_all.extend(s.ping_echo_ms)
        keepalive_misses += int(s.keepalive_misses)
        errs += int(s.protocol_errors)
        if s.hung:
            hung_sessions_total += 1
        if s.closed_early:
            if "WRITE_TIMEOUT_BACKPRESSURE" in str(s.close_reason):
                write_timeout_backpressure_closes_total += 1
            else:
                unexpected_closes_total += 1

    print("**WS Load Test Summary**")
    print(f"url={args.url}")
    print(
        f"sessions={args.sessions} turns={args.turns} "
        f"duration_sec={args.duration_sec} turn_interval_ms={args.turn_interval_ms}"
    )
    print(f"protocol_errors_total={errs}")
    print(f"keepalive_misses_total={keepalive_misses}")
    print(f"write_timeout_backpressure_closes_total={write_timeout_backpressure_closes_total}")
    print(f"unexpected_closes_total={unexpected_closes_total}")
    print(f"hung_sessions_total={hung_sessions_total}")
    print(
        "ack_latency_ms="
        f"p50={_percentile(ack_all, 50)} p95={_percentile(ack_all, 95)} p99={_percentile(ack_all, 99)}"
    )
    print(
        "cancel_latency_ms="
        f"p50={_percentile(cancel_all, 50)} p95={_percentile(cancel_all, 95)} p99={_percentile(cancel_all, 99)}"
    )
    print(
        "ping_echo_delay_ms="
        f"p50={_percentile(ping_all, 50)} p95={_percentile(ping_all, 95)} p99={_percentile(ping_all, 99)}"
    )
    if args.assert_keepalive:
        if keepalive_misses > 0:
            raise SystemExit(
                "keepalive deadline misses observed: "
                f"{keepalive_misses} > 0 (deadline={args.keepalive_deadline_ms}ms)"
            )
        if hung_sessions_total > 0:
            raise SystemExit(f"hung sessions observed: {hung_sessions_total} > 0")
        if unexpected_closes_total > 0:
            raise SystemExit(f"unexpected closes observed: {unexpected_closes_total} > 0")


def main() -> None:
    ap = argparse.ArgumentParser(description="Real-socket WebSocket load test (Retell-style message flow).")
    ap.add_argument(
        "--url",
        type=str,
        default="ws://127.0.0.1:8080/llm-websocket",
        help="base ws URL (no trailing call_id), e.g. ws://127.0.0.1:8080/llm-websocket",
    )
    ap.add_argument("--sessions", type=int, default=25, help="number of concurrent WS sessions")
    ap.add_argument("--turns", type=int, default=2, help="number of turns per session when duration-sec=0")
    ap.add_argument(
        "--duration-sec",
        type=int,
        default=0,
        help="if >0, ignore --turns and run each session loop for this wall-clock duration",
    )
    ap.add_argument(
        "--turn-interval-ms",
        type=int,
        default=250,
        help="delay between turns in duration mode (and after each turn in turns mode)",
    )
    ap.add_argument(
        "--torture-pause-reads-ms",
        type=int,
        default=0,
        help="if >0, pause reads for this duration to create send backpressure",
    )
    ap.add_argument(
        "--torture-pause-reads-every-turn",
        action="store_true",
        help="apply pause-reads torture on every turn (default is first turn only)",
    )
    ap.add_argument(
        "--keepalive-deadline-ms",
        type=int,
        default=5000,
        help="deadline for ping echo latency and unresolved ping checks",
    )
    ap.add_argument(
        "--assert-keepalive",
        action="store_true",
        help="fail non-zero exit if keepalive misses, hangs, or unexpected closes are observed",
    )
    args = ap.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
