"""Contradictions and the pullback distinction (master spec sections 10, 17).

The case that matters most is the one that must **not** fire: a lower
timeframe correcting inside higher timeframes that agree is a retracement, and
a system that called it a conflict would warn on every healthy trend.

Every market here is TEST_FIXTURE data.
"""

from __future__ import annotations

import pytest

from app.domain.analysis.contradictions import (
    ContradictionSeverity,
    ContradictionType,
)
from app.domain.analysis.engine import analyse_multi_timeframe
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.analysis.timeframes import ROLES_BROADEST_FIRST, TimeframeRole, TimeframeView
from app.domain.common.enums import Timeframe
from tests.factories_analysis import (
    BEARISH_DRIFT,
    BULLISH_DRIFT,
    FLAT_DRIFT,
    market_view,
    retracement,
    view,
)


def bull(role: TimeframeRole) -> TimeframeView:
    return market_view(role, drift=BULLISH_DRIFT)


def bear(role: TimeframeRole) -> TimeframeView:
    return market_view(role, drift=BEARISH_DRIFT)


def flat(role: TimeframeRole) -> TimeframeView:
    return market_view(role, drift=FLAT_DRIFT)


def report(*views: TimeframeView):  # type: ignore[no-untyped-def]
    return analyse_multi_timeframe(views).contradictions


def types_of(found) -> set[ContradictionType]:  # type: ignore[no-untyped-def]
    return {item.contradiction_type for item in found.contradictions}


# ----------------------------------------------------------------------
# The pullback that must not be a contradiction
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_five_minute_retracement_under_agreeing_higher_timeframes_is_not_a_conflict() -> None:
    """1D bullish, 1H bullish, 15M bullish, 5M correcting.

    Section 10's worked example. The correct reading is "timing is weak", not
    "the timeframes disagree", and the engine must say so without raising a
    contradiction.
    """
    found = report(
        bull(TimeframeRole.REGIME),
        bull(TimeframeRole.BIAS),
        bull(TimeframeRole.SETUP),
        view(TimeframeRole.ENTRY, retracement()),
    )

    assert ContradictionType.LOWER_TIMEFRAME_REVERSAL not in types_of(found)
    assert len(found.pullbacks) == 1
    pullback = found.pullbacks[0]
    assert pullback.timeframe is Timeframe.M5
    assert pullback.against is EvidenceDirection.BULLISH
    assert "retracement" in pullback.reason


@pytest.mark.unit
def test_the_pullback_reading_still_records_the_five_minute_direction() -> None:
    """Exempting it from being a conflict does not hide it."""
    found = report(
        bull(TimeframeRole.REGIME),
        bull(TimeframeRole.BIAS),
        bull(TimeframeRole.SETUP),
        view(TimeframeRole.ENTRY, retracement()),
    )
    entry = found.reading_for(TimeframeRole.ENTRY)
    assert entry is not None
    assert entry.direction is EvidenceDirection.BEARISH


@pytest.mark.unit
def test_an_entry_timeframe_in_a_strong_opposing_trend_is_more_than_a_pullback() -> None:
    """The exemption is revoked when the entry timeframe is itself a strong
    trend the other way - that has more persistence than a retracement."""
    found = report(
        bull(TimeframeRole.REGIME),
        bull(TimeframeRole.BIAS),
        bull(TimeframeRole.SETUP),
        bear(TimeframeRole.ENTRY),
    )
    assert ContradictionType.LOWER_TIMEFRAME_REVERSAL in types_of(found)
    assert found.pullbacks == ()


@pytest.mark.unit
def test_there_is_no_pullback_exemption_without_a_consensus_to_pull_back_from() -> None:
    """With 1D and 1H disagreeing there is no agreed trend, so a 5M move
    against one of them is not a retracement within anything."""
    found = report(
        bull(TimeframeRole.REGIME),
        bear(TimeframeRole.BIAS),
        bear(TimeframeRole.ENTRY),
    )
    assert found.pullbacks == ()


