"""The property the runner's one-pass indicator computation rests on.

Phase 1 documents that indicator value ``i`` derives from candles ``0..i``
only. The backtest runner leans on that: it computes technicals once over the
driver series and indexes by boundary, rather than recomputing over each
prefix, which turns a quadratic walk into one pass.

That is only sound while the property holds. If an indicator ever became
non-causal - a centred average, a look-ahead smoothing, a fix that peeks one
bar forward - every backtest decision would silently gain the future. So the
property is *proven here* rather than assumed there, and this test is the
thing that fails first.
"""

from __future__ import annotations

import pytest

from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from app.domain.market.quality import DataQualityEngine
from app.domain.market.series import CandleSeries, ValidatedCandleSeries
from app.domain.technical.engine import TechnicalSnapshot, compute_technicals
from tests.factories_replay import FIXTURE_SYMBOL, five_minute

TOTAL = 320


def candles(count: int) -> list[Candle]:
    return [
        Candle(
            symbol=FIXTURE_SYMBOL,
            timeframe=Timeframe.M5,
            open_time=row.open_time,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            is_closed=True,
        )
        for row in five_minute(count)
    ]


def validated(items: list[Candle]) -> ValidatedCandleSeries:
    series = DataQualityEngine().assess(CandleSeries.of(tuple(items))).series
    assert series is not None, "the fixture must pass data quality"
    return series


def readings(snapshot: TechnicalSnapshot, index: int) -> dict[str, float | None]:
    """Every indicator the backtest reads, plus the ones near it."""
    return {
        "ema9": snapshot.ema[9][index],
        "ema20": snapshot.ema[20][index],
        "ema50": snapshot.ema[50][index],
        "sma20": snapshot.sma[20][index],
        "rsi": snapshot.rsi[index],
        "atr": snapshot.atr[index],
        "adx": snapshot.adx[index],
        "macd": snapshot.macd.macd[index],
        "macd_signal": snapshot.macd.signal[index],
        "bollinger_upper": snapshot.bollinger.upper[index],
        "vwap": snapshot.vwap[index],
        "relative_volume": snapshot.relative_volume[index],
    }


EVERY = candles(TOTAL)
FULL = compute_technicals(validated(EVERY))


@pytest.mark.unit
@pytest.mark.parametrize("index", [40, 61, 97, 128, 199, 256, TOTAL - 1])
def test_every_indicator_at_index_equals_its_value_over_the_prefix(index: int) -> None:
    """Computed over 0..i, the last value is the full series' value at i."""
    prefix = compute_technicals(validated(EVERY[: index + 1]))
    assert readings(FULL, index) == readings(prefix, index)


@pytest.mark.unit
def test_the_property_holds_across_the_whole_series_for_the_read_indicators() -> None:
    """A denser sweep over exactly what the reference strategy consults."""
    for index in range(30, TOTAL, 11):
        prefix = compute_technicals(validated(EVERY[: index + 1]))
        for name in ("ema9", "ema20", "atr", "adx"):
            assert readings(FULL, index)[name] == readings(prefix, index)[name], (
                f"{name} at index {index} differs between the full series and its prefix"
            )


@pytest.mark.unit
def test_a_later_candle_cannot_change_an_earlier_reading() -> None:
    """The same property, stated as the thing a backtest actually fears.

    Replacing a *future* candle with an absurd one must leave every earlier
    indicator value untouched.
    """
    index = 150
    altered = list(EVERY)
    victim = altered[260]
    altered[260] = Candle(
        symbol=victim.symbol,
        timeframe=victim.timeframe,
        open_time=victim.open_time,
        open=victim.open,
        high=victim.high * 1000,
        low=victim.low,
        close=victim.close,
        volume=victim.volume,
        is_closed=True,
    )
    changed = compute_technicals(validated(altered))
    assert readings(FULL, index) == readings(changed, index)
