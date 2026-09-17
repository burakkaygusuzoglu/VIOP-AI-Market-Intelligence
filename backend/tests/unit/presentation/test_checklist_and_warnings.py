"""Pre-Trade Checklist (§49) and beginner risk warnings (§109).

The rule most of this file defends: **missing is never PASS.**

Every market and every contract value is TEST_FIXTURE data.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.application.presentation import checklist as checklist_module
from app.application.presentation.checklist import (
    DEFAULT_CRITICAL_ITEMS,
    ChecklistAssessment,
    ChecklistItem,
    ChecklistPolicy,
    ChecklistVerdict,
    CheckStatus,
    assess_checklist,
)
from app.application.presentation.modes import ExperienceMode
from app.application.presentation.review import review_trade
from app.application.presentation.risk_warnings import (
    WarningAudience,
    beginner_risk_warnings,
    warning_texts,
)
from app.application.presentation.safety import violations
from app.domain.analysis.engine import MultiTimeframeAnalysis, analyse_multi_timeframe
from app.domain.analysis.scenarios import ScenarioCase
from app.domain.analysis.timeframes import ROLES_BROADEST_FIRST, TimeframeRole
from app.domain.common.enums import Direction
from app.domain.futures.risk import assess_margin, size_position
from app.domain.market.quality import (
    DataQualityCode,
    DataQualityIssue,
    DataQualityReport,
    DataQualitySeverity,
    DataQualityVerdict,
)
from app.domain.risk.margin import MarginAssessment
from app.domain.risk.reward import risk_reward
from app.domain.risk.sizing import AccountState, PositionSizing, RiskMode, RiskPolicy
from tests.factories_analysis import BEARISH_DRIFT, BULLISH_DRIFT, market_view
from tests.factories_futures import contract, verified

TIGHT = RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("75"))
ROOMY = RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("5000"))
CLEAN_DATA = DataQualityReport(verdict=DataQualityVerdict.ACCEPTED)
BAD_DATA = DataQualityReport(
    verdict=DataQualityVerdict.BLOCKED,
    issues=(
        DataQualityIssue(
            code=DataQualityCode.OUT_OF_ORDER,
            severity=DataQualitySeverity.BLOCK,
            message="fixture",
        ),
    ),
)


def analysis(
    drift: float = BULLISH_DRIFT,
    roles: tuple[TimeframeRole, ...] = ROLES_BROADEST_FIRST,
) -> MultiTimeframeAnalysis:
    return analyse_multi_timeframe(tuple(market_view(role, drift=drift) for role in roles))


def permitted_sizing() -> PositionSizing:
    return size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10), initial_margin=verified(100)),
        AccountState(equity=Decimal("100000")),
        ROOMY,
    )


def zero_risk_sizing() -> PositionSizing:
    return size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(100)),
        AccountState(equity=Decimal("2500")),
        TIGHT,
    )


def undetermined_sizing() -> PositionSizing:
    return size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10), tick_size=verified("0.05")),
        AccountState(equity=Decimal("100000")),
        ROOMY,
    )


GOOD_RR = risk_reward(Direction.LONG, Decimal("105"), Decimal("104"), Decimal("108"))
POOR_RR = risk_reward(Direction.LONG, Decimal("105"), Decimal("104"), Decimal("105.5"))


def run(**kwargs: Any) -> ChecklistAssessment:
    return assess_checklist(analysis(), ScenarioCase.BULL, **kwargs)


def healthy(**overrides: Any) -> ChecklistAssessment:
    kwargs: dict[str, Any] = {
        "sizing": permitted_sizing(),
        "risk_reward": GOOD_RR,
        "data_quality": CLEAN_DATA,
        "stop_defined": True,
    }
    kwargs.update(overrides)
    return run(**kwargs)


# ----------------------------------------------------------------------
# The §49 list itself
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_all_eleven_section_49_checks_are_present_in_order() -> None:
    expected = (
        "TREND_IDENTIFIED",
        "SETUP_VALID",
        "ENTRY_TRIGGER_CONFIRMED",
        "STOP_DEFINED",
        "RISK_CALCULATED",
        "RISK_REWARD_ACCEPTABLE",
        "POSITION_SIZE_VALID",
        "VOLUME_CONFIRMATION",
        "NO_MAJOR_CONTRADICTION",
        "DATA_QUALITY_ACCEPTABLE",
        "LIQUIDITY_ACCEPTABLE",
    )
    assert tuple(item.value for item in ChecklistItem) == expected
    assert tuple(item.item.value for item in healthy().results) == expected


@pytest.mark.unit
def test_every_result_carries_an_explicit_reason() -> None:
    for result in healthy().results:
        assert result.status in set(CheckStatus)
        assert result.reason.strip()


@pytest.mark.unit
def test_the_supported_checks_pass_with_full_inputs() -> None:
    assessment = healthy()
    assert assessment.verdict is ChecklistVerdict.SUFFICIENT
    for item in DEFAULT_CRITICAL_ITEMS:
        result = assessment.result_for(item)
        assert result is not None
        assert result.status is CheckStatus.PASS


# ----------------------------------------------------------------------
# Missing is never PASS
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_supplying_nothing_is_incomplete_never_sufficient() -> None:
    """The failure this whole module exists to prevent.

    Only the checks that *need* a supplied input are asserted here.
    `NO_MAJOR_CONTRADICTION` is critical but reads the analysis, which is
    always present, so it can legitimately pass with nothing else supplied -
    asserting otherwise would be testing the wrong invariant.
    """
    assessment = run()
    assert assessment.verdict is ChecklistVerdict.INCOMPLETE
    assert not assessment.all_passed

    input_dependent = DEFAULT_CRITICAL_ITEMS - {
        ChecklistItem.NO_MAJOR_CONTRADICTION,
    }
    for item in input_dependent:
        result = assessment.result_for(item)
        assert result is not None
        assert result.status is not CheckStatus.PASS
        assert not result.evaluated


@pytest.mark.unit
@pytest.mark.parametrize(
    ("item", "overrides"),
    (
        (ChecklistItem.STOP_DEFINED, {"stop_defined": None, "sizing": None}),
        (ChecklistItem.RISK_CALCULATED, {"sizing": None}),
        (ChecklistItem.RISK_REWARD_ACCEPTABLE, {"risk_reward": None}),
        (ChecklistItem.POSITION_SIZE_VALID, {"sizing": None}),
        (ChecklistItem.DATA_QUALITY_ACCEPTABLE, {"data_quality": None}),
    ),
)
def test_an_unsupplied_input_never_passes_its_check(
    item: ChecklistItem, overrides: dict[str, object]
) -> None:
    result = healthy(**overrides).result_for(item)
    assert result is not None
    assert result.status is not CheckStatus.PASS
    assert not result.evaluated


@pytest.mark.unit
def test_a_missing_stop_is_a_failure_not_a_valid_stop() -> None:
    """Explicitly absent differs from nobody said - the first is a failure."""
    explicit = healthy(stop_defined=False).result_for(ChecklistItem.STOP_DEFINED)
    unstated = healthy(stop_defined=None, sizing=None).result_for(ChecklistItem.STOP_DEFINED)
    assert explicit is not None
    assert unstated is not None
    assert explicit.status is CheckStatus.FAIL
    assert explicit.evaluated
    assert unstated.status is CheckStatus.WARNING
    assert not unstated.evaluated


@pytest.mark.unit
def test_a_missing_trigger_is_a_warning_not_a_permanent_veto() -> None:
    """A setup that has not triggered is pending, not defective.

    Failing it would make every forming trade insufficient and collapse the
    WAIT / NO TRADE distinction Phase 4 preserved.
    """
    result = healthy().result_for(ChecklistItem.ENTRY_TRIGGER_CONFIRMED)
    assert result is not None
    assert result.status is CheckStatus.WARNING
    assert not result.critical
    assert healthy().verdict is ChecklistVerdict.SUFFICIENT


@pytest.mark.unit
def test_unavailable_volume_is_not_weak_volume() -> None:
    result = healthy().result_for(ChecklistItem.VOLUME_CONFIRMATION)
    assert result is not None
    assert result.status is not CheckStatus.PASS
    assert "zayıf olduğu anlamına gelmez" in result.reason


# ----------------------------------------------------------------------
# Liquidity: the documented capability gap
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_liquidity_can_never_pass_without_data() -> None:
    """§49 requires the check; no order book or bid/ask feed exists."""
    for assessment in (healthy(), run()):
        result = assessment.result_for(ChecklistItem.LIQUIDITY_ACCEPTABLE)
        assert result is not None
        assert result.status is not CheckStatus.PASS
        assert not result.evaluated
        assert "değerlendirilmedi" in result.reason


@pytest.mark.unit
def test_liquidity_is_not_critical_by_default() -> None:
    """A known, documented gap must not block every trade."""
    assert ChecklistItem.LIQUIDITY_ACCEPTABLE not in DEFAULT_CRITICAL_ITEMS
    assert healthy().verdict is ChecklistVerdict.SUFFICIENT


@pytest.mark.unit
def test_a_stricter_policy_can_fail_liquidity_instead() -> None:
    strict = ChecklistPolicy(warn_on_unevaluated_liquidity=False)
    result = healthy(policy=strict).result_for(ChecklistItem.LIQUIDITY_ACCEPTABLE)
    assert result is not None
    assert result.status is CheckStatus.FAIL


# ----------------------------------------------------------------------
# Critical failures produce TRADE QUALITY INSUFFICIENT
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("label", "overrides"),
    (
        ("risk not permitted", {"sizing": zero_risk_sizing()}),
        ("poor risk/reward", {"risk_reward": POOR_RR}),
        ("bad data", {"data_quality": BAD_DATA}),
        ("missing stop", {"stop_defined": False}),
    ),
)
def test_a_critical_failure_produces_trade_quality_insufficient(
    label: str, overrides: dict[str, object]
) -> None:
    assessment = healthy(**overrides)
    assert assessment.verdict is ChecklistVerdict.TRADE_QUALITY_INSUFFICIENT, label
    assert assessment.is_insufficient
    assert assessment.critical_failures


@pytest.mark.unit
def test_a_major_contradiction_fails_the_contradiction_check() -> None:
    conflicted = analyse_multi_timeframe(
        (
            market_view(TimeframeRole.REGIME, drift=BULLISH_DRIFT),
            market_view(TimeframeRole.BIAS, drift=BEARISH_DRIFT),
        )
    )
    assessment = assess_checklist(
        conflicted,
        ScenarioCase.BULL,
        sizing=permitted_sizing(),
        risk_reward=GOOD_RR,
        data_quality=CLEAN_DATA,
        stop_defined=True,
    )
    result = assessment.result_for(ChecklistItem.NO_MAJOR_CONTRADICTION)
    assert result is not None
    assert result.status is CheckStatus.FAIL
    assert assessment.verdict is ChecklistVerdict.TRADE_QUALITY_INSUFFICIENT


@pytest.mark.unit
def test_undetermined_sizing_does_not_pass_and_does_not_fail() -> None:
    """An unknown constraint is not a satisfied one, and not a proven fault."""
    result = healthy(sizing=undetermined_sizing()).result_for(ChecklistItem.POSITION_SIZE_VALID)
    assert result is not None
    assert result.status is CheckStatus.WARNING
    assert not result.evaluated


@pytest.mark.unit
def test_a_warning_only_result_is_not_reported_as_all_passed() -> None:
    """SUFFICIENT tolerates non-critical warnings; `all_passed` does not."""
    assessment = healthy()
    assert assessment.verdict is ChecklistVerdict.SUFFICIENT
    assert assessment.warnings
    assert not assessment.all_passed


@pytest.mark.unit
def test_criticality_is_configurable() -> None:
    lenient = ChecklistPolicy(critical_items=frozenset())
    assert healthy(stop_defined=False, policy=lenient).verdict is ChecklistVerdict.SUFFICIENT


@pytest.mark.unit
def test_the_checklist_is_deterministic() -> None:
    assert healthy() == healthy()


# ----------------------------------------------------------------------
# It assesses; it does not trade
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_checklist_creates_no_trade_and_chooses_no_direction() -> None:
    tree = ast.parse(Path(checklist_module.__file__).read_text(encoding="utf-8"))
    forbidden = {"open_position", "place_order", "submit", "execute", "paper_trade", "decide"}
    names = {
        node.name.lstrip("_").lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.ClassDef)
    }
    assert forbidden.isdisjoint(names)
    assert {"LONG", "SHORT", "WAIT"}.isdisjoint({item.value for item in ChecklistVerdict})


# ----------------------------------------------------------------------
# §109 beginner risk warnings
# ----------------------------------------------------------------------


def margin_under_pressure() -> MarginAssessment:
    return assess_margin(
        contract(multiplier=verified(100), initial_margin=verified(250)),
        AccountState(equity=Decimal("2500"), used_margin=Decimal("2000")),
        Decimal("105"),
        5,
        TIGHT,
    )


@pytest.mark.unit
def test_phase_3_warnings_are_translated_not_re_derived() -> None:
    """One warning in, one warning out, with the engine's own values kept."""
    margin = margin_under_pressure()
    warnings = beginner_risk_warnings(margin=margin)
    assert {item.code for item in warnings} == {item.code.value for item in margin.warnings}

    by_code = {item.code.value: item for item in margin.warnings}
    for translated in warnings:
        source = by_code[translated.code]
        assert translated.observed == source.observed
        assert translated.threshold == source.threshold
        assert source.message in translated.pro


