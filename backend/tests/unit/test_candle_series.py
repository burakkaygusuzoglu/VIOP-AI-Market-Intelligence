"""Candle series types and the Decimal-to-float boundary.

Fixture values only; nothing here is a market quotation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.common.enums import Timeframe
from app.domain.market.series import (
    CandleSeries,
    InvalidCandleSeriesError,
    ValidatedCandleSeries,
)
from tests.factories import FIXTURE_SYMBOL, candle, candles_from_closes


@pytest.mark.unit
def test_raw_series_accepts_whatever_a_provider_returned() -> None:
    """It must be able to hold bad data, or the engine cannot report on it."""
    broken = (
        candle(index=5, close="100"),
        candle(index=0, close="101"),  # out of order, on purpose
    )
    series = CandleSeries.of(broken)
    assert len(series) == 2
    assert series[0].open_time > series[1].open_time


@pytest.mark.unit
def test_empty_raw_series() -> None:
    series = CandleSeries.of(())
    assert series.is_empty
    assert len(series) == 0


@pytest.mark.unit
def test_validated_series_exposes_aligned_price_columns() -> None:
    series = ValidatedCandleSeries(
        candles=candles_from_closes(["100", "101", "102"]),
        symbol=FIXTURE_SYMBOL,
        timeframe=Timeframe.M15,
    )
    assert series.closes == (Decimal("100"), Decimal("101"), Decimal("102"))
    assert len(series.highs) == len(series.lows) == len(series.opens) == 3
    assert series.interval == timedelta(minutes=15)


@pytest.mark.unit
def test_prices_stay_decimal_until_the_named_boundary() -> None:
    """Exactness is preserved on the data side; float appears only on request."""
    series = ValidatedCandleSeries(
        candles=candles_from_closes(["100.10", "100.40"]),
        symbol=FIXTURE_SYMBOL,
        timeframe=Timeframe.M15,
    )
    assert all(isinstance(value, Decimal) for value in series.closes)
    assert series.closes[1] - series.closes[0] == Decimal("0.30")

    floats = series.float_closes()
    assert all(isinstance(value, float) for value in floats)
    assert floats == (100.10, 100.40)


@pytest.mark.unit
def test_forming_candle_cannot_enter_a_validated_series() -> None:
    """Master spec section 52: a forming bar is never a historical fact."""
    with pytest.raises(InvalidCandleSeriesError, match="forming"):
        ValidatedCandleSeries(
            candles=(candle(index=0, is_closed=False),),
            symbol=FIXTURE_SYMBOL,
            timeframe=Timeframe.M15,
        )


@pytest.mark.unit
def test_out_of_order_candles_cannot_enter_a_validated_series() -> None:
    with pytest.raises(InvalidCandleSeriesError, match="strictly after"):
        ValidatedCandleSeries(
            candles=(candle(index=1), candle(index=0)),
            symbol=FIXTURE_SYMBOL,
            timeframe=Timeframe.M15,
        )


@pytest.mark.unit
def test_duplicate_timestamps_cannot_enter_a_validated_series() -> None:
    with pytest.raises(InvalidCandleSeriesError, match="strictly after"):
        ValidatedCandleSeries(
            candles=(candle(index=0), candle(index=0)),
            symbol=FIXTURE_SYMBOL,
            timeframe=Timeframe.M15,
        )


@pytest.mark.unit
def test_mixed_symbols_cannot_enter_a_validated_series() -> None:
    with pytest.raises(InvalidCandleSeriesError, match="symbol"):
        ValidatedCandleSeries(
            candles=(candle(index=0), candle(index=1, symbol="OTHER_FIXTURE")),
            symbol=FIXTURE_SYMBOL,
            timeframe=Timeframe.M15,
        )


@pytest.mark.unit
def test_mixed_timeframes_cannot_enter_a_validated_series() -> None:
    with pytest.raises(InvalidCandleSeriesError, match="timeframe"):
        ValidatedCandleSeries(
            candles=(candle(index=0), candle(index=1, timeframe=Timeframe.H1)),
            symbol=FIXTURE_SYMBOL,
            timeframe=Timeframe.M15,
        )


@pytest.mark.unit
def test_naive_timestamps_cannot_enter_a_validated_series() -> None:
    naive = candle(index=0, origin=datetime(2026, 1, 2, 9, 0))  # noqa: DTZ001
    with pytest.raises(InvalidCandleSeriesError, match="naive"):
        ValidatedCandleSeries(candles=(naive,), symbol=FIXTURE_SYMBOL, timeframe=Timeframe.M15)


@pytest.mark.unit
def test_a_non_utc_offset_is_accepted_when_the_instants_still_ascend() -> None:
    """Aware timestamps in another zone denote real instants and compare fine."""
    istanbul = timezone(timedelta(hours=3))
    candles = (
        candle(index=0, origin=datetime(2026, 1, 2, 12, 0, tzinfo=istanbul)),
        candle(index=1, origin=datetime(2026, 1, 2, 12, 0, tzinfo=istanbul)),
    )
    series = ValidatedCandleSeries(candles=candles, symbol=FIXTURE_SYMBOL, timeframe=Timeframe.M15)
    assert series.open_times[0] < series.open_times[1]
    assert series.open_times[0].astimezone(UTC).hour == 9


@pytest.mark.unit
def test_empty_validated_series_is_allowed() -> None:
    """Zero candles violates no invariant; emptiness is the quality engine's call."""
    series = ValidatedCandleSeries(candles=(), symbol=FIXTURE_SYMBOL, timeframe=Timeframe.M15)
    assert len(series) == 0
    assert series.float_closes() == ()
