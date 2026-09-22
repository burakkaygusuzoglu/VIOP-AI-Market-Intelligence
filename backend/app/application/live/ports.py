"""What a live market-data provider must offer, and what it may (Phase 13).

Small on purpose. A provider that can only stream implements
:class:`LiveMarketDataProvider` and :class:`LiveSubscription` and nothing
else; one that can also replay missed candles additionally satisfies
:class:`BackfillCapable`, which is checked structurally at runtime rather than
forced on every provider as a method that raises "not supported".

No field here is vendor-specific. Instrument identity is a stream label, time
comes as market event time, and provenance is the provider's to state - and
the only provider in this build states that it is simulated.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

from app.domain.common.enums import Timeframe
from app.domain.live.events import RawCandleEvent, StreamItem, StreamKey, StreamProvenance

__all__ = ["BackfillCapable", "LiveMarketDataProvider", "LiveSubscription"]


@runtime_checkable
class LiveSubscription(Protocol):
    """One open stream. Pull-based: the consumer decides when to read.

    Pulling is what makes backpressure free for a source that can be paused.
    A source that cannot - a network push - must put a bounded buffer between
    itself and ``items`` and say OVERFLOW when it fills, rather than grow or
    drop silently. :class:`app.application.live.buffer.BoundedEventBuffer` is
    that buffer.
    """

    def items(self) -> AsyncIterator[StreamItem]: ...

    async def close(self) -> None: ...


class LiveMarketDataProvider(Protocol):
    @property
    def provenance(self) -> StreamProvenance: ...

    async def subscribe(
        self, symbol: str, timeframes: tuple[Timeframe, ...]
    ) -> LiveSubscription: ...


@runtime_checkable
class BackfillCapable(Protocol):
    """A provider that can resend closed candles missed during an outage.

    Backfill is requested by *sequence*, because only a sequenced stream can
    prove that what came back fills the hole. Whatever is returned goes through
    the same validation as live events, and continuity is then proven by the
    sequence numbers - never by the provider asserting that it is complete.
    """

    async def backfill(
        self, key: StreamKey, *, after_sequence: int, limit: int
    ) -> Sequence[RawCandleEvent]: ...