@pytest.mark.unit
def test_an_entry_timeframe_agreeing_with_the_others_produces_neither() -> None:
    found = report(
        bull(TimeframeRole.REGIME),
        bull(TimeframeRole.BIAS),
        bull(TimeframeRole.ENTRY),
    )
    assert found.pullbacks == ()
    assert ContradictionType.LOWER_TIMEFRAME_REVERSAL not in types_of(found)


# ----------------------------------------------------------------------
# Real conflict
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_two_slowest_timeframes_disagreeing_is_a_major_conflict() -> None:
    """1D bullish against 1H bearish: no lower-timeframe timing settles this."""
    found = report(bull(TimeframeRole.REGIME), bear(TimeframeRole.BIAS))
    conflict = next(
        item
        for item in found.contradictions
        if item.contradiction_type is ContradictionType.HIGHER_TIMEFRAME_CONFLICT
    )
    assert conflict.severity is ContradictionSeverity.MAJOR
    assert conflict.timeframes == (Timeframe.D1, Timeframe.H1)
    assert conflict.roles == (TimeframeRole.REGIME, TimeframeRole.BIAS)


@pytest.mark.unit
def test_a_setup_forming_against_the_bias_is_a_moderate_conflict() -> None:
    """Section 17's worked example: 1D and 1H bullish, 15M bearish."""
    found = report(bull(TimeframeRole.REGIME), bull(TimeframeRole.BIAS), bear(TimeframeRole.SETUP))
    conflict = next(
        item
        for item in found.contradictions
        if item.contradiction_type is ContradictionType.SETUP_AGAINST_BIAS
    )
    assert conflict.severity is ContradictionSeverity.MODERATE
    assert Timeframe.M15 in conflict.timeframes


@pytest.mark.unit
def test_a_conflict_exposes_the_evidence_that_produced_it() -> None:
    """Section 17 requires the conflict to be visible, which means auditable."""
    found = report(bull(TimeframeRole.REGIME), bear(TimeframeRole.BIAS))
    conflict = next(
        item
        for item in found.contradictions
        if item.contradiction_type is ContradictionType.HIGHER_TIMEFRAME_CONFLICT
    )
    assert conflict.evidence
    assert {item.role for item in conflict.evidence} == {
        TimeframeRole.REGIME,
        TimeframeRole.BIAS,
    }
    assert conflict.reason
    assert conflict.confirmed_at is not None


@pytest.mark.unit
def test_a_conflict_is_dated_to_when_it_became_knowable() -> None:
    """The latest confirmation among the evidence involved: a conflict is only
    as old as its newer half."""
    regime_view = bull(TimeframeRole.REGIME)
    bias_view = bear(TimeframeRole.BIAS)
    found = report(regime_view, bias_view)
    conflict = next(
        item
        for item in found.contradictions
        if item.contradiction_type is ContradictionType.HIGHER_TIMEFRAME_CONFLICT
    )
    assert conflict.confirmed_at == max(regime_view.last_time, bias_view.last_time)


@pytest.mark.unit
def test_multiple_conflicts_are_all_reported() -> None:
    """Nothing is collapsed into a single worst-case line."""
    found = report(
        bull(TimeframeRole.REGIME),
        bear(TimeframeRole.BIAS),
        bull(TimeframeRole.SETUP),
    )
    assert {
        ContradictionType.HIGHER_TIMEFRAME_CONFLICT,
        ContradictionType.SETUP_AGAINST_BIAS,
    } <= types_of(found)
    assert found.highest_severity is ContradictionSeverity.MAJOR


@pytest.mark.unit
def test_agreeing_timeframes_raise_no_timeframe_conflict() -> None:
    found = report(*(bull(role) for role in ROLES_BROADEST_FIRST))
    cross_timeframe = {
        ContradictionType.HIGHER_TIMEFRAME_CONFLICT,
        ContradictionType.SETUP_AGAINST_BIAS,
        ContradictionType.LOWER_TIMEFRAME_REVERSAL,
    }
    assert cross_timeframe.isdisjoint(types_of(found))


