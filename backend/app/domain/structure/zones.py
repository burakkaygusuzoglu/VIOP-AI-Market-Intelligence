"""Support and resistance as zones built from prices the market actually paid.

Master spec section 13 asks for zones rather than "unrealistic single exact
values", and there is a specific trap in obeying it. The obvious way to make a
band out of a level is ``level ± k × ATR``. That produces a zone whose edges
are a smoothed float estimate wearing the costume of a price - change the ATR
period and the "support" moves, though nothing in the market did.

So the edges here are not manufactured. A zone spans the **observed swing
prices in its cluster**: ``low = min(prices)``, ``high = max(prices)``. Every
boundary is a price at which the market genuinely turned, kept exact as
``Decimal``. ATR appears only as the clustering *tolerance* - a question of
whether two turns were close enough to be the same level - which is a
comparison, not a level.

A zone therefore needs at least two touches by default. One swing would give a
zone of zero width, which is the single exact value the specification asks us
to avoid.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.market.series import ValidatedCandleSeries
from app.domain.structure.swings import SwingPoint, SwingType
from app.domain.technical.types import IndicatorValues


@unique
class ZoneKind(StrEnum):
    """Which side of price a zone sits on, by construction."""

    SUPPORT = "SUPPORT"
    """Built from swing lows."""

    RESISTANCE = "RESISTANCE"
    """Built from swing highs."""


@dataclass(frozen=True, slots=True)
class ZoneScoreBreakdown:
    """Every component of the strength score, so the number can be argued with.

    Each component is normalised to ``0.0 - 1.0`` before weighting.
    """

    touches: float
    recency: float
    reaction: float
    volume: float

    def total(self, weights: ZoneWeights) -> float:
        return (
            self.touches * weights.touches
            + self.recency * weights.recency
            + self.reaction * weights.reaction
            + self.volume * weights.volume
        )


@dataclass(frozen=True, slots=True)
class ZoneWeights:
    """Relative importance of each score component. Must sum to 1."""

    touches: float = 0.35
    recency: float = 0.25
    reaction: float = 0.25
    volume: float = 0.15

    def __post_init__(self) -> None:
        total = self.touches + self.recency + self.reaction + self.volume
        if not math.isclose(total, 1.0, rel_tol=1e-9):
            raise ValueError(f"zone weights must sum to 1.0, got {total}")


@dataclass(frozen=True, slots=True)
class Zone:
    """A price band the market has turned at more than once.

    ``strength`` is a **heuristic quality score from 0 to 100**. It is not a
    probability, it is not calibrated against outcomes, and it must never be
    presented as a likelihood that the zone will hold. Master spec section 19
    reserves probability language for measured empirical frequencies, which do
    not exist until the backtest phase.
    """

    kind: ZoneKind
    low: Decimal
    high: Decimal
    touches: tuple[SwingPoint, ...]
    strength: float
    breakdown: ZoneScoreBreakdown
    first_touch_index: int
    last_touch_index: int
    last_touch_time: datetime
    confirmed_index: int
    """When the zone became knowable: the confirmation of its latest swing."""

    @property
    def width(self) -> Decimal:
        return self.high - self.low

    @property
    def midpoint(self) -> Decimal:
        return (self.low + self.high) / 2

    @property
    def touch_count(self) -> int:
        return len(self.touches)

    def contains(self, price: Decimal) -> bool:
        """Inclusive on both edges - an exact touch of the edge is a touch."""
        return self.low <= price <= self.high

    def known_at(self, index: int) -> bool:
        return self.confirmed_index <= index


@dataclass(frozen=True, slots=True)
class ZoneConfig:
    """How swings are grouped into zones and scored.

    Every threshold is a **project heuristic**, configurable and documented.
    None of them describes VIOP, a contract or a session, so none is a section
    118 exchange fact.
    """

    cluster_atr_multiple: float = 0.5
    """Two swings belong to the same zone when their prices differ by less than
    this multiple of ATR. Half an ATR is roughly "within the same candle's
    normal range", which is the intuition a chart reader applies."""

    min_touches: int = 2
    """Below two, a zone would have zero width - the single exact value the
    specification tells us to avoid. Configurable for callers who accept
    that."""

    max_touches_for_score: int = 5
    """Touch count saturates here. A level tested twenty times is not ten times
    stronger than one tested twice; past a handful it mostly means the level is
    old."""

    recency_halflife: float = 50.0
    """Candles over which the recency component decays by half."""

    reaction_atr_target: float = 2.0
    """Move away from the zone, in ATR, that scores a full reaction mark."""

    weights: ZoneWeights = ZoneWeights()

    def __post_init__(self) -> None:
        if self.cluster_atr_multiple <= 0:
            raise ValueError("cluster_atr_multiple must be positive")
        if self.min_touches < 1:
            raise ValueError("min_touches must be >= 1")
        if self.recency_halflife <= 0:
            raise ValueError("recency_halflife must be positive")


