"""Breakout lifecycle, false breakouts, retests and volume confirmation.

The central assertion of this module: a false breakout is stamped with the
candle that *revealed* the failure, never with the breach it invalidates. A
reader asking "what did we know at the breach?" must get a breach and nothing
more.

Prices are TEST_FIXTURE data.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.common.enums import Direction
from app.domain.market.series import ValidatedCandleSeries
from app.domain.structure.breakouts import (
    BreakoutConfig,
    BreakoutEvent,
    BreakoutEventType,
    RetestEventType,
    VolumeConfirmation,
    detect_breakouts,
    detect_retests,
)
from app.domain.structure.swings import SwingConfig, detect_swings
from app.domain.structure.zones import Zone, build_zones
from tests.factories import ohlcv_series

WINDOW = SwingConfig(left=1, right=1)


def _flat(value: float | None, size: int) -> tuple[float | None, ...]:
    return (value,) * size


Scenario = tuple[ValidatedCandleSeries, tuple[Zone, ...], tuple[BreakoutEvent, ...]]


def _scenario(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    atr: float = 2.0,
    relative: float | None = 1.0,
    config: BreakoutConfig | None = None,
) -> Scenario:
    series = ohlcv_series(highs, lows, closes)
    swings = detect_swings(series, WINDOW)
    support, resistance = build_zones(
        series, swings, _flat(atr, len(series)), _flat(relative, len(series))
    )
    zones = support + resistance
    events = detect_breakouts(series, zones, _flat(relative, len(series)), config)
    return series, zones, events


def _resistance_scenario(
    tail_highs: list[float],
    tail_lows: list[float],
    tail_closes: list[float],
    *,
    atr: float = 2.0,
    relative: float | None = 1.0,
    config: BreakoutConfig | None = None,
) -> Scenario:
    """A twice-tested 120 resistance, then whatever the test appends."""
    highs = [100.0, 120.0, 100.0, 120.0, 100.0, *tail_highs]
    lows = [90.0, 110.0, 90.0, 110.0, 90.0, *tail_lows]
    closes = [95.0, 115.0, 95.0, 115.0, 95.0, *tail_closes]
    return _scenario(highs, lows, closes, atr=atr, relative=relative, config=config)


# ----------------------------------------------------------------------
# Breach and confirmation
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_close_beyond_the_zone_is_a_breach() -> None:
    _, _, events = _resistance_scenario([135.0] * 5, [125.0] * 5, [130.0] * 5)
    breaches = [e for e in events if e.event_type is BreakoutEventType.BREACH]
    assert len(breaches) == 1
    assert breaches[0].direction is Direction.LONG
    assert breaches[0].price == Decimal("130.0")


@pytest.mark.unit
def test_a_breach_that_holds_is_confirmed_after_the_failure_window() -> None:
    """Confirmation lags by exactly the window: that is when failure is ruled out."""
    _, _, events = _resistance_scenario([135.0] * 6, [125.0] * 6, [130.0] * 6)
    breach = next(e for e in events if e.event_type is BreakoutEventType.BREACH)
    confirmed = next(e for e in events if e.event_type is BreakoutEventType.CONFIRMED)

    assert confirmed.breach_index == breach.event_index
    assert confirmed.confirmed_index == breach.event_index + BreakoutConfig().failure_window


@pytest.mark.unit
def test_price_entering_the_zone_without_closing_beyond_is_only_a_challenge() -> None:
    _, _, events = _resistance_scenario([119.0] * 4, [112.0] * 4, [115.0] * 4)
    kinds = {event.event_type for event in events}
    assert BreakoutEventType.CHALLENGE in kinds
    assert BreakoutEventType.BREACH not in kinds


@pytest.mark.unit
def test_a_support_zone_breaks_downward() -> None:
    highs = [120.0, 110.0, 120.0, 110.0, 120.0, 95.0, 95.0, 95.0, 95.0]
    lows = [110.0, 100.0, 110.0, 100.0, 110.0, 80.0, 80.0, 80.0, 80.0]
    closes = [115.0, 105.0, 115.0, 105.0, 115.0, 85.0, 85.0, 85.0, 85.0]
    _, _, events = _scenario(highs, lows, closes)
    breaches = [e for e in events if e.event_type is BreakoutEventType.BREACH]
    assert breaches
    assert breaches[0].direction is Direction.SHORT


# ----------------------------------------------------------------------
# False breakout — the look-ahead trap
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_breach_that_returns_inside_becomes_a_false_breakout() -> None:
    _, _, events = _resistance_scenario(
        [135.0, 125.0, 120.0, 120.0],
        [125.0, 110.0, 105.0, 105.0],
        [130.0, 115.0, 110.0, 110.0],
    )
    kinds = [event.event_type for event in events]
    assert BreakoutEventType.FALSE_BREAKOUT in kinds
    assert BreakoutEventType.CONFIRMED not in kinds


@pytest.mark.unit
def test_the_false_breakout_is_stamped_at_the_failure_not_the_breach() -> None:
    """The heart of the matter.

    Labelling the breach candle "false breakout" would let a backtest decline
    the trade using information from several candles into its own future.
    """
    _, _, events = _resistance_scenario(
        [135.0, 125.0, 120.0, 120.0],
        [125.0, 110.0, 105.0, 105.0],
        [130.0, 115.0, 110.0, 110.0],
    )
    breach = next(e for e in events if e.event_type is BreakoutEventType.BREACH)
    failure = next(e for e in events if e.event_type is BreakoutEventType.FALSE_BREAKOUT)

    assert failure.breach_index == breach.event_index
    assert failure.confirmed_index > breach.confirmed_index
    assert not failure.known_at(breach.confirmed_index)
    assert failure.known_at(failure.confirmed_index)


@pytest.mark.unit
def test_the_breach_event_is_never_rewritten_by_its_own_failure() -> None:
    _, _, events = _resistance_scenario(
        [135.0, 125.0, 120.0, 120.0],
        [125.0, 110.0, 105.0, 105.0],
        [130.0, 115.0, 110.0, 110.0],
    )
    breach = next(e for e in events if e.event_type is BreakoutEventType.BREACH)
    assert breach.event_type is BreakoutEventType.BREACH
    assert breach.confirmed_index == breach.event_index


@pytest.mark.unit
def test_a_return_after_the_failure_window_does_not_undo_a_confirmation() -> None:
    """Once confirmed, a later reversal is new price action, not a false break."""
    config = BreakoutConfig(failure_window=2)
    _, _, events = _resistance_scenario(
        [135.0, 135.0, 135.0, 120.0, 120.0],
        [125.0, 125.0, 125.0, 105.0, 105.0],
        [130.0, 130.0, 130.0, 110.0, 110.0],
        config=config,
    )
    kinds = [event.event_type for event in events]
    assert BreakoutEventType.CONFIRMED in kinds
    assert BreakoutEventType.FALSE_BREAKOUT not in kinds


@pytest.mark.unit
def test_an_unresolved_breach_at_the_end_of_the_data_reports_nothing_further() -> None:
    """The window has not finished, so neither outcome is known yet."""
    _, _, events = _resistance_scenario([135.0], [125.0], [130.0])
    kinds = {event.event_type for event in events}
    assert BreakoutEventType.BREACH in kinds
    assert BreakoutEventType.CONFIRMED not in kinds
    assert BreakoutEventType.FALSE_BREAKOUT not in kinds


# ----------------------------------------------------------------------
# Volume confirmation
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_heavy_volume_at_the_breach_confirms_it() -> None:
    _, _, events = _resistance_scenario([135.0] * 5, [125.0] * 5, [130.0] * 5, relative=2.0)
    breach = next(e for e in events if e.event_type is BreakoutEventType.BREACH)
    assert breach.volume_confirmation is VolumeConfirmation.CONFIRMED
    assert breach.relative_volume == pytest.approx(2.0)


@pytest.mark.unit
def test_light_volume_at_the_breach_is_weak_not_a_veto() -> None:
    _, _, events = _resistance_scenario([135.0] * 5, [125.0] * 5, [130.0] * 5, relative=0.8)
    breach = next(e for e in events if e.event_type is BreakoutEventType.BREACH)
    assert breach.volume_confirmation is VolumeConfirmation.WEAK
    assert breach.event_type is BreakoutEventType.BREACH  # still a breach


@pytest.mark.unit
def test_missing_volume_reports_unavailable_rather_than_weak() -> None:
    """A measurement that was not taken must not read as a measurement of zero."""
    _, _, events = _resistance_scenario([135.0] * 5, [125.0] * 5, [130.0] * 5, relative=None)
    breach = next(e for e in events if e.event_type is BreakoutEventType.BREACH)
    assert breach.volume_confirmation is VolumeConfirmation.UNAVAILABLE
    assert breach.relative_volume is None


@pytest.mark.unit
def test_volume_confirmation_never_becomes_a_direction() -> None:
    assert set(VolumeConfirmation) == {
        VolumeConfirmation.CONFIRMED,
        VolumeConfirmation.WEAK,
        VolumeConfirmation.UNAVAILABLE,
    }


# ----------------------------------------------------------------------
# Retests
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_retest_requires_a_confirmed_breakout_first() -> None:
    series, _, events = _resistance_scenario(
        [135.0, 125.0, 120.0, 120.0],
        [125.0, 110.0, 105.0, 105.0],
        [130.0, 115.0, 110.0, 110.0],
    )
    # This scenario produces a false breakout, never a confirmation.
    assert detect_retests(series, events) == ()


@pytest.mark.unit
def test_a_successful_retest_is_reported_as_held() -> None:
    series, _, events = _resistance_scenario(
        [135.0, 135.0, 135.0, 135.0, 122.0, 140.0, 140.0],
        [125.0, 125.0, 125.0, 125.0, 112.0, 130.0, 130.0],
        [130.0, 130.0, 130.0, 130.0, 121.0, 135.0, 135.0],
    )
    retests = detect_retests(series, events)
    kinds = [event.event_type for event in retests]
    assert RetestEventType.TOUCHED in kinds
    assert RetestEventType.HELD in kinds


@pytest.mark.unit
def test_a_failed_retest_is_reported_as_failed() -> None:
    series, _, events = _resistance_scenario(
        [135.0, 135.0, 135.0, 135.0, 122.0, 115.0, 115.0],
        [125.0, 125.0, 125.0, 125.0, 112.0, 100.0, 100.0],
        [130.0, 130.0, 130.0, 130.0, 121.0, 105.0, 105.0],
    )
    retests = detect_retests(series, events)
    assert RetestEventType.FAILED in [event.event_type for event in retests]


@pytest.mark.unit
def test_a_retest_never_precedes_the_breakout_it_belongs_to() -> None:
    series, _, events = _resistance_scenario(
        [135.0, 135.0, 135.0, 135.0, 122.0, 140.0, 140.0],
        [125.0, 125.0, 125.0, 125.0, 112.0, 130.0, 130.0],
        [130.0, 130.0, 130.0, 130.0, 121.0, 135.0, 135.0],
    )
    for retest in detect_retests(series, events):
        assert retest.event_index > retest.breakout_confirmed_index
        assert retest.confirmed_index >= retest.event_index


@pytest.mark.unit
def test_a_retest_outside_its_window_is_not_attributed_to_the_breakout() -> None:
    config = BreakoutConfig(retest_window=1)
    series, _, events = _resistance_scenario(
        [135.0, 135.0, 135.0, 135.0, 135.0, 135.0, 122.0, 140.0],
        [125.0, 125.0, 125.0, 125.0, 125.0, 125.0, 112.0, 130.0],
        [130.0, 130.0, 130.0, 130.0, 130.0, 130.0, 121.0, 135.0],
        config=config,
    )
    assert detect_retests(series, events, config) == ()


# ----------------------------------------------------------------------
# Configuration and determinism
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    (
        {"failure_window": 0},
        {"retest_window": 0},
        {"retest_resolution_window": 0},
        {"volume_confirmation_multiple": 0.0},
    ),
)
def test_invalid_breakout_configuration_is_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        BreakoutConfig(**kwargs)  # type: ignore[arg-type]


@pytest.mark.unit
def test_detection_is_reproducible() -> None:
    first = _resistance_scenario([135.0] * 5, [125.0] * 5, [130.0] * 5)[2]
    second = _resistance_scenario([135.0] * 5, [125.0] * 5, [130.0] * 5)[2]
    assert first == second


@pytest.mark.unit
def test_events_are_ordered_by_when_they_became_knowable() -> None:
    _, _, events = _resistance_scenario([135.0] * 8, [125.0] * 8, [130.0] * 8)
    indices = [event.confirmed_index for event in events]
    assert indices == sorted(indices)


@pytest.mark.unit
def test_no_zones_means_no_events() -> None:
    series = ohlcv_series([100.0] * 20, [90.0] * 20, [95.0] * 20)
    assert detect_breakouts(series, (), _flat(1.0, 20)) == ()
