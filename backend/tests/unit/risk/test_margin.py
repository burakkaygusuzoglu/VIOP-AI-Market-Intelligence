"""Margin safety, exposure and leverage (master spec section 44).

All values are TEST_FIXTURE data.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.common.enums import Direction
from app.domain.futures.risk import assess_margin, size_position
from app.domain.risk.margin import RiskWarningCode, effective_leverage, notional_exposure
from app.domain.risk.sizing import (
    AccountState,
    MarginFeasibility,
    RiskMode,
    RiskPolicy,
    SizingOutcome,
)
from tests.factories_futures import contract, unverified, verified

POLICY = RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("75"))
ACCOUNT = AccountState(equity=Decimal("2500"))


# ----------------------------------------------------------------------
# Exposure and leverage
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_notional_exposure_hand_calculated() -> None:
    """105 x 100 x 1 = 10,500 of value controlled."""
    assert notional_exposure(Decimal("105"), Decimal("100"), 1) == Decimal("10500")


@pytest.mark.unit
def test_notional_scales_with_contracts() -> None:
    assert notional_exposure(Decimal("105"), Decimal("100"), 3) == Decimal("31500")


@pytest.mark.unit
def test_effective_leverage_is_notional_over_equity() -> None:
    """10,500 controlled on 2,500 of equity is 4.2x."""
    assert effective_leverage(Decimal("10500"), Decimal("2500")) == Decimal("4.2")


@pytest.mark.unit
def test_leverage_denominator_is_equity_not_margin() -> None:
    """Notional over margin measures the contract; over equity measures you."""
    assert effective_leverage(Decimal("10500"), Decimal("2500")) == Decimal("4.2")
    assert effective_leverage(Decimal("10500"), Decimal("250")) == Decimal("42")


@pytest.mark.unit
def test_leverage_on_a_zero_account_is_undefined() -> None:
    assert effective_leverage(Decimal("10500"), Decimal("0")) is None


# ----------------------------------------------------------------------
# The section 44 panel
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_panel_reports_every_section_44_figure() -> None:
    assessment = assess_margin(
        contract(multiplier=verified(100), initial_margin=verified(250)),
        AccountState(equity=Decimal("2500"), used_margin=Decimal("500")),
        Decimal("105"),
        2,
        POLICY,
        risk_to_stop=Decimal("60"),
    )

    assert assessment.account_equity == Decimal("2500")
    assert assessment.used_margin == Decimal("500")
    assert assessment.free_margin == Decimal("2000")
    assert assessment.required_margin == Decimal("500")
    assert assessment.resulting_used_margin == Decimal("1000")
    assert assessment.margin_utilisation == Decimal("0.4")
    assert assessment.margin_utilisation_percent == Decimal("40.0")
    assert assessment.remaining_free_margin == Decimal("1500")
    assert assessment.notional_exposure == Decimal("21000")
    assert assessment.effective_leverage == Decimal("8.4")
    assert assessment.risk_to_stop == Decimal("60")


@pytest.mark.unit
def test_margin_is_never_presented_as_a_loss_cap() -> None:
    """A futures position can lose more than the margin behind it.

    The assessment therefore has no field a reader could take for a maximum
    loss, and the exposure sits next to the margin so the gap is visible.
    """
    from app.domain.risk.margin import MarginAssessment

    fields = set(MarginAssessment.__slots__)
    forbidden = {"max_loss", "maximum_loss", "worst_case", "loss_cap", "capped_loss"}
    assert not (fields & forbidden)

    assessment = assess_margin(
        contract(multiplier=verified(100), initial_margin=verified(250)),
        ACCOUNT,
        Decimal("105"),
        1,
        POLICY,
    )
    assert assessment.required_margin == Decimal("250")
    assert assessment.notional_exposure == Decimal("10500")
    assert assessment.notional_exposure > assessment.required_margin * 40


# ----------------------------------------------------------------------
# Missing and unverified margin
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_missing_margin_leaves_every_derived_figure_unset() -> None:
    assessment = assess_margin(
        contract(multiplier=verified(100)), ACCOUNT, Decimal("105"), 1, POLICY
    )
    assert assessment.margin_feasibility is MarginFeasibility.MISSING
    assert assessment.required_margin is None
    assert assessment.resulting_used_margin is None
    assert assessment.margin_utilisation is None
    assert assessment.remaining_free_margin is None
    assert RiskWarningCode.MARGIN_UNKNOWN in {w.code for w in assessment.warnings}


@pytest.mark.unit
def test_unverified_margin_is_treated_as_unknown_not_as_a_number() -> None:
    assessment = assess_margin(
        contract(multiplier=verified(100), initial_margin=unverified(250)),
        ACCOUNT,
        Decimal("105"),
        1,
        POLICY,
    )
    assert assessment.margin_feasibility is MarginFeasibility.UNVERIFIED
    assert assessment.required_margin is None
    assert RiskWarningCode.MARGIN_UNKNOWN in {w.code for w in assessment.warnings}


@pytest.mark.unit
def test_exposure_still_computes_without_margin() -> None:
    """Margin and multiplier are separate facts; one missing is not both."""
    assessment = assess_margin(
        contract(multiplier=verified(100)), ACCOUNT, Decimal("105"), 1, POLICY
    )
    assert assessment.notional_exposure == Decimal("10500")
    assert assessment.effective_leverage == Decimal("4.2")


@pytest.mark.unit
def test_an_unverified_multiplier_leaves_exposure_unset() -> None:
    assessment = assess_margin(
        contract(multiplier=unverified(100)), ACCOUNT, Decimal("105"), 1, POLICY
    )
    assert assessment.notional_exposure is None
    assert assessment.effective_leverage is None


# ----------------------------------------------------------------------
# Warnings are policy, not exchange rules
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_high_margin_utilisation_warns_against_the_configured_limit() -> None:
    assessment = assess_margin(
        contract(multiplier=verified(100), initial_margin=verified(1000)),
        ACCOUNT,
        Decimal("105"),
        2,
        POLICY,
    )
    warning = next(
        w for w in assessment.warnings if w.code is RiskWarningCode.HIGH_MARGIN_UTILIZATION
    )
    assert warning.threshold == POLICY.max_margin_utilisation
    assert "user setting, not an exchange rule" in warning.message


@pytest.mark.unit
def test_excessive_leverage_warns_against_the_configured_limit() -> None:
    assessment = assess_margin(
        contract(multiplier=verified(100), initial_margin=verified(250)),
        ACCOUNT,
        Decimal("105"),
        2,
        POLICY,
    )
    warning = next(
        w for w in assessment.warnings if w.code is RiskWarningCode.EXCESSIVE_EFFECTIVE_LEVERAGE
    )
    assert warning.observed == Decimal("8.4")
    assert warning.threshold == Decimal("5")


@pytest.mark.unit
def test_risk_limit_exceeded_warns_when_risk_to_stop_is_over_budget() -> None:
    assessment = assess_margin(
        contract(multiplier=verified(100), initial_margin=verified(250)),
        ACCOUNT,
        Decimal("105"),
        1,
        POLICY,
        risk_to_stop=Decimal("500"),
    )
    warning = next(w for w in assessment.warnings if w.code is RiskWarningCode.RISK_LIMIT_EXCEEDED)
    assert warning.threshold == Decimal("75")


@pytest.mark.unit
def test_thresholds_are_configurable_and_change_the_warnings() -> None:
    relaxed = RiskPolicy(
        mode=RiskMode.FIXED,
        fixed_risk=Decimal("75"),
        max_effective_leverage=Decimal("100"),
        max_margin_utilisation=Decimal("0.99"),
    )
    assessment = assess_margin(
        contract(multiplier=verified(100), initial_margin=verified(250)),
        ACCOUNT,
        Decimal("105"),
        2,
        relaxed,
    )
    codes = {w.code for w in assessment.warnings}
    assert RiskWarningCode.EXCESSIVE_EFFECTIVE_LEVERAGE not in codes
    assert RiskWarningCode.HIGH_MARGIN_UTILIZATION not in codes


@pytest.mark.unit
def test_a_comfortable_position_raises_no_warnings() -> None:
    assessment = assess_margin(
        contract(multiplier=verified(1), initial_margin=verified(50)),
        ACCOUNT,
        Decimal("105"),
        1,
        POLICY,
        risk_to_stop=Decimal("20"),
    )
    assert not assessment.has_warnings


# ----------------------------------------------------------------------
# Margin-aware sizing
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_margin_can_be_the_binding_constraint() -> None:
    """Risk allows 7, margin allows 4; the answer is 4."""
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10), initial_margin=verified(500)),
        ACCOUNT,
        POLICY,
    )
    assert result.maximum_by_risk == 7
    assert result.maximum_by_margin == 5
    assert result.allowed_contracts == 5
    assert result.outcome is SizingOutcome.ALLOWED
    assert "margin" in result.reason


@pytest.mark.unit
def test_risk_can_be_the_binding_constraint() -> None:
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10), initial_margin=verified(100)),
        ACCOUNT,
        POLICY,
    )
    assert result.maximum_by_risk == 7
    assert result.maximum_by_margin == 25
    assert result.allowed_contracts == 7
    assert "risk" in result.reason


@pytest.mark.unit
def test_both_constraints_agreeing_is_handled() -> None:
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10), initial_margin=verified(357)),
        ACCOUNT,
        POLICY,
    )
    assert result.maximum_by_risk == 7
    assert result.maximum_by_margin == 7
    assert result.allowed_contracts == 7


@pytest.mark.unit
def test_insufficient_free_margin_permits_nothing() -> None:
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10), initial_margin=verified(3000)),
        ACCOUNT,
        POLICY,
    )
    assert result.maximum_by_margin == 0
    assert result.allowed_contracts == 0
    assert result.outcome is SizingOutcome.NOT_PERMITTED


@pytest.mark.unit
def test_existing_used_margin_reduces_capacity() -> None:
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10), initial_margin=verified(500)),
        AccountState(equity=Decimal("2500"), used_margin=Decimal("1500")),
        POLICY,
    )
    assert result.maximum_by_margin == 2


@pytest.mark.unit
def test_unknown_margin_makes_the_final_allowance_undetermined() -> None:
    """The critical case: risk says 7, but 7 is not the answer.

    Reporting 7 would present half an analysis as a whole one.
    """
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10), initial_margin=unverified(500)),
        ACCOUNT,
        POLICY,
    )
    assert result.outcome is SizingOutcome.UNDETERMINED
    assert result.maximum_by_risk == 7
    assert result.maximum_by_margin is None
    assert result.allowed_contracts is None
    assert not result.is_tradeable
    assert "cannot be determined" in result.reason


@pytest.mark.unit
def test_zero_from_risk_stays_definite_even_without_margin() -> None:
    """Zero is permitted by any margin, so the answer is knowable."""
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(100)),
        ACCOUNT,
        POLICY,
    )
    assert result.outcome is SizingOutcome.NOT_PERMITTED
    assert result.allowed_contracts == 0


@pytest.mark.unit
def test_a_user_contract_cap_applies_on_top() -> None:
    capped = RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("75"), max_contracts=3)
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10), initial_margin=verified(100)),
        ACCOUNT,
        capped,
    )
    assert result.allowed_contracts == 3
    assert "cap" in result.reason