# ----------------------------------------------------------------------
# Neutral and missing never manufacture agreement
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_ranging_timeframe_contradicts_nothing() -> None:
    """Neutral is a measurement, and it disagrees with nobody."""
    found = report(bull(TimeframeRole.REGIME), flat(TimeframeRole.BIAS))
    assert ContradictionType.HIGHER_TIMEFRAME_CONFLICT not in types_of(found)


@pytest.mark.unit
def test_a_missing_timeframe_is_reported_absent_and_never_counted_as_agreement() -> None:
    found = report(bull(TimeframeRole.REGIME), bull(TimeframeRole.BIAS))
    assert found.unavailable_roles == (TimeframeRole.SETUP, TimeframeRole.ENTRY)
    assert found.reading_for(TimeframeRole.SETUP) is None
    assert ContradictionType.SETUP_AGAINST_BIAS not in types_of(found)


@pytest.mark.unit
def test_a_rule_whose_timeframe_is_absent_is_skipped_not_defaulted() -> None:
    """Without the 1H view there is no bias to conflict with, so the major
    rule cannot fire on a substituted neutral."""
    found = report(bull(TimeframeRole.REGIME), bear(TimeframeRole.SETUP))
    assert ContradictionType.HIGHER_TIMEFRAME_CONFLICT not in types_of(found)


# ----------------------------------------------------------------------
# Inside one timeframe
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_evidence_on_both_sides_of_one_timeframe_is_reported_not_netted() -> None:
    """A trend carrying volume divergences against it is exactly section 16's
    example, where bullish and bearish lists sit side by side."""
    found = report(bull(TimeframeRole.BIAS))
    conflict = next(
        item
        for item in found.contradictions
        if item.contradiction_type is ContradictionType.EVIDENCE_CONFLICT
    )
    directions = {item.direction for item in conflict.evidence}
    assert directions == {EvidenceDirection.BULLISH, EvidenceDirection.BEARISH}
    assert conflict.severity is ContradictionSeverity.MINOR


@pytest.mark.unit
def test_a_reading_needs_regime_and_structure_to_agree_before_it_commits() -> None:
    found = report(bull(TimeframeRole.BIAS))
    reading = found.reading_for(TimeframeRole.BIAS)
    assert reading is not None
    assert reading.regime_direction is EvidenceDirection.BULLISH
    assert reading.structure_direction is EvidenceDirection.BULLISH
    assert reading.direction is EvidenceDirection.BULLISH
    assert reading.evidence


@pytest.mark.unit
def test_a_reading_with_no_data_is_unavailable_rather_than_neutral() -> None:
    from tests.factories_analysis import zigzag  # noqa: PLC0415

    found = report(
        view(TimeframeRole.BIAS, zigzag(drift=BULLISH_DRIFT, size=6, timeframe=Timeframe.H1))
    )
    reading = found.reading_for(TimeframeRole.BIAS)
    assert reading is not None
    assert reading.direction is EvidenceDirection.UNAVAILABLE


# ----------------------------------------------------------------------
# Determinism
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_detection_repeats_exactly() -> None:
    views = (bull(TimeframeRole.REGIME), bear(TimeframeRole.BIAS), bull(TimeframeRole.SETUP))
    first = analyse_multi_timeframe(views).contradictions
    second = analyse_multi_timeframe(views).contradictions
    assert first == second


@pytest.mark.unit
def test_the_same_market_analysed_twice_gives_the_same_evidence() -> None:
    views = (bull(TimeframeRole.REGIME), bull(TimeframeRole.BIAS))
    assert analyse_multi_timeframe(views).evidence == analyse_multi_timeframe(views).evidence
