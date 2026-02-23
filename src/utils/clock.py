from __future__ import annotations

import asyncio
import heapq
from dataclasses import dataclass, field
from typing import Protocol


class Clock(Protocol):
    def now_ms(self) -> int: ...

    async def sleep_ms(self, delay_ms: int) -> None: ...


class RealClock:
    def now_ms(self) -> int:
        return int(asyncio.get_running_loop().time() * 1000)

    async def sleep_ms(self, delay_ms: int) -> None:
        if delay_ms <= 0:
            await asyncio.sleep(0)
            return
        await asyncio.sleep(delay_ms / 1000.0)


@dataclass(order=True)
class _Sleeper:
    wake_at: int
    fut: asyncio.Future[None] = field(compare=False)


class FakeClock:
    def __init__(self, *, start_ms: int = 0) -> None:
        self._now_ms = int(start_ms)
        self._sleepers: list[_Sleeper] = []

    def now_ms(self) -> int:
        return int(self._now_ms)

    async def sleep_ms(self, delay_ms: int) -> None:
        if delay_ms <= 0:
            await asyncio.sleep(0)
            return
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[None] = loop.create_future()
        heapq.heappush(self._sleepers, _Sleeper(self._now_ms + int(delay_ms), fut))
        await fut

    async def advance(self, delta_ms: int) -> None:
        self._now_ms += int(delta_ms)
        while self._sleepers and self._sleepers[0].wake_at <= self._now_ms:
            sl = heapq.heappop(self._sleepers)
            if not sl.fut.done():
                sl.fut.set_result(None)
        await asyncio.sleep(0)
