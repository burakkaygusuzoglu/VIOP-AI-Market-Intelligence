"""Higher highs, lower lows, and the bias they do or do not imply.

Each confirmed swing is compared with the previous confirmed swing **of the
same type**: highs against highs, lows against lows. That is the only
comparison that means anything - a swing high is not "higher" or "lower" than a
swing low, it is simply a different kind of point.

The output is deliberately allowed to be undecided. A market that has printed a
higher high and a lower low has expanded, not turned bullish; a market whose
last two highs are equal has said nothing. Forcing those into BULLISH or
BEARISH would manufacture confidence the price action does not support, and
master spec section 2 forbids exactly that.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.structure.swings import SwingPoint, SwingType


@unique
class StructureLabel(StrEnum):
    """How a swing compares with the previous swing of its own type."""

    HIGHER_HIGH = "HH"
    LOWER_HIGH = "LH"
    EQUAL_HIGH = "EQH"
    HIGHER_LOW = "HL"
    LOWER_LOW = "LL"
    EQUAL_LOW = "EQL"
    FIRST_HIGH = "FIRST_HIGH"
    """No earlier high to compare against. Not a judgement, an absence."""
    FIRST_LOW = "FIRST_LOW"


@unique
class StructureBias(StrEnum):
    """What the most recent high/low pair implies, including "nothing"."""

    BULLISH = "BULLISH"
    """Higher high and higher low."""

    BEARISH = "BEARISH"
    """Lower high and lower low."""

    CONTRACTING = "CONTRACTING"
    """Lower high and higher low - the range is narrowing, direction unknown."""

    EXPANDING = "EXPANDING"
    """Higher high and lower low - both sides extended, whipsaw."""

    AMBIGUOUS = "AMBIGUOUS"
    """An equal high or equal low leaves the comparison undecided."""

    INSUFFICIENT = "INSUFFICIENT"
    """Fewer than two highs or two lows have been confirmed."""

    @property
    def is_directional(self) -> bool:
        return self in (StructureBias.BULLISH, StructureBias.BEARISH)


@dataclass(frozen=True, slots=True)
class LabelledSwing:
    """A confirmed swing together with its comparison to the previous one."""

    swing: SwingPoint
    label: StructureLabel
    previous: SwingPoint | None

    @property
    def confirmed_index(self) -> int:
        return self.swing.confirmed_index


@dataclass(frozen=True, slots=True)
class MarketStructure:
    """The labelled swing sequence and the bias it supports."""

    labelled: tuple[LabelledSwing, ...]
    bias: StructureBias
    last_high: SwingPoint | None
    last_low: SwingPoint | None
    last_high_label: StructureLabel | None
    last_low_label: StructureLabel | None
    alternates: bool
    """False when two swings of the same type were confirmed in a row.

    Detection does not guarantee that highs and lows alternate - a strong
    one-way move can print two highs with no low between them. Labelling still
    works, because each swing is compared with the previous of its own type,
    but downstream readers are told, because a non-alternating sequence is
    weaker evidence of a clean structure.
    """

    @property
    def highs(self) -> tuple[LabelledSwing, ...]:
        return tuple(item for item in self.labelled if item.swing.swing_type is SwingType.HIGH)

    @property
    def lows(self) -> tuple[LabelledSwing, ...]:
        return tuple(item for item in self.labelled if item.swing.swing_type is SwingType.LOW)


@dataclass(frozen=True, slots=True)
class StructureLabelConfig:
    """When two levels count as equal.

    ``equal_tolerance`` is an absolute price distance. The default is exactly
    zero, which means only genuinely identical prices are called equal.

    That is the honest default rather than a cautious one. Exchange prices
    arrive on a tick grid as exact ``Decimal`` values, so equality is a real,
    observable event - a double top that retests the same tick. Any non-zero
    default would be a guess about one instrument's tick size and typical
    noise, which is precisely the kind of assumption master spec section 118
    exists to prevent. Callers who want slack set it explicitly, and both
    settings are tested.
    """

    equal_tolerance: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.equal_tolerance < 0:
            raise ValueError(f"equal_tolerance must be >= 0, got {self.equal_tolerance}")


def label_swings(
    swings: Sequence[SwingPoint],
    config: StructureLabelConfig | None = None,
) -> MarketStructure:
    """Label a confirmed swing sequence and derive its bias.

    ``swings`` must already be filtered to what was confirmed at the moment of
    interest - see ``swings_known_at``. Nothing here re-checks confirmation,
    because the caller is the only one who knows which moment it is asking
    about.

    **Bias truth table**, from the most recent high label and low label:

    ===============  ==============  =============
    last high        last low        bias
    ===============  ==============  =============
    HH               HL              BULLISH
    LH               LL              BEARISH
    LH               HL              CONTRACTING
    HH               LL              EXPANDING
    EQH or EQL       any             AMBIGUOUS
    fewer than 2 of either           INSUFFICIENT
    ===============  ==============  =============

    Every remaining combination is AMBIGUOUS. CONTRACTING and EXPANDING are
    kept distinct from AMBIGUOUS because they are informative - one is a
    narrowing range, the other a widening one - while AMBIGUOUS genuinely means
    the comparison did not resolve.
    """
    settings = config if config is not None else StructureLabelConfig()

    labelled: list[LabelledSwing] = []
    previous_high: SwingPoint | None = None
    previous_low: SwingPoint | None = None
    alternates = True
    previous_type: SwingType | None = None

    for swing in sorted(swings, key=lambda item: (item.pivot_index, item.swing_type)):
        if previous_type is not None and swing.swing_type is previous_type:
            alternates = False
        previous_type = swing.swing_type

        if swing.swing_type is SwingType.HIGH:
            label = _label_high(swing.price, previous_high, settings.equal_tolerance)
            labelled.append(LabelledSwing(swing=swing, label=label, previous=previous_high))
            previous_high = swing
        else:
            label = _label_low(swing.price, previous_low, settings.equal_tolerance)
            labelled.append(LabelledSwing(swing=swing, label=label, previous=previous_low))
            previous_low = swing

    highs = [item for item in labelled if item.swing.swing_type is SwingType.HIGH]
    lows = [item for item in labelled if item.swing.swing_type is SwingType.LOW]

    last_high_label = highs[-1].label if highs else None
    last_low_label = lows[-1].label if lows else None

    return MarketStructure(
        labelled=tuple(labelled),
        bias=_derive_bias(last_high_label, last_low_label),
        last_high=highs[-1].swing if highs else None,
        last_low=lows[-1].swing if lows else None,
        last_high_label=last_high_label,
        last_low_label=last_low_label,
        alternates=alternates,
    )


def _label_high(price: Decimal, previous: SwingPoint | None, tolerance: Decimal) -> StructureLabel:
    if previous is None:
        return StructureLabel.FIRST_HIGH
    if abs(price - previous.price) <= tolerance:
        return StructureLabel.EQUAL_HIGH
    return StructureLabel.HIGHER_HIGH if price > previous.price else StructureLabel.LOWER_HIGH


def _label_low(price: Decimal, previous: SwingPoint | None, tolerance: Decimal) -> StructureLabel:
    if previous is None:
        return StructureLabel.FIRST_LOW
    if abs(price - previous.price) <= tolerance:
        return StructureLabel.EQUAL_LOW
    return StructureLabel.HIGHER_LOW if price > previous.price else StructureLabel.LOWER_LOW


def _derive_bias(
    high_label: StructureLabel | None, low_label: StructureLabel | None
) -> StructureBias:
    first = (StructureLabel.FIRST_HIGH, StructureLabel.FIRST_LOW)
    if high_label is None or low_label is None or high_label in first or low_label in first:
        return StructureBias.INSUFFICIENT
    if high_label is StructureLabel.EQUAL_HIGH or low_label is StructureLabel.EQUAL_LOW:
        return StructureBias.AMBIGUOUS

    match (high_label, low_label):
        case (StructureLabel.HIGHER_HIGH, StructureLabel.HIGHER_LOW):
            return StructureBias.BULLISH
        case (StructureLabel.LOWER_HIGH, StructureLabel.LOWER_LOW):
            return StructureBias.BEARISH
        case (StructureLabel.LOWER_HIGH, StructureLabel.HIGHER_LOW):
            return StructureBias.CONTRACTING
        case (StructureLabel.HIGHER_HIGH, StructureLabel.LOWER_LOW):
            return StructureBias.EXPANDING
        case _:
            return StructureBias.AMBIGUOUS
