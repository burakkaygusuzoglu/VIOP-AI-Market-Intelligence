"""The bounded buffer a push source must use (Phase 13).

Market data does not wait for its reader. A provider that receives events on
its own schedule has two honest choices when the reader falls behind: stop, or
lose events and say so. Growing without bound is not one of them, and neither
is losing events quietly while the downstream state goes on describing itself
as complete.

This buffer takes the second honest choice and makes it loud. When it is full
the next offer is refused and the buffer latches an overflow; the reader then
receives everything that was accepted, followed by an OVERFLOW signal, and the
stream is terminated. A stream with a hole it cannot describe is not
continued.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator

from app.domain.live.events import ProviderSignal, SignalKind, StreamItem

__all__ = ["BoundedEventBuffer"]


class BoundedEventBuffer:
    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._items: deque[StreamItem] = deque()
        self._overflowed = False
        self._closed = False
        self._ready = asyncio.Event()
        self.refused = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def overflowed(self) -> bool:
        return self._overflowed

    def __len__(self) -> int:
        return len(self._items)

    def offer(self, item: StreamItem) -> bool:
        """Accept an item, or refuse it and latch an overflow. Never blocks."""
        if self._closed or self._overflowed:
            self.refused += 1
            return False
        if len(self._items) >= self._capacity:
            self._overflowed = True
            self.refused += 1
            self._ready.set()
            return False
        self._items.append(item)
        self._ready.set()
        return True

    def close(self) -> None:
        self._closed = True
        self._ready.set()

    async def drain(self) -> AsyncIterator[StreamItem]:
        """Everything accepted, in order - then OVERFLOW if anything was lost."""
        while True:
            while self._items:
                yield self._items.popleft()
            if self._overflowed:
                yield ProviderSignal(SignalKind.OVERFLOW)
                return
            if self._closed:
                return
            self._ready.clear()
            await self._ready.wait()
