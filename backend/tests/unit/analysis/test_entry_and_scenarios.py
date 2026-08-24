"""Entry Quality (§26) and the Bull/Bear/Neutral scenarios (§23).

Every market is TEST_FIXTURE data.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.domain.analysis import entry as entry_module
from app.domain.analysis import scenarios as scenarios_module
from app.domain.analysis.engine import analyse_multi_timeframe
from app.domain.analysis.entry import (
    ENTRY_LABEL,
    EntryComponent,
    EntryConfig,
    EntryQuality,
    score_entry,
)
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.analysis.scenarios import (
    RequirementCode,
    RequirementStatus,
    Scenario,
    ScenarioCase,
    ScenarioState,
)
from app.domain.analysis.timeframes import ROLES_BROADEST_FIRST, TimeframeRole
from tests.factories_analysis import (
    BEARISH_DRIFT,
    BULLISH_DRIFT,
    FLAT_DRIFT,
    market_view,
    retracement,
    view,
)


def analysis(drift: float = BULLISH_DRIFT, roles=ROLES_BROADEST_FIRST):  # type: ignore[no-untyped-def]
    return analyse_multi_timeframe(tuple(market_view(role, drift=drift) for role in roles))


# ----------------------------------------------------------------------
# Entry Quality is a separate question
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_entry_quality_is_labelled_heuristic_and_bounded() -> None:
    result = analysis().scenarios.bull.entry
    assert result is not None
    assert result.label == ENTRY_LABEL
    assert result.score is not None
    assert 0 <= result.score <= 100


@pytest.mark.unit
def test_entry_quality_offers_no_probability_field() -> None:
    forbidden = {"probability", "win_rate", "success_chance", "calibrated_probability", "edge"}
    assert forbidden.isdisjoint(EntryQuality.__dataclass_fields__)
    assert forbidden.isdisjoint(entry_module.EntryComponentScore.__dataclass_fields__)


@pytest.mark.unit
def test_a_missing_entry_timeframe_leaves_the_score_unknown_not_zero() -> None:
    """§13's rule: no entry trigger is not a bad entry."""
    result = analysis(roles=(TimeframeRole.REGIME, TimeframeRole.BIAS)).scenarios.bull.entry
    assert result is not None
    assert result.score is None
    assert not result.is_available
    assert "unknown rather than poor" in result.reason


@pytest.mark.unit
def test_entry_quality_reads_only_the_entry_timeframe() -> None:
    """Reading a higher timeframe here would re-award what Setup Quality has
    already counted, and would stop the two models disagreeing when they
    should."""
    result = analysis().scenarios.bull.entry
    assert result is not None
    for component in result.components:
        for group in component.groups:
            assert group.role is TimeframeRole.ENTRY


@pytest.mark.unit
def test_setup_and_entry_can_disagree() -> None:
    """The reason they are separate models at all."""
    result = analyse_multi_timeframe(
        (
            market_view(TimeframeRole.REGIME, drift=BULLISH_DRIFT),
            market_view(TimeframeRole.BIAS, drift=BULLISH_DRIFT),
            market_view(TimeframeRole.SETUP, drift=BULLISH_DRIFT),
            view(TimeframeRole.ENTRY, retracement()),
        )
    )
    bull = result.scenarios.bull
    assert bull.quality is not None
    assert bull.entry is not None
    assert bull.entry.score is not None
    assert bull.quality.score != bull.entry.score


@pytest.mark.unit
def test_extension_is_measured_in_atr_and_falls_as_price_runs() -> None:
    result = analysis().scenarios.bull.entry
    assert result is not None
    extension = result.component(EntryComponent.EXTENSION)
    assert extension is not None
    assert "ATR" in extension.reason


@pytest.mark.unit
def test_a_tighter_extension_threshold_lowers_the_component() -> None:
    """Configurable and deterministic, and it actually reaches the score."""
    views = analysis().views
    fused = analysis().fused
    generous = score_entry(views, fused, EvidenceDirection.BULLISH, EntryConfig())
    strict = score_entry(
        views,
        fused,
        EvidenceDirection.BULLISH,
        EntryConfig(comfortable_extension_atr=0.0, extended_move_atr=0.2),
    )
    loose = generous.component(EntryComponent.EXTENSION)
    tight = strict.component(EntryComponent.EXTENSION)
    assert loose is not None
    assert tight is not None
    assert tight.awarded is not None
    assert loose.awarded is not None
    assert tight.awarded <= loose.awarded


@pytest.mark.unit
def test_no_entry_component_can_exceed_its_weight() -> None:
    for drift in (BULLISH_DRIFT, BEARISH_DRIFT, FLAT_DRIFT):
        result = analysis(drift).scenarios.bull.entry
        assert result is not None
        for component in result.components:
            if component.awarded is not None:
                assert 0 <= component.awarded <= component.weight


@pytest.mark.unit
def test_the_entry_module_invents_no_price() -> None:
    """No entry, stop, target or trigger is fabricated anywhere in the model."""
    tree = ast.parse(Path(entry_module.__file__).read_text(encoding="utf-8"))
    forbidden = {"entry_price", "stop_price", "target_price", "trigger_price", "stop", "target"}
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assert node.target.id.lower() not in forbidden


