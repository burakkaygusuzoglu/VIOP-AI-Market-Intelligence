"""What a vision pass observed, and how sure it was (§38).

§38's example is one extracted value:

    {"field": "RSI", "value": 63.2, "source": "screenshot", "confidence": 0.74}

This module gives that shape a type, and adds the two distinctions §38's
example leaves implicit.

## Read is not the same as inferred

    Directly visible:  the text "RSI 63.2" is printed on the chart
    Visually inferred: "the trend appears bullish"

Both are legitimate observations and both come from the same image, but they
are different **epistemic categories**. The first can be wrong because the
model misread a digit; the second can be wrong because it formed a judgement.
Collapsing them would let an opinion inherit the credibility of a reading, so
`ObservationKind` keeps them apart and the precedence rules in
``precedence.py`` rank them differently.

## Model-reported confidence is not calibrated probability

A confidence of 0.74 means *the model said 0.74*. Nothing in this project has
compared those numbers against recorded outcomes, so it is not a frequency, not
a win rate, and certainly not a probability of profit. The type is named
`VisionConfidence` rather than `probability` for that reason, and a test
forbids the vocabulary that would blur it.

**Missing confidence stays missing.** A model that omits the field has not
told us it was certain, and has not told us it was guessing. `None` is
preserved rather than defaulted to 0 or 1, both of which would be inventions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.enums import DataSourcePriority, Timeframe
from app.domain.vision.assets import ScreenshotId
from app.domain.vision.slots import ScreenshotSlot


@unique
class ObservationKind(StrEnum):
    """How the observation was arrived at."""

    DIRECTLY_VISIBLE = "DIRECTLY_VISIBLE"
    """Text or a value legible on the chart - a printed indicator readout, an
    axis label, a symbol in the corner."""

    VISUALLY_INFERRED = "VISUALLY_INFERRED"
    """A judgement formed from the picture: the trend looks upward, the
    structure looks like higher highs. Weaker than a reading, and ranked lower
    by `DataSourcePriority`."""

    @property
    def source_priority(self) -> DataSourcePriority:
        """The Phase 0 precedence rank this kind carries.

        Reuses the enum §1 of the master spec already defined rather than
        introducing a parallel ordering - two rankings of the same thing would
        eventually disagree.
        """
        return (
            DataSourcePriority.SCREENSHOT_EXTRACTED
            if self is ObservationKind.DIRECTLY_VISIBLE
            else DataSourcePriority.AI_VISUAL_INFERENCE
        )


@unique
class ObservedField(StrEnum):
    """What a screenshot observation can be about.

    Deliberately a closed set of *chart-visible* things. There is no field for
    a computed indicator series, a position size or a margin requirement:
    vision observes what is drawn, and §16 of this phase forbids it becoming
    the authority for anything calculated.
    """

    SYMBOL = "SYMBOL"
    TIMEFRAME = "TIMEFRAME"
    LAST_PRICE = "LAST_PRICE"
    TREND_CONTEXT = "TREND_CONTEXT"
    MARKET_STRUCTURE = "MARKET_STRUCTURE"
    SUPPORT_LEVEL = "SUPPORT_LEVEL"
    RESISTANCE_LEVEL = "RESISTANCE_LEVEL"
    CANDLESTICK_CONTEXT = "CANDLESTICK_CONTEXT"
    INDICATOR_READING = "INDICATOR_READING"
    VOLUME_CONTEXT = "VOLUME_CONTEXT"
    USER_DRAWN_LEVEL = "USER_DRAWN_LEVEL"


class ConfidenceError(ValueError):
    """A confidence outside the representable range."""


@dataclass(frozen=True, slots=True)
class VisionConfidence:
    """How sure the model reported being, 0-1.

    A `Decimal` because this repository's convention is that a bounded ratio
    with a fixed meaning is exact, and because a float would make two equal
    confidences compare unequal after a round trip through JSON.

    **Not a probability of anything.** See the module docstring.
    """

    value: Decimal

    def __post_init__(self) -> None:
        if not self.value.is_finite():
            raise ConfidenceError(f"confidence must be finite, got {self.value}")
        if not Decimal(0) <= self.value <= Decimal(1):
            raise ConfidenceError(f"confidence must lie in 0-1, got {self.value}")

    @classmethod
    def of(cls, value: Decimal | str | int | float) -> VisionConfidence:
        """Build from whatever a boundary layer parsed.

        ``float`` is accepted because JSON has no decimal type; it is routed
        through ``str`` so 0.74 stays 0.74 rather than becoming
        0.74000000000000000222.
        """
        return cls(value=Decimal(str(value)))

    def at_least(self, threshold: Decimal) -> bool:
        return self.value >= threshold


@dataclass(frozen=True, slots=True)
class ExtractedValue:
    """One thing a vision pass observed, with everything needed to judge it.

    Carries §38's four required attributes - field, value, source, confidence -
    plus the screenshot identity and slot, so an observation can always be tied
    back to the exact image and chart it came from.
    """

    field: ObservedField
    value: str
    """The observation as read, kept as text.

    Deliberately not parsed into a number here. "63.2" and "approximately 63"
    are both things a model can report, and forcing the second into a float
    would invent precision the image never had. A consumer that needs a number
    parses it and handles failure explicitly."""

    kind: ObservationKind
    screenshot_id: ScreenshotId
    slot: ScreenshotSlot
    confidence: VisionConfidence | None = None
    """``None`` means the model did not report one - never 0, never 1."""

    timeframe: Timeframe | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError(f"observation of {self.field.value} carries no value")

    @property
    def source_priority(self) -> DataSourcePriority:
        return self.kind.source_priority

    @property
    def has_confidence(self) -> bool:
        return self.confidence is not None

    @property
    def is_directly_visible(self) -> bool:
        return self.kind is ObservationKind.DIRECTLY_VISIBLE


@dataclass(frozen=True, slots=True)
class UnreadableField:
    """Something the model looked for and could not read.

    Recorded rather than omitted: "the symbol was not legible" is a finding
    about the screenshot, and a silently absent field is indistinguishable
    from one nobody looked for.
    """

    field: ObservedField
    reason: str


@dataclass(frozen=True, slots=True)
class VisionExtraction:
    """Everything one vision pass produced for one screenshot."""

    screenshot_id: ScreenshotId
    slot: ScreenshotSlot
    values: tuple[ExtractedValue, ...] = field(default_factory=tuple)
    unreadable: tuple[UnreadableField, ...] = field(default_factory=tuple)
    observed_at: datetime | None = None
    model: str = ""
    """Which model produced this, for auditing. Empty until an adapter fills
    it in - Phase 6A has no adapter."""

    def __post_init__(self) -> None:
        for value in self.values:
            if value.screenshot_id != self.screenshot_id:
                raise ValueError(
                    f"observation of {value.field.value} cites screenshot "
                    f"{value.screenshot_id} but this extraction is for "
                    f"{self.screenshot_id}"
                )
        if self.observed_at is not None and self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")

    def of_field(self, field_name: ObservedField) -> tuple[ExtractedValue, ...]:
        return tuple(item for item in self.values if item.field is field_name)

    @property
    def directly_visible(self) -> tuple[ExtractedValue, ...]:
        return tuple(item for item in self.values if item.is_directly_visible)

    @property
    def inferred(self) -> tuple[ExtractedValue, ...]:
        return tuple(item for item in self.values if not item.is_directly_visible)

    @property
    def without_confidence(self) -> tuple[ExtractedValue, ...]:
        """Observations the model gave no confidence for.

        Surfaced so a consumer can decide what to do about it, rather than
        discovering a silently defaulted value later.
        """
        return tuple(item for item in self.values if not item.has_confidence)