@pytest.mark.unit
def test_the_configured_threshold_travels_with_the_warning() -> None:
    warnings = beginner_risk_warnings(margin=margin_under_pressure())
    assert warnings
    for item in warnings:
        assert "threshold=" in item.pro


@pytest.mark.unit
def test_a_missing_stop_produces_a_beginner_warning() -> None:
    warnings = beginner_risk_warnings(stop_defined=False)
    assert [item.code for item in warnings] == ["NO_STOP_DEFINED"]
    assert "azami kaybı belirsizdir" in warnings[0].beginner


@pytest.mark.unit
def test_zero_permitted_contracts_produces_a_beginner_warning() -> None:
    warnings = beginner_risk_warnings(sizing=zero_risk_sizing())
    assert [item.code for item in warnings] == ["RISK_NOT_PERMITTED"]


@pytest.mark.unit
def test_poor_risk_reward_uses_the_supplied_floor_not_its_own() -> None:
    """This module holds no threshold; the caller passes the configured one."""
    warnings = beginner_risk_warnings(risk_reward=POOR_RR, minimum_risk_reward=Decimal("1.5"))
    assert [item.code for item in warnings] == ["POOR_RISK_REWARD"]
    assert warnings[0].threshold == Decimal("1.5")

    assert beginner_risk_warnings(risk_reward=POOR_RR) == ()


