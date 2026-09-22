"""Stored historical datasets, played through the live stream (Phase 13 Part 2A).

The simulated source for the Live Intelligence workspace. It plays candles
that already exist - datasets somebody uploaded to replay or backtesting,
stored immutably under a content-derived id - and invents none. Every event it
emits is a closed historical candle, and its provenance is the Part 1 constant
``SIMULATED_HISTORICAL_STREAM``: there is no parameter, environment variable
or request field that could make it claim anything else.

## What it does not do

* **No forming candles.** A stored candle is a finished one. Emitting its
  "in-progress" states would mean inventing intermediate highs, lows and
  closes that nobody observed, so this source publishes closed candles only.
* **No grid-slot numbering.** Sequence numbers are the candle's position in
  the loaded window. If the dataset itself jumps in time - a weekend, a
  session break, a hole in the file - the numbers stay contiguous and the time
  does not, which Part 1 records as an unexplained TEMPORAL_GAP. Numbering by
  grid slot instead would assert that candles were lost, which this source
  does not know either.
* **No contract facts.** The dataset's symbol is passed on as a stream label.
  Nothing here reads a multiplier, a tick size, a margin or an expiry.

## Pacing is receive-clock only

Events are separated by a fixed real delay chosen from :class:`PlaybackPace`.
The delay changes when an event *arrives*; the event's market times are the
stored ones, untouched.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta

from app.adapters.live.mock_stream import Advance, Emit, Signal, Step, historical_script
from app.application.live.catalog import (
    OpenedSource,
    PlaybackPace,
    SourceError,
    SourceErrorKind,
    SourceSummary,
    SourceTimeframe,
)
from app.application.replay.ports import ReplayStore, ReplayStoreUnavailableError, StoredDataset
from app.domain.common.enums import Timeframe
from app.domain.live.events import ProviderSignal, StreamItem, StreamProvenance
from app.domain.live.limits import LiveLimits
from app.domain.market.candle import Candle

__all__ = [
    "DEFAULT_SPACING",
    "PROVIDER_ID",
    "SOURCE_ORIGIN",
    "PacedPlaybackProvider",
    "ReplayDatasetCatalog",
]

PROVIDER_ID = "STORED_DATASET_PLAYBACK"
SOURCE_ORIGIN = "USER_SUPPLIED_HISTORICAL"
"""The same label the replay API gives these candles: somebody's upload."""

DEFAULT_SPACING: Mapping[PlaybackPace, float] = {
    PlaybackPace.SLOW: 1.0,
    PlaybackPace.NORMAL: 0.25,
    PlaybackPace.FAST: 0.1,
}
"""Seconds between two played events."""

_STREAM_SYMBOL = re.compile(r"^[A-Za-z0-9._-]+$")
"""The Part 1 stream-identity rule, checked up front so a source that would
have every event refused is refused once, with a reason, instead."""


# ----------------------------------------------------------------------
# The provider
# ----------------------------------------------------------------------


@dataclass
class _PacedSubscription:
    steps: Sequence[Step]
    closed: bool = False

    async def items(self) -> AsyncIterator[StreamItem]:
        for step in self.steps:
            if self.closed:
                return
            if isinstance(step, Advance):
                await asyncio.sleep(step.delta.total_seconds())
            elif isinstance(step, Emit):
                yield step.event
            elif isinstance(step, Signal):
                yield ProviderSignal(step.kind)

    async def close(self) -> None:
        self.closed = True


class PacedPlaybackProvider:
    """A finite, pre-built script of historical events, played in real time."""

    def __init__(self, steps: Sequence[Step]) -> None:
        self._steps = tuple(steps)

    @property
    def provenance(self) -> StreamProvenance:
        return StreamProvenance.SIMULATED_HISTORICAL_STREAM

    async def subscribe(self, symbol: str, timeframes: tuple[Timeframe, ...]) -> _PacedSubscription:
        return _PacedSubscription(self._steps)


# ----------------------------------------------------------------------
# The catalog
# ----------------------------------------------------------------------


