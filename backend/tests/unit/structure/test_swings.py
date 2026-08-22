"""Swing detection: the pivot rule, its tie-breaks, and its confirmation lag.

Every price here is TEST_FIXTURE data and describes no instrument.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.market.series import ValidatedCandleSeries
from app.domain.structure.swings import (
    SwingConfig,
    SwingPoint,
    SwingType,
    detect_swings,
    last_swing,
    swings_known_at,
)
from tests.factories import ohlcv_series, pivot_series

ONE_ONE = SwingConfig(left=1, right=1)


def _highs(series: ValidatedCandleSeries, config: SwingConfig = ONE_ONE) -> list[SwingPoint]:
    return [s for s in detect_swings(series, config) if s.swing_type is SwingType.HIGH]


def _lows(series: ValidatedCandleSeries, config: SwingConfig = ONE_ONE) -> list[SwingPoint]:
    return [s for s in detect_swings(series, config) if s.swing_type is SwingType.LOW]


# ----------------------------------------------------------------------
# The rule
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_single_peak_is_a_swing_high() -> None:
    series = pivot_series([10.0, 12.0, 10.0])
    highs = _highs(series)
    assert len(highs) == 1
    assert highs[0].pivot_index == 1
    assert highs[0].price == Decimal("12.0")


@pytest.mark.unit
def test_a_single_trough_is_a_swing_low() -> None:
    series = pivot_series(highs=[12.0, 10.0, 12.0], lows=[11.0, 8.0, 11.0])
    lows = _lows(series)
    assert len(lows) == 1
    assert lows[0].pivot_index == 1
    assert lows[0].price == Decimal("8.0")


@pytest.mark.unit
def test_pivot_price_comes_from_the_wick_by_default() -> None:
    """A swing high is the highest high, which is where stops actually sit."""
    series = ohlcv_series(highs=[10.0, 20.0, 10.0], lows=[9.0, 11.0, 9.0], closes=[9.5, 12.0, 9.5])
    high = _highs(series)[0]
    assert high.price == Decimal("20.0")


@pytest.mark.unit
def test_pivots_can_be_taken_from_closes_instead() -> None:
    series = ohlcv_series(highs=[10.0, 20.0, 10.0], lows=[9.0, 11.0, 9.0], closes=[9.5, 12.0, 9.5])
    high = _highs(series, SwingConfig(left=1, right=1, use_wicks=False))[0]
    assert high.price == Decimal("12.0")


@pytest.mark.unit
@pytest.mark.parametrize(("left", "right"), ((1, 1), (2, 2), (3, 1), (1, 3)))
def test_wider_windows_need_more_neighbours(left: int, right: int) -> None:
    peak = 5
    highs = [10.0] * 11
    highs[peak] = 20.0
    series = pivot_series(highs)
    found = _highs(series, SwingConfig(left=left, right=right))
    assert [swing.pivot_index for swing in found] == [peak]


# ----------------------------------------------------------------------
# Confirmation lag — the property everything else depends on
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_confirmation_lags_the_pivot_by_the_right_window() -> None:
    """The prompt's example: a pivot at 100 with right=2 is knowable at 102."""
    highs = [10.0] * 130
    highs[100] = 50.0
    series = pivot_series(highs)
    swing = _highs(series, SwingConfig(left=2, right=2))[0]

    assert swing.pivot_index == 100
    assert swing.confirmed_index == 102
    assert swing.confirmation_lag == 2
    assert swing.pivot_time == series.open_times[100]
    assert swing.confirmed_time == series.open_times[102]


@pytest.mark.unit
def test_a_swing_is_not_known_before_it_is_confirmed() -> None:
    highs = [10.0] * 130
    highs[100] = 50.0
    series = pivot_series(highs)
    swing = _highs(series, SwingConfig(left=2, right=2))[0]

    assert not swing.known_at(100)
    assert not swing.known_at(101)
    assert swing.known_at(102)
    assert swing.known_at(129)


@pytest.mark.unit
def test_swings_known_at_filters_by_confirmation_never_by_pivot() -> None:
    highs = [10.0] * 60
    highs[20] = 30.0
    highs[40] = 40.0
    series = pivot_series(highs)
    swings = detect_swings(series, SwingConfig(left=2, right=2))

    assert [s.pivot_index for s in swings_known_at(swings, 21)] == []
    assert [s.pivot_index for s in swings_known_at(swings, 22)] == [20]
    assert [s.pivot_index for s in swings_known_at(swings, 41)] == [20]
    assert [s.pivot_index for s in swings_known_at(swings, 42)] == [20, 40]


@pytest.mark.unit
def test_a_pivot_too_close_to_the_end_is_not_emitted() -> None:
    """It cannot be confirmed yet, so it does not exist yet."""
    series = pivot_series([10.0, 11.0, 12.0, 20.0, 11.0])
    assert _highs(series, SwingConfig(left=2, right=2)) == []


