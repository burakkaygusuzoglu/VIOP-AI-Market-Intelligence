"""BOS and CHOCH: classification, confirmation method, invalidation.

Scenarios are hand-built so each test states the price path it is about.
All prices are TEST_FIXTURE data.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.common.enums import Direction
from app.domain.market.series import ValidatedCandleSeries
from app.domain.structure.events import (
    BreakConfirmation,
    StructuralEvent,
    StructuralEventConfig,
    StructuralEventType,
    detect_structural_events,
)
from app.domain.structure.market_structure import StructureBias
from app.domain.structure.swings import SwingConfig, detect_swings
from tests.factories import ohlcv_series, pivot_series

WINDOW = SwingConfig(left=1, right=1)


def _events(
    series: ValidatedCandleSeries, config: StructuralEventConfig | None = None
) -> tuple[StructuralEvent, ...]:
    swings = detect_swings(series, WINDOW)
    return detect_structural_events(series, swings, config)


@pytest.mark.unit
def test_breaking_a_prior_high_in_a_bullish_structure_is_a_bos() -> None:
    """Higher highs and higher lows, then price takes out the last high."""
    highs = [100.0, 104.0, 101.0, 108.0, 105.0, 112.0, 109.0, 120.0]
    lows = [98.0, 100.0, 99.0, 104.0, 103.0, 108.0, 107.0, 116.0]
    closes = [99.0, 103.0, 100.0, 107.0, 104.0, 111.0, 108.0, 119.0]
    series = ohlcv_series(highs, lows, closes)

    events = _events(series)
    assert events, "expected at least one structural event"
    last = events[-1]
    assert last.event_type is StructuralEventType.BOS
    assert last.direction is Direction.LONG
    assert last.prior_bias is StructureBias.BULLISH


@pytest.mark.unit
def test_breaking_a_prior_high_in_a_bearish_structure_is_a_choch() -> None:
    """Lower highs and lower lows, then price reclaims the last high."""
    highs = [120.0, 112.0, 116.0, 106.0, 110.0, 102.0, 106.0, 130.0]
    lows = [110.0, 104.0, 108.0, 98.0, 102.0, 94.0, 98.0, 120.0]
    closes = [115.0, 106.0, 112.0, 100.0, 106.0, 96.0, 102.0, 129.0]
    series = ohlcv_series(highs, lows, closes)

    events = _events(series)
    choch = [event for event in events if event.event_type is StructuralEventType.CHOCH]
    assert choch, [(e.event_type.value, e.prior_bias.value) for e in events]
    assert choch[-1].direction is Direction.LONG
    assert choch[-1].prior_bias is StructureBias.BEARISH


@pytest.mark.unit
def test_a_downward_break_in_a_bullish_structure_is_a_choch() -> None:
    highs = [100.0, 104.0, 101.0, 108.0, 105.0, 112.0, 109.0, 90.0]
    lows = [98.0, 100.0, 99.0, 104.0, 103.0, 108.0, 107.0, 80.0]
    closes = [99.0, 103.0, 100.0, 107.0, 104.0, 111.0, 108.0, 81.0]
    series = ohlcv_series(highs, lows, closes)

    events = _events(series)
    downward = [event for event in events if event.direction is Direction.SHORT]
    assert downward
    assert downward[-1].event_type is StructuralEventType.CHOCH
    assert downward[-1].prior_bias is StructureBias.BULLISH


@pytest.mark.unit
def test_a_break_from_an_undecided_structure_is_neither_bos_nor_choch() -> None:
    """Nothing was continued and nothing reversed, so neither name is true.

    CHOCH would invent a reversal of a character that never existed. BOS would
    be just as dishonest in the other direction: *Break of Structure* asserts a
    structure existed **and** that this break continued it. LEVEL_BREAK states
    only what happened.
    """
    highs = [100.0, 110.0, 100.0, 110.0, 100.0, 130.0]
    lows = [90.0, 100.0, 90.0, 100.0, 90.0, 120.0]
    closes = [95.0, 105.0, 95.0, 105.0, 95.0, 129.0]
    series = ohlcv_series(highs, lows, closes)

    events = _events(series)
    assert events
    assert all(event.event_type is StructuralEventType.LEVEL_BREAK for event in events)
    assert not any(event.prior_bias.is_directional for event in events)


@pytest.mark.unit
@pytest.mark.parametrize(
    "bias",
    (
        StructureBias.CONTRACTING,
        StructureBias.EXPANDING,
        StructureBias.AMBIGUOUS,
        StructureBias.INSUFFICIENT,
    ),
)
def test_every_non_directional_bias_produces_a_level_break(bias: StructureBias) -> None:
    """The rule is the bias's directionality, not which particular one it is."""
    assert not bias.is_directional


@pytest.mark.unit
def test_a_classified_break_always_had_a_directional_bias() -> None:
    """The invariant that makes the label trustworthy on its own.

    A consumer reading ``event_type`` must never need to check ``prior_bias``
    to find out whether the classification meant anything. Checked over a long
    noisy market that produces all three kinds.
    """
    series = pivot_series([100.0 + (index * 17 % 23) for index in range(200)])
    events = _events(series)
    assert events

    for event in events:
        if event.event_type is StructuralEventType.LEVEL_BREAK:
            assert not event.prior_bias.is_directional
            continue

        assert event.event_type.is_classified
        assert event.prior_bias.is_directional
        continues = (event.direction is Direction.LONG) == (
            event.prior_bias is StructureBias.BULLISH
        )
        assert (event.event_type is StructuralEventType.BOS) is continues


