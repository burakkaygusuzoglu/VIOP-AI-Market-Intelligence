"""Controllable sources for the Part 2A workspace tests.

``QueueProvider`` delivers exactly what a test puts in its queue, when the
test puts it there, so ordering, overflow and cancellation can be driven one
item at a time. Its provenance is the Part 1 constant, like every provider.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timedelta

from app.adapters.live.mock_stream import ManualClock, candle_event
from app.adapters.market_data.csv_provider import CsvCandleTextParser
from app.application.live.catalog import (
    OpenedSource,
    PlaybackPace,
    SourceError,
    SourceErrorKind,
    SourceSummary,
    SourceTimeframe,
)
from app.application.live.workspace import LiveWorkspace, WorkspaceLimits
from app.domain.common.enums import Timeframe
from app.domain.live.events import (
    ProviderSignal,
    RawCandleEvent,
    SignalKind,
    StreamItem,
    StreamProvenance,
)
from app.domain.market.candle import Candle
from tests.unit.live.support import M5, RECEIVE_START, SYMBOL, at, fixture_market

SOURCE_ID = "RD-" + "a" * 32


class QueueProvider:
    """Emits what the test enqueues. ``None`` ends the iteration silently."""

    def __init__(self, *, fail_on_subscribe: bool = False) -> None:
        self.queue: asyncio.Queue[StreamItem | Exception | None] = asyncio.Queue()
        self.closed = False
        self._fail = fail_on_subscribe

    @property
    def provenance(self) -> StreamProvenance:
        return StreamProvenance.SIMULATED_HISTORICAL_STREAM

    async def subscribe(self, symbol: str, timeframes: tuple[Timeframe, ...]) -> QueueProvider:
        if self._fail:
            raise ConnectionError("postgresql://user:sk-live-do-not-leak-0002@db/prod")
        return self

    async def items(self) -> AsyncIterator[StreamItem]:
        while True:
            item = await self.queue.get()
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item

    async def close(self) -> None:
        self.closed = True

    def put(self, *items: StreamItem | Exception | None) -> None:
        for item in items:
            self.queue.put_nowait(item)


def summary(label: str = SYMBOL) -> SourceSummary:
    return SourceSummary(
        source_id=SOURCE_ID,
        instrument_label=label,
        origin="USER_SUPPLIED_HISTORICAL",
        timeframes=(SourceTimeframe(M5, 288, at(0), at(5 * 287)),),
        streamable=True,
        refusal=None,
    )


class FakeCatalog:
    """Hands out a fresh ``QueueProvider`` per session and remembers it."""

    def __init__(self, *, fail_on_subscribe: bool = False) -> None:
        self.providers: list[QueueProvider] = []
        self.opened = 0
        self._fail = fail_on_subscribe

    async def list_sources(
        self, *, offset: int, limit: int
    ) -> tuple[tuple[SourceSummary, ...], int]:
        return (summary(),), 1

    async def open(
        self,
        source_id: str,
        *,
        timeframes: tuple[Timeframe, ...],
        window_candles: int,
        pace: PlaybackPace,
    ) -> OpenedSource:
        if source_id != SOURCE_ID:
            raise SourceError(SourceErrorKind.NOT_FOUND, "SOURCE_NOT_FOUND", "no such dataset")
        self.opened += 1
        provider = QueueProvider(fail_on_subscribe=self._fail)
        self.providers.append(provider)
        return OpenedSource(
            summary=summary(),
            provider=provider,
            provider_id="TEST_QUEUE_PROVIDER",
            timeframes=timeframes,
            candles_per_timeframe=dict.fromkeys(timeframes, window_candles),
            market_window_start=at(0),
            market_window_end=at(5 * window_candles),
            event_spacing_seconds=1.0,
            total_events=window_candles * len(timeframes),
        )


def workspace(
    catalog: FakeCatalog | None = None,
    *,
    clock: ManualClock | None = None,
    **limits: object,
) -> tuple[LiveWorkspace, FakeCatalog, ManualClock]:
    source = catalog or FakeCatalog()
    moment = clock or ManualClock(RECEIVE_START)
    return (
        LiveWorkspace(
            catalog=source,
            clock=moment,
            parser=CsvCandleTextParser(),
            limits=WorkspaceLimits(**limits),  # type: ignore[arg-type]
        ),
        source,
        moment,
    )


async def create(ws: LiveWorkspace, *, timeframes: tuple[Timeframe, ...] = (M5,)) -> str:
    view = await ws.create(
        source_id=SOURCE_ID, timeframes=timeframes, window_candles=100, pace=PlaybackPace.FAST
    )
    return view.session_id


def bar(index: int, *, sequence: int | None = None) -> RawCandleEvent:
    return candle_event(
        SYMBOL, M5, at(5 * index), "100", "101", "99", "100.5",
        sequence=index if sequence is None else sequence,
    )  # fmt: skip


def fixture_bars(count: int) -> list[RawCandleEvent]:
    """Real fixture candles (the replay factories'), as raw 5M events."""
    series: Sequence[Candle] = fixture_market(count)[M5]
    return [
        RawCandleEvent(
            symbol=c.symbol,
            timeframe=c.timeframe.value,
            open_time=c.open_time,
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=c.volume,
            closed=True,
            event_time=c.open_time + timedelta(minutes=5),
            sequence=index,
        )
        for index, c in enumerate(series)
    ]


CONNECTED = ProviderSignal(SignalKind.CONNECTED)
DISCONNECTED = ProviderSignal(SignalKind.DISCONNECTED)
END = ProviderSignal(SignalKind.END_OF_STREAM)


async def settle() -> None:
    """Let the session task consume everything already queued."""
    for _ in range(20):
        await asyncio.sleep(0)


def later(clock: ManualClock, seconds: float) -> datetime:
    clock.advance(timedelta(seconds=seconds))
    return clock.now()
