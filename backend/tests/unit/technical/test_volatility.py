"""True Range, ATR and Bollinger Bands: hand-computed values and edge cases."""

from __future__ import annotations

import math

import pytest

from app.domain.technical.types import IndicatorInputError
from app.domain.technical.volatility import atr, bollinger_bands, true_range


@pytest.mark.unit
def test_true_range_is_undefined_on_the_first_candle() -> None:
    """No previous close exists, so the true range is genuinely unknown.

    Substituting high - low there is the common shortcut; it fabricates a
    value and then feeds it into the ATR seed.
    """
    result = true_range([10.0, 11.0], [9.0, 10.0], [9.5, 10.5])
    assert result[0] is None


@pytest.mark.unit
def test_true_range_captures_a_gap_the_bar_range_would_miss() -> None:
    """The property that makes it a *true* range.

    Bar 1 opens far above bar 0's close. Its own high-low span is only 1.00,
    but the move from the previous close is 2.50, and that is the risk a stop
    actually faced.
    """
    highs = [10.0, 12.0]
    lows = [9.0, 11.0]
    closes = [9.5, 11.5]
    result = true_range(highs, lows, closes)
    assert result[1] == pytest.approx(2.5)
    assert result[1] != pytest.approx(highs[1] - lows[1])


@pytest.mark.unit
def test_true_range_uses_the_bar_span_when_there_is_no_gap() -> None:
    result = true_range([10.0, 10.5], [9.0, 9.5], [9.5, 10.0])
    assert result[1] == pytest.approx(1.0)


@pytest.mark.unit
def test_atr_seeds_with_the_mean_of_the_first_defined_true_ranges() -> None:
    """Period 3 over a series whose true ranges are all 2.0 -> ATR 2.0."""
    highs = [10.0, 12.0, 14.0, 16.0, 18.0]
    lows = [8.0, 10.0, 12.0, 14.0, 16.0]
    closes = [10.0, 12.0, 14.0, 16.0, 18.0]
    # TR[i] = max(2, |high-prevClose|, |low-prevClose|) = max(2, 2, 2) = 2
    ranges = true_range(highs, lows, closes)
    assert ranges[1:] == (2.0, 2.0, 2.0, 2.0)

    result = atr(highs, lows, closes, period=3)
    assert result[:3] == (None, None, None)
    assert result[3] == pytest.approx(2.0)


@pytest.mark.unit
def test_atr_first_value_lands_at_index_period() -> None:
    """One later than an SMA, because index 0 has no true range."""
    size = 40
    highs = [100.0 + index for index in range(size)]
    lows = [99.0 + index for index in range(size)]
    closes = [99.5 + index for index in range(size)]
    result = atr(highs, lows, closes, period=14)
    assert all(value is None for value in result[:14])
    assert result[14] is not None


@pytest.mark.unit
def test_atr_of_a_frozen_market_is_zero() -> None:
    flat = [50.0] * 30
    result = atr(flat, flat, flat, period=14)
    assert result[-1] == pytest.approx(0.0)


@pytest.mark.unit
@pytest.mark.parametrize("size", (0, 1))
def test_atr_of_a_series_too_short_to_have_a_range(size: int) -> None:
    values = [10.0] * size
    assert atr(values, values, values, 14) == (None,) * size


@pytest.mark.unit
def test_mismatched_parallel_series_are_rejected() -> None:
    with pytest.raises(IndicatorInputError, match="same length"):
        true_range([1.0, 2.0], [1.0], [1.0, 2.0])


# ----------------------------------------------------------------------
# Bollinger Bands
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_bollinger_bands_hand_computed() -> None:
    """closes 1..5, period 5: mean 3, population sigma sqrt(2)."""
    result = bollinger_bands([1.0, 2.0, 3.0, 4.0, 5.0], period=5, multiplier=2.0)
    sigma = math.sqrt(2.0)
    assert result.middle[4] == pytest.approx(3.0)
    assert result.upper[4] == pytest.approx(3.0 + 2.0 * sigma)
    assert result.lower[4] == pytest.approx(3.0 - 2.0 * sigma)


@pytest.mark.unit
def test_bollinger_uses_population_not_sample_deviation() -> None:
    """The sample form would widen every band by sqrt(N/(N-1))."""
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = bollinger_bands(closes, period=5, multiplier=2.0)
    sample_upper = 3.0 + 2.0 * math.sqrt(2.5)
    assert result.upper[4] is not None
    assert result.upper[4] != pytest.approx(sample_upper)


@pytest.mark.unit
def test_bollinger_bands_of_a_flat_window_coincide() -> None:
    result = bollinger_bands([25.0] * 25, period=20, multiplier=2.0)
    assert result.upper[-1] == pytest.approx(25.0)
    assert result.middle[-1] == pytest.approx(25.0)
    assert result.lower[-1] == pytest.approx(25.0)


@pytest.mark.unit
def test_bollinger_bands_are_ordered_and_symmetric() -> None:
    closes = [100.0 + (index % 9) * 1.5 for index in range(60)]
    result = bollinger_bands(closes, period=20, multiplier=2.0)
    for upper, middle, lower in zip(result.upper, result.middle, result.lower, strict=True):
        if middle is None:
            assert upper is None and lower is None
            continue
        assert upper is not None and lower is not None
        assert lower <= middle <= upper
        assert upper - middle == pytest.approx(middle - lower)


@pytest.mark.unit
def test_bollinger_warm_up_matches_its_moving_average() -> None:
    result = bollinger_bands([float(index) for index in range(30)], period=20)
    assert all(value is None for value in result.middle[:19])
    assert result.middle[19] is not None


@pytest.mark.unit
def test_bollinger_multiplier_must_be_positive() -> None:
    with pytest.raises(ValueError, match="multiplier"):
        bollinger_bands([1.0] * 25, 20, 0.0)
