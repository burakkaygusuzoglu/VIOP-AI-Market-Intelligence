"""Moving averages and smoothing, verified by hand.

Values small enough to check with arithmetic on paper, so the tests do not
merely restate the implementation.
"""

from __future__ import annotations

import math

import pytest

from app.domain.technical.smoothing import ema, rolling_population_stdev, sma, wilder
from app.domain.technical.types import IndicatorInputError

RISING = [1.0, 2.0, 3.0, 4.0, 5.0]


@pytest.mark.unit
def test_sma_is_the_arithmetic_mean_of_a_trailing_window() -> None:
    # windows: [1,2,3]=2, [2,3,4]=3, [3,4,5]=4
    assert sma(RISING, 3) == (None, None, 2.0, 3.0, 4.0)


@pytest.mark.unit
def test_sma_period_one_is_the_input() -> None:
    assert sma(RISING, 1) == tuple(RISING)


@pytest.mark.unit
def test_ema_seeds_with_an_sma_and_then_decays() -> None:
    # period 3 -> alpha 0.5. seed = mean(1,2,3) = 2 at index 2.
    # index 3: 2 + 0.5*(4-2) = 3.  index 4: 3 + 0.5*(5-3) = 4.
    result = ema(RISING, 3)
    assert result[:2] == (None, None)
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx(3.0)
    assert result[4] == pytest.approx(4.0)


@pytest.mark.unit
def test_wilder_uses_alpha_one_over_period_not_two_over_period_plus_one() -> None:
    # period 3 -> alpha 1/3. seed = 2 at index 2.
    # index 3: 2 + (4-2)/3 = 2.666...  index 4: + (5-2.666)/3 = 3.444...
    result = wilder(RISING, 3)
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx(2.0 + 2.0 / 3.0)
    assert result[4] == pytest.approx(2.0 + 2.0 / 3.0 + (5.0 - (2.0 + 2.0 / 3.0)) / 3.0)


@pytest.mark.unit
def test_wilder_decays_more_slowly_than_ema_of_the_same_period() -> None:
    """The confusion this separation exists to prevent.

    A Wilder 14 behaves like an EMA 27, so substituting one for the other in
    RSI or ATR produces a plausible but consistently wrong series.
    """
    step = [0.0] * 20 + [100.0] * 20
    fast = ema(step, 14)
    slow = wilder(step, 14)
    assert fast[25] is not None and slow[25] is not None
    assert fast[25] > slow[25]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("function", "expected_alpha"),
    ((ema, 2.0 / (14 + 1)), (wilder, 1.0 / 14)),
)
def test_smoothing_factor_is_the_documented_one(function, expected_alpha: float) -> None:  # type: ignore[no-untyped-def]
    """Recover alpha from two consecutive outputs.

    ``next = previous + alpha * (input - previous)``, so alpha is implied by
    the step the average takes. This pins the constant itself rather than
    trusting the recursion to look right.
    """
    values = [10.0] * 14 + [110.0]
    result = function(values, 14)
    previous, current = result[13], result[14]
    assert previous is not None and current is not None
    recovered = (current - previous) / (values[14] - previous)
    assert recovered == pytest.approx(expected_alpha, rel=1e-12)


@pytest.mark.unit
@pytest.mark.parametrize("function", (sma, ema, wilder))
def test_shorter_than_period_is_all_none(function) -> None:  # type: ignore[no-untyped-def]
    assert function([1.0, 2.0], 5) == (None, None)


@pytest.mark.unit
@pytest.mark.parametrize("function", (sma, ema, wilder, rolling_population_stdev))
def test_empty_input_is_empty_output(function) -> None:  # type: ignore[no-untyped-def]
    assert function([], 14) == ()


@pytest.mark.unit
@pytest.mark.parametrize("function", (sma, ema, wilder, rolling_population_stdev))
def test_period_must_be_positive(function) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(IndicatorInputError):
        function(RISING, 0)


@pytest.mark.unit
@pytest.mark.parametrize("bad", (float("nan"), float("inf"), float("-inf")))
def test_non_finite_input_is_rejected_rather_than_propagated(bad: float) -> None:
    """A NaN would flow silently through every later calculation."""
    with pytest.raises(IndicatorInputError):
        sma([1.0, bad, 3.0], 2)


@pytest.mark.unit
def test_constant_input_gives_a_constant_average() -> None:
    flat = [7.0] * 10
    for values in (sma(flat, 4), ema(flat, 4), wilder(flat, 4)):
        assert all(value == pytest.approx(7.0) for value in values[3:])


@pytest.mark.unit
def test_population_standard_deviation_not_sample() -> None:
    # window [1,2,3,4,5]: mean 3, population variance (4+1+0+1+4)/5 = 2.
    result = rolling_population_stdev(RISING, 5)
    assert result[4] == pytest.approx(math.sqrt(2.0))
    # The sample form would divide by 4 and give sqrt(2.5) - visibly different.
    assert result[4] != pytest.approx(math.sqrt(2.5))


@pytest.mark.unit
def test_standard_deviation_of_a_flat_window_is_zero() -> None:
    assert rolling_population_stdev([5.0] * 6, 3)[5] == pytest.approx(0.0)


@pytest.mark.unit
def test_sma_does_not_drift_over_a_long_series() -> None:
    """fsum keeps a long window exact; a running total would accumulate error."""
    values = [0.1] * 5000
    result = sma(values, 200)
    assert result[-1] == pytest.approx(0.1, abs=1e-15)
