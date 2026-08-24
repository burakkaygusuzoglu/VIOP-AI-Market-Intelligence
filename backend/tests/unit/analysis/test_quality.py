"""Heuristic Setup Quality (master spec §18, §19).

The tests that matter most here are the negative ones: that the number cannot
become a probability, that bull and bear are not two halves of one
distribution, and that repeating evidence cannot buy a better score.

Every market is TEST_FIXTURE data.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.domain.analysis import quality as quality_module
from app.domain.analysis.engine import AnalysisConfig, analyse_multi_timeframe
from app.domain.analysis.entry import EntryWeights
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.analysis.fusion import fuse_evidence
from app.domain.analysis.quality import (
    QUALITY_LABEL,
    ComponentAvailability,
    QualityComponent,
    QualityConfig,
    QualityWeights,
    SetupQuality,
    score_setup,
)
from app.domain.analysis.scenarios import ScenarioConfig
from app.domain.analysis.timeframes import ROLES_BROADEST_FIRST, TimeframeRole
from tests.factories_analysis import BEARISH_DRIFT, BULLISH_DRIFT, FLAT_DRIFT, market_view


def analysis(drift: float = BULLISH_DRIFT, roles=ROLES_BROADEST_FIRST):  # type: ignore[no-untyped-def]
    return analyse_multi_timeframe(tuple(market_view(role, drift=drift) for role in roles))


def bull_quality(drift: float = BULLISH_DRIFT) -> SetupQuality:
    return score_setup(analysis(drift).fused, EvidenceDirection.BULLISH)


# ----------------------------------------------------------------------
# It is 0-100, and it is a heuristic
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("drift", (BULLISH_DRIFT, BEARISH_DRIFT, FLAT_DRIFT))
def test_the_score_is_always_within_zero_and_one_hundred(drift: float) -> None:
    for direction in (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH):
        result = score_setup(analysis(drift).fused, direction)
        assert 0 <= result.score <= 100


@pytest.mark.unit
def test_the_result_says_it_is_a_heuristic() -> None:
    result = bull_quality()
    assert result.label == QUALITY_LABEL
    assert "HEURISTIC" in result.label
    assert result.method_version


@pytest.mark.unit
def test_no_quality_type_offers_a_probability_field() -> None:
    """§19: an uncalibrated analysis score may not present itself as a
    likelihood, and none of these types gives a caller the vocabulary."""
    forbidden = {
        "probability",
        "win_rate",
        "win_probability",
        "success_chance",
        "calibrated_probability",
        "expected_return",
        "edge",
        "confidence",
        "odds",
        "likelihood",
    }
    for model in (SetupQuality, quality_module.ComponentScore):
        assert forbidden.isdisjoint(model.__dataclass_fields__), model.__name__


@pytest.mark.unit
def test_the_quality_module_declares_no_probability_variable() -> None:
    tree = ast.parse(Path(quality_module.__file__).read_text(encoding="utf-8"))
    forbidden = {"probability", "win_rate", "success_chance", "calibrated_probability"}
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assert node.target.id.lower() not in forbidden


@pytest.mark.unit
def test_a_coherent_case_scores_above_an_incoherent_one() -> None:
    """The only directional claim the score makes: coherence, not likelihood."""
    rising = analysis(BULLISH_DRIFT).fused
    assert (
        score_setup(rising, EvidenceDirection.BULLISH).score
        > score_setup(rising, EvidenceDirection.BEARISH).score
    )


# ----------------------------------------------------------------------
# Bull and bear are independent measurements
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("drift", (BULLISH_DRIFT, BEARISH_DRIFT, FLAT_DRIFT))
def test_bull_and_bear_are_not_forced_to_sum_to_one_hundred(drift: float) -> None:
    """Both cases can be poor at once. That is what a directionless market
    looks like, and manufacturing a neutral remainder would invent a
    distribution out of two unrelated measurements."""
    fused = analysis(drift).fused
    total = (
        score_setup(fused, EvidenceDirection.BULLISH).score
        + score_setup(fused, EvidenceDirection.BEARISH).score
    )
    assert total != 100 or drift == BULLISH_DRIFT  # never *enforced* to be 100


@pytest.mark.unit
def test_a_directionless_market_leaves_both_cases_weak() -> None:
    fused = analysis(FLAT_DRIFT).fused
    bull = score_setup(fused, EvidenceDirection.BULLISH).score
    bear = score_setup(fused, EvidenceDirection.BEARISH).score
    assert bull < 60
    assert bear < 60
    assert bull + bear < 100


@pytest.mark.unit
def test_scoring_a_non_directional_case_is_refused() -> None:
    fused = analysis().fused
    for direction in (EvidenceDirection.NEUTRAL, EvidenceDirection.UNAVAILABLE):
        with pytest.raises(ValueError, match="directional"):
            score_setup(fused, direction)


# ----------------------------------------------------------------------
# Correlated evidence cannot inflate the score
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_duplicating_every_evidence_item_cannot_move_the_score() -> None:
    """The headline anti-double-counting proof.

    Fusion collapses repetition into one group per category, and components
    read groups, so the number of records never reaches the arithmetic.
    """
    result = analysis()
    baseline = score_setup(result.fused, EvidenceDirection.BULLISH)
    inflated = fuse_evidence(result.evidence * 5, result.contract_evidence, result.contradictions)
    assert score_setup(inflated, EvidenceDirection.BULLISH).score == baseline.score


@pytest.mark.unit
def test_repeating_one_supporting_item_cannot_raise_its_component() -> None:
    result = analysis()
    baseline = score_setup(result.fused, EvidenceDirection.BULLISH)
    supporting = next(
        item for item in result.evidence if item.direction is EvidenceDirection.BULLISH
    )
    stacked = fuse_evidence(
        result.evidence + (supporting,) * 30,
        result.contract_evidence,
        result.contradictions,
    )
    assert score_setup(stacked, EvidenceDirection.BULLISH).score == baseline.score


@pytest.mark.unit
def test_no_component_can_exceed_its_weight() -> None:
    """The cap is what stops correlated components from compounding."""
    for drift in (BULLISH_DRIFT, BEARISH_DRIFT, FLAT_DRIFT):
        for direction in (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH):
            for score in score_setup(analysis(drift).fused, direction).components:
                if score.awarded is not None:
                    assert 0 <= score.awarded <= score.weight


@pytest.mark.unit
def test_the_regime_component_is_weighted_below_trend_and_structure() -> None:
    """Deliberate: the regime is derived partly from the EMA stack and the
    structure bias, so most of what it knows has already been counted."""
    weights = QualityWeights()
    assert weights.regime_suitability < weights.trend_alignment
    assert weights.regime_suitability < weights.market_structure


@pytest.mark.unit
def test_repeated_divergence_is_charged_once_not_once_per_record() -> None:
    """A fixture artefact must not crush the score through repetition."""
    result = analysis()
    burden = score_setup(result.fused, EvidenceDirection.BULLISH).component(
        QualityComponent.CONTRADICTION_BURDEN
    )
    assert burden is not None
    assert burden.awarded is not None
    assert burden.awarded >= 0
    assert "once per conflict" in burden.reason


@pytest.mark.unit
def test_the_contradiction_component_can_never_drive_the_total_negative() -> None:
    heavy = QualityConfig(
        major_contradiction_cost=1000,
        moderate_contradiction_cost=1000,
        minor_contradiction_cost=1000,
    )
    result = score_setup(analysis().fused, EvidenceDirection.BULLISH, heavy)
    burden = result.component(QualityComponent.CONTRADICTION_BURDEN)
    assert burden is not None
    assert burden.awarded == 0
    assert result.score >= 0


# ----------------------------------------------------------------------
# Missing data
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_an_unavailable_component_is_excluded_rather_than_scored_zero() -> None:
    """Zero would read as measured and bad. ``None`` reads as not measured."""
    result = bull_quality(FLAT_DRIFT)
    missing = [score for score in result.components if not score.is_available]
    for score in missing:
        assert score.awarded is None
        assert score.availability is ComponentAvailability.UNAVAILABLE
    assert result.available_weight <= result.total_weight


@pytest.mark.unit
def test_the_denominator_actually_used_is_published() -> None:
    result = bull_quality()
    expected = sum(score.weight for score in result.components if score.is_available)
    assert result.available_weight == expected
    assert result.total_weight == QualityWeights().total


@pytest.mark.unit
def test_missing_data_is_not_free() -> None:
    """Excluding a component from the denominator keeps the score fair on what
    was measured; the availability component charges for what was not."""
    partial = analysis(roles=(TimeframeRole.REGIME, TimeframeRole.BIAS))
    result = score_setup(partial.fused, EvidenceDirection.BULLISH)
    availability = result.component(QualityComponent.DATA_AVAILABILITY)
    assert availability is not None
    assert availability.awarded is not None
    assert result.coverage <= 1.0


# ----------------------------------------------------------------------
# Timeframe alignment vs timeframe coverage
# ----------------------------------------------------------------------

ALL_FOUR = ROLES_BROADEST_FIRST
HIGH_PAIR = (TimeframeRole.REGIME, TimeframeRole.BIAS)


def component_of(  # type: ignore[no-untyped-def]
    roles, which: QualityComponent, drift: float = BULLISH_DRIFT
):
    return score_setup(analysis(drift, roles).fused, EvidenceDirection.BULLISH).component(which)


def alignment(roles, drift: float = BULLISH_DRIFT):  # type: ignore[no-untyped-def]
    return component_of(roles, QualityComponent.TIMEFRAME_ALIGNMENT, drift)


def coverage(roles, drift: float = BULLISH_DRIFT):  # type: ignore[no-untyped-def]
    return component_of(roles, QualityComponent.TIMEFRAME_COVERAGE, drift)


@pytest.mark.unit
def test_one_timeframe_cannot_demonstrate_alignment_at_all() -> None:
    """Alignment is a relation, so one reading cannot exhibit it.

    Two earlier drafts got this wrong from opposite directions: the first
    scored a lone daily view full marks for agreeing with itself, the second
    scored it 7/20 - which still claims to have measured a relation that does
    not exist. A missing timeframe is neither agreement nor disagreement.
    """
    lone = alignment((TimeframeRole.REGIME,))
    assert lone is not None
    assert lone.availability is ComponentAvailability.UNAVAILABLE
    assert lone.awarded is None
    assert "at least 2" in lone.reason
    assert "neither agreement nor disagreement" in lone.reason


@pytest.mark.unit
def test_two_agreeing_timeframes_are_genuinely_aligned() -> None:
    aligned = alignment(HIGH_PAIR)
    assert aligned is not None
    assert aligned.availability is ComponentAvailability.AVAILABLE
    assert aligned.awarded == aligned.weight
    assert "disagreeing: none" in aligned.reason


@pytest.mark.unit
def test_two_conflicting_timeframes_score_far_below_two_agreeing_ones() -> None:
    agreeing = score_setup(analysis(roles=HIGH_PAIR).fused, EvidenceDirection.BULLISH)
    conflicted = score_setup(
        analyse_multi_timeframe(
            (
                market_view(TimeframeRole.REGIME, drift=BULLISH_DRIFT),
                market_view(TimeframeRole.BIAS, drift=BEARISH_DRIFT),
            )
        ).fused,
        EvidenceDirection.BULLISH,
    )
    assert conflicted.score < agreeing.score

    component = conflicted.component(QualityComponent.TIMEFRAME_ALIGNMENT)
    assert component is not None
    assert component.awarded is not None
    assert component.awarded < component.weight
    assert "disagreeing: BIAS" in component.reason


@pytest.mark.unit
def test_all_four_agreeing_is_fully_aligned_and_fully_covered() -> None:
    aligned = alignment(ALL_FOUR)
    covered = coverage(ALL_FOUR)
    assert aligned is not None
    assert covered is not None
    assert aligned.awarded == aligned.weight
    assert covered.awarded == covered.weight
    assert "4 of 4 roles readable" in covered.reason


@pytest.mark.unit
@pytest.mark.parametrize(
    ("roles", "absent"),
    (
        ((TimeframeRole.REGIME, TimeframeRole.BIAS, TimeframeRole.ENTRY), "SETUP"),
        ((TimeframeRole.REGIME, TimeframeRole.BIAS, TimeframeRole.SETUP), "ENTRY"),
    ),
)
def test_a_missing_role_reduces_coverage_and_leaves_alignment_alone(
    roles: tuple[TimeframeRole, ...], absent: str
) -> None:
    """The split doing its job.

    The timeframes present still agree, so alignment stays full; what is
    missing shows up as coverage, which names the absent role.
    """
    aligned = alignment(roles)
    covered = coverage(roles)
    assert aligned is not None
    assert covered is not None
    assert aligned.awarded == aligned.weight
    assert covered.awarded is not None
    assert covered.awarded < covered.weight
    assert absent in covered.reason


@pytest.mark.unit
def test_a_missing_higher_role_costs_more_coverage_than_a_missing_lower_one() -> None:
    """§10 forbids treating timeframes equally, and coverage honours that."""
    without_setup = coverage((TimeframeRole.REGIME, TimeframeRole.BIAS, TimeframeRole.ENTRY))
    without_entry = coverage((TimeframeRole.REGIME, TimeframeRole.BIAS, TimeframeRole.SETUP))
    assert without_setup is not None
    assert without_entry is not None
    assert without_setup.awarded is not None
    assert without_entry.awarded is not None
    assert without_setup.awarded < without_entry.awarded


@pytest.mark.unit
def test_dropping_alignment_from_the_denominator_cannot_improve_the_score() -> None:
    """The trap this split had to avoid.

    Making alignment UNAVAILABLE removes its weight from the denominator,
    which on its own would *raise* a lone timeframe's normalised score.
    `TIMEFRAME_COVERAGE` is what stops that, and this asserts the net effect
    rather than trusting the reasoning.
    """
    lone = score_setup(analysis(roles=(TimeframeRole.REGIME,)).fused, EvidenceDirection.BULLISH)
    full = score_setup(analysis(roles=ALL_FOUR).fused, EvidenceDirection.BULLISH)
    assert lone.score < full.score


@pytest.mark.unit
def test_coverage_is_measurable_even_when_alignment_is_not() -> None:
    """ "How much do I have" is answerable with one timeframe; "does it agree"
    is not. Separating them is what lets both answers be honest."""
    lone_alignment = alignment((TimeframeRole.REGIME,))
    lone_coverage = coverage((TimeframeRole.REGIME,))
    assert lone_alignment is not None
    assert lone_coverage is not None
    assert lone_alignment.availability is ComponentAvailability.UNAVAILABLE
    assert lone_coverage.availability is ComponentAvailability.AVAILABLE
    assert lone_coverage.awarded is not None
    assert lone_coverage.awarded < lone_coverage.weight


@pytest.mark.unit
def test_the_minimum_role_requirement_is_configurable() -> None:
    strict = score_setup(
        analysis(roles=HIGH_PAIR).fused,
        EvidenceDirection.BULLISH,
        QualityConfig(minimum_roles_for_alignment=3),
    ).component(QualityComponent.TIMEFRAME_ALIGNMENT)
    assert strict is not None
    assert strict.availability is ComponentAvailability.UNAVAILABLE


# ----------------------------------------------------------------------
# Breakdown and determinism
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_every_component_is_reported_with_its_weight_and_a_reason() -> None:
    result = bull_quality()
    assert {score.component for score in result.components} == set(QualityComponent)
    for score in result.components:
        assert score.weight >= 0
        assert score.reason


@pytest.mark.unit
def test_a_component_can_be_traced_back_to_the_groups_it_read() -> None:
    result = bull_quality()
    trend = result.component(QualityComponent.TREND_ALIGNMENT)
    assert trend is not None
    if trend.is_available:
        assert trend.groups


# ----------------------------------------------------------------------
# Weights are defaults, not hidden constants
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_default_profile_is_the_documented_one() -> None:
    """DEFAULT POLICY, NOT MARKET FACT - and the defaults are what the report
    describes, so a silent re-weighting shows up here."""
    weights = QualityWeights()
    assert weights.total == 100
    assert weights.timeframe_alignment == 16
    assert weights.timeframe_coverage == 8
    assert weights.trend_alignment == weights.market_structure == 15
    assert weights.regime_suitability == 8


@pytest.mark.unit
def test_a_custom_weight_actually_changes_the_component_contribution() -> None:
    """The test that matters: not that the config is *accepted*, but that it
    reaches the arithmetic. Asserting only ``total_weight`` would pass even if
    the weight were ignored everywhere it counts."""
    fused = analysis().fused
    default = score_setup(fused, EvidenceDirection.BULLISH)
    custom = score_setup(
        fused,
        EvidenceDirection.BULLISH,
        QualityConfig(weights=QualityWeights(regime_suitability=60)),
    )
    before = default.component(QualityComponent.REGIME_SUITABILITY)
    after = custom.component(QualityComponent.REGIME_SUITABILITY)
    assert before is not None
    assert after is not None
    assert after.weight == 60
    assert after.awarded != before.awarded
    assert custom.score != default.score


@pytest.mark.unit
def test_a_custom_profile_reaches_the_score_through_the_whole_engine() -> None:
    """Proven end-to-end through `AnalysisConfig`, not only by calling
    ``score_setup`` directly - a config that the engine failed to thread
    through would be configurable in name only."""
    views = tuple(market_view(role, drift=BULLISH_DRIFT) for role in ROLES_BROADEST_FIRST)
    default = analyse_multi_timeframe(views).scenarios.bull.quality
    custom = analyse_multi_timeframe(
        views,
        config=AnalysisConfig(
            scenarios=ScenarioConfig(
                quality=QualityConfig(weights=QualityWeights(regime_suitability=60))
            )
        ),
    ).scenarios.bull.quality
    assert default is not None
    assert custom is not None
    assert custom.total_weight != default.total_weight
    assert custom.score != default.score


@pytest.mark.unit
def test_a_negative_weight_is_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        QualityWeights(trend_alignment=-1)


@pytest.mark.unit
def test_a_profile_with_no_weight_at_all_is_rejected() -> None:
    """Normalisation would have nothing to divide by."""
    zeroed = dict.fromkeys(QualityWeights.__dataclass_fields__, 0)
    with pytest.raises(ValueError, match="at least one component"):
        QualityWeights(**zeroed)


@pytest.mark.unit
def test_the_defaults_preserve_the_correlated_evidence_protection() -> None:
    """The regime is derived partly from the EMA stack and the structure bias,
    so it must stay weighted below both. Pinned so a future re-weighting
    cannot quietly undo the protection §9 of the review asked for."""
    weights = QualityWeights()
    assert weights.regime_suitability < weights.trend_alignment
    assert weights.regime_suitability < weights.market_structure


@pytest.mark.unit
def test_a_custom_profile_is_deterministic() -> None:
    fused = analysis().fused
    custom = QualityConfig(weights=QualityWeights(volume=30, momentum=2))
    assert score_setup(fused, EvidenceDirection.BULLISH, custom) == score_setup(
        fused, EvidenceDirection.BULLISH, custom
    )


@pytest.mark.unit
def test_entry_weights_are_validated_the_same_way() -> None:
    """They were not, before this closeout: `EntryWeights` had no validation
    at all, so a negative entry weight would have been accepted silently."""
    with pytest.raises(ValueError, match="negative"):
        EntryWeights(extension=-5)
    zeroed = dict.fromkeys(EntryWeights.__dataclass_fields__, 0)
    with pytest.raises(ValueError, match="at least one entry component"):
        EntryWeights(**zeroed)


@pytest.mark.unit
def test_scoring_is_deterministic() -> None:
    fused = analysis().fused
    assert score_setup(fused, EvidenceDirection.BULLISH) == score_setup(
        fused, EvidenceDirection.BULLISH
    )
