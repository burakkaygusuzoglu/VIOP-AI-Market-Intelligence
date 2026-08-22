"""Volume divergence — the Phase 1 deferral that confirmed swings unblock.

Phase 1 recorded this as NOT STARTED because it "needs swing highs and lows to
compare price and volume against". Those now exist, and only they are used: the
comparison is between two **confirmed** swings of the same type, never between
arbitrary candles. Divergence read off raw candle-to-candle noise is a
different, much weaker measure that happens to share the name.

The semantics, stated in full because "divergence" alone determines nothing:

**Bearish divergence** - price made a higher high, participation did not
follow. The later swing high is above the earlier one while the volume measure
at the later pivot is *lower*.

**Bullish divergence** - price made a lower low on lighter volume. The later
swing low is below the earlier one while volume at the later pivot is lower.

Rising volume into a new extreme is not divergence; it is confirmation, and it
produces no event.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.structure.market_structure import StructureLabelConfig
from app.domain.structure.swings import SwingPoint, SwingType
from app.domain.technical.types import IndicatorValues


@unique
class DivergenceType(StrEnum):
    """Direction of the disagreement between price and participation."""

    BEARISH = "BEARISH"
    """Higher high on lower volume."""

    BULLISH = "BULLISH"
    """Lower low on lower volume."""


@dataclass(frozen=True, slots=True)
class DivergenceEvent:
    """One price/volume disagreement between two confirmed swings."""

    divergence_type: DivergenceType
    earlier_swing: SwingPoint
    later_swing: SwingPoint
    earlier_volume: float
    later_volume: float
    confirmed_index: int
    """The later swing's confirmation. A divergence is knowable exactly when
    the second of its two pivots is - not when that pivot printed."""

    confirmed_time: datetime
    reason: str

    @property
    def price_change(self) -> Decimal:
        return self.later_swing.price - self.earlier_swing.price

    @property
    def volume_change(self) -> float:
        return self.later_volume - self.earlier_volume

    def known_at(self, index: int) -> bool:
        return self.confirmed_index <= index


@dataclass(frozen=True, slots=True)
class DivergenceConfig:
    """Comparison rules.

    ``volume_tolerance`` is a *relative* band: volumes within this fraction of
    each other count as unchanged, so a 0.1% difference does not become a
    signal. Prices use the same absolute ``equal_tolerance`` as the structure
    labels, so "higher high" means the same thing in both engines rather than
    two subtly different things.
    """

    volume_tolerance: float = 0.05
    label_config: StructureLabelConfig = StructureLabelConfig()

    def __post_init__(self) -> None:
        if self.volume_tolerance < 0:
            raise ValueError(f"volume_tolerance must be >= 0, got {self.volume_tolerance}")


def detect_volume_divergence(
    swings: Sequence[SwingPoint],
    volume_measure: IndicatorValues,
    config: DivergenceConfig | None = None,
) -> tuple[DivergenceEvent, ...]:
    """Compare consecutive confirmed swings of the same type.

    ``volume_measure`` is a Phase 1 indicator series aligned with the candles -
    ``volume_moving_average`` in the assembled engine. The smoothed average is
    used rather than the raw volume of the single pivot candle: one candle's
    volume is noisy enough that the comparison would flip on a single large
    print, while the 20-candle average describes the participation *behind* the
    move that made the swing.

    **Insufficient history** is reported by absence. When either pivot falls in
    the volume measure's warm-up, no event is emitted for that pair - the
    comparison was not possible, and a zero would claim it was.
    """
    settings = config if config is not None else DivergenceConfig()
    tolerance = settings.label_config.equal_tolerance

    events: list[DivergenceEvent] = []
    for swing_type in (SwingType.HIGH, SwingType.LOW):
        ordered = sorted(
            (swing for swing in swings if swing.swing_type is swing_type),
            key=lambda swing: swing.pivot_index,
        )
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            event = _compare(earlier, later, volume_measure, tolerance, settings)
            if event is not None:
                events.append(event)

    return tuple(sorted(events, key=lambda event: event.confirmed_index))


def _compare(
    earlier: SwingPoint,
    later: SwingPoint,
    volume_measure: IndicatorValues,
    price_tolerance: Decimal,
    settings: DivergenceConfig,
) -> DivergenceEvent | None:
    earlier_volume = _measure_at(volume_measure, earlier.pivot_index)
    later_volume = _measure_at(volume_measure, later.pivot_index)
    if earlier_volume is None or later_volume is None or earlier_volume <= 0:
        return None

    price_delta = later.price - earlier.price
    if abs(price_delta) <= price_tolerance:
        return None

    relative_volume_change = (later_volume - earlier_volume) / earlier_volume
    if relative_volume_change >= -settings.volume_tolerance:
        return None  # volume held up or grew: confirmation, not divergence

    if later.swing_type is SwingType.HIGH and price_delta > 0:
        kind = DivergenceType.BEARISH
        headline = "higher high on lower volume"
    elif later.swing_type is SwingType.LOW and price_delta < 0:
        kind = DivergenceType.BULLISH
        headline = "lower low on lower volume"
    else:
        return None

    return DivergenceEvent(
        divergence_type=kind,
        earlier_swing=earlier,
        later_swing=later,
        earlier_volume=earlier_volume,
        later_volume=later_volume,
        confirmed_index=later.confirmed_index,
        confirmed_time=later.confirmed_time,
        reason=(
            f"{headline}: price {earlier.price} -> {later.price}, "
            f"volume {earlier_volume:.1f} -> {later_volume:.1f} "
            f"({relative_volume_change:+.1%})"
        ),
    )


def _measure_at(values: IndicatorValues, index: int) -> float | None:
    if index >= len(values):
        return None
    return values[index]
