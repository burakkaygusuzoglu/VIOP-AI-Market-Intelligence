"""Volume divergence between confirmed swings.

Swings are constructed directly so each test names the exact pair it compares.
Prices and volumes are TEST_FIXTURE data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.structure.divergence import (
    DivergenceConfig,
    DivergenceType,
    detect_volume_divergence,
)
from app.domain.structure.market_structure import StructureLabelConfig
from app.domain.structure.swings import SwingPoint, SwingType

ORIGIN = datetime(2026, 1, 2, 9, 0, tzinfo=UTC)


def swing(kind: SwingType, index: int, price: str) -> SwingPoint:
    moment = ORIGIN + index * timedelta(minutes=15)
    return SwingPoint(
        swing_type=kind,
        pivot_index=index,
        pivot_time=moment,
        price=Decimal(price),
        confirmed_index=index + 2,
        confirmed_time=moment + 2 * timedelta(minutes=15),
    )


def volumes(**at_index: float) -> tuple[float | None, ...]:
    """A volume series with values only at the named indices."""
    size = max(int(key.lstrip("i")) for key in at_index) + 1
    values: list[float | None] = [None] * size
    for key, value in at_index.items():
        values[int(key.lstrip("i"))] = value
    return tuple(values)


@pytest.mark.unit
def test_a_higher_high_on_lower_volume_is_bearish_divergence() -> None:
    swings = [swing(SwingType.HIGH, 2, "100"), swing(SwingType.HIGH, 8, "110")]
    events = detect_volume_divergence(swings, volumes(i2=1000.0, i8=600.0))

    assert len(events) == 1
    assert events[0].divergence_type is DivergenceType.BEARISH
    assert events[0].price_change == Decimal("10")
    assert events[0].volume_change == pytest.approx(-400.0)


@pytest.mark.unit
def test_a_lower_low_on_lower_volume_is_bullish_divergence() -> None:
    swings = [swing(SwingType.LOW, 2, "100"), swing(SwingType.LOW, 8, "90")]
    events = detect_volume_divergence(swings, volumes(i2=1000.0, i8=600.0))

    assert len(events) == 1
    assert events[0].divergence_type is DivergenceType.BULLISH


@pytest.mark.unit
def test_a_higher_high_on_rising_volume_is_confirmation_not_divergence() -> None:
    swings = [swing(SwingType.HIGH, 2, "100"), swing(SwingType.HIGH, 8, "110")]
    assert detect_volume_divergence(swings, volumes(i2=600.0, i8=1000.0)) == ()


@pytest.mark.unit
def test_a_lower_high_on_lower_volume_is_not_divergence() -> None:
    """Price and volume falling together agree; there is nothing to diverge."""
    swings = [swing(SwingType.HIGH, 2, "110"), swing(SwingType.HIGH, 8, "100")]
    assert detect_volume_divergence(swings, volumes(i2=1000.0, i8=600.0)) == ()


@pytest.mark.unit
def test_a_divergence_is_knowable_when_its_later_pivot_confirms() -> None:
    later = swing(SwingType.HIGH, 8, "110")
    swings = [swing(SwingType.HIGH, 2, "100"), later]
    event = detect_volume_divergence(swings, volumes(i2=1000.0, i8=600.0))[0]

    assert event.confirmed_index == later.confirmed_index
    assert event.confirmed_time == later.confirmed_time
    assert not event.known_at(later.pivot_index)
    assert event.known_at(later.confirmed_index)


@pytest.mark.unit
def test_highs_and_lows_are_compared_within_their_own_type() -> None:
    swings = [
        swing(SwingType.HIGH, 2, "100"),
        swing(SwingType.LOW, 4, "80"),
        swing(SwingType.HIGH, 8, "110"),
        swing(SwingType.LOW, 12, "70"),
    ]
    events = detect_volume_divergence(swings, volumes(i2=1000.0, i4=1000.0, i8=600.0, i12=600.0))
    kinds = {event.divergence_type for event in events}
    assert kinds == {DivergenceType.BEARISH, DivergenceType.BULLISH}


# ----------------------------------------------------------------------
# Tolerance and insufficient data
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_marginal_volume_drop_is_within_tolerance() -> None:
    swings = [swing(SwingType.HIGH, 2, "100"), swing(SwingType.HIGH, 8, "110")]
    assert detect_volume_divergence(swings, volumes(i2=1000.0, i8=980.0)) == ()


@pytest.mark.unit
def test_the_volume_tolerance_is_configurable() -> None:
    swings = [swing(SwingType.HIGH, 2, "100"), swing(SwingType.HIGH, 8, "110")]
    config = DivergenceConfig(volume_tolerance=0.0)
    assert len(detect_volume_divergence(swings, volumes(i2=1000.0, i8=980.0), config)) == 1


@pytest.mark.unit
def test_prices_use_the_same_equality_rule_as_the_structure_labels() -> None:
    swings = [swing(SwingType.HIGH, 2, "100.00"), swing(SwingType.HIGH, 8, "100.01")]
    config = DivergenceConfig(label_config=StructureLabelConfig(equal_tolerance=Decimal("0.05")))
    assert detect_volume_divergence(swings, volumes(i2=1000.0, i8=500.0), config) == ()


@pytest.mark.unit
def test_a_pivot_inside_the_volume_warm_up_produces_no_event() -> None:
    """The comparison was not possible; a zero would claim it was."""
    swings = [swing(SwingType.HIGH, 2, "100"), swing(SwingType.HIGH, 8, "110")]
    assert detect_volume_divergence(swings, volumes(i2=1000.0, i8=600.0)[:5]) == ()


@pytest.mark.unit
def test_fewer_than_two_swings_of_a_type_produce_nothing() -> None:
    assert detect_volume_divergence([swing(SwingType.HIGH, 2, "100")], volumes(i2=1.0)) == ()
    assert detect_volume_divergence([], ()) == ()


@pytest.mark.unit
def test_negative_tolerance_is_rejected() -> None:
    with pytest.raises(ValueError, match="volume_tolerance"):
        DivergenceConfig(volume_tolerance=-0.1)


@pytest.mark.unit
def test_detection_is_reproducible() -> None:
    swings = [swing(SwingType.HIGH, 2, "100"), swing(SwingType.HIGH, 8, "110")]
    measure = volumes(i2=1000.0, i8=600.0)
    assert detect_volume_divergence(swings, measure) == detect_volume_divergence(swings, measure)


@pytest.mark.unit
def test_events_are_ordered_by_confirmation() -> None:
    swings = [
        swing(SwingType.HIGH, 2, "100"),
        swing(SwingType.HIGH, 8, "110"),
        swing(SwingType.HIGH, 14, "120"),
    ]
    events = detect_volume_divergence(swings, volumes(i2=1000.0, i8=800.0, i14=500.0))
    assert [event.confirmed_index for event in events] == sorted(
        event.confirmed_index for event in events
    )