def build_zones(
    series: ValidatedCandleSeries,
    swings: Sequence[SwingPoint],
    atr: IndicatorValues,
    relative_volume: IndicatorValues,
    config: ZoneConfig | None = None,
    as_of: int | None = None,
) -> tuple[tuple[Zone, ...], tuple[Zone, ...]]:
    """Cluster confirmed swings into support and resistance zones.

    Returns ``(support_zones, resistance_zones)``, each ordered by price.

    ``as_of`` is the candle the zones describe, defaulting to the last one.
    **It is what keeps zone construction free of look-ahead**, and it exists
    because the first version of this function did not have it. Zones were
    built from every swing in the series and scored against the final candle,
    so a zone that a breakout at candle 40 was measured against had boundaries
    determined partly by swings that confirmed at candle 200. The breach could
    not have been detected against that band in real time. Passing ``as_of``
    restricts the swings, the reference ATR and the recency measurement to
    information available at that candle, so a zone built for candle 40 is the
    same object whether the series stops at 140 or runs to 220.

    Swings are filtered by ``confirmed_index <= as_of``; the reference ATR is
    the last one defined at or before ``as_of``.

    Clustering is complete linkage over price-sorted swings - see
    ``_zones_from`` for why single linkage was wrong.
    """
    settings = config if config is not None else ZoneConfig()
    if not swings or len(series) == 0:
        return (), ()

    limit = len(series) - 1 if as_of is None else min(as_of, len(series) - 1)
    if limit < 0:
        return (), ()

    visible = [swing for swing in swings if swing.confirmed_index <= limit]
    if not visible:
        return (), ()

    reference_atr = _last_defined(atr, limit)
    if reference_atr is None or reference_atr <= 0:
        # Without a volatility scale there is no defensible clustering
        # tolerance. Returning nothing is better than inventing one.
        return (), ()

    highs = [swing for swing in visible if swing.swing_type is SwingType.HIGH]
    lows = [swing for swing in visible if swing.swing_type is SwingType.LOW]

    resistance = _zones_from(
        lows_or_highs=highs,
        kind=ZoneKind.RESISTANCE,
        series=series,
        atr=reference_atr,
        relative_volume=relative_volume,
        settings=settings,
        as_of=limit,
    )
    support = _zones_from(
        lows_or_highs=lows,
        kind=ZoneKind.SUPPORT,
        series=series,
        atr=reference_atr,
        relative_volume=relative_volume,
        settings=settings,
        as_of=limit,
    )
    return support, resistance


def _zones_from(
    *,
    lows_or_highs: Sequence[SwingPoint],
    kind: ZoneKind,
    series: ValidatedCandleSeries,
    atr: float,
    relative_volume: IndicatorValues,
    settings: ZoneConfig,
    as_of: int,
) -> tuple[Zone, ...]:
    if not lows_or_highs:
        return ()

    ordered = sorted(lows_or_highs, key=lambda swing: (swing.price, swing.pivot_index))
    tolerance = atr * settings.cluster_atr_multiple

    # Complete linkage, not single linkage: a swing joins a cluster only while
    # the cluster's *total* spread stays inside the tolerance.
    #
    # Single linkage - comparing each swing with its nearest neighbour - chains.
    # In a steady trend every consecutive high sits within half an ATR of the
    # one before it, so the whole advance merges into a single "zone" spanning
    # the entire move. That was the first behaviour this function had, and a
    # 300-candle uptrend produced one resistance zone 14% of price wide with 25
    # touches: an impressive-looking object that describes nothing. Bounding the
    # spread makes the width mean what the name implies - the prices at which
    # the market repeatedly turned, not the range it travelled.
    clusters: list[list[SwingPoint]] = [[ordered[0]]]
    for swing in ordered[1:]:
        spread = float(swing.price - clusters[-1][0].price)
        if spread <= tolerance:
            clusters[-1].append(swing)
        else:
            clusters.append([swing])

    zones = [
        _build_zone(cluster, kind, series, atr, relative_volume, settings, as_of)
        for cluster in clusters
        if len(cluster) >= settings.min_touches
    ]
    return tuple(sorted(zones, key=lambda zone: zone.low))


