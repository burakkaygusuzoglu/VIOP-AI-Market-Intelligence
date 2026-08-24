"""The Evidence Fusion Engine (master spec §16).

The load-bearing test in this file is the double-counting one: fusion exists so
that a category which happened to produce twenty records argues once.

Every market is TEST_FIXTURE data.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.analysis.engine import analyse_multi_timeframe
from app.domain.analysis.evidence import (
    EvidenceCategory,
    EvidenceDirection,
    EvidenceReliability,
    EvidenceSource,
    EvidenceStrength,
)
from app.domain.analysis.fusion import fuse_evidence, strongest_group
from app.domain.analysis.timeframes import ROLES_BROADEST_FIRST, TimeframeRole
from app.domain.common.verification import VerificationStatus
from app.domain.futures.basis import BasisContext, BasisResult
from tests.factories_analysis import BEARISH_DRIFT, BULLISH_DRIFT, FLAT_DRIFT, market_view


def analysis(drift: float = BULLISH_DRIFT):  # type: ignore[no-untyped-def]
    return analyse_multi_timeframe(
        tuple(market_view(role, drift=drift) for role in ROLES_BROADEST_FIRST)
    )


# ----------------------------------------------------------------------
# The partition §16 asks for
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_every_item_lands_in_exactly_one_partition() -> None:
    fused = analysis().fused
    assert fused.item_total == len(fused.bullish) + len(fused.bearish) + len(fused.neutral) + len(
        fused.unavailable
    )


@pytest.mark.unit
def test_the_four_questions_section_16_asks_can_be_answered() -> None:
    fused = analysis().fused
    assert fused.bullish
    assert fused.bearish
    assert isinstance(fused.neutral, tuple)
    assert isinstance(fused.unavailable, tuple)
    assert fused.contradictions is not None


@pytest.mark.unit
def test_partitions_preserve_the_full_item_not_a_summary() -> None:
    """Role, source, strength, reliability, timing and provenance all survive.

    A partition that kept only directions would make the evidence unreadable,
    which is the failure §16 exists to prevent.
    """
    for item in analysis().fused.bullish:
        assert item.source in set(EvidenceSource)
        assert item.strength in set(EvidenceStrength)
        assert item.reliability in set(EvidenceReliability)
        assert item.role in set(TimeframeRole)
        assert item.reason


@pytest.mark.unit
def test_unavailable_is_never_folded_into_neutral() -> None:
    fused = analysis(FLAT_DRIFT).fused
    assert all(item.direction is EvidenceDirection.NEUTRAL for item in fused.neutral)
    assert all(item.direction is EvidenceDirection.UNAVAILABLE for item in fused.unavailable)


@pytest.mark.unit
def test_contract_context_reaches_the_neutral_partition_not_a_direction() -> None:
    """§32 refuses to call a premium bullish, and fusion does not overrule it."""
    result = analyse_multi_timeframe(
        (market_view(TimeframeRole.BIAS, drift=BULLISH_DRIFT),),
        basis=BasisResult(
            context=BasisContext.PREMIUM,
            basis=Decimal("1"),
            basis_ratio=Decimal("0.01"),
            futures_price=Decimal("101"),
            spot_price=Decimal("100"),
            reason="fixture",
        ),
    )
    basis_items = [item for item in result.fused.neutral if item.source is EvidenceSource.BASIS]
    assert len(basis_items) == 1
    assert result.fused.contract == result.contract_evidence


# ----------------------------------------------------------------------
# Grouping: the anti-double-counting device
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_category_produces_one_group_per_timeframe_however_many_records() -> None:
    """The zigzag fixtures generate a divergence at nearly every swing.

    Fused, all of them are one voice on that timeframe. The record count stays
    visible on the group, and is not an input to anything.
    """
    fused = analysis().fused
    divergence = [group for group in fused.groups if group.category is EvidenceCategory.DIVERGENCE]
    assert divergence
    for group in divergence:
        assert group.item_count >= 1
    per_role = [group.role for group in divergence]
    assert len(per_role) == len(set(per_role))


@pytest.mark.unit
def test_duplicating_evidence_cannot_change_the_fused_stance() -> None:
    """The mechanical proof.

    Twenty copies of one observation are still one observation. Repetition
    must not deepen a group's strength, flip its direction, or add a voice.
    """
    result = analysis()
    original = result.fused
    inflated = fuse_evidence(
        result.evidence + tuple(result.evidence[:1] * 20),
        result.contract_evidence,
        result.contradictions,
    )

    first = original.groups[0]
    same = inflated.group_for(first.role, first.category) if first.role else None
    assert same is not None
    assert same.direction is first.direction
    assert same.strength is first.strength
    assert len(inflated.groups) == len(original.groups)


@pytest.mark.unit
def test_group_strength_is_a_maximum_and_never_a_total() -> None:
    result = analysis()
    base = next(group for group in result.fused.groups if group.strength is not None)
    item = base.items[0]
    doubled = fuse_evidence(
        result.evidence + (item, item, item),
        result.contract_evidence,
        result.contradictions,
    )
    again = doubled.group_for(base.role, base.category) if base.role else None
    assert again is not None
    assert again.strength is base.strength


@pytest.mark.unit
def test_a_category_arguing_with_itself_is_neutral_not_a_majority_vote() -> None:
    """Counting rows would let the number of records decide a market question."""
    result = analysis()
    bullish = next(
        item
        for item in result.evidence
        if item.direction is EvidenceDirection.BULLISH and item.role is TimeframeRole.BIAS
    )
    opposing = replace(bullish, direction=EvidenceDirection.BEARISH)
    fused = fuse_evidence(
        (*result.evidence, opposing, opposing, opposing),
        result.contract_evidence,
        result.contradictions,
    )
    group = fused.group_for(TimeframeRole.BIAS, bullish.category)
    assert group is not None
    assert group.direction is EvidenceDirection.NEUTRAL
    assert group.strength is None


# ----------------------------------------------------------------------
# Group metadata
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_group_is_only_as_settled_as_its_least_settled_item() -> None:
    """VWAP rests on a development-default anchor, so anything relying on it
    inherits that caveat rather than quietly shedding it."""
    fused = analysis().fused
    intraday = [group for group in fused.groups if group.category is EvidenceCategory.INTRADAY]
    assert intraday
    for group in intraday:
        if group.is_directional:
            assert group.reliability is EvidenceReliability.UNVERIFIED_SOURCE


@pytest.mark.unit
def test_reliability_is_derived_from_provenance_and_timing() -> None:
    fused = analysis().fused
    for item in fused.bullish + fused.bearish:
        if item.provenance is not None and item.provenance not in (
            VerificationStatus.VERIFIED_CURRENT_FACT,
            VerificationStatus.TEST_FIXTURE,
        ):
            assert item.reliability is EvidenceReliability.UNVERIFIED_SOURCE
        elif item.point_in_time:
            assert item.reliability is EvidenceReliability.PROVISIONAL
        else:
            assert item.reliability is EvidenceReliability.CONFIRMED


@pytest.mark.unit
def test_groups_are_ordered_broadest_role_first() -> None:
    roles = [group.role.rank for group in analysis().fused.groups if group.role]
    assert roles == sorted(roles)


@pytest.mark.unit
def test_supporting_and_opposing_read_the_same_groups_from_both_sides() -> None:
    fused = analysis().fused
    bulls = fused.supporting(EvidenceDirection.BULLISH)
    against_bears = fused.opposing(EvidenceDirection.BEARISH)
    assert set(bulls) == set(against_bears)


@pytest.mark.unit
def test_neutral_groups_oppose_nothing() -> None:
    fused = analysis(FLAT_DRIFT).fused
    for group in fused.opposing(EvidenceDirection.BULLISH):
        assert group.direction is EvidenceDirection.BEARISH


@pytest.mark.unit
def test_strongest_group_is_a_maximum() -> None:
    fused = analysis().fused
    groups = fused.supporting(EvidenceDirection.BULLISH)
    best = strongest_group(groups)
    assert best is not None
    assert all(group.strength is None or best.rank >= group.strength.rank for group in groups)
    assert strongest_group(()) is None


@pytest.mark.unit
def test_fusion_is_deterministic() -> None:
    views = tuple(market_view(role, drift=BEARISH_DRIFT) for role in ROLES_BROADEST_FIRST)
    assert analyse_multi_timeframe(views).fused == analyse_multi_timeframe(views).fused