@pytest.mark.unit
def test_omitting_an_input_produces_no_reassuring_silence() -> None:
    """No margin supplied means no margin warnings - and the checklist is
    where the absence is reported as unevaluated."""
    assert beginner_risk_warnings() == ()
    risk = run().result_for(ChecklistItem.RISK_CALCULATED)
    assert risk is not None
    assert not risk.passed


@pytest.mark.unit
def test_both_audiences_receive_the_same_warnings() -> None:
    """§109: never hide critical risk information inside Pro Mode."""
    warnings = beginner_risk_warnings(
        margin=margin_under_pressure(), sizing=zero_risk_sizing(), stop_defined=False
    )
    beginner = warning_texts(warnings, WarningAudience.BEGINNER)
    pro = warning_texts(warnings, WarningAudience.PRO)
    assert len(beginner) == len(pro) == len(warnings)
    assert beginner != pro


@pytest.mark.unit
@pytest.mark.parametrize("mode", tuple(ExperienceMode))
def test_a_review_shows_the_same_warning_codes_in_every_mode(mode: ExperienceMode) -> None:
    review = review_trade(
        analysis(),
        ScenarioCase.BULL,
        mode=mode,
        margin=margin_under_pressure(),
        sizing=zero_risk_sizing(),
        stop_defined=False,
    )
    assert review.shows_warnings
    assert [item.code for item in review.warnings] == [
        "HIGH_MARGIN_UTILIZATION",
        "EXCESSIVE_EFFECTIVE_LEVERAGE",
        "NO_STOP_DEFINED",
        "RISK_NOT_PERMITTED",
    ]


@pytest.mark.unit
def test_no_warning_or_check_makes_a_forbidden_claim() -> None:
    review = review_trade(
        analysis(),
        ScenarioCase.BULL,
        margin=margin_under_pressure(),
        sizing=zero_risk_sizing(),
        risk_reward=POOR_RR,
        data_quality=BAD_DATA,
        stop_defined=False,
    )
    for warning in review.warnings:
        assert not violations(warning.beginner), warning.code
        assert not violations(warning.pro), warning.code
    for result in review.checklist.results:
        assert not violations(result.reason), result.item.value


@pytest.mark.unit
def test_a_review_is_deterministic() -> None:
    result = analysis()
    first = review_trade(result, ScenarioCase.BULL, sizing=permitted_sizing())
    second = review_trade(result, ScenarioCase.BULL, sizing=permitted_sizing())
    assert first.checklist == second.checklist
    assert first.warnings == second.warnings
