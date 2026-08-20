"""RSI and MACD: hand-computed values, warm-up, and degenerate markets."""

from __future__ import annotations

import pytest

from app.domain.technical.momentum import RSI_NEUTRAL, macd, rsi
from app.domain.technical.types import IndicatorInputError


@pytest.mark.unit
def test_rsi_hand_computed() -> None:
    """Small enough to verify with arithmetic.

    closes 10, 11, 10, 12 -> changes +1, -1, +2
    gains  1, 0, 2   losses 0, 1, 0
    period 2, Wilder seed = mean of the first two changes:
      avgGain[0] = 0.5, avgLoss[0] = 0.5 -> RS 1   -> RSI 50
      avgGain[1] = 0.5 + (2-0.5)/2 = 1.25
      avgLoss[1] = 0.5 + (0-0.5)/2 = 0.25 -> RS 5  -> RSI 100 - 100/6
    """
    result = rsi([10.0, 11.0, 10.0, 12.0], period=2)
    assert result[0] is None
    assert result[1] is None
    assert result[2] == pytest.approx(50.0)
    assert result[3] == pytest.approx(100.0 - 100.0 / 6.0)


@pytest.mark.unit
def test_rsi_first_value_lands_one_bar_after_an_sma_of_the_same_period() -> None:
    """Index 0 yields no change, so RSI 14 starts at index 14, not 13."""
    closes = [float(100 + index) for index in range(40)]
    result = rsi(closes, period=14)
    assert all(value is None for value in result[:14])
    assert result[14] is not None


@pytest.mark.unit
def test_rsi_is_one_hundred_when_nothing_ever_falls() -> None:
    result = rsi([float(100 + index) for index in range(30)], period=14)
    assert result[-1] == pytest.approx(100.0)


@pytest.mark.unit
def test_rsi_is_zero_when_nothing_ever_rises() -> None:
    result = rsi([float(200 - index) for index in range(30)], period=14)
    assert result[-1] == pytest.approx(0.0)


@pytest.mark.unit
def test_rsi_constant_price_is_neutral() -> None:
    """The documented ``0 / 0`` decision.

    A market that has not moved has no gains and no losses. Reporting 100 -
    which a naive "no losses means maximum" branch does - would claim maximum
    bullish momentum for a flat tape. 50 says there is no directional
    pressure, which is what actually happened.
    """
    result = rsi([100.0] * 30, period=14)
    assert result[14] == pytest.approx(RSI_NEUTRAL)
    assert result[-1] == pytest.approx(RSI_NEUTRAL)


@pytest.mark.unit
def test_rsi_stays_within_bounds() -> None:
    closes = [100.0 + (index % 7) * 3.0 - (index % 5) * 2.0 for index in range(120)]
    for value in rsi(closes, 14):
        if value is not None:
            assert 0.0 <= value <= 100.0


@pytest.mark.unit
@pytest.mark.parametrize("closes", ([], [100.0]))
def test_rsi_needs_at_least_two_closes(closes: list[float]) -> None:
    assert rsi(closes, 14) == (None,) * len(closes)


@pytest.mark.unit
def test_rsi_rejects_a_non_positive_period() -> None:
    with pytest.raises(IndicatorInputError):
        rsi([1.0, 2.0, 3.0], 0)


# ----------------------------------------------------------------------
# MACD
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_macd_warm_up_indices_are_exactly_as_documented() -> None:
    """MACD line at slow-1, signal 8 bars later, histogram with the signal."""
    closes = [100.0 + index * 0.5 for index in range(60)]
    result = macd(closes, 12, 26, 9)

    assert all(value is None for value in result.macd[:25])
    assert result.macd[25] is not None

    assert all(value is None for value in result.signal[:33])
    assert result.signal[33] is not None

    assert all(value is None for value in result.histogram[:33])
    assert result.histogram[33] is not None


@pytest.mark.unit
def test_macd_histogram_is_exactly_the_difference() -> None:
    closes = [100.0 + (index % 11) * 2.0 for index in range(80)]
    result = macd(closes, 12, 26, 9)
    for line, signal, histogram in zip(result.macd, result.signal, result.histogram, strict=True):
        if line is None or signal is None:
            assert histogram is None
        else:
            assert histogram == pytest.approx(line - signal)


@pytest.mark.unit
def test_macd_of_a_flat_market_is_zero() -> None:
    result = macd([50.0] * 60, 12, 26, 9)
    assert result.macd[-1] == pytest.approx(0.0)
    assert result.signal[-1] == pytest.approx(0.0)
    assert result.histogram[-1] == pytest.approx(0.0)


@pytest.mark.unit
def test_macd_is_positive_while_the_fast_average_leads() -> None:
    rising = [100.0 + index for index in range(60)]
    assert (result := macd(rising, 12, 26, 9).macd[-1]) is not None and result > 0

    falling = [200.0 - index for index in range(60)]
    assert (result := macd(falling, 12, 26, 9).macd[-1]) is not None and result < 0


@pytest.mark.unit
def test_macd_signal_ignores_the_warm_up_rather_than_treating_it_as_zero() -> None:
    """Feeding ``None`` positions in as zeros would drag the signal to zero.

    With a strictly positive MACD line, a signal seeded over zeros would sit
    far below the line for many bars. Seeding over the defined values only
    keeps the first signal within the range the line has actually taken.
    """
    closes = [100.0 + index * 2.0 for index in range(60)]
    result = macd(closes, 12, 26, 9)
    defined_line = [value for value in result.macd if value is not None]
    first_signal = result.signal[33]
    assert first_signal is not None
    assert min(defined_line) <= first_signal <= max(defined_line)


@pytest.mark.unit
def test_macd_rejects_a_fast_period_that_is_not_shorter_than_the_slow_one() -> None:
    with pytest.raises(ValueError, match="shorter"):
        macd([100.0] * 60, 26, 26, 9)


@pytest.mark.unit
def test_macd_of_a_short_series_is_all_none() -> None:
    result = macd([100.0] * 10, 12, 26, 9)
    assert result.macd == (None,) * 10
    assert result.signal == (None,) * 10
    assert result.histogram == (None,) * 10