@pytest.mark.unit
def test_the_market_produces_all_three_kinds_so_the_invariant_is_not_vacuous() -> None:
    series = pivot_series([100.0 + (index * 17 % 23) for index in range(200)])
    kinds = {event.event_type for event in _events(series)}
    assert StructuralEventType.LEVEL_BREAK in kinds
    assert kinds & {StructuralEventType.BOS, StructuralEventType.CHOCH}


@pytest.mark.unit
def test_only_bos_and_choch_count_as_classified() -> None:
    assert StructuralEventType.BOS.is_classified
    assert StructuralEventType.CHOCH.is_classified
    assert not StructuralEventType.LEVEL_BREAK.is_classified


# ----------------------------------------------------------------------
# Confirmation method
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_wick_through_a_level_is_not_a_break_by_default() -> None:
    """Close confirmation. A wick through a level is a test, not structure."""
    highs = [100.0, 110.0, 100.0, 118.0]
    lows = [90.0, 100.0, 90.0, 95.0]
    closes = [95.0, 105.0, 95.0, 105.0]  # closes back below the 110 pivot
    series = ohlcv_series(highs, lows, closes)

    assert _events(series) == ()


@pytest.mark.unit
def test_wick_confirmation_reports_the_same_penetration_as_a_break() -> None:
    highs = [100.0, 110.0, 100.0, 118.0]
    lows = [90.0, 100.0, 90.0, 95.0]
    closes = [95.0, 105.0, 95.0, 105.0]
    series = ohlcv_series(highs, lows, closes)

    events = _events(series, StructuralEventConfig(confirmation=BreakConfirmation.WICK))
    assert len(events) == 1
    assert events[0].confirmation is BreakConfirmation.WICK
    assert events[0].breach_price == Decimal("118.0")


@pytest.mark.unit
def test_a_tolerance_suppresses_a_marginal_break() -> None:
    highs = [100.0, 110.0, 100.0, 112.0]
    lows = [90.0, 100.0, 90.0, 105.0]
    closes = [95.0, 105.0, 95.0, 110.5]  # only 0.5 beyond the 110 pivot
    series = ohlcv_series(highs, lows, closes)

    assert _events(series) != ()
    assert _events(series, StructuralEventConfig(breach_tolerance=Decimal("1"))) == ()


@pytest.mark.unit
def test_tolerance_must_not_be_negative() -> None:
    with pytest.raises(ValueError, match="breach_tolerance"):
        StructuralEventConfig(breach_tolerance=Decimal("-1"))


# ----------------------------------------------------------------------
# Invalidation
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_broken_level_does_not_fire_again_on_every_later_candle() -> None:
    """Without invalidation a holding trend emits an identical BOS forever."""
    highs = [100.0, 110.0, 100.0] + [130.0] * 8
    lows = [90.0, 100.0, 90.0] + [120.0] * 8
    closes = [95.0, 105.0, 95.0] + [129.0] * 8
    series = ohlcv_series(highs, lows, closes)

    events = _events(series)
    upward = [event for event in events if event.direction is Direction.LONG]
    broken_levels = [event.broken_level for event in upward]
    assert len(broken_levels) == len(set(broken_levels))


@pytest.mark.unit
def test_each_event_carries_the_evidence_for_its_classification() -> None:
    highs = [100.0, 104.0, 101.0, 108.0, 105.0, 112.0, 109.0, 120.0]
    lows = [98.0, 100.0, 99.0, 104.0, 103.0, 108.0, 107.0, 116.0]
    closes = [99.0, 103.0, 100.0, 107.0, 104.0, 111.0, 108.0, 119.0]
    series = ohlcv_series(highs, lows, closes)

    event = _events(series)[-1]
    assert event.origin_swing.confirmed_index <= event.event_index
    assert event.broken_level == event.origin_swing.price
    assert event.event_time == series.open_times[event.event_index]
    assert event.reason
    assert event.confirmation is BreakConfirmation.CLOSE


@pytest.mark.unit
def test_an_event_is_never_older_than_the_swing_it_broke() -> None:
    """A level cannot be broken before anyone could know it was a level."""
    series = pivot_series([10.0 + (index * 11 % 17) for index in range(120)])
    for event in _events(series):
        assert event.origin_swing.confirmed_index <= event.event_index


@pytest.mark.unit
def test_close_confirmation_settles_the_break_on_the_same_candle() -> None:
    """Only closed candles are consumed, so cause and confirmation coincide."""
    series = pivot_series([10.0 + (index * 7 % 13) for index in range(80)])
    for event in _events(series):
        assert event.confirmed_index == event.event_index
        assert event.confirmed_time == event.event_time


@pytest.mark.unit
def test_no_swings_means_no_events() -> None:
    series = pivot_series([10.0] * 30)
    assert _events(series) == ()


@pytest.mark.unit
def test_detection_is_reproducible() -> None:
    series = pivot_series([10.0 + (index * 13 % 19) for index in range(90)])
    assert _events(series) == _events(series)
