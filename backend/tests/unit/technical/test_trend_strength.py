"""Directional movement and ADX: the DM rules, warm-up arithmetic, edge cases."""

from __future__ import annotations

import pytest

from app.domain.technical.trend_strength import adx


def _rising(size: int) -> tuple[list[float], list[float], list[float]]:
    highs = [100.0 + index * 2.0 for index in range(size)]
    lows = [99.0 + index * 2.0 for index in range(size)]
    closes = [99.5 + index * 2.0 for index in range(size)]
    return highs, lows, closes


def _falling(size: int) -> tuple[list[float], list[float], list[float]]:
    highs = [200.0 - index * 2.0 for index in range(size)]
    lows = [199.0 - index * 2.0 for index in range(size)]
    closes = [199.5 - index * 2.0 for index in range(size)]
    return highs, lows, closes


@pytest.mark.unit
def test_directional_indicators_start_at_index_period() -> None:
    result = adx(*_rising(60), period=14)
    assert all(value is None for value in result.plus_di[:14])
    assert result.plus_di[14] is not None
    assert result.minus_di[14] is not None
    assert result.dx[14] is not None


@pytest.mark.unit
def test_adx_starts_at_twice_the_period_minus_one() -> None:
    """Two Wilder stages, so index 27 for period 14 - not index 14.

    Seeding the second stage from a single DX would report an ADX thirteen
    bars early and overstate trend strength for the whole warm-up.
    """
    result = adx(*_rising(60), period=14)
    assert all(value is None for value in result.adx[:27])
    assert result.adx[27] is not None


@pytest.mark.unit
@pytest.mark.parametrize("period", (2, 5, 14, 20))
def test_adx_warm_up_scales_with_the_period(period: int) -> None:
    result = adx(*_rising(80), period=period)
    first = next(index for index, value in enumerate(result.adx) if value is not None)
    assert first == 2 * period - 1


@pytest.mark.unit
def test_a_steady_uptrend_has_plus_di_above_minus_di() -> None:
    result = adx(*_rising(60), period=14)
    plus, minus = result.plus_di[-1], result.minus_di[-1]
    assert plus is not None and minus is not None
    assert plus > minus
    assert minus == pytest.approx(0.0)


@pytest.mark.unit
def test_a_steady_downtrend_has_minus_di_above_plus_di() -> None:
    result = adx(*_falling(60), period=14)
    plus, minus = result.plus_di[-1], result.minus_di[-1]
    assert plus is not None and minus is not None
    assert minus > plus
    assert plus == pytest.approx(0.0)


@pytest.mark.unit
def test_an_unbroken_trend_drives_dx_to_one_hundred() -> None:
    """With movement on one side only, |+DI - -DI| equals their sum."""
    result = adx(*_rising(60), period=14)
    assert result.dx[-1] == pytest.approx(100.0)
    assert result.adx[-1] == pytest.approx(100.0)


@pytest.mark.unit
def test_a_frozen_market_gives_zero_rather_than_dividing_by_zero() -> None:
    """Smoothed true range is zero, so both DI values are undefined by division.

    Reporting 0 says there is no directional movement to measure, which is
    true. Raising, or emitting NaN, would both be wrong.
    """
    flat = [50.0] * 60
    result = adx(flat, flat, flat, period=14)
    assert result.plus_di[-1] == pytest.approx(0.0)
    assert result.minus_di[-1] == pytest.approx(0.0)
    assert result.dx[-1] == pytest.approx(0.0)
    assert result.adx[-1] == pytest.approx(0.0)


@pytest.mark.unit
def test_inside_bars_generate_no_directional_movement() -> None:
    """Each bar sits inside the previous one, so neither +DM nor -DM fires."""
    size = 40
    highs = [100.0 - index * 0.1 for index in range(size)]
    lows = [90.0 + index * 0.1 for index in range(size)]
    closes = [95.0] * size
    result = adx(highs, lows, closes, period=14)
    assert result.plus_di[-1] == pytest.approx(0.0)
    assert result.minus_di[-1] == pytest.approx(0.0)


@pytest.mark.unit
def test_directional_indicators_stay_within_bounds() -> None:
    size = 120
    highs = [100.0 + (index % 13) * 1.5 for index in range(size)]
    lows = [98.0 + (index % 11) * 1.2 for index in range(size)]
    closes = [99.0 + (index % 7) * 1.1 for index in range(size)]
    result = adx(highs, lows, closes, period=14)
    for values in (result.plus_di, result.minus_di, result.dx, result.adx):
        for value in values:
            if value is not None:
                assert 0.0 <= value <= 100.0


@pytest.mark.unit
@pytest.mark.parametrize("size", (0, 1))
def test_too_short_to_have_directional_movement(size: int) -> None:
    values = [10.0] * size
    result = adx(values, values, values, 14)
    assert result.plus_di == (None,) * size
    assert result.adx == (None,) * size


@pytest.mark.unit
def test_series_shorter_than_the_adx_warm_up_yields_no_adx() -> None:
    result = adx(*_rising(20), period=14)
    assert result.plus_di[14] is not None
    assert all(value is None for value in result.adx)