class ReplayDatasetCatalog:
    """Stored replay datasets as simulated live sources. Reads, never writes."""

    def __init__(
        self,
        store: ReplayStore,
        *,
        spacing: Mapping[PlaybackPace, float] = DEFAULT_SPACING,
        live_limits: LiveLimits | None = None,
    ) -> None:
        self._store = store
        self._spacing = dict(spacing)
        self._limits = live_limits or LiveLimits()

    async def list_sources(
        self, *, offset: int, limit: int
    ) -> tuple[tuple[SourceSummary, ...], int]:
        try:
            datasets, total = await self._store.list_datasets(offset=offset, limit=limit)
        except ReplayStoreUnavailableError:
            raise _unavailable() from None
        return tuple(self._summary(item) for item in datasets), total

    async def open(
        self,
        source_id: str,
        *,
        timeframes: tuple[Timeframe, ...],
        window_candles: int,
        pace: PlaybackPace,
    ) -> OpenedSource:
        try:
            dataset = await self._store.get_dataset(source_id)
        except ReplayStoreUnavailableError:
            raise _unavailable() from None
        if dataset is None:
            raise SourceError(
                SourceErrorKind.NOT_FOUND, "SOURCE_NOT_FOUND", "no stored dataset has this id"
            )
        summary = self._summary(dataset)
        if not summary.streamable:
            raise SourceError(
                SourceErrorKind.INVALID,
                "SOURCE_NOT_STREAMABLE",
                summary.refusal or "this dataset cannot be streamed",
            )
        held = {item.timeframe for item in dataset.timeframes}
        missing = [tf.value for tf in timeframes if tf not in held]
        if missing:
            raise SourceError(
                SourceErrorKind.INVALID,
                "TIMEFRAME_NOT_IN_SOURCE",
                "this dataset holds no candles for: " + ", ".join(missing),
            )
        ordered = tuple(sorted(timeframes, key=lambda tf: tf.minutes))
        cap = self._limits.max_closed_candles
        try:
            candles = await self._load(
                source_id, dataset.symbol, ordered, min(window_candles, cap), cap
            )
        except ReplayStoreUnavailableError:
            raise _unavailable() from None
        finest = candles[ordered[0]]
        if not finest:
            raise SourceError(
                SourceErrorKind.INVALID, "SOURCE_EMPTY", "the dataset window holds no candles"
            )
        spacing = self._spacing[pace]
        steps = historical_script(
            candles, spacing=timedelta(seconds=spacing), sequenced=True, end=True
        )
        total_events = sum(len(series) for series in candles.values())
        window_end = max(
            series[-1].open_time + timedelta(minutes=tf.minutes)
            for tf, series in candles.items()
            if series
        )
        return OpenedSource(
            summary=summary,
            provider=PacedPlaybackProvider(steps),
            provider_id=PROVIDER_ID,
            timeframes=ordered,
            candles_per_timeframe={tf: len(series) for tf, series in candles.items()},
            market_window_start=finest[0].open_time,
            market_window_end=window_end,
            event_spacing_seconds=spacing,
            total_events=total_events,
        )

    async def _load(
        self,
        source_id: str,
        label: str,
        ordered: tuple[Timeframe, ...],
        window: int,
        cap: int,
    ) -> dict[Timeframe, tuple[Candle, ...]]:
        """The last ``window`` candles of the finest timeframe, and every coarser
        timeframe over the same market stretch - each read bounded in SQL.

        Stored rows carry no symbol (the replay store leaves it to the caller's
        context), so the dataset's own label is attached here - the same label
        the session streams under, and nothing inferred from it.
        """
        finest = await self._store.candles(source_id, ordered[0], limit=window, newest_first=True)
        loaded: dict[Timeframe, tuple[Candle, ...]] = {ordered[0]: _labelled(finest, label)}
        if not finest:
            return loaded
        start = finest[0].open_time
        end = finest[-1].open_time + timedelta(minutes=ordered[0].minutes)
        for timeframe in ordered[1:]:
            series = await self._store.candles(
                source_id, timeframe, since=start, until=end, limit=cap
            )
            loaded[timeframe] = _labelled(series, label)
        return loaded

    def _summary(self, dataset: StoredDataset) -> SourceSummary:
        label = dataset.symbol
        refusal: str | None = None
        if len(label) > self._limits.max_symbol_length or not _STREAM_SYMBOL.fullmatch(label):
            refusal = (
                "the dataset's symbol is not a permitted stream identifier (letters, digits, "
                f"'.', '_', '-', at most {self._limits.max_symbol_length} characters)"
            )
        return SourceSummary(
            source_id=dataset.dataset_id,
            instrument_label=label,
            origin=SOURCE_ORIGIN,
            timeframes=tuple(
                SourceTimeframe(
                    timeframe=item.timeframe,
                    rows=item.rows,
                    first_open_time=item.first_open_time,
                    last_open_time=item.last_open_time,
                )
                for item in sorted(dataset.timeframes, key=lambda t: t.timeframe.minutes)
            ),
            streamable=refusal is None,
            refusal=refusal,
        )


def _labelled(series: Sequence[Candle], label: str) -> tuple[Candle, ...]:
    return tuple(replace(candle, symbol=label) for candle in series)


def _unavailable() -> SourceError:
    return SourceError(
        SourceErrorKind.UNAVAILABLE, "SOURCE_STORE_UNAVAILABLE", "the dataset store is unreachable"
    )
