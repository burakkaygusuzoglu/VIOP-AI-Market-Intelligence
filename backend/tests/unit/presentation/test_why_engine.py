"""The Why Engine (master spec §92, §57).

§92's closing rule - *never show an unexplained score* - is the one most of
these tests exist to hold.

Every market and every contract value is TEST_FIXTURE data.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from app.application.presentation import why as why_module
from app.application.presentation.why import (
    Explanation,
    Reason,
    ReasonSeverity,
    ReasonSource,
    WhyTopic,
    why_contradiction,
    why_direction,
    why_entry_quality,
    why_no_trade_block,
    why_pending_confirmation,
    why_position_size,
    why_risk_findings,
    why_scenario_state,
    why_score_changed,
    why_setup_quality,
    why_zone,
)
from app.domain.analysis.engine import MultiTimeframeAnalysis, analyse_multi_timeframe
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.analysis.quality import QualityConfig, QualityWeights, score_setup
from app.domain.analysis.scenarios import ScenarioState
from app.domain.analysis.timeframes import ROLES_BROADEST_FIRST, TimeframeRole
from app.domain.common.enums import Direction
from app.domain.risk.margin import assess_margin
from app.domain.risk.sizing import (
    AccountState,
    PositionSizing,
    RiskMode,
    RiskPolicy,
    size_position,
)
from app.domain.suitability.no_trade import assess_no_trade
from tests.factories_analysis import BEARISH_DRIFT, BULLISH_DRIFT, FLAT_DRIFT, market_view
from tests.factories_futures import contract, verified

TIGHT = RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("75"))
ROOMY = RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("5000"))


def analysis(
    drift: float = BULLISH_DRIFT,
    roles: tuple[TimeframeRole, ...] = ROLES_BROADEST_FIRST,
) -> MultiTimeframeAnalysis:
    return analyse_multi_timeframe(tuple(market_view(role, drift=drift) for role in roles))


def zero_risk_sizing() -> PositionSizing:
    return size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(100)),
        AccountState(equity=Decimal("2500")),
        TIGHT,
    )


def permitted_sizing() -> PositionSizing:
    return size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10), initial_margin=verified(100)),
        AccountState(equity=Decimal("100000")),
        ROOMY,
    )


# ----------------------------------------------------------------------
# Why bullish / bearish
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_why_bullish_lists_the_groups_that_support_it() -> None:
    explanation = why_direction(analysis(), EvidenceDirection.BULLISH)
    assert explanation.topic is WhyTopic.BULLISH_EVIDENCE
    assert explanation.available
    assert explanation.reasons
    for reason in explanation.reasons:
        assert reason.source is ReasonSource.EVIDENCE
        assert reason.evidence


@pytest.mark.unit
def test_why_bearish_reads_the_other_side_of_the_same_analysis() -> None:
    result = analysis(BEARISH_DRIFT)
    explanation = why_direction(result, EvidenceDirection.BEARISH)
    assert explanation.topic is WhyTopic.BEARISH_EVIDENCE
    assert explanation.reasons


@pytest.mark.unit
def test_a_side_with_no_supporting_evidence_is_unavailable_not_empty() -> None:
    """§92 forbids an unexplained conclusion; it also forbids inventing one."""
    result = analysis(roles=(TimeframeRole.REGIME,))
    for direction in (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH):
        explanation = why_direction(result, direction)
        if not explanation.reasons:
            assert not explanation.available
            assert explanation.unavailable_reason


@pytest.mark.unit
def test_each_reason_cites_the_evidence_behind_it() -> None:
    explanation = why_direction(analysis(), EvidenceDirection.BULLISH)
    for reason in explanation.reasons:
        assert all(item.direction is EvidenceDirection.BULLISH for item in reason.evidence)


# ----------------------------------------------------------------------
# Why this score - from the actual breakdown
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_setup_quality_is_explained_by_every_one_of_its_components() -> None:
    """The explanation walks the scorer's own component list.

    Not a description of what a score means - a report of what each component
    actually awarded. Remove a component and this loses a line automatically.
    """
    quality = analysis().scenarios.bull.quality
    assert quality is not None
    explanation = why_setup_quality(quality)

    assert explanation.codes == tuple(item.component.value for item in quality.components)
    assert str(quality.score) in explanation.subject
    for reason in explanation.reasons:
        assert reason.source is ReasonSource.QUALITY_COMPONENT


@pytest.mark.unit
def test_the_pro_rendering_carries_the_exact_points_awarded() -> None:
    quality = analysis().scenarios.bull.quality
    assert quality is not None
    explanation = why_setup_quality(quality)
    for component, reason in zip(quality.components, explanation.reasons, strict=True):
        if component.is_available:
            assert f"{component.awarded}/{component.weight}" in reason.pro
        else:
            assert "UNAVAILABLE" in reason.pro


@pytest.mark.unit
def test_entry_quality_is_explained_by_its_own_components() -> None:
    entry = analysis().scenarios.bull.entry
    assert entry is not None
    explanation = why_entry_quality(entry)
    assert explanation.codes == tuple(item.component.value for item in entry.components)


@pytest.mark.unit
def test_an_unavailable_entry_score_is_not_explained_away() -> None:
    entry = analysis(roles=(TimeframeRole.REGIME, TimeframeRole.BIAS)).scenarios.bull.entry
    assert entry is not None
    explanation = why_entry_quality(entry)
    assert not explanation.available
    assert explanation.unavailable_reason


@pytest.mark.unit
def test_an_explanation_cannot_claim_to_be_available_with_no_reasons() -> None:
    """The §92 invariant, enforced at construction."""
    with pytest.raises(ValueError, match="lists no reason"):
        Explanation(topic=WhyTopic.SETUP_QUALITY, subject="Kurulum Kalitesi 70/100")


@pytest.mark.unit
def test_an_unavailable_explanation_must_say_why() -> None:
    with pytest.raises(ValueError, match="without saying why"):
        Explanation(
            topic=WhyTopic.SETUP_QUALITY, subject="x", available=False, unavailable_reason="  "
        )


# ----------------------------------------------------------------------
# Scenario state, pending confirmation, contradictions, zones
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_why_the_scenario_is_in_its_state() -> None:
    scenario = analysis().scenarios.bull
    explanation = why_scenario_state(scenario)
    assert explanation.topic is WhyTopic.SCENARIO_STATE
    assert any(item.code.startswith("STATE:") for item in explanation.reasons)
    assert len(explanation.reasons) == 1 + len(scenario.requirements)


@pytest.mark.unit
def test_why_a_confirmation_is_pending_names_the_outstanding_conditions() -> None:
    scenario = analysis().scenarios.bull
    assert scenario.state is ScenarioState.WAITING_FOR_CONFIRMATION
    explanation = why_pending_confirmation(scenario)
    assert explanation.codes == tuple(item.code.value for item in scenario.outstanding_requirements)


@pytest.mark.unit
def test_nothing_pending_is_reported_as_nothing_pending() -> None:
    scenario = analysis(BEARISH_DRIFT).scenarios.bull
    explanation = why_pending_confirmation(scenario)
    if not scenario.outstanding_requirements:
        assert not explanation.available


@pytest.mark.unit
def test_why_a_contradiction_exists_shows_both_sides() -> None:
    conflicted = analyse_multi_timeframe(
        (
            market_view(TimeframeRole.REGIME, drift=BULLISH_DRIFT),
            market_view(TimeframeRole.BIAS, drift=BEARISH_DRIFT),
        )
    )
    contradiction = conflicted.contradictions.contradictions[0]
    explanation = why_contradiction(contradiction)
    assert explanation.topic is WhyTopic.CONTRADICTION
    assert explanation.reasons[0].code == contradiction.contradiction_type.value
    assert len(explanation.reasons) == 1 + len(contradiction.evidence)


@pytest.mark.unit
def test_a_major_contradiction_is_marked_critical() -> None:
    conflicted = analyse_multi_timeframe(
        (
            market_view(TimeframeRole.REGIME, drift=BULLISH_DRIFT),
            market_view(TimeframeRole.BIAS, drift=BEARISH_DRIFT),
        )
    )
    major = next(
        item for item in conflicted.contradictions.contradictions if item.severity.value == "MAJOR"
    )
    assert why_contradiction(major).critical


@pytest.mark.unit
def test_why_a_zone_exists_reports_its_touches_and_score_breakdown() -> None:
    """A ranging market is used deliberately: a market making new highs every
    swing never revisits a level, so it produces no repeated-touch zones."""
    result = analysis(FLAT_DRIFT)
    view = result.views.view_for(TimeframeRole.BIAS)
    assert view is not None
    zones = view.structure.support_zones + view.structure.resistance_zones
    assert zones, "the ranging fixture should produce at least one zone"
    explanation = why_zone(zones[0])
    assert explanation.codes == ("TOUCHES", "ZONE_SCORE")
    assert "olasılık değildir" in explanation.reasons[1].beginner


# ----------------------------------------------------------------------
# Risk - read, never recomputed
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_why_position_size_reads_the_phase_3_result() -> None:
    sizing = permitted_sizing()
    explanation = why_position_size(sizing)
    assert explanation.topic is WhyTopic.POSITION_SIZE
    assert str(sizing.allowed_contracts) in explanation.subject
    assert any(str(sizing.risk_amount) in item.pro for item in explanation.reasons)


@pytest.mark.unit
def test_why_zero_contracts_were_permitted_is_marked_critical() -> None:
    explanation = why_position_size(zero_risk_sizing())
    assert explanation.critical
    assert any("NOT_PERMITTED" in item.code for item in explanation.reasons)


@pytest.mark.unit
def test_why_risk_findings_carries_the_phase_3_threshold() -> None:
    """The threshold travels with the warning so a reader sees it is a
    configured policy - and so this layer never holds one."""
    margin = assess_margin(
        contract(multiplier=verified(100), initial_margin=verified(250)),
        AccountState(equity=Decimal("2500"), used_margin=Decimal("2000")),
        Decimal("105"),
        5,
        TIGHT,
    )
    explanation = why_risk_findings(margin)
    assert explanation.reasons
    for reason in explanation.reasons:
        assert reason.severity is ReasonSeverity.CRITICAL
        assert "threshold=" in reason.pro


@pytest.mark.unit
def test_no_risk_warning_is_reported_as_no_warning() -> None:
    margin = assess_margin(
        contract(multiplier=verified(10), initial_margin=verified(100)),
        AccountState(equity=Decimal("1000000")),
        Decimal("105"),
        1,
        ROOMY,
    )
    explanation = why_risk_findings(margin)
    if not margin.warnings:
        assert not explanation.available


@pytest.mark.unit
def test_why_a_no_trade_block_exists() -> None:
    result = analysis()
    verdict = assess_no_trade(result, EvidenceDirection.BULLISH, sizing=zero_risk_sizing())
    explanation = why_no_trade_block(verdict)
    assert explanation.available
    assert all(item.severity is ReasonSeverity.CRITICAL for item in explanation.reasons)


@pytest.mark.unit
def test_nothing_blocking_is_reported_as_nothing_blocking() -> None:
    verdict = assess_no_trade(analysis(), EvidenceDirection.BULLISH)
    explanation = why_no_trade_block(verdict)
    if not verdict.blocking:
        assert not explanation.available


@pytest.mark.unit
def test_the_why_module_performs_no_risk_arithmetic() -> None:
    """Phase 3 owns the numbers; this layer reads them."""
    tree = ast.parse(Path(why_module.__file__).read_text(encoding="utf-8"))
    forbidden = {
        "size_position",
        "risk_reward",
        "stop_distance",
        "assess_margin",
        "calculate_pnl",
        "score_setup",
        "score_entry",
    }
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert forbidden.isdisjoint(called)


# ----------------------------------------------------------------------
# Score change (§57)
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_changed_component_is_named_as_the_cause() -> None:
    fused = analysis().fused
    before = score_setup(fused, EvidenceDirection.BULLISH)
    after = score_setup(
        fused,
        EvidenceDirection.BULLISH,
        QualityConfig(weights=QualityWeights(regime_suitability=1)),
    )
    explanation = why_score_changed(before, after)
    assert explanation.topic is WhyTopic.SCORE_CHANGE
    assert f"{before.score} → {after.score}" in explanation.subject
    assert any("REGIME_SUITABILITY" in item.code for item in explanation.reasons)


@pytest.mark.unit
def test_a_changed_scoring_model_is_stated_rather_than_left_implicit() -> None:
    """Found by probing: a re-weighted component moves the score without any
    awarded points differing, and the explanation was silently incomplete."""
    fused = analysis().fused
    before = score_setup(fused, EvidenceDirection.BULLISH)
    after = score_setup(
        fused,
        EvidenceDirection.BULLISH,
        QualityConfig(weights=QualityWeights(volume=40, regime_suitability=1)),
    )
    explanation = why_score_changed(before, after)
    assert before.score != after.score
    assert "SCORING_MODEL" in explanation.codes


@pytest.mark.unit
def test_an_identical_pair_has_no_change_to_explain() -> None:
    quality = analysis().scenarios.bull.quality
    assert quality is not None
    explanation = why_score_changed(quality, quality)
    assert not explanation.available
    assert "aynı" in explanation.unavailable_reason


@pytest.mark.unit
def test_the_change_explanation_invents_no_market_event() -> None:
    """§57's own example lists market events - price lost VWAP, volume faded.

    This module never sees a candle, so it cannot observe any of them. It
    names the component that moved and stops, which is the honest limit of
    what two breakdowns can prove.
    """
    fused = analysis().fused
    before = score_setup(fused, EvidenceDirection.BULLISH)
    after = score_setup(
        fused,
        EvidenceDirection.BULLISH,
        QualityConfig(weights=QualityWeights(regime_suitability=1)),
    )
    explanation = why_score_changed(before, after)
    invented = ("VWAP'ı kaybetti", "hacim söndü", "reddedildi", "geri döndü")
    for reason in explanation.reasons:
        for phrase in invented:
            assert phrase not in reason.beginner
        assert reason.source is ReasonSource.COMPONENT_DELTA


@pytest.mark.unit
def test_comparing_opposite_directions_is_refused() -> None:
    result = analysis()
    bull = result.scenarios.bull.quality
    bear = result.scenarios.bear.quality
    assert bull is not None
    assert bear is not None
    with pytest.raises(ValueError, match="one direction"):
        why_score_changed(bull, bear)


# ----------------------------------------------------------------------
# Beginner and Pro share one source
# ----------------------------------------------------------------------


def every_explanation() -> tuple[Explanation, ...]:
    result = analysis()
    scenario = result.scenarios.bull
    assert scenario.quality is not None
    assert scenario.entry is not None
    return (
        why_direction(result, EvidenceDirection.BULLISH),
        why_setup_quality(scenario.quality),
        why_entry_quality(scenario.entry),
        why_scenario_state(scenario),
        why_pending_confirmation(scenario),
        why_position_size(permitted_sizing()),
    )


@pytest.mark.unit
def test_beginner_and_pro_have_one_reason_each_and_the_same_codes() -> None:
    """They are two renderings of one fact, so neither can gain or lose a
    reason the other does not have."""
    for explanation in every_explanation():
        if not explanation.available:
            continue
        assert len(explanation.beginner_texts) == len(explanation.pro_texts)
        assert len(explanation.codes) == len(explanation.reasons)


@pytest.mark.unit
def test_every_reason_is_phrased_for_both_audiences() -> None:
    for explanation in every_explanation():
        for reason in explanation.reasons:
            assert reason.beginner.strip()
            assert reason.pro.strip()
            assert reason.beginner != reason.pro


@pytest.mark.unit
def test_a_reason_missing_one_audience_cannot_be_constructed() -> None:
    with pytest.raises(ValueError, match="both audiences"):
        Reason(code="X", source=ReasonSource.EVIDENCE, beginner="var", pro="   ")


@pytest.mark.unit
def test_the_pro_rendering_is_the_detailed_one() -> None:
    """Beginner explains significance; Pro carries the values."""
    quality = analysis().scenarios.bull.quality
    assert quality is not None
    explanation = why_setup_quality(quality)
    assert any(any(ch.isdigit() for ch in item.pro) for item in explanation.reasons)


# ----------------------------------------------------------------------
# The Phase 7 boundary
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_no_topic_names_a_trade_action() -> None:
    """§92 asks WHY LONG / SHORT / WAIT. Phase 7 owns the synthesis that would
    produce them, so this engine cannot answer those three."""
    forbidden = ("LONG", "SHORT", "WAIT", "BUY", "SELL")
    for topic in WhyTopic:
        for word in forbidden:
            assert word not in topic.value


@pytest.mark.unit
def test_explanations_are_deterministic() -> None:
    result = analysis()
    assert why_direction(result, EvidenceDirection.BULLISH) == why_direction(
        result, EvidenceDirection.BULLISH
    )
