"""A deterministic, scripted market-data stream. Simulated, and says so.

This is the only live provider in the build. It plays a script - candle
events, connection signals, clock advances, failures - so that every
behaviour the live architecture promises can be exercised exactly, repeatably,
and without waiting for real time to pass.

Its provenance is ``SIMULATED_HISTORICAL_STREAM`` and cannot be anything else:
it is a class constant, the raw events it emits have no provenance field, and
no argument changes it. Historical fixture candles played through it remain
historical fixture candles, whatever the receive clock says.

No network, no credentials, no exchange, no scraping.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from app.application.live.buffer import BoundedEventBuffer
from app.domain.common.enums import Timeframe
from app.domain.live.events import (
    ProviderSignal,
    RawCandleEvent,
    SignalKind,
    StreamItem,
    StreamKey,
    StreamProvenance,
)
from app.domain.market.candle import Candle

__all__ = [
    "Advance",
    "Emit",
    "Fail",
    "ManualClock",
    "MockBackfillProvider",
    "MockPushProvider",
    "MockStreamProvider",
    "Signal",
    "candle_event",
    "historical_script",
]


class ManualClock:
    """A receive-side clock that moves only when told to."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("the clock must be timezone-aware")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        if delta < timedelta(0):
            raise ValueError("a clock does not run backwards")
        self._now += delta


@dataclass(frozen=True, slots=True)
class Emit:
    event: RawCandleEvent


@dataclass(frozen=True, slots=True)
class Signal:
    kind: SignalKind


@dataclass(frozen=True, slots=True)
class Advance:
    delta: timedelta


@dataclass(frozen=True, slots=True)
class Fail:
    """Raise mid-stream. The message is whatever the test wants to prove is
    never leaked."""

    message: str


Step = Emit | Signal | Advance | Fail


def candle_event(
    symbol: str,
    timeframe: Timeframe,
    open_time: datetime,
    open_: str,
    high: str,
    low: str,
    close: str,
    volume: str = "1000",
    *,
    closed: bool = True,
    sequence: int | None = None,
    event_time: datetime | None = None,
) -> RawCandleEvent:
    """A well-formed raw event. ``event_time`` defaults to the interval end."""
    end = open_time + timedelta(minutes=timeframe.minutes)
    return RawCandleEvent(
        symbol=symbol,
        timeframe=timeframe.value,
        open_time=open_time,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        closed=closed,
        event_time=event_time if event_time is not None else end,
        sequence=sequence,
    )


def historical_script(
    candles: Mapping[Timeframe, Sequence[Candle]],
    *,
    spacing: timedelta = timedelta(seconds=1),
    sequenced: bool = True,
    end: bool = True,
) -> list[Step]:
    """Historical closed candles played in market-event order.

    Interleaves timeframes by each candle's interval end - the moment it became
    a confirmed fact - so a 1H candle arrives after the twelve 5M candles it
    covers, as it would from a real feed. The receive clock advances by
    ``spacing`` before each event: simulated arrival, not market time.
    """
    ordered: list[tuple[datetime, int, Timeframe, int, Candle]] = []
    for rank, (timeframe, series) in enumerate(
        sorted(candles.items(), key=lambda kv: kv[0].minutes)
    ):
        for index, candle in enumerate(series):
            finished = candle.open_time + timedelta(minutes=timeframe.minutes)
            ordered.append((finished, rank, timeframe, index, candle))
    ordered.sort(key=lambda item: (item[0], item[1]))

    steps: list[Step] = [Signal(SignalKind.CONNECTED)]
    for _, _, timeframe, index, candle in ordered:
        steps.append(Advance(spacing))
        steps.append(
            Emit(
                RawCandleEvent(
                    symbol=candle.symbol,
                    timeframe=timeframe.value,
                    open_time=candle.open_time,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    volume=candle.volume,
                    closed=True,
                    event_time=candle.open_time + timedelta(minutes=timeframe.minutes),
                    sequence=index if sequenced else None,
                )
            )
        )
    if end:
        steps.append(Signal(SignalKind.END_OF_STREAM))
    return steps


# ----------------------------------------------------------------------


@dataclass
class _ScriptedSubscription:
    script: Sequence[Step]
    clock: ManualClock
    closed: bool = False

    async def items(self) -> AsyncIterator[StreamItem]:
        for step in self.script:
            if self.closed:
                return
            if isinstance(step, Advance):
                self.clock.advance(step.delta)
            elif isinstance(step, Emit):
                yield step.event
            elif isinstance(step, Signal):
                yield ProviderSignal(step.kind)
            else:
                raise RuntimeError(step.message)

    async def close(self) -> None:
        self.closed = True


@dataclass
class MockStreamProvider:
    """Plays one script per subscription. Streams only - no backfill."""

    script: Sequence[Step]
    clock: ManualClock
    fail_on_subscribe: str | None = None
    subscriptions: list[_ScriptedSubscription] = field(default_factory=list)

    @property
    def provenance(self) -> StreamProvenance:
        return StreamProvenance.SIMULATED_HISTORICAL_STREAM

    async def subscribe(
        self, symbol: str, timeframes: tuple[Timeframe, ...]
    ) -> _ScriptedSubscription:
        if self.fail_on_subscribe is not None:
            raise ConnectionError(self.fail_on_subscribe)
        subscription = _ScriptedSubscription(script=self.script, clock=self.clock)
        self.subscriptions.append(subscription)
        return subscription


@dataclass
class MockBackfillProvider(MockStreamProvider):
    """A scripted stream that can also resend closed candles by sequence."""

    archive: Mapping[StreamKey, Sequence[RawCandleEvent]] = field(default_factory=dict)
    backfill_calls: list[tuple[StreamKey, int]] = field(default_factory=list)

    async def backfill(
        self, key: StreamKey, *, after_sequence: int, limit: int
    ) -> Sequence[RawCandleEvent]:
        self.backfill_calls.append((key, after_sequence))
        found = [
            event
            for event in self.archive.get(key, ())
            if isinstance(event.sequence, int) and event.sequence > after_sequence
        ]
        return found[:limit]


@dataclass
class _BufferedSubscription:
    buffer: BoundedEventBuffer

    def items(self) -> AsyncIterator[StreamItem]:
        return self.buffer.drain()

    async def close(self) -> None:
        self.buffer.close()


class MockPushProvider:
    """A source that pushes on its own schedule, through a bounded buffer.

    ``push`` stands for a network callback: it never blocks and never grows
    the buffer past its capacity. What happens when it fills is the buffer's
    documented policy - the stream is declared broken, not quietly thinned.
    """

    def __init__(self, capacity: int) -> None:
        self.buffer = BoundedEventBuffer(capacity)

    @property
    def provenance(self) -> StreamProvenance:
        return StreamProvenance.SIMULATED_HISTORICAL_STREAM

    def push(self, item: StreamItem) -> bool:
        return self.buffer.offer(item)

    async def subscribe(
        self, symbol: str, timeframes: tuple[Timeframe, ...]
    ) -> _BufferedSubscription:
        return _BufferedSubscription(self.buffer)
