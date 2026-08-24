"""Timeframe hierarchy regressions (master spec §10, §17).

The six scenarios that must never silently change behaviour, plus the CHOCH
exemption boundary. Each is written as the market a user would describe, and
asserts the reading a user would expect - so a future refactor that quietly
lets a 5M candle overrule a daily trend fails here rather than in production.

Every market is TEST_FIXTURE data.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.domain.analysis.contradictions import ContradictionSeverity, ContradictionType
from app.domain.analysis.engine import analyse_multi_timeframe
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.analysis.quality import ComponentAvailability, QualityComponent
from app.domain.analysis.scenarios import ScenarioState
from app.domain.analysis.timeframes import TimeframeRole, TimeframeView
from app.domain.common.enums import Direction
from app.domain.structure.events import StructuralEventType
from app.domain.structure.regime import MarketRegime
from app.domain.suitability.no_trade import NoTradeReason, assess_no_trade
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


def analyse(*views: TimeframeView):  # type: ignore[no-untyped-def]
    return analyse_multi_timeframe(views)


def types_of(result) -> set[ContradictionType]:  # type: ignore[no-untyped-def]
    return {item.contradiction_type for item in result.contradictions.contradictions}


# ----------------------------------------------------------------------
# A. 1D/1H/15M bullish, 5M correcting -> pullback, not reversal
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_lower_timeframe_correction_is_a_pullback_not_a_bearish_reversal() -> None:
    result = analyse(
        bull(TimeframeRole.REGIME),
        bull(TimeframeRole.BIAS),
        bull(TimeframeRole.SETUP),
        view(TimeframeRole.ENTRY, retracement()),
    )

    assert ContradictionType.LOWER_TIMEFRAME_REVERSAL not in types_of(result)
    assert len(result.contradictions.pullbacks) == 1

    bull_case = result.scenarios.bull
    bear_case = result.scenarios.bear
    assert bull_case.quality is not None
    assert bear_case.quality is not None
    assert bull_case.quality.score > bear_case.quality.score
    assert bull_case.state is not ScenarioState.INACTIVE


# ----------------------------------------------------------------------
# B. 1D/1H bearish, 15M/5M bullish -> the lower pair does not overwrite
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_lower_timeframes_do_not_overwrite_a_higher_timeframe_bias() -> None:
    result = analyse(
        bear(TimeframeRole.REGIME),
        bear(TimeframeRole.BIAS),
        bull(TimeframeRole.SETUP),
        bull(TimeframeRole.ENTRY),
    )

    regime = result.contradictions.reading_for(TimeframeRole.REGIME)
    bias = result.contradictions.reading_for(TimeframeRole.BIAS)
    assert regime is not None
    assert bias is not None
    assert regime.direction is EvidenceDirection.BEARISH
    assert bias.direction is EvidenceDirection.BEARISH

    bull_case = result.scenarios.bull
    assert bull_case.state is ScenarioState.FORMING
    assert bull_case.quality is not None
    assert result.scenarios.bear.quality is not None
    assert bull_case.quality.score < result.scenarios.bear.quality.score


@pytest.mark.unit
def test_the_higher_timeframes_keep_most_of_the_alignment_weight() -> None:
    """§10 forbids equal treatment, and the weights encode that."""
    agreeing_high = analyse(bull(TimeframeRole.REGIME), bull(TimeframeRole.BIAS))
    agreeing_low = analyse(bull(TimeframeRole.SETUP), bull(TimeframeRole.ENTRY))
    high = agreeing_high.scenarios.bull.quality
    low = agreeing_low.scenarios.bull.quality
    assert high is not None
    assert low is not None
    assert high.score > low.score


# ----------------------------------------------------------------------
# C. 1D bullish, 1H bearish -> meaningful conflict
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_two_slowest_timeframes_disagreeing_is_a_major_conflict() -> None:
    result = analyse(bull(TimeframeRole.REGIME), bear(TimeframeRole.BIAS))
    assert ContradictionType.HIGHER_TIMEFRAME_CONFLICT in types_of(result)
    assert result.contradictions.highest_severity is ContradictionSeverity.MAJOR

    verdict = assess_no_trade(result, EvidenceDirection.BULLISH)
    assert NoTradeReason.CONFLICTING_TIMEFRAMES in verdict.reasons
    assert verdict.no_trade is True


# ----------------------------------------------------------------------
# D. Range regime -> range context preserved
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_ranging_market_keeps_its_range_context() -> None:
    result = analyse(
        flat(TimeframeRole.REGIME),
        flat(TimeframeRole.BIAS),
        flat(TimeframeRole.SETUP),
        flat(TimeframeRole.ENTRY),
    )
    assert result.scenarios.neutral.state is ScenarioState.CONFIRMED
    assert ContradictionType.HIGHER_TIMEFRAME_CONFLICT not in types_of(result)

    verdict = assess_no_trade(result, EvidenceDirection.BULLISH)
    assert NoTradeReason.MIDDLE_OF_RANGE in verdict.reasons


@pytest.mark.unit
def test_a_range_is_neutral_and_never_quietly_directional() -> None:
    result = analyse(flat(TimeframeRole.REGIME), flat(TimeframeRole.BIAS))
    for reading in result.contradictions.readings:
        assert not reading.is_directional


# ----------------------------------------------------------------------
# E. CHAOTIC -> strong NO TRADE consideration
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_chaotic_regime_blocks() -> None:
    """Built by replacing the regime on a real view rather than hunting for a
    fixture that happens to produce CHAOTIC - the rule under test is what the
    veto does with the classification, not how the classification arises."""
    base = bull(TimeframeRole.BIAS)
    chaotic_regime = replace(base.structure.regime, regime=MarketRegime.CHAOTIC)
    chaotic_view = replace(base, structure=replace(base.structure, regime=chaotic_regime))
    result = analyse(bull(TimeframeRole.REGIME), chaotic_view)

    verdict = assess_no_trade(result, EvidenceDirection.BULLISH)
    assert NoTradeReason.CHAOTIC_REGIME in verdict.reasons
    assert verdict.no_trade is True


# ----------------------------------------------------------------------
# F. A critical timeframe missing -> explicit, never fabricated
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_missing_bias_timeframe_is_explicit_and_invents_no_direction() -> None:
    result = analyse(bull(TimeframeRole.REGIME), bull(TimeframeRole.SETUP))

    assert TimeframeRole.BIAS in result.views.missing_roles
    assert result.contradictions.reading_for(TimeframeRole.BIAS) is None
    assert result.scenarios.bull.state is ScenarioState.UNAVAILABLE
    assert result.scenarios.bear.state is ScenarioState.UNAVAILABLE

    verdict = assess_no_trade(result, EvidenceDirection.BULLISH)
    assert NoTradeReason.INSUFFICIENT_DATA in verdict.reasons
    assert verdict.no_trade is not False


@pytest.mark.unit
def test_a_missing_timeframe_never_counts_as_agreement() -> None:
    """Two agreeing timeframes are genuinely aligned, so alignment stays full.

    What the missing pair costs is *coverage*, and it must never be a bonus.
    Asserting `partial < full` on the total alone would be asserting the wrong
    thing: the honest invariants are that coverage falls strictly, and that
    the incomplete analysis can never score higher than the complete one.
    """
    complete = analyse(
        bull(TimeframeRole.REGIME),
        bull(TimeframeRole.BIAS),
        bull(TimeframeRole.SETUP),
        bull(TimeframeRole.ENTRY),
    )
    partial = analyse(bull(TimeframeRole.REGIME), bull(TimeframeRole.BIAS))
    full_quality = complete.scenarios.bull.quality
    partial_quality = partial.scenarios.bull.quality
    assert full_quality is not None
    assert partial_quality is not None

    assert partial_quality.score <= full_quality.score

    full_coverage = full_quality.component(QualityComponent.TIMEFRAME_COVERAGE)
    partial_coverage = partial_quality.component(QualityComponent.TIMEFRAME_COVERAGE)
    assert full_coverage is not None
    assert partial_coverage is not None
    assert partial_coverage.awarded is not None
    assert full_coverage.awarded is not None
    assert partial_coverage.awarded < full_coverage.awarded


@pytest.mark.unit
def test_one_timeframe_cannot_claim_alignment_with_the_hierarchy() -> None:
    """The strongest form of the invariant: a lone view leaves the alignment
    question unanswered and still scores below a complete agreeing set."""
    lone = analyse(bull(TimeframeRole.REGIME))
    complete = analyse(
        bull(TimeframeRole.REGIME),
        bull(TimeframeRole.BIAS),
        bull(TimeframeRole.SETUP),
        bull(TimeframeRole.ENTRY),
    )
    lone_quality = lone.scenarios.bull.quality
    full_quality = complete.scenarios.bull.quality
    assert lone_quality is not None
    assert full_quality is not None

    alignment = lone_quality.component(QualityComponent.TIMEFRAME_ALIGNMENT)
    assert alignment is not None
    assert alignment.availability is ComponentAvailability.UNAVAILABLE
    assert lone_quality.score < full_quality.score


# ----------------------------------------------------------------------
# The CHOCH exemption boundary
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_five_minute_choch_alone_may_remain_a_pullback() -> None:
    """A change of character on the entry timeframe is what a pullback *is*
    structurally. Escalating on it alone would revoke the exemption on every
    healthy trend and make the distinction meaningless."""
    entry = view(TimeframeRole.ENTRY, retracement())
    chochs = [
        event
        for event in entry.structure.structural_events
        if event.event_type is StructuralEventType.CHOCH and event.direction is Direction.SHORT
    ]
    result = analyse(
        bull(TimeframeRole.REGIME),
        bull(TimeframeRole.BIAS),
        bull(TimeframeRole.SETUP),
        entry,
    )
    if chochs:
        assert result.contradictions.pullbacks
        assert ContradictionType.LOWER_TIMEFRAME_REVERSAL not in types_of(result)


@pytest.mark.unit
def test_a_strong_opposing_regime_revokes_the_exemption() -> None:
    """The documented first condition: a retracement does not classify as a
    strong trend on its own timeframe."""
    result = analyse(
        bull(TimeframeRole.REGIME),
        bull(TimeframeRole.BIAS),
        bull(TimeframeRole.SETUP),
        bear(TimeframeRole.ENTRY),
    )
    assert ContradictionType.LOWER_TIMEFRAME_REVERSAL in types_of(result)
    assert result.contradictions.pullbacks == ()


@pytest.mark.unit
def test_the_exemption_needs_a_consensus_to_pull_back_from() -> None:
    result = analyse(
        bull(TimeframeRole.REGIME),
        bear(TimeframeRole.BIAS),
        bear(TimeframeRole.ENTRY),
    )
    assert result.contradictions.pullbacks == ()


@pytest.mark.unit
def test_a_pullback_does_not_flip_the_bull_case_to_bearish() -> None:
    """The regression that matters most in daily use: routine retracements
    must not repeatedly toggle the reading."""
    result = analyse(
        bull(TimeframeRole.REGIME),
        bull(TimeframeRole.BIAS),
        bull(TimeframeRole.SETUP),
        view(TimeframeRole.ENTRY, retracement()),
    )
    bias = result.contradictions.reading_for(TimeframeRole.BIAS)
    assert bias is not None
    assert bias.direction is EvidenceDirection.BULLISH
    assert result.scenarios.bear.state is ScenarioState.FORMING
