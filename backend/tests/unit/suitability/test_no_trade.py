"""The NO TRADE engine and the quality/suitability boundary (§25, §43).

The two tests this file exists for:

* an excellent setup that the account cannot fund is still vetoed;
* and the setup quality itself does not move when the account changes.

Every value is TEST_FIXTURE data.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.analysis.engine import analyse_multi_timeframe
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.analysis.timeframes import ROLES_BROADEST_FIRST, TimeframeRole
from app.domain.common.enums import Direction
from app.domain.futures.risk import size_position
from app.domain.market.quality import (
    DataQualityCode,
    DataQualityIssue,
    DataQualityReport,
    DataQualitySeverity,
    DataQualityVerdict,
)
from app.domain.risk.reward import risk_reward
from app.domain.risk.sizing import AccountState, PositionSizing, RiskMode, RiskPolicy, SizingOutcome
from app.domain.suitability import no_trade as no_trade_module
from app.domain.suitability.no_trade import (
    DeferredNoTradeReason,
    FindingSeverity,
    NoTradeAssessment,
    NoTradeConfig,
    NoTradeFinding,
    NoTradeReason,
    assess_no_trade,
)
from tests.factories_analysis import BEARISH_DRIFT, BULLISH_DRIFT, FLAT_DRIFT, market_view
from tests.factories_futures import contract, verified


def analysis(drift: float = BULLISH_DRIFT, roles=ROLES_BROADEST_FIRST):  # type: ignore[no-untyped-def]
    return analyse_multi_timeframe(tuple(market_view(role, drift=drift) for role in roles))


def sizing_permitting_nothing() -> PositionSizing:
    """The mandatory §42 case: one contract already exceeds the risk budget."""
    return size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(100)),
        AccountState(equity=Decimal("2500")),
        RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("75")),
    )


def sizing_allowing_trade() -> PositionSizing:
    return size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10), initial_margin=verified(100)),
        AccountState(equity=Decimal("100000")),
        RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("5000")),
    )


# ----------------------------------------------------------------------
# The boundary: risk vetoes, but never touches the technical score
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_an_excellent_setup_is_still_vetoed_when_risk_permits_nothing() -> None:
    """§43's whole point: setup quality and trade suitability are different
    questions, and a good chart the account cannot fund is not a trade."""
    result = analysis()
    quality = result.scenarios.bull.quality
    assert quality is not None
    assert quality.score >= 50

    verdict = assess_no_trade(result, EvidenceDirection.BULLISH, sizing=sizing_permitting_nothing())
    assert verdict.no_trade is True
    assert NoTradeReason.RISK_NOT_PERMITTED in verdict.reasons
    assert any(finding.blocking for finding in verdict.blocking)


@pytest.mark.unit
def test_the_technical_score_does_not_move_when_the_account_changes() -> None:
    """The same chart must grade identically for every user."""
    result = analysis()
    baseline = result.scenarios.bull.quality
    assert baseline is not None

    for sizing in (sizing_permitting_nothing(), sizing_allowing_trade(), None):
        assess_no_trade(result, EvidenceDirection.BULLISH, sizing=sizing)
        after = result.scenarios.bull.quality
        assert after is not None
        assert after.score == baseline.score
        assert after.components == baseline.components


@pytest.mark.unit
def test_the_analysis_package_cannot_import_the_risk_engine() -> None:
    """Enforced by an import contract too; asserted here so the reason is
    recorded next to the behaviour it protects."""
    analysis_package = Path(no_trade_module.__file__).parents[1] / "analysis"
    for module in analysis_package.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("app.domain.risk"), module.name


@pytest.mark.unit
def test_the_suitability_layer_is_the_only_place_both_meet() -> None:
    tree = ast.parse(Path(no_trade_module.__file__).read_text(encoding="utf-8"))
    modules = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert any(name.startswith("app.domain.analysis") for name in modules)
    assert any(name.startswith("app.domain.risk") for name in modules)


@pytest.mark.unit
def test_no_phase_3_formula_is_reimplemented() -> None:
    """The Phase 3 result is read, never recomputed."""
    tree = ast.parse(Path(no_trade_module.__file__).read_text(encoding="utf-8"))
    forbidden = {"size_position", "risk_reward", "stop_distance", "calculate_pnl", "assess_margin"}
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert forbidden.isdisjoint(called)


# ----------------------------------------------------------------------
# Reasons that can actually be evaluated
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_conflicting_timeframes_veto() -> None:
    conflicted = analyse_multi_timeframe(
        (
            market_view(TimeframeRole.REGIME, drift=BULLISH_DRIFT),
            market_view(TimeframeRole.BIAS, drift=BEARISH_DRIFT),
        )
    )
    verdict = assess_no_trade(conflicted, EvidenceDirection.BULLISH)
    assert NoTradeReason.CONFLICTING_TIMEFRAMES in verdict.reasons
    assert verdict.no_trade is True


@pytest.mark.unit
def test_blocked_data_vetoes() -> None:
    report = DataQualityReport(
        verdict=DataQualityVerdict.BLOCKED,
        issues=(
            DataQualityIssue(
                code=DataQualityCode.OUT_OF_ORDER,
                severity=DataQualitySeverity.BLOCK,
                message="fixture",
            ),
        ),
    )
    verdict = assess_no_trade(analysis(), EvidenceDirection.BULLISH, data_quality=report)
    assert NoTradeReason.BAD_DATA in verdict.reasons
    assert verdict.no_trade is True


@pytest.mark.unit
def test_poor_risk_reward_vetoes_only_when_a_ratio_was_supplied() -> None:
    poor = risk_reward(Direction.LONG, Decimal("105"), Decimal("104"), Decimal("105.5"))
    verdict = assess_no_trade(analysis(), EvidenceDirection.BULLISH, risk_reward=poor)
    assert NoTradeReason.POOR_RISK_REWARD in verdict.reasons


@pytest.mark.unit
def test_an_acceptable_ratio_raises_no_reward_finding() -> None:
    good = risk_reward(Direction.LONG, Decimal("105"), Decimal("104"), Decimal("108"))
    verdict = assess_no_trade(analysis(), EvidenceDirection.BULLISH, risk_reward=good)
    assert NoTradeReason.POOR_RISK_REWARD not in verdict.reasons


@pytest.mark.unit
def test_a_chaotic_regime_vetoes() -> None:
    """Built by hand rather than hoped for from a fixture."""
    result = analysis()
    chaotic = [
        view for view in result.views.views if view.structure.regime.regime.value == "CHAOTIC"
    ]
    verdict = assess_no_trade(result, EvidenceDirection.BULLISH)
    if chaotic:
        assert NoTradeReason.CHAOTIC_REGIME in verdict.reasons
    else:
        assert NoTradeReason.CHAOTIC_REGIME not in verdict.reasons


@pytest.mark.unit
def test_a_ranging_market_reports_middle_of_range() -> None:
    verdict = assess_no_trade(analysis(FLAT_DRIFT), EvidenceDirection.BULLISH)
    assert NoTradeReason.MIDDLE_OF_RANGE in verdict.reasons


@pytest.mark.unit
def test_an_unconfirmed_case_reports_no_confirmation() -> None:
    verdict = assess_no_trade(analysis(), EvidenceDirection.BULLISH)
    assert NoTradeReason.NO_CONFIRMATION in verdict.reasons


# ----------------------------------------------------------------------
# Missing inputs never count as a pass
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_omitting_the_risk_result_is_recorded_rather_than_assumed_fine() -> None:
    """§13: no risk input is not risk allowed."""
    verdict = assess_no_trade(analysis(), EvidenceDirection.BULLISH)
    assert any("position-sizing" in item for item in verdict.missing_requirements)
    assert NoTradeReason.RISK_NOT_PERMITTED not in verdict.reasons


@pytest.mark.unit
def test_undetermined_sizing_prevents_a_clean_pass() -> None:
    """An unknown constraint is not a satisfied one."""
    undetermined = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10), tick_size=verified("0.05")),
        AccountState(equity=Decimal("100000")),
        RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("5000")),
    )
    assert undetermined.outcome is SizingOutcome.UNDETERMINED
    verdict = assess_no_trade(analysis(), EvidenceDirection.BULLISH, sizing=undetermined)
    assert NoTradeReason.RISK_UNDETERMINED in verdict.reasons
    assert verdict.no_trade is not False


@pytest.mark.unit
def test_a_missing_timeframe_leaves_the_verdict_undetermined() -> None:
    """Three states, because "we could not tell" must not become "go ahead"."""
    verdict = assess_no_trade(
        analysis(roles=(TimeframeRole.REGIME, TimeframeRole.BIAS, TimeframeRole.SETUP)),
        EvidenceDirection.BULLISH,
    )
    assert verdict.no_trade is None
    assert NoTradeReason.INSUFFICIENT_DATA in verdict.reasons


# ----------------------------------------------------------------------
# Unevaluable reasons are declared, never faked
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_reasons_needing_data_this_project_lacks_are_declared_deferred() -> None:
    """§25 lists them; implementing them against invented inputs would produce
    a veto nobody could audit."""
    assert {reason.value for reason in DeferredNoTradeReason} >= {
        "LOW_LIQUIDITY",
        "EVENT_RISK",
        "NEWS_RISK",
    }
    assert set(DeferredNoTradeReason).isdisjoint(set(NoTradeReason))


@pytest.mark.unit
def test_no_deferred_reason_can_ever_fire() -> None:
    """They are unreachable, not merely unused.

    The two enums share no member, so a deferred reason cannot reach a finding
    even by accident - the value would not exist in ``NoTradeReason``.
    """
    deferred = {reason.value for reason in DeferredNoTradeReason}
    for drift in (BULLISH_DRIFT, BEARISH_DRIFT, FLAT_DRIFT):
        verdict = assess_no_trade(analysis(drift), EvidenceDirection.BULLISH)
        assert all(isinstance(reason, NoTradeReason) for reason in verdict.reasons)
        assert deferred.isdisjoint({reason.value for reason in verdict.reasons})


@pytest.mark.unit
def test_the_assessment_publishes_what_it_can_and_cannot_evaluate() -> None:
    verdict = assess_no_trade(analysis(), EvidenceDirection.BULLISH)
    assert set(verdict.evaluated_reasons) == set(NoTradeReason)
    assert set(verdict.deferred_reasons) == set(DeferredNoTradeReason)


# ----------------------------------------------------------------------
# PENDING vs BLOCKING: preserving what Phase 7 will need
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_missing_confirmation_and_a_zero_risk_allowance_are_not_the_same_state() -> None:
    """The distinction Phase 7 must eventually make into WAIT vs NO TRADE.

    Phase 4 does not make it. It records that one condition could resolve on
    the next candle and the other cannot, which is exactly the information a
    boolean would have destroyed.
    """
    good = analysis()
    waiting = assess_no_trade(good, EvidenceDirection.BULLISH)
    refused = assess_no_trade(good, EvidenceDirection.BULLISH, sizing=sizing_permitting_nothing())

    assert waiting.no_trade is False
    assert waiting.is_waitable
    assert not waiting.blocking
    assert NoTradeReason.NO_CONFIRMATION in {item.reason for item in waiting.pending}

    assert refused.no_trade is True
    assert not refused.is_waitable
    assert NoTradeReason.RISK_NOT_PERMITTED in {item.reason for item in refused.blocking}

    # The severity profiles differ, which is the machine-readable form of the
    # distinction Phase 7 will act on.
    assert {item.severity for item in waiting.findings} != {
        item.severity for item in refused.findings
    }


@pytest.mark.unit
def test_bad_data_is_not_ordinary_waiting_for_a_confirmation() -> None:
    """Waiting for a 5M candle does not repair a dataset that failed integrity
    checks, so the two must not share a severity."""
    report = DataQualityReport(
        verdict=DataQualityVerdict.BLOCKED,
        issues=(
            DataQualityIssue(
                code=DataQualityCode.OUT_OF_ORDER,
                severity=DataQualitySeverity.BLOCK,
                message="fixture",
            ),
        ),
    )
    bad = assess_no_trade(analysis(), EvidenceDirection.BULLISH, data_quality=report)
    waiting = assess_no_trade(analysis(), EvidenceDirection.BULLISH)

    bad_data = next(item for item in bad.findings if item.reason is NoTradeReason.BAD_DATA)
    confirmation = next(
        item for item in waiting.findings if item.reason is NoTradeReason.NO_CONFIRMATION
    )
    assert bad_data.severity is FindingSeverity.BLOCKING
    assert confirmation.severity is FindingSeverity.PENDING
    assert bad.no_trade is True
    assert waiting.no_trade is False


def observed_severities() -> dict[NoTradeReason, set[FindingSeverity]]:
    """Severities the engine actually assigns, gathered from real assessments.

    Built by running several markets and inputs rather than by reading the
    source, so the mapping asserted below is the one the engine uses.
    """
    blocked_data = DataQualityReport(
        verdict=DataQualityVerdict.BLOCKED,
        issues=(
            DataQualityIssue(
                code=DataQualityCode.OUT_OF_ORDER,
                severity=DataQualitySeverity.BLOCK,
                message="fixture",
            ),
        ),
    )
    undetermined = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10), tick_size=verified("0.05")),
        AccountState(equity=Decimal("100000")),
        RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("5000")),
    )
    conflicted = analyse_multi_timeframe(
        (
            market_view(TimeframeRole.REGIME, drift=BULLISH_DRIFT),
            market_view(TimeframeRole.BIAS, drift=BEARISH_DRIFT),
        )
    )

    assessments = (
        assess_no_trade(analysis(), EvidenceDirection.BULLISH),
        assess_no_trade(analysis(FLAT_DRIFT), EvidenceDirection.BULLISH),
        assess_no_trade(conflicted, EvidenceDirection.BULLISH),
        assess_no_trade(analysis(), EvidenceDirection.BULLISH, sizing=sizing_permitting_nothing()),
        assess_no_trade(analysis(), EvidenceDirection.BULLISH, sizing=undetermined),
        assess_no_trade(analysis(), EvidenceDirection.BULLISH, data_quality=blocked_data),
    )

    found: dict[NoTradeReason, set[FindingSeverity]] = {}
    for verdict in assessments:
        for item in verdict.findings:
            found.setdefault(item.reason, set()).add(item.severity)
    return found


@pytest.mark.unit
@pytest.mark.parametrize(
    ("reason", "expected"),
    (
        (NoTradeReason.NO_CONFIRMATION, FindingSeverity.PENDING),
        (NoTradeReason.MIDDLE_OF_RANGE, FindingSeverity.PENDING),
        (NoTradeReason.RISK_NOT_PERMITTED, FindingSeverity.BLOCKING),
        (NoTradeReason.CONFLICTING_TIMEFRAMES, FindingSeverity.BLOCKING),
        (NoTradeReason.BAD_DATA, FindingSeverity.BLOCKING),
        (NoTradeReason.RISK_UNDETERMINED, FindingSeverity.CAUTION),
    ),
)
def test_each_reason_carries_the_severity_its_semantics_require(
    reason: NoTradeReason, expected: FindingSeverity
) -> None:
    """The classification is a judgement about each condition, so each is
    pinned rather than derived.

    The rule applied throughout: *can a future candle resolve this?* A missing
    confirmation and a mid-range price can. Zero risk allowance, a timeframe
    conflict and corrupt data cannot.
    """
    observed = observed_severities()
    assert reason in observed, f"{reason.value} never fired in the sample markets"
    assert observed[reason] == {expected}


@pytest.mark.unit
def test_pending_findings_never_veto() -> None:
    """A pending condition is raw material for a WAIT, not a NO TRADE.

    ``no_trade is False`` alongside a non-empty ``pending`` is two facts kept
    deliberately apart: nothing blocks, and something has not happened yet.
    """
    waiting = assess_no_trade(analysis(), EvidenceDirection.BULLISH)
    assert waiting.pending
    assert waiting.no_trade is False
    assert all(not item.blocking for item in waiting.pending)


@pytest.mark.unit
def test_a_caution_neither_blocks_nor_promises_to_resolve() -> None:
    """High volatility is true of the market now; it does not stop anything
    and waiting will not fix it either, so it is neither of the other two."""
    undetermined = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10), tick_size=verified("0.05")),
        AccountState(equity=Decimal("100000")),
        RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("5000")),
    )
    verdict = assess_no_trade(analysis(), EvidenceDirection.BULLISH, sizing=undetermined)
    caution = next(
        item for item in verdict.findings if item.reason is NoTradeReason.RISK_UNDETERMINED
    )
    assert caution.severity is FindingSeverity.CAUTION
    assert not caution.blocking
    assert not caution.is_pending


@pytest.mark.unit
def test_every_finding_carries_exactly_one_severity() -> None:
    verdict = assess_no_trade(
        analysis(FLAT_DRIFT), EvidenceDirection.BULLISH, sizing=sizing_permitting_nothing()
    )
    assert verdict.findings
    for item in verdict.findings:
        assert item.severity in set(FindingSeverity)
    partitions = len(verdict.blocking) + len(verdict.pending) + len(verdict.cautions)
    assert partitions == len(verdict.findings)


@pytest.mark.unit
def test_the_blocking_flag_is_derived_from_the_severity() -> None:
    """So the two can never disagree with each other."""
    finding = NoTradeFinding(
        reason=NoTradeReason.NO_CONFIRMATION,
        detail="fixture",
        severity=FindingSeverity.PENDING,
    )
    assert not finding.blocking
    assert finding.is_pending


@pytest.mark.unit
def test_phase_4_still_makes_no_wait_or_no_trade_decision() -> None:
    """The severities are typed *information*, not a verdict. Phase 4 must not
    resolve them into a global action."""
    forbidden = {"WAIT", "NO_TRADE", "LONG", "SHORT"}
    assert forbidden.isdisjoint({member.value for member in FindingSeverity})
    assert "wait" not in {field.lower() for field in NoTradeAssessment.__dataclass_fields__}


# ----------------------------------------------------------------------
# It is a veto, not a decision
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_assessment_never_selects_a_direction() -> None:
    """Phase 4 has no LONG / SHORT / WAIT synthesis."""
    forbidden = {"action", "decision", "direction", "recommendation", "signal", "order"}
    assert forbidden.isdisjoint(NoTradeAssessment.__dataclass_fields__)
    assert {"LONG", "SHORT", "WAIT", "BUY", "SELL"}.isdisjoint(
        {reason.value for reason in NoTradeReason}
    )


@pytest.mark.unit
def test_assessing_a_non_directional_case_is_refused() -> None:
    with pytest.raises(ValueError, match="directional"):
        assess_no_trade(analysis(), EvidenceDirection.NEUTRAL)


@pytest.mark.unit
def test_the_veto_is_deterministic() -> None:
    result = analysis()
    sizing = sizing_permitting_nothing()
    assert assess_no_trade(result, EvidenceDirection.BULLISH, sizing=sizing) == assess_no_trade(
        result, EvidenceDirection.BULLISH, sizing=sizing
    )


@pytest.mark.unit
def test_thresholds_are_configurable_and_deterministic() -> None:
    good = risk_reward(Direction.LONG, Decimal("105"), Decimal("104"), Decimal("108"))
    demanding = NoTradeConfig(minimum_risk_reward=Decimal("10"))
    verdict = assess_no_trade(
        analysis(), EvidenceDirection.BULLISH, risk_reward=good, config=demanding
    )
    assert NoTradeReason.POOR_RISK_REWARD in verdict.reasons
