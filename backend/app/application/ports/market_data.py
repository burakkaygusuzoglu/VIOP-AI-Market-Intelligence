"""Market data ports (master spec sections 73 and 74).

Only the data provider changes between live, replay and backtest execution;
the deterministic engines behind these ports stay identical.

Two protocols, one of them optional:

``HistoricalMarketDataProvider``
    The Phase 0 contract, unchanged. The minimum any source must offer.

``DiagnosticHistoricalMarketDataProvider``
    An additive capability for sources that can also report what went wrong
    while reading - a malformed CSV row, for instance. It exists so an adapter
    never has to choose between dropping a bad row silently and raising an
    exception that aborts an otherwise usable dataset: the finding travels
    with the candles into the same Data Quality report. Providers that have
    nothing to report simply do not implement it, and callers fall back to
    ``get_candles``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from app.domain.market.quality import DataQualityIssue


@runtime_checkable
class HistoricalMarketDataProvider(Protocol):
    """Supplies closed historical candles for a symbol and timeframe."""

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]:
        """Return candles with ``start <= open_time < end``, ascending.

        Implementations must not fabricate missing bars. Gaps are reported as
        absent candles and are classified by the Data Quality Engine.
        """
        ...


@dataclass(frozen=True, slots=True)
class MarketDataFetch:
    """Candles read from a source, together with any problems reading them."""

    candles: tuple[Candle, ...]
    issues: tuple[DataQualityIssue, ...] = ()


@runtime_checkable
class DiagnosticHistoricalMarketDataProvider(Protocol):
    """A historical provider that also reports its own read failures."""

    async def fetch(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> MarketDataFetch:
        """Return candles plus the issues encountered producing them."""
        ...
