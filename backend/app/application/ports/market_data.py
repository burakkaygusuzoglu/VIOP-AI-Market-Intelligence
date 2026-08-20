"""Market data ports (master spec sections 73 and 74).

Only the data provider changes between live, replay and backtest execution;
the deterministic engines behind these ports stay identical. No provider is
implemented in Phase 0 - CSV, mock and historical providers arrive in Phase 1.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle


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
