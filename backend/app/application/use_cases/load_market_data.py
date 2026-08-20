"""Load candles from a provider and put them through the Data Quality Engine.

This is the seam that makes validation unavoidable. A provider returns raw
candles; nothing downstream accepts raw candles, because the indicator engine
takes a ``ValidatedCandleSeries`` and only the Data Quality Engine produces
one. Adding a second provider later - live, replay, a vendor API - therefore
cannot introduce an unvalidated path without deliberately rewriting this use
case (master spec sections 40 and 74).

It never raises for bad market data. A blocked dataset is a normal, expected
outcome that the caller must be able to display with its reasons, not an
exception to be caught somewhere far away.
"""

from __future__ import annotations

from datetime import datetime

from app.application.ports.market_data import (
    DiagnosticHistoricalMarketDataProvider,
    HistoricalMarketDataProvider,
    MarketDataFetch,
)
from app.domain.common.enums import Timeframe
from app.domain.market.quality import DataQualityAssessment, DataQualityEngine
from app.domain.market.series import CandleSeries


class LoadValidatedCandles:
    """Fetch, assess, and hand back either a usable series or the reasons why not."""

    def __init__(
        self,
        provider: HistoricalMarketDataProvider,
        engine: DataQualityEngine | None = None,
    ) -> None:
        self._provider = provider
        self._engine = engine if engine is not None else DataQualityEngine()

    async def execute(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> DataQualityAssessment:
        """Return the quality assessment, with its series when not blocked."""
        fetch = await self._fetch(symbol, timeframe, start, end)
        return self._engine.assess(
            CandleSeries.of(fetch.candles),
            extra_issues=fetch.issues,
        )

    async def _fetch(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> MarketDataFetch:
        """Prefer the richer contract when the provider offers it.

        A provider that can report its own read failures - a malformed CSV row
        - has them folded into the same report as the domain findings. One
        that cannot simply returns candles, and nothing is lost that it never
        knew.
        """
        if isinstance(self._provider, DiagnosticHistoricalMarketDataProvider):
            return await self._provider.fetch(symbol, timeframe, start, end)
        candles = await self._provider.get_candles(symbol, timeframe, start, end)
        return MarketDataFetch(candles=tuple(candles))
