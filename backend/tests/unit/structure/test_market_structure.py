"""HH / HL / LH / LL labelling and the bias truth table.

Swings are built directly rather than detected, so each test states exactly the
sequence it is about. Prices are TEST_FIXTURE data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.structure.market_structure import (
    StructureBias,
    StructureLabel,
    StructureLabelConfig,
    label_swings,
)
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


def high(index: int, price: str) -> SwingPoint:
    return swing(SwingType.HIGH, index, price)


def low(index: int, price: str) -> SwingPoint:
    return swing(SwingType.LOW, index, price)


# ----------------------------------------------------------------------
# Labels
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_first_swing_of_each_type_is_labelled_as_absent_not_judged() -> None:
    structure = label_swings([high(1, "100"), low(2, "90")])
    labels = [item.label for item in structure.labelled]
    assert labels == [StructureLabel.FIRST_HIGH, StructureLabel.FIRST_LOW]
    assert structure.bias is StructureBias.INSUFFICIENT


@pytest.mark.unit
def test_highs_are_compared_only_with_highs() -> None:
    """A swing high is not 'higher' than a swing low; the types never mix."""
    structure = label_swings([high(1, "100"), low(2, "150"), high(3, "110")])
    high_labels = [item.label for item in structure.highs]
    assert high_labels == [StructureLabel.FIRST_HIGH, StructureLabel.HIGHER_HIGH]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("first", "second", "expected"),
    (
        ("100", "110", StructureLabel.HIGHER_HIGH),
        ("110", "100", StructureLabel.LOWER_HIGH),
        ("100", "100", StructureLabel.EQUAL_HIGH),
    ),
)
def test_high_labels(first: str, second: str, expected: StructureLabel) -> None:
    structure = label_swings([high(1, first), high(3, second)])
    assert structure.last_high_label is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("first", "second", "expected"),
    (
        ("90", "95", StructureLabel.HIGHER_LOW),
        ("95", "90", StructureLabel.LOWER_LOW),
        ("90", "90", StructureLabel.EQUAL_LOW),
    ),
)
def test_low_labels(first: str, second: str, expected: StructureLabel) -> None:
    structure = label_swings([low(1, first), low(3, second)])
    assert structure.last_low_label is expected


# ----------------------------------------------------------------------
# Bias truth table
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_higher_high_and_higher_low_is_bullish() -> None:
    structure = label_swings([low(1, "90"), high(2, "100"), low(3, "95"), high(4, "110")])
    assert structure.bias is StructureBias.BULLISH


@pytest.mark.unit
def test_lower_high_and_lower_low_is_bearish() -> None:
    structure = label_swings([high(1, "110"), low(2, "95"), high(3, "100"), low(4, "90")])
    assert structure.bias is StructureBias.BEARISH


@pytest.mark.unit
def test_lower_high_and_higher_low_is_contracting_not_a_direction() -> None:
    """A narrowing range says the market is coiling, not which way it breaks."""
    structure = label_swings([high(1, "110"), low(2, "90"), high(3, "105"), low(4, "95")])
    assert structure.bias is StructureBias.CONTRACTING
    assert not structure.bias.is_directional


@pytest.mark.unit
def test_higher_high_and_lower_low_is_expanding_not_bullish() -> None:
    """Both sides extended. Calling this bullish would invent a trend."""
    structure = label_swings([high(1, "100"), low(2, "95"), high(3, "110"), low(4, "90")])
    assert structure.bias is StructureBias.EXPANDING
    assert not structure.bias.is_directional


@pytest.mark.unit
def test_an_equal_high_leaves_the_bias_ambiguous() -> None:
    structure = label_swings([high(1, "100"), low(2, "90"), high(3, "100"), low(4, "95")])
    assert structure.last_high_label is StructureLabel.EQUAL_HIGH
    assert structure.bias is StructureBias.AMBIGUOUS


@pytest.mark.unit
def test_an_equal_low_leaves_the_bias_ambiguous() -> None:
    structure = label_swings([high(1, "100"), low(2, "90"), high(3, "110"), low(4, "90")])
    assert structure.bias is StructureBias.AMBIGUOUS


@pytest.mark.unit
@pytest.mark.parametrize(
    "swings",
    (
        [],
        [high(1, "100")],
        [high(1, "100"), high(3, "110")],
        [low(1, "90"), low(3, "95")],
        [high(1, "100"), low(2, "90"), high(3, "110")],
    ),
)
def test_fewer_than_two_of_either_type_is_insufficient(swings: list[SwingPoint]) -> None:
    assert label_swings(swings).bias is StructureBias.INSUFFICIENT


# ----------------------------------------------------------------------
# Tolerance
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_default_tolerance_is_exact_equality() -> None:
    """Prices arrive on a tick grid, so equality is a real observable event."""
    structure = label_swings([high(1, "100.00"), high(3, "100.01")])
    assert structure.last_high_label is StructureLabel.HIGHER_HIGH


@pytest.mark.unit
def test_a_configured_tolerance_treats_near_levels_as_equal() -> None:
    config = StructureLabelConfig(equal_tolerance=Decimal("0.05"))
    structure = label_swings([high(1, "100.00"), high(3, "100.01")], config)
    assert structure.last_high_label is StructureLabel.EQUAL_HIGH


@pytest.mark.unit
def test_tolerance_must_not_be_negative() -> None:
    with pytest.raises(ValueError, match="equal_tolerance"):
        StructureLabelConfig(equal_tolerance=Decimal("-1"))


# ----------------------------------------------------------------------
# Alternation
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_alternating_swings_are_reported_as_alternating() -> None:
    structure = label_swings([low(1, "90"), high(2, "100"), low(3, "95"), high(4, "110")])
    assert structure.alternates


@pytest.mark.unit
def test_two_highs_in_a_row_are_still_labelled_but_flagged() -> None:
    """A one-way move can print two highs with no low between them."""
    structure = label_swings([high(1, "100"), high(2, "110"), low(3, "95"), low(4, "90")])
    assert not structure.alternates
    assert structure.last_high_label is StructureLabel.HIGHER_HIGH
    assert structure.last_low_label is StructureLabel.LOWER_LOW


@pytest.mark.unit
def test_labelling_is_reproducible() -> None:
    swings = [low(1, "90"), high(2, "100"), low(3, "95"), high(4, "110")]
    assert label_swings(swings) == label_swings(swings)


@pytest.mark.unit
def test_the_latest_swings_are_exposed_for_the_break_engine() -> None:
    structure = label_swings([high(1, "100"), low(2, "90"), high(3, "110"), low(4, "95")])
    assert structure.last_high is not None and structure.last_high.price == Decimal("110")
    assert structure.last_low is not None and structure.last_low.price == Decimal("95")