def _build_zone(
    cluster: Sequence[SwingPoint],
    kind: ZoneKind,
    series: ValidatedCandleSeries,
    atr: float,
    relative_volume: IndicatorValues,
    settings: ZoneConfig,
    as_of: int,
) -> Zone:
    prices = [swing.price for swing in cluster]
    pivot_indices = [swing.pivot_index for swing in cluster]

    touches_score = min(len(cluster), settings.max_touches_for_score) / (
        settings.max_touches_for_score
    )
    age = as_of - max(pivot_indices)
    recency_score = 0.5 ** (age / settings.recency_halflife)
    reaction_score = _reaction_score(cluster, kind, series, atr, settings, as_of)
    volume_score = _volume_score(cluster, relative_volume)

    breakdown = ZoneScoreBreakdown(
        touches=touches_score,
        recency=recency_score,
        reaction=reaction_score,
        volume=volume_score,
    )

    return Zone(
        kind=kind,
        low=min(prices),
        high=max(prices),
        touches=tuple(sorted(cluster, key=lambda swing: swing.pivot_index)),
        strength=100.0 * breakdown.total(settings.weights),
        breakdown=breakdown,
        first_touch_index=min(pivot_indices),
        last_touch_index=max(pivot_indices),
        last_touch_time=series.open_times[max(pivot_indices)],
        confirmed_index=max(swing.confirmed_index for swing in cluster),
    )


def _reaction_score(
    cluster: Sequence[SwingPoint],
    kind: ZoneKind,
    series: ValidatedCandleSeries,
    atr: float,
    settings: ZoneConfig,
    as_of: int,
) -> float:
    """How far price travelled away from the zone after each touch, in ATR.

    Measured only over candles at or before ``as_of``, so it cannot see past
    the moment the zone is being described.
    """
    if atr <= 0:
        return 0.0

    reactions: list[float] = []
    closes = series.closes
    for swing in cluster:
        window_end = min(as_of, swing.confirmed_index)
        if window_end <= swing.pivot_index:
            continue
        segment = closes[swing.pivot_index : window_end + 1]
        if kind is ZoneKind.RESISTANCE:
            travel = float(swing.price - min(segment))
        else:
            travel = float(max(segment) - swing.price)
        reactions.append(max(travel, 0.0) / atr)

    if not reactions:
        return 0.0
    average = sum(reactions) / len(reactions)
    return min(average / settings.reaction_atr_target, 1.0)


def _volume_score(cluster: Sequence[SwingPoint], relative_volume: IndicatorValues) -> float:
    """Phase 1 relative volume at the touch candles, saturating at 2x average.

    Reuses the Phase 1 calculation rather than recomputing a volume average -
    there is exactly one implementation of that formula in the codebase.
    """
    values = [
        relative_volume[swing.pivot_index]
        for swing in cluster
        if swing.pivot_index < len(relative_volume)
        and relative_volume[swing.pivot_index] is not None
    ]
    if not values:
        return 0.0
    average = sum(value for value in values if value is not None) / len(values)
    return min(average / 2.0, 1.0)


def _last_defined(values: IndicatorValues, as_of: int) -> float | None:
    """The most recent defined value at or before ``as_of``."""
    for index in range(min(as_of, len(values) - 1), -1, -1):
        value = values[index]
        if value is not None:
            return value
    return None
