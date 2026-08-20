"""Candle value object.

Values here are TEST_FIXTURE data, not current market quotations.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle


def _candle(is_closed: bool = True) -> Candle:
    return Candle(
        symbol="TEST_FIXTURE_SYMBOL",
        timeframe=Timeframe.M15,
        open_time=datetime(2026, 1, 2, 10, 30, tzinfo=UTC),
        open=Decimal("100.10"),
        high=Decimal("100.90"),
        low=Decimal("99.80"),
        close=Decimal("100.40"),
        volume=Decimal("1500"),
        is_closed=is_closed,
    )


@pytest.mark.unit
def test_candle_is_immutable() -> None:
    candle = _candle()
    with pytest.raises(AttributeError):
        candle.close = Decimal("101")  # type: ignore[misc]


@pytest.mark.unit
def test_prices_are_decimal_not_float() -> None:
    """Binary floats are not acceptable for money or tick arithmetic."""
    candle = _candle()
    for price in (candle.open, candle.high, candle.low, candle.close, candle.volume):
        assert isinstance(price, Decimal)


@pytest.mark.unit
def test_decimal_prices_are_exact() -> None:
    candle = _candle()
    assert candle.close - candle.open == Decimal("0.30")


@pytest.mark.unit
def test_forming_and_closed_candles_are_distinguishable() -> None:
    """Master spec section 52: a forming bar is never a confirmed signal."""
    assert _candle(is_closed=True).is_closed
    assert not _candle(is_closed=False).is_closed


@pytest.mark.unit
def test_open_interest_is_optional_and_absent_by_default() -> None:
    """Missing data stays missing; it is never fabricated as zero."""
    assert _candle().open_interest is None
