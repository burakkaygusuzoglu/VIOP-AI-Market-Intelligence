"""Builders for test candles.

Every value produced here is TEST_FIXTURE data under master spec section 118.
None of it describes a real instrument, and none of it may be read as a current
exchange specification.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from app.domain.market.series import CandleSeries, ValidatedCandleSeries

FIXTURE_SYMBOL = "TEST_FIXTURE_SYMBOL"
FIXTURE_ORIGIN = datetime(2026, 1, 2, 9, 0, tzinfo=UTC)


def candle(
    index: int = 0,
    *,
    close: str | Decimal = "100",
    open: str | Decimal | None = None,
    high: str | Decimal | None = None,
    low: str | Decimal | None = None,
    volume: str | Decimal = "1000",
    timeframe: Timeframe = Timeframe.M15,
    symbol: str = FIXTURE_SYMBOL,
    origin: datetime = FIXTURE_ORIGIN,
    is_closed: bool = True,
    open_interest: str | Decimal | None = None,
) -> Candle:
    """One candle, with a valid OHLC envelope derived from ``close`` by default."""
    close_value = Decimal(str(close))
    open_value = Decimal(str(open)) if open is not None else close_value
    high_value = (
        Decimal(str(high)) if high is not None else max(open_value, close_value) + Decimal("1")
    )
    low_value = (
        Decimal(str(low)) if low is not None else min(open_value, close_value) - Decimal("1")
    )
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=origin + index * timedelta(minutes=timeframe.minutes),
        open=open_value,
        high=high_value,
        low=low_value,
        close=close_value,
        volume=Decimal(str(volume)),
        is_closed=is_closed,
        open_interest=None if open_interest is None else Decimal(str(open_interest)),
    )


def candles_from_closes(
    closes: Sequence[str | Decimal | int | float],
    *,
    timeframe: Timeframe = Timeframe.M15,
    symbol: str = FIXTURE_SYMBOL,
    origin: datetime = FIXTURE_ORIGIN,
    volumes: Sequence[str | Decimal | int] | None = None,
) -> tuple[Candle, ...]:
    """A well-formed ascending series whose closes are exactly ``closes``."""
    return tuple(
        candle(
            index=position,
            close=Decimal(str(value)),
            timeframe=timeframe,
            symbol=symbol,
            origin=origin,
            volume=Decimal(str(volumes[position])) if volumes is not None else Decimal("1000"),
        )
        for position, value in enumerate(closes)
    )


def series_from_closes(
    closes: Sequence[str | Decimal | int | float],
    **kwargs: object,
) -> CandleSeries:
    return CandleSeries.of(candles_from_closes(closes, **kwargs))  # type: ignore[arg-type]


def validated_from_closes(
    closes: Sequence[str | Decimal | int | float],
    *,
    timeframe: Timeframe = Timeframe.M15,
    symbol: str = FIXTURE_SYMBOL,
    origin: datetime = FIXTURE_ORIGIN,
    volumes: Sequence[str | Decimal | int] | None = None,
) -> ValidatedCandleSeries:
    return ValidatedCandleSeries(
        candles=candles_from_closes(
            closes, timeframe=timeframe, symbol=symbol, origin=origin, volumes=volumes
        ),
        symbol=symbol,
        timeframe=timeframe,
    )


def ohlcv_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float] | None = None,
    *,
    timeframe: Timeframe = Timeframe.M15,
    symbol: str = FIXTURE_SYMBOL,
    origin: datetime = FIXTURE_ORIGIN,
) -> ValidatedCandleSeries:
    """A validated series with explicit highs, lows and closes."""
    built = tuple(
        Candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=origin + position * timedelta(minutes=timeframe.minutes),
            open=Decimal(str(closes[position])),
            high=Decimal(str(highs[position])),
            low=Decimal(str(lows[position])),
            close=Decimal(str(closes[position])),
            volume=Decimal(str(volumes[position])) if volumes is not None else Decimal("1000"),
            is_closed=True,
        )
        for position in range(len(closes))
    )
    return ValidatedCandleSeries(candles=built, symbol=symbol, timeframe=timeframe)
