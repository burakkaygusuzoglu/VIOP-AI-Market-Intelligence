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

``CandleTextParser``
    Turns text a *user supplied* into candles. Added in Phase 8, when analysis
    stopped being something the system read off disk for itself and became
    something a person asks for with their own file.

    It is a port rather than a direct call for the ordinary reason: the file
    format is infrastructure. The first version of the Phase 8 orchestrator
    imported the CSV adapter directly and broke the "application depends only
    on domain" contract, which was the contract doing its job - a second format
    later (a paste, a different vendor's export) would have had to be threaded
    through the application layer by hand.
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


class CandleParseError(ValueError):
    """The supplied text cannot be read as market data at all.

    Distinct from a malformed *row*, which is a finding that travels with the
    candles. This is "there is no dataset to assess": no header, a missing
    required column, more rows than the caller will accept.

    Declared here rather than in an adapter so the application layer can catch
    it without importing infrastructure.
    """


class CandleRowLimitError(CandleParseError):
    """More rows than the caller is willing to accept.

    Separate from a schema problem so a boundary can map it to its own typed
    rejection. A truncated series would be a wrong analysis rather than a
    refused one, so implementations must raise rather than trim.
    """


@runtime_checkable
class CandleTextParser(Protocol):
    """Parses user-supplied text into candles and per-row findings."""

    def parse(
        self,
        text: str,
        *,
        symbol: str,
        timeframe: Timeframe,
        source_name: str,
        max_rows: int | None = None,
    ) -> MarketDataFetch:
        """Return candles and the rows that could not be read.

        Implementations must not sort, deduplicate, fill gaps or clamp values:
        every such decision belongs to the Data Quality Engine, which can
        *report* it. Repairing here would destroy the evidence that it happened.
        """
        ...
