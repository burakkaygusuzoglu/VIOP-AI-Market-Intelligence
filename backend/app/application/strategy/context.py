"""Confirmed candles to strategy readings (Phase 12 runner, Phase 14 shadow).

Moved here unchanged from the Phase 12 backtest runner, where these were
private helpers, because Phase 14 evaluates the *same* policies over a stream
and must not grow a second way of preparing their inputs. The runner now
imports them; its behaviour is unchanged, and its existing tests hold it there.

Nothing here decides anything or computes an indicator of its own: the Phase 1
engine produces the series and these functions read one index out of it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.domain.backtest.policy import Readings
from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from app.domain.market.quality import DataQualityEngine
from app.domain.market.series import CandleSeries
from app.domain.replay import coverage_end
from app.domain.technical.engine import TechnicalSnapshot, compute_technicals

__all__ = ["confirmed_higher", "readings_at", "readings_series", "value_at"]


def readings_series(candles: Sequence[Candle]) -> list[Readings]:
    """Indicator readings per candle, or empty readings when unusable.

    Computed once over the whole series. Phase 1 guarantees value ``i`` derives
    from candles ``0..i`` only, and a unit test proves that guarantee holds for
    every indicator read here - so indexing is identical to recomputing over
    each prefix, at a fraction of the cost.
    """
    if not candles:
        return []
    assessment = DataQualityEngine().assess(CandleSeries.of(tuple(candles)))
    series = assessment.series
    if series is None:
        return [Readings() for _ in candles]
    snapshot = compute_technicals(series)
    return [readings_at(snapshot, index) for index in range(len(candles))]


def readings_at(snapshot: TechnicalSnapshot, index: int) -> Readings:
    return Readings(
        ema_fast=value_at(snapshot.ema.get(9), index),
        ema_slow=value_at(snapshot.ema.get(20), index),
        rsi=value_at(snapshot.rsi, index),
        atr=value_at(snapshot.atr, index),
        adx=value_at(snapshot.adx, index),
    )


def value_at(values: Sequence[float | None] | None, index: int) -> float | None:
    if values is None or index < 0 or index >= len(values):
        return None
    return values[index]


def confirmed_higher(
    candles: dict[Timeframe, tuple[Candle, ...]],
    readings: dict[Timeframe, list[Readings]],
    boundary: datetime,
) -> dict[Timeframe, Readings]:
    """Higher-timeframe readings, and only for candles that have closed.

    A forming 1H bar is *absent*, not present with partial values: the last
    index whose coverage ended at or before the boundary is the only one a
    strategy may see.
    """
    confirmed: dict[Timeframe, Readings] = {}
    for timeframe, series in candles.items():
        last = -1
        for index, candle in enumerate(series):
            if coverage_end(candle) <= boundary:
                last = index
            else:
                break
        if last >= 0:
            confirmed[timeframe] = readings[timeframe][last]
    return confirmed
