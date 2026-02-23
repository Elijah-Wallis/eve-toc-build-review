from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Generic, Optional, TypeVar


T = TypeVar("T")


class QueueClosed(Exception):
    pass


EvictPredicate = Callable[[T], bool]


class BoundedDequeQueue(Generic[T]):
    """
    Bounded async queue with explicit eviction policies.

    - Non-blocking put: if full, caller may provide an eviction predicate.
    - Single consumer is assumed, but multiple producers are safe.
    - Supports drop_where() for epoch compaction.
    """

    def __init__(self, maxsize: int) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be > 0")
        self._maxsize = int(maxsize)
        self._q: Deque[T] = deque()
        self._closed = False
        self._cv = asyncio.Condition()

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def qsize(self) -> int:
        return len(self._q)

    def closed(self) -> bool:
        return self._closed

    async def put(self, item: T, *, evict: Optional[EvictPredicate[T]] = None) -> bool:
        async with self._cv:
            if self._closed:
                return False

            if len(self._q) < self._maxsize:
                self._q.append(item)
                self._cv.notify()
                return True

            if evict is not None:
                # Find a victim to drop.
                for existing in list(self._q):
                    if evict(existing):
                        try:
                            self._q.remove(existing)
                        except ValueError:
                            pass
                        break
                if len(self._q) < self._maxsize:
                    self._q.append(item)
                    self._cv.notify()
                    return True

            return False

    async def get(self) -> T:
        async with self._cv:
            while not self._q and not self._closed:
                await self._cv.wait()

            if self._q:
                return self._q.popleft()

            raise QueueClosed()

    async def get_prefer(self, pred: EvictPredicate[T]) -> T:
        """
        Dequeue the first item matching pred, else FIFO.
        """
        async with self._cv:
            while not self._q and not self._closed:
                await self._cv.wait()

            if not self._q:
                raise QueueClosed()

            for existing in list(self._q):
                if pred(existing):
                    try:
                        self._q.remove(existing)
                    except ValueError:
                        break
                    return existing

            return self._q.popleft()

    async def wait_for_any(self, pred: EvictPredicate[T]) -> bool:
        """
        Block until any queued item matches pred.
        """
        async with self._cv:
            while True:
                if any(pred(x) for x in self._q):
                    return True
                if self._closed:
                    raise QueueClosed()
                await self._cv.wait()

    async def close(self) -> None:
        async with self._cv:
            self._closed = True
            self._cv.notify_all()

    async def drop_where(self, pred: EvictPredicate[T]) -> int:
        async with self._cv:
            before = len(self._q)
            self._q = deque([x for x in self._q if not pred(x)])
            dropped = before - len(self._q)
            if dropped > 0:
                self._cv.notify_all()
            return dropped

    async def any_where(self, pred: EvictPredicate[T]) -> bool:
        async with self._cv:
            return any(pred(x) for x in self._q)

    async def remove_where(self, pred: EvictPredicate[T]) -> int:
        return await self.drop_where(pred)

    async def evict_one_where(self, pred: EvictPredicate[T]) -> bool:
        async with self._cv:
            for existing in list(self._q):
                if pred(existing):
                    try:
                        self._q.remove(existing)
                    except ValueError:
                        return False
                    self._cv.notify_all()
                    return True
            return False
