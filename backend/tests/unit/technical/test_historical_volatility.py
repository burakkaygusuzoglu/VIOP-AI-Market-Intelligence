"""Historical volatility — the Phase 1 deferral, taken up in Phase 2.

The decision that matters most here is what the number is *not*: it is not
annualised, because the periods-per-year factor is a property of the VIOP
trading calendar and therefore a section 118 fact this project does not hold.
``test_the_value_is_per_candle_and_not_annualised`` pins that.
"""

from __future__ import annotations

import math

import pytest

from app.domain.technical.types import IndicatorInputError
from app.domain.technical.volatility import historical_volatility, log_returns


@pytest.mark.unit
def test_log_returns_are_undefined_on_the_first_candle() -> None:
    result = log_returns([100.0, 110.0])
    assert result[0] is None
    assert result[1] == pytest.approx(math.log(1.1))


@pytest.mark.unit
def test_log_returns_are_symmetric_in_direction() -> None:
    """A rise then an equal proportional fall returns to the starting point."""
    up, down = log_returns([100.0, 110.0, 100.0])[1:]
    assert up is not None and down is not None
    assert up == pytest.approx(-down)


@pytest.mark.unit
def test_volatility_hand_computed() -> None:
    """Alternating +ln(1.1) / -ln(1.1) returns: the population sigma is ln(1.1)."""
    closes = [100.0, 110.0, 100.0, 110.0, 100.0]
    result = historical_volatility(closes, period=4)
    assert result[4] == pytest.approx(math.log(1.1))


@pytest.mark.unit
def test_the_first_value_lands_one_bar_after_an_sma_of_the_same_period() -> None:
    """Index 0 yields no return, exactly as with RSI and ATR."""
    closes = [100.0 + index for index in range(40)]
    result = historical_volatility(closes, period=20)
    assert all(value is None for value in result[:20])
    assert result[20] is not None


@pytest.mark.unit
def test_a_constant_price_has_zero_volatility() -> None:
    result = historical_volatility([50.0] * 30, period=10)
    assert result[-1] == pytest.approx(0.0)


@pytest.mark.unit
def test_a_steadily_compounding_series_has_zero_volatility() -> None:
    """Constant *proportional* growth means constant log returns, so no spread."""
    closes = [100.0 * (1.01**index) for index in range(40)]
    result = historical_volatility(closes, period=20)
    assert result[-1] == pytest.approx(0.0, abs=1e-12)


@pytest.mark.unit
def test_a_wilder_market_reports_higher_volatility() -> None:
    calm = [100.0 + (index % 2) * 0.1 for index in range(60)]
    wild = [100.0 + (index % 2) * 10.0 for index in range(60)]
    calm_value = historical_volatility(calm, 20)[-1]
    wild_value = historical_volatility(wild, 20)[-1]
    assert calm_value is not None and wild_value is not None
    assert wild_value > calm_value


@pytest.mark.unit
def test_the_value_is_per_candle_and_not_annualised() -> None:
    """The section 118 decision, pinned as a test.

    Alternating +/- ln(1.1) gives a per-candle sigma of exactly ln(1.1) ~ 0.0953.
    If an annualisation factor had crept in - sqrt(252) being the usual one -
    the value would be about 1.51 instead. This test fails the moment anyone
    multiplies by a trading-calendar constant that has never been verified.
    """
    closes = [100.0, 110.0, 100.0, 110.0, 100.0, 110.0, 100.0]
    value = historical_volatility(closes, period=6)[6]
    assert value is not None
    assert value == pytest.approx(math.log(1.1), rel=1e-9)
    assert value < 1.0


@pytest.mark.unit
def test_volatility_is_scale_invariant() -> None:
    """Quoting the same shape in different units must not change the reading."""
    base = [100.0, 110.0, 105.0, 115.0, 108.0, 120.0, 112.0, 125.0]
    scaled = [value * 1000.0 for value in base]
    assert historical_volatility(base, 4)[-1] == pytest.approx(historical_volatility(scaled, 4)[-1])


@pytest.mark.unit
@pytest.mark.parametrize("size", (0, 1))
def test_too_short_to_have_a_return(size: int) -> None:
    assert historical_volatility([100.0] * size, 20) == (None,) * size


@pytest.mark.unit
def test_shorter_than_the_period_is_all_none() -> None:
    assert historical_volatility([100.0, 101.0, 102.0], 20) == (None, None, None)


@pytest.mark.unit
@pytest.mark.parametrize("bad", (0.0, -1.0))
def test_a_non_positive_close_is_rejected_rather_than_producing_a_nan(bad: float) -> None:
    with pytest.raises(IndicatorInputError, match="must be positive"):
        historical_volatility([100.0, bad, 100.0], 2)


@pytest.mark.unit
def test_a_non_finite_close_is_rejected() -> None:
    with pytest.raises(IndicatorInputError):
        historical_volatility([100.0, float("nan"), 100.0], 2)


@pytest.mark.unit
def test_period_must_be_positive() -> None:
    with pytest.raises(IndicatorInputError):
        historical_volatility([100.0, 101.0], 0)


@pytest.mark.unit
def test_no_value_is_ever_nan_or_infinite() -> None:
    closes = [100.0 + (index * 7 % 11) for index in range(120)]
    for value in historical_volatility(closes, 20):
        if value is not None:
            assert math.isfinite(value)
            assert value >= 0.0


@pytest.mark.unit
def test_output_is_reproducible() -> None:
    closes = [100.0 + (index * 5 % 13) for index in range(80)]
    assert historical_volatility(closes, 20) == historical_volatility(closes, 20)


@pytest.mark.unit
def test_appending_candles_does_not_change_earlier_values() -> None:
    closes = [100.0 + (index * 3 % 17) for index in range(120)]
    prefix = historical_volatility(closes[:80], 20)
    full = historical_volatility(closes, 20)
    assert prefix == full[:80]
