"""Where a simulated stream's candles come from (Phase 13 Part 2A).

The live workspace plays *stored historical datasets* through the Part 1
streaming machinery. It does not generate prices, and it has no upload path of
its own: a source is a dataset somebody already supplied - through replay
(Phase 11) or a backtest (Phase 12) - identified by that dataset's own
content-derived id.

## Five identities, kept apart

* **stream identity** - a live session id, minted by the server;
* **source (dataset) identity** - the stored dataset's id;
* **provider identity** - which adapter plays it (a constant, server-side);
* **instrument label** - the symbol text in the dataset. A *label*: it
  establishes no asset class, venue, multiplier, tick size, margin or expiry;
* **financial contract identity** - never established here. There is no field
  for it, so nothing can fill it in.

## A port, because this layer may not reach replay storage

``app.application.live`` is forbidden from importing the replay application
layer (a stream must have no path to a replay position). The adapter that
implements this port may read stored datasets; the workspace only ever sees
the summaries and the provider it hands back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique
from typing import Protocol

from app.application.live.ports import LiveMarketDataProvider
from app.domain.common.enums import Timeframe

__all__ = [
    "OpenedSource",
    "PlaybackPace",
    "SimulatedSourceCatalog",
    "SourceError",
    "SourceErrorKind",
    "SourceSummary",
    "SourceTimeframe",
]


@unique
class PlaybackPace(StrEnum):
    """How fast stored candles are played. Receive-clock pacing only: it
    changes when events *arrive*, never what they say."""

    SLOW = "SLOW"
    NORMAL = "NORMAL"
    FAST = "FAST"


@dataclass(frozen=True, slots=True)
class SourceTimeframe:
    timeframe: Timeframe
    rows: int
    first_open_time: datetime
    last_open_time: datetime


@dataclass(frozen=True, slots=True)
class SourceSummary:
    source_id: str
    instrument_label: str
    """The dataset's symbol text. A label - never a verified contract."""

    origin: str
    """Where the dataset's candles came from, as the dataset store records it."""

    timeframes: tuple[SourceTimeframe, ...]
    streamable: bool
    refusal: str | None
    """Why this source cannot be streamed, when it cannot. Never a guess."""


@dataclass(frozen=True, slots=True)
class OpenedSource:
    """A source ready to play: a provider, and what it will play."""

    summary: SourceSummary
    provider: LiveMarketDataProvider
    provider_id: str
    timeframes: tuple[Timeframe, ...]
    candles_per_timeframe: dict[Timeframe, int]
    market_window_start: datetime
    market_window_end: datetime
    """Market time: the first open and the last coverage end being played."""

    event_spacing_seconds: float
    """Receive-clock gap between two played events."""

    total_events: int


@unique
class SourceErrorKind(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    INVALID = "INVALID"
    UNAVAILABLE = "UNAVAILABLE"


class SourceError(RuntimeError):
    def __init__(self, kind: SourceErrorKind, code: str, detail: str) -> None:
        self.kind = kind
        self.code = code
        self.detail = detail
        super().__init__(code)


class SimulatedSourceCatalog(Protocol):
    async def list_sources(
        self, *, offset: int, limit: int
    ) -> tuple[tuple[SourceSummary, ...], int]:
        """One bounded page of sources, with the total. Never reads candles."""
        ...

    async def open(
        self,
        source_id: str,
        *,
        timeframes: tuple[Timeframe, ...],
        window_candles: int,
        pace: PlaybackPace,
    ) -> OpenedSource:
        """Load a bounded window of the source and build a provider for it.

        ``window_candles`` counts candles of the finest requested timeframe;
        coarser timeframes are loaded over the same market window, so every
        timeframe describes the same stretch of history. Raises
        :class:`SourceError`.
        """
        ...
