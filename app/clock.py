from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Protocol, TypeVar


T = TypeVar("T")


class Clock(Protocol):
    def now_ms(self) -> int: ...

    async def sleep_ms(self, ms: int) -> None: ...

    async def run_with_timeout(self, awaitable: Awaitable[T], timeout_ms: int) -> T: ...


@dataclass(frozen=True, slots=True)
class RealClock(Clock):
    def now_ms(self) -> int:
        return int(time.monotonic() * 1000)

    async def sleep_ms(self, ms: int) -> None:
        await asyncio.sleep(ms / 1000.0)

    async def run_with_timeout(self, awaitable: Awaitable[T], timeout_ms: int) -> T:
        if timeout_ms <= 0:
            return await awaitable
        return await asyncio.wait_for(awaitable, timeout=timeout_ms / 1000.0)


class FakeClock(Clock):
    """
    Deterministic clock for tests.

    - now_ms() is monotonic and controlled by advance().
    - sleep_ms() blocks until advance() reaches wake time.
    """

    def __init__(self, start_ms: int = 0) -> None:
        self._now_ms = start_ms
        self._lock = asyncio.Lock()
        self._sleepers: list[tuple[int, asyncio.Future[None]]] = []

    def now_ms(self) -> int:
        return self._now_ms

    async def sleep_ms(self, ms: int) -> None:
        if ms <= 0:
            await asyncio.sleep(0)
            return

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[None] = loop.create_future()
        wake_at = self._now_ms + ms

        async with self._lock:
            # If time already advanced (possible in tests with interleavings), resolve immediately.
            if wake_at <= self._now_ms:
                fut.set_result(None)
            else:
                self._sleepers.append((wake_at, fut))
                self._sleepers.sort(key=lambda x: x[0])

        await fut

    async def run_with_timeout(self, awaitable: Awaitable[T], timeout_ms: int) -> T:
        if timeout_ms <= 0:
            return await awaitable

        main_task = asyncio.ensure_future(awaitable)
        timeout_task = asyncio.create_task(self.sleep_ms(timeout_ms))
        try:
            done, pending = await asyncio.wait(
                {main_task, timeout_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if timeout_task in done and not main_task.done():
                main_task.cancel()
                await asyncio.gather(main_task, timeout_task, return_exceptions=True)
                raise TimeoutError(f"operation timed out after {timeout_ms}ms")

            timeout_task.cancel()
            await asyncio.gather(timeout_task, return_exceptions=True)
            return await main_task
        except asyncio.CancelledError:
            main_task.cancel()
            timeout_task.cancel()
            await asyncio.gather(main_task, timeout_task, return_exceptions=True)
            raise

    async def advance(self, ms: int) -> None:
        if ms < 0:
            raise ValueError("FakeClock.advance(ms): ms must be >= 0")

        # Yield once before advancing so tasks scheduled in the same tick can
        # register their sleepers against the pre-advance time.
        await asyncio.sleep(0)

        async with self._lock:
            self._now_ms += ms
            ready: list[asyncio.Future[None]] = []
            remaining: list[tuple[int, asyncio.Future[None]]] = []
            for wake_at, fut in self._sleepers:
                if fut.done():
                    continue
                if wake_at <= self._now_ms:
                    ready.append(fut)
                else:
                    remaining.append((wake_at, fut))
            self._sleepers = remaining

        for fut in ready:
            if not fut.done():
                fut.set_result(None)

        # Yield once after waking sleepers so resumed tasks can run without requiring
        # tests to sprinkle arbitrary extra yields.
        await asyncio.sleep(0)
