"""Structural breaks: BOS, CHOCH, and the honest third case.

All three are the same physical event - price closing beyond a confirmed swing
- and they differ only in what the structure was doing beforehand:

**BOS** continues a *directional* structure. Price was making higher highs and
higher lows, and it takes out the last swing high. The trend did what it was
already doing.

**CHOCH** contradicts a *directional* structure. Price was making lower highs
and lower lows, and then takes out the last swing high. That is the first
structural evidence that the downtrend may be over.

**LEVEL_BREAK** is what happens when there was no direction to speak of. A
confirmed level was taken out while the structure was contracting, expanding,
ambiguous or not yet established. Nothing was continued and nothing was
reversed, so neither of the other names is true.

That third case is not a formality. An earlier version of this module labelled
those breaks BOS, on the reasoning that "a level was broken" is a fact while "the
character changed" is a claim. The first half of that is right and the second
half does not follow: *Break of Structure* asserts both that a structure existed
and that this break continued it. Applying it to an undecided market writes a
trend into the record that the swings never showed - and section 16 evidence
fusion will read these labels as evidence rather than re-deriving them from
``prior_bias``. Leaving the disambiguation to a second field that every future
consumer must remember to check is the same trap ``ValidatedCandleSeries``
exists to avoid.

The distinction is therefore *not* a property of the break; it is a property of
the break plus the bias that preceded it. Both are recorded on the event, so a
reader can check the classification rather than trust it.

**Neither is a buy or sell signal.** A BOS says the market broke a level, which
is a fact about structure. Whether that is tradeable depends on risk, location,
regime and confirmation - none of which exist in this phase, and some of which
are deliberately several phases away.

The detector walks candles forward, and at each candle asks only what was
confirmed by that candle. It cannot see its own future.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.enums import Direction
from app.domain.market.series import ValidatedCandleSeries
from app.domain.structure.market_structure import (
    StructureBias,
    StructureLabelConfig,
    label_swings,
)
from app.domain.structure.swings import SwingPoint, SwingType, swings_known_at


@unique
class StructuralEventType(StrEnum):
    """A structural fact, never a trade instruction."""

    BOS = "BOS"
    """Break of Structure - a *directional* structure continued.

    Only ever emitted when the prior bias was BULLISH or BEARISH and the break
    ran the same way."""

    CHOCH = "CHOCH"
    """Change of Character - a *directional* structure was contradicted.

    Only ever emitted when the prior bias was BULLISH or BEARISH and the break
    ran against it."""

    LEVEL_BREAK = "LEVEL_BREAK"
    """A confirmed swing level was taken out, and that is all that is known.

    Emitted when the prior structure had no direction - CONTRACTING,
    EXPANDING, AMBIGUOUS or INSUFFICIENT. There was nothing to continue and
    nothing to reverse, so neither BOS nor CHOCH can be said honestly.

    This third state exists because the alternative is worse. Both other names
    presuppose an established structure: BOS asserts one existed *and* that
    this break continued it. Applying it to an undecided market smuggles a
    trend into the record, and section 16 evidence fusion will later read these
    labels as evidence rather than re-deriving them from ``prior_bias``. The
    structure layer already models honest uncertainty with
    ``StructureBias.AMBIGUOUS`` and ``INSUFFICIENT``; the event layer mirrors
    it rather than collapsing it."""

    @property
    def is_classified(self) -> bool:
        """True when the break could be read as continuation or reversal."""
        return self in (StructuralEventType.BOS, StructuralEventType.CHOCH)


@unique
class BreakConfirmation(StrEnum):
    """What counts as having broken a level."""

    CLOSE = "CLOSE"
    """The candle must close beyond the level. The default.

    A wick through a level is a test, not a break: it is exactly what a stop
    run looks like, and treating it as structure produces a stream of events
    that reverse a candle later."""

    WICK = "WICK"
    """The candle's high or low reaching beyond the level is enough.

    Earlier and noisier. Offered because some analysis genuinely wants to know
    a level was touched, but never the default."""


@dataclass(frozen=True, slots=True)
class StructuralEvent:
    """A BOS or CHOCH, carrying the evidence for its own classification."""

    event_type: StructuralEventType
    direction: Direction
    broken_level: Decimal
    origin_swing: SwingPoint
    """The swing whose price was taken out - including when *it* was confirmed."""

    event_index: int
    event_time: datetime
    confirmed_index: int
    confirmed_time: datetime
    prior_bias: StructureBias
    """The structure immediately before the break. This is what decides BOS
    versus CHOCH, so it is recorded rather than recomputed."""

    confirmation: BreakConfirmation
    breach_price: Decimal
    """The close (or wick extreme) that broke the level."""

    reason: str

    def known_at(self, index: int) -> bool:
        return self.confirmed_index <= index


@dataclass(frozen=True, slots=True)
class StructuralEventConfig:
    """Break rules.

    ``breach_tolerance`` is an absolute price distance the break must exceed,
    on top of simply passing the level. Zero - the default - means a strict
    inequality: any close beyond the level counts.

    It is deliberately *not* expressed in ATR multiples. An ATR-scaled
    threshold would make the level itself depend on a smoothed float estimate,
    so the same candle could break a level on one run and not on another after
    a change to the ATR period. Keeping it an absolute ``Decimal`` keeps a
    structural level exact and the decision reproducible.
    """

    confirmation: BreakConfirmation = BreakConfirmation.CLOSE
    breach_tolerance: Decimal = Decimal("0")
    label_config: StructureLabelConfig | None = None

    def __post_init__(self) -> None:
        if self.breach_tolerance < 0:
            raise ValueError(f"breach_tolerance must be >= 0, got {self.breach_tolerance}")


def detect_structural_events(
    series: ValidatedCandleSeries,
    swings: Sequence[SwingPoint],
    config: StructuralEventConfig | None = None,
) -> tuple[StructuralEvent, ...]:
    """Walk the series forward, emitting BOS and CHOCH as they become knowable.

    At each candle the detector rebuilds the structure from **only** the swings
    confirmed by that candle, then asks whether this candle broke the most
    recent confirmed swing high or low. Because the bias is recomputed from
    that restricted set, a swing that confirms later cannot retroactively
    change how an earlier break was classified.

    **Classification.** A break upward through the last confirmed swing high is
    a BOS when the prior bias was BULLISH, and a CHOCH when it was BEARISH.
    Downward breaks mirror this. When the prior bias is neither - CONTRACTING,
    EXPANDING, AMBIGUOUS or INSUFFICIENT - the break is recorded as a
    **LEVEL_BREAK**: the level was taken out, and nothing beyond that is
    claimed. BOS and CHOCH are therefore emitted *only* from a directional
    prior bias, which ``test_a_classified_break_always_had_a_directional_bias``
    pins as an invariant over every event the engine can produce.

    **Invalidation.** A swing is consumed by the break that takes it out and
    never fires again. Without this, a trend that holds above a broken high
    would emit an identical BOS on every subsequent candle. A new event needs a
    new confirmed swing, which is what actually constitutes new structure.

    **Timestamps.** ``event_index`` and ``confirmed_index`` are equal here, and
    that is not an oversight: the engine consumes closed candles only, so the
    candle that closes beyond a level both causes the break and settles it.
    Both fields exist because the retest and breakout engines have genuine lag
    between the two, and a caller reading events from several engines should
    not have to remember which ones coincide.
    """
    settings = config if config is not None else StructuralEventConfig()
    label_config = settings.label_config or StructureLabelConfig()
    total = len(series)
    if total == 0 or not swings:
        return ()

    highs = series.highs
    lows = series.lows
    closes = series.closes
    times = series.open_times

    events: list[StructuralEvent] = []
    broken_high_pivots: set[int] = set()
    broken_low_pivots: set[int] = set()

    for index in range(total):
        known = swings_known_at(swings, index)
        if not known:
            continue
        structure = label_swings(known, label_config)

        upper = _latest_unbroken(known, SwingType.HIGH, broken_high_pivots)
        if upper is not None:
            wick = settings.confirmation is BreakConfirmation.WICK
            probe = highs[index] if wick else closes[index]
            if probe > upper.price + settings.breach_tolerance:
                broken_high_pivots.add(upper.pivot_index)
                events.append(
                    _build_event(
                        direction=Direction.LONG,
                        origin=upper,
                        prior_bias=structure.bias,
                        index=index,
                        time=times[index],
                        breach_price=probe,
                        settings=settings,
                    )
                )

        lower = _latest_unbroken(known, SwingType.LOW, broken_low_pivots)
        if lower is not None:
            wick = settings.confirmation is BreakConfirmation.WICK
            probe = lows[index] if wick else closes[index]
            if probe < lower.price - settings.breach_tolerance:
                broken_low_pivots.add(lower.pivot_index)
                events.append(
                    _build_event(
                        direction=Direction.SHORT,
                        origin=lower,
                        prior_bias=structure.bias,
                        index=index,
                        time=times[index],
                        breach_price=probe,
                        settings=settings,
                    )
                )

    return tuple(events)


def _latest_unbroken(
    known: Sequence[SwingPoint], swing_type: SwingType, consumed: set[int]
) -> SwingPoint | None:
    latest: SwingPoint | None = None
    for swing in known:
        if swing.swing_type is not swing_type or swing.pivot_index in consumed:
            continue
        if latest is None or swing.pivot_index > latest.pivot_index:
            latest = swing
    return latest


def _build_event(
    *,
    direction: Direction,
    origin: SwingPoint,
    prior_bias: StructureBias,
    index: int,
    time: datetime,
    breach_price: Decimal,
    settings: StructuralEventConfig,
) -> StructuralEvent:
    bullish = direction is Direction.LONG
    side = "high" if bullish else "low"
    headline = f"close {breach_price} broke the {side} at {origin.price}"

    if not prior_bias.is_directional:
        # Nothing to continue and nothing to reverse. Naming it either way
        # would assert a structure the swings never established.
        event_type = StructuralEventType.LEVEL_BREAK
        reason = (
            f"{headline} while the structure was {prior_bias.value}; "
            "neither a continuation nor a reversal can be claimed"
        )
    elif (bullish and prior_bias is StructureBias.BEARISH) or (
        not bullish and prior_bias is StructureBias.BULLISH
    ):
        event_type = StructuralEventType.CHOCH
        reason = f"{headline} against a {prior_bias.value} structure"
    else:
        event_type = StructuralEventType.BOS
        reason = f"{headline} continuing a {prior_bias.value} structure"

    return StructuralEvent(
        event_type=event_type,
        direction=direction,
        broken_level=origin.price,
        origin_swing=origin,
        event_index=index,
        event_time=time,
        confirmed_index=index,
        confirmed_time=time,
        prior_bias=prior_bias,
        confirmation=settings.confirmation,
        breach_price=breach_price,
        reason=reason,
    )
