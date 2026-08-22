"""Support and resistance zones: clustering, exact edges, heuristic scoring.

Indicator inputs are supplied explicitly rather than computed, so each test
controls the ATR the clustering sees. Prices are TEST_FIXTURE data.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.market.series import ValidatedCandleSeries
from app.domain.structure.swings import SwingConfig, detect_swings
from app.domain.structure.zones import (
    Zone,
    ZoneConfig,
    ZoneKind,
    ZoneWeights,
    build_zones,
)
from tests.factories import ohlcv_series, pivot_series

WINDOW = SwingConfig(left=1, right=1)


def _flat(value: float | None, size: int) -> tuple[float | None, ...]:
    return (value,) * size


def _zones(
    series: ValidatedCandleSeries,
    atr: float = 1.0,
    relative: float = 1.0,
    config: ZoneConfig | None = None,
) -> tuple[tuple[Zone, ...], tuple[Zone, ...]]:
    swings = detect_swings(series, WINDOW)
    return build_zones(
        series,
        swings,
        _flat(atr, len(series)),
        _flat(relative, len(series)),
        config,
    )


@pytest.mark.unit
def test_repeated_turns_at_one_level_form_a_zone() -> None:
    highs = [100.0, 120.0, 100.0, 120.0, 100.0, 120.0, 100.0]
    series = pivot_series(highs)
    _, resistance = _zones(series, atr=2.0)
    assert len(resistance) == 1
    assert resistance[0].kind is ZoneKind.RESISTANCE
    assert resistance[0].touch_count >= 2


@pytest.mark.unit
def test_zone_edges_are_prices_the_market_actually_paid() -> None:
    """Not level +/- k*ATR: every boundary is an observed swing price."""
    highs = [100.0, 120.0, 100.0, 120.5, 100.0]
    series = pivot_series(highs)
    _, resistance = _zones(series, atr=5.0)
    zone = resistance[0]
    assert zone.low == Decimal("120.0")
    assert zone.high == Decimal("120.5")
    assert {swing.price for swing in zone.touches} == {Decimal("120.0"), Decimal("120.5")}


@pytest.mark.unit
def test_zone_edges_stay_exact_decimals() -> None:
    series = pivot_series([100.0, 120.25, 100.0, 120.75, 100.0])
    _, resistance = _zones(series, atr=5.0)
    zone = resistance[0]
    assert isinstance(zone.low, Decimal)
    assert zone.width == Decimal("0.50")


@pytest.mark.unit
def test_swing_lows_build_support_and_highs_build_resistance() -> None:
    # Peaks at odd indices, troughs at even ones, so each side gets two touches.
    highs = [110.0, 130.0, 110.0, 130.0, 110.0, 130.0, 110.0]
    lows = [100.0, 120.0, 100.0, 120.0, 100.0, 120.0, 100.0]
    closes = [105.0, 125.0, 105.0, 125.0, 105.0, 125.0, 105.0]
    series = ohlcv_series(highs, lows, closes)
    support, resistance = _zones(series, atr=3.0)
    assert all(zone.kind is ZoneKind.SUPPORT for zone in support)
    assert all(zone.kind is ZoneKind.RESISTANCE for zone in resistance)
    assert support and resistance


# ----------------------------------------------------------------------
# Clustering
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_levels_further_apart_than_the_tolerance_stay_separate() -> None:
    highs = [100.0, 120.0, 100.0, 120.0, 100.0, 160.0, 100.0, 160.0, 100.0]
    series = pivot_series(highs)
    _, resistance = _zones(series, atr=2.0)
    assert len(resistance) == 2
    assert [zone.low for zone in resistance] == [Decimal("120.0"), Decimal("160.0")]


@pytest.mark.unit
def test_clustering_bounds_the_zone_width_and_does_not_chain() -> None:
    """The defect this rule exists to prevent.

    Single linkage compares each swing with its nearest neighbour, so a steady
    advance where every high is within tolerance of the previous one merges the
    entire move into one enormous "zone". Complete linkage bounds the total
    spread, so a trend produces several honest zones instead of one meaningless
    one.
    """
    highs: list[float] = []
    for step in range(12):
        highs.extend([100.0 + step * 2.0, 90.0 + step * 2.0])
    highs.append(80.0)
    series = pivot_series(highs)

    atr = 4.0
    config = ZoneConfig(cluster_atr_multiple=0.5)
    _, resistance = _zones(series, atr=atr, config=config)

    tolerance = Decimal(str(atr * config.cluster_atr_multiple))
    assert len(resistance) > 1
    for zone in resistance:
        assert zone.width <= tolerance


@pytest.mark.unit
def test_a_single_touch_does_not_become_a_zone() -> None:
    """One swing gives a zero-width band - the exact value section 13 rejects."""
    series = pivot_series([100.0, 150.0, 100.0, 110.0, 100.0])
    _, resistance = _zones(series, atr=1.0)
    assert all(zone.touch_count >= 2 for zone in resistance)


@pytest.mark.unit
def test_the_minimum_touch_count_is_configurable() -> None:
    series = pivot_series([100.0, 150.0, 100.0])
    _, resistance = _zones(series, atr=1.0, config=ZoneConfig(min_touches=1))
    assert len(resistance) == 1
    assert resistance[0].width == Decimal("0")


@pytest.mark.unit
def test_without_a_volatility_scale_no_zone_is_invented() -> None:
    """No ATR means no defensible clustering tolerance."""
    series = pivot_series([100.0, 120.0, 100.0, 120.0, 100.0])
    swings = detect_swings(series, WINDOW)
    support, resistance = build_zones(
        series, swings, _flat(None, len(series)), _flat(1.0, len(series))
    )
    assert support == () and resistance == ()


@pytest.mark.unit
def test_no_swings_means_no_zones() -> None:
    series = pivot_series([100.0] * 20)
    assert _zones(series) == ((), ())


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_strength_is_a_bounded_heuristic_score() -> None:
    series = pivot_series([100.0, 120.0, 100.0, 120.0, 100.0, 120.0, 100.0])
    _, resistance = _zones(series, atr=2.0)
    assert 0.0 <= resistance[0].strength <= 100.0


@pytest.mark.unit
def test_every_score_component_is_exposed_for_audit() -> None:
    """A number nobody can decompose is not evidence."""
    series = pivot_series([100.0, 120.0, 100.0, 120.0, 100.0, 120.0, 100.0])
    _, resistance = _zones(series, atr=2.0)
    breakdown = resistance[0].breakdown
    for component in (
        breakdown.touches,
        breakdown.recency,
        breakdown.reaction,
        breakdown.volume,
    ):
        assert 0.0 <= component <= 1.0


@pytest.mark.unit
def test_more_touches_score_higher_than_fewer() -> None:
    few = pivot_series([100.0, 120.0, 100.0, 120.0, 100.0])
    many = pivot_series([100.0, 120.0, 100.0, 120.0, 100.0, 120.0, 100.0, 120.0, 100.0])
    _, few_zones = _zones(few, atr=2.0)
    _, many_zones = _zones(many, atr=2.0)
    assert many_zones[0].breakdown.touches > few_zones[0].breakdown.touches


@pytest.mark.unit
def test_recency_decays_with_age() -> None:
    recent = pivot_series([100.0, 120.0, 100.0, 120.0, 100.0])
    stale = pivot_series([100.0, 120.0, 100.0, 120.0] + [100.0] * 60)
    _, recent_zones = _zones(recent, atr=2.0)
    _, stale_zones = _zones(stale, atr=2.0)
    assert recent_zones[0].breakdown.recency > stale_zones[0].breakdown.recency


@pytest.mark.unit
def test_the_score_is_deterministic() -> None:
    series = pivot_series([100.0, 120.0, 100.0, 120.0, 100.0, 120.0, 100.0])
    assert _zones(series, atr=2.0) == _zones(series, atr=2.0)


@pytest.mark.unit
def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        ZoneWeights(touches=0.9, recency=0.9, reaction=0.9, volume=0.9)


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    ({"cluster_atr_multiple": 0.0}, {"min_touches": 0}, {"recency_halflife": 0.0}),
)
def test_invalid_zone_configuration_is_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        ZoneConfig(**kwargs)  # type: ignore[arg-type]


@pytest.mark.unit
def test_a_zone_reports_containment_inclusively() -> None:
    series = pivot_series([100.0, 120.0, 100.0, 120.5, 100.0])
    _, resistance = _zones(series, atr=5.0)
    zone = resistance[0]
    assert zone.contains(zone.low)
    assert zone.contains(zone.high)
    assert zone.contains(zone.midpoint)
    assert not zone.contains(zone.high + Decimal("0.01"))


@pytest.mark.unit
def test_a_zone_is_not_known_before_its_latest_touch_confirms() -> None:
    series = pivot_series([100.0, 120.0, 100.0, 120.0, 100.0, 120.0, 100.0])
    _, resistance = _zones(series, atr=2.0)
    zone = resistance[0]
    assert zone.confirmed_index == max(swing.confirmed_index for swing in zone.touches)
    assert not zone.known_at(zone.confirmed_index - 1)
    assert zone.known_at(zone.confirmed_index)