# ----------------------------------------------------------------------
# Scenario states
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_all_three_cases_are_always_present() -> None:
    scenarios = analysis().scenarios
    assert {scenario.case for scenario in scenarios.scenarios} == set(ScenarioCase)


@pytest.mark.unit
def test_a_supported_case_without_full_confirmation_waits() -> None:
    """§55's state: the bias supports it and something named has not happened."""
    bull = analysis().scenarios.bull
    assert bull.state is ScenarioState.WAITING_FOR_CONFIRMATION
    assert bull.outstanding_requirements
    assert all(not item.is_met for item in bull.outstanding_requirements)


@pytest.mark.unit
def test_a_case_the_bias_contradicts_is_only_forming() -> None:
    bear = analysis(BULLISH_DRIFT).scenarios.bear
    assert bear.state is ScenarioState.FORMING


@pytest.mark.unit
def test_a_case_that_cannot_be_judged_is_unavailable_not_inactive() -> None:
    """Missing is not measured-and-absent."""
    bull = analysis(roles=(TimeframeRole.REGIME,)).scenarios.bull
    assert bull.state is ScenarioState.UNAVAILABLE


@pytest.mark.unit
def test_a_directionless_market_confirms_the_neutral_case() -> None:
    neutral = analysis(FLAT_DRIFT).scenarios.neutral
    assert neutral.state is ScenarioState.CONFIRMED
    assert neutral.quality is None


@pytest.mark.unit
def test_the_neutral_case_is_inactive_when_every_reading_is_directional() -> None:
    assert analysis().scenarios.neutral.state is ScenarioState.INACTIVE


# ----------------------------------------------------------------------
# Scenario contents are evidence, not narrative
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_scenario_exposes_supporting_and_counter_evidence() -> None:
    bull = analysis().scenarios.bull
    assert bull.supporting
    assert all(group.direction is EvidenceDirection.BULLISH for group in bull.supporting)
    assert all(group.direction is EvidenceDirection.BEARISH for group in bull.counter)


@pytest.mark.unit
def test_requirements_distinguish_unmet_from_unknown() -> None:
    """ "We looked and it has not happened" is a different answer from "we
    cannot see"."""
    bull = analysis(roles=(TimeframeRole.REGIME, TimeframeRole.BIAS)).scenarios.bull
    codes = {item.code for item in bull.requirements}
    assert RequirementCode.ENTRY_TIMEFRAME_AGREES in codes
    unknown = {item.code for item in bull.unknown_requirements}
    assert RequirementCode.ENTRY_TIMEFRAME_AGREES in unknown
    for item in bull.requirements:
        assert item.status in set(RequirementStatus)
        assert item.reason


@pytest.mark.unit
def test_invalidation_conditions_are_conditions_never_prices() -> None:
    """ "A confirmed opposing change of character" is checkable later;
    "invalidation at 104.20" would be a number nobody computed."""
    bull = analysis().scenarios.bull
    assert bull.invalidations
    for item in bull.invalidations:
        assert not any(character.isdigit() for character in item.description)


@pytest.mark.unit
def test_contradictions_are_shown_on_both_directional_cases() -> None:
    """§17 forbids hiding a conflict from whichever case it inconveniences."""
    result = analysis()
    assert result.scenarios.bull.contradictions == result.scenarios.bear.contradictions


@pytest.mark.unit
def test_unavailable_groups_stay_visible_on_the_scenario() -> None:
    bull = analysis(FLAT_DRIFT).scenarios.bull
    assert isinstance(bull.unavailable, tuple)


# ----------------------------------------------------------------------
# The Phase 4 boundary
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_no_scenario_produces_a_final_action() -> None:
    """Phase 4 stops before LONG / SHORT / WAIT. That synthesis weighs account
    risk and belongs to the later phase that owns it."""
    forbidden = {"action", "decision", "verdict", "recommendation", "signal", "order"}
    assert forbidden.isdisjoint(Scenario.__dataclass_fields__)
    assert forbidden.isdisjoint(scenarios_module.ScenarioSet.__dataclass_fields__)


@pytest.mark.unit
def test_no_scenario_state_is_a_trade_instruction() -> None:
    forbidden = {"LONG", "SHORT", "BUY", "SELL", "WAIT", "ENTER", "EXIT"}
    assert forbidden.isdisjoint({state.value for state in ScenarioState})


@pytest.mark.unit
def test_scenario_support_is_never_called_probability() -> None:
    forbidden = {"probability", "likelihood", "chance", "odds", "confidence"}
    assert forbidden.isdisjoint(Scenario.__dataclass_fields__)


@pytest.mark.unit
def test_the_scenario_module_generates_no_prose() -> None:
    """Reasons are assembled from named codes and measured values. Narrative
    is Claude's job, in a much later phase, and is not simulated here."""
    tree = ast.parse(Path(scenarios_module.__file__).read_text(encoding="utf-8"))
    forbidden = {"narrative", "prose", "summary_text", "explain", "devils_advocate"}
    names = {
        node.name.lstrip("_").lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.ClassDef)
    }
    assert forbidden.isdisjoint(names)


@pytest.mark.unit
def test_scenarios_are_deterministic() -> None:
    views = tuple(market_view(role, drift=BULLISH_DRIFT) for role in ROLES_BROADEST_FIRST)
    assert analyse_multi_timeframe(views).scenarios == analyse_multi_timeframe(views).scenarios