# ----------------------------------------------------------------------
# Equal highs and lows — the tie-break
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_equal_highs_yield_one_pivot_at_the_first_bar() -> None:
    """Strict on the left, permissive on the right - see ``detect_swings``.

    A plateau produces exactly one pivot, at the bar that first reached the
    level. Strict-on-both-sides would produce none, and a textbook double top
    would vanish; permissive-on-both would mark every bar of the plateau.
    """
    series = pivot_series([10.0, 20.0, 20.0, 10.0])
    highs = _highs(series)
    assert [swing.pivot_index for swing in highs] == [1]
    assert highs[0].price == Decimal("20.0")


@pytest.mark.unit
def test_a_long_plateau_still_yields_exactly_one_pivot() -> None:
    series = pivot_series([10.0, 20.0, 20.0, 20.0, 20.0, 10.0])
    assert [swing.pivot_index for swing in _highs(series)] == [1]


@pytest.mark.unit
def test_equal_lows_yield_one_pivot_at_the_first_bar() -> None:
    series = pivot_series(highs=[20.0, 12.0, 12.0, 20.0], lows=[15.0, 5.0, 5.0, 15.0])
    lows = _lows(series)
    assert [swing.pivot_index for swing in lows] == [1]
    assert lows[0].price == Decimal("5.0")


@pytest.mark.unit
def test_a_flat_market_has_no_swings_at_all() -> None:
    """No bar is strictly above its neighbour. That is the honest answer."""
    series = pivot_series([10.0] * 20)
    assert detect_swings(series, ONE_ONE) == ()


# ----------------------------------------------------------------------
# Market shapes
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_clean_uptrend_produces_rising_swings() -> None:
    highs = [10.0, 14.0, 11.0, 18.0, 14.0, 22.0, 18.0, 26.0, 22.0]
    series = pivot_series(highs)
    found = _highs(series)
    prices = [swing.price for swing in found]
    assert len(prices) >= 3
    assert prices == sorted(prices)


@pytest.mark.unit
def test_clean_downtrend_produces_falling_swings() -> None:
    highs = [26.0, 22.0, 25.0, 18.0, 21.0, 14.0, 17.0, 10.0, 13.0]
    series = pivot_series(highs)
    prices = [swing.price for swing in _lows(series)]
    assert len(prices) >= 3
    assert prices == sorted(prices, reverse=True)


@pytest.mark.unit
def test_a_range_produces_swings_on_both_sides() -> None:
    highs = [10.0, 20.0, 10.0, 20.0, 10.0, 20.0, 10.0]
    series = pivot_series(highs)
    swings = detect_swings(series, ONE_ONE)
    assert any(swing.swing_type is SwingType.HIGH for swing in swings)
    assert any(swing.swing_type is SwingType.LOW for swing in swings)


@pytest.mark.unit
def test_a_noisy_market_still_produces_ordered_swings() -> None:
    highs = [10.0 + (index * 7 % 13) for index in range(80)]
    series = pivot_series(highs)
    swings = detect_swings(series, ONE_ONE)
    indices = [swing.pivot_index for swing in swings]
    assert indices == sorted(indices)
    assert all(swing.confirmed_index > swing.pivot_index for swing in swings)


# ----------------------------------------------------------------------
# Edges
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("size", (0, 1, 2))
def test_insufficient_history_yields_nothing(size: int) -> None:
    series = pivot_series([10.0] * size) if size else pivot_series([])
    assert detect_swings(series, ONE_ONE) == ()


@pytest.mark.unit
def test_exactly_the_minimum_history_can_produce_a_pivot() -> None:
    config = SwingConfig(left=2, right=2)
    assert config.minimum_candles == 5
    series = pivot_series([10.0, 11.0, 20.0, 11.0, 10.0])
    assert [swing.pivot_index for swing in _highs(series, config)] == [2]


@pytest.mark.unit
@pytest.mark.parametrize("scale", (0.0001, 1.0, 1_000_000.0))
def test_extreme_price_scales_do_not_change_the_shape(scale: float) -> None:
    highs = [value * scale for value in (10.0, 20.0, 10.0, 30.0, 10.0)]
    series = pivot_series(highs)
    assert [swing.pivot_index for swing in _highs(series)] == [1, 3]


@pytest.mark.unit
@pytest.mark.parametrize(("left", "right"), ((0, 1), (1, 0), (-1, 2)))
def test_windows_must_be_positive(left: int, right: int) -> None:
    with pytest.raises(ValueError, match="must be >="):
        SwingConfig(left=left, right=right)


@pytest.mark.unit
def test_detection_is_reproducible() -> None:
    series = pivot_series([10.0 + (index * 5 % 11) for index in range(60)])
    assert detect_swings(series, ONE_ONE) == detect_swings(series, ONE_ONE)


@pytest.mark.unit
def test_last_swing_picks_the_most_recent_of_a_type() -> None:
    highs = [10.0] * 40
    highs[10] = 30.0
    highs[25] = 20.0
    series = pivot_series(highs)
    swings = detect_swings(series, SwingConfig(left=2, right=2))
    latest = last_swing(swings, SwingType.HIGH)
    assert latest is not None
    assert latest.pivot_index == 25


@pytest.mark.unit
def test_last_swing_of_an_absent_type_is_none() -> None:
    assert last_swing((), SwingType.HIGH) is None
