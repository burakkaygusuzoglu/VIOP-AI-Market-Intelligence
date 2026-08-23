"""Margin safety and exposure (master spec section 44).

Reports account equity, used margin, free margin, margin utilisation, notional
exposure, effective leverage and risk to stop, with warnings against thresholds
the **user** configured.

**Margin is not maximum loss.** Nothing in this module suggests otherwise, and
``MarginAssessment`` has no field that could be mistaken for a loss cap.
Posting 250 to control 10,500 of notional does not mean 250 is the worst case;
a futures position can lose more than the margin behind it, which is precisely
why the exposure and leverage figures are reported next to the margin ones.

**Every warning threshold is a project or user policy.** "Margin utilisation
above 50%" is a choice this project offers as a default, not a VIOP rule, and
it is presented as such. Section 45 forbids automatically changing a user's
configured real-money risk, so the engine warns and never adjusts.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.arithmetic import as_percent, safe_ratio
from app.domain.futures.contract import FuturesContract
from app.domain.futures.validation import require_calculable
from app.domain.risk.sizing import AccountState, MarginFeasibility, RiskPolicy


@unique
class RiskWarningCode(StrEnum):
    """The section 44 warnings, plus the honest absence."""

    HIGH_MARGIN_UTILIZATION = "HIGH_MARGIN_UTILIZATION"
    EXCESSIVE_EFFECTIVE_LEVERAGE = "EXCESSIVE_EFFECTIVE_LEVERAGE"
    RISK_LIMIT_EXCEEDED = "RISK_LIMIT_EXCEEDED"
    MARGIN_UNKNOWN = "MARGIN_UNKNOWN"
    """Margin feasibility could not be assessed. A warning in its own right:
    an unknown constraint is not a satisfied one."""

    MARGIN_DEFICIT = "MARGIN_DEFICIT"
    """Committed margin already exceeds equity.

    The account is past its own margin, which is a call rather than merely a
    full one. Reported with the deficit amount, because how far past matters."""


@dataclass(frozen=True, slots=True)
class RiskWarning:
    """One warning, with the threshold that produced it.

    The threshold travels with the warning so a reader can see it is a
    configured policy rather than an exchange limit.
    """

    code: RiskWarningCode
    message: str
    observed: Decimal | None = None
    threshold: Decimal | None = None


@dataclass(frozen=True, slots=True)
class MarginAssessment:
    """The section 44 panel for a proposed position.

    ``required_margin`` is ``None`` when the contract's margin is missing or
    unverified, and every figure derived from it is ``None`` too. Missing
    information stays missing.
    """

    account_equity: Decimal
    used_margin: Decimal
    free_margin: Decimal
    contracts: int
    notional_exposure: Decimal | None
    effective_leverage: Decimal | None
    required_margin: Decimal | None
    resulting_used_margin: Decimal | None
    margin_utilisation: Decimal | None
    """Resulting used margin as a fraction of equity."""

    remaining_free_margin: Decimal | None
    margin_deficit: Decimal
    """How far committed margin already exceeds equity; zero when healthy.

    Kept separate from ``free_margin`` so a deficit is visible as a magnitude
    rather than flattened into a zero."""

    available_for_new_positions: Decimal
    """``max(free_margin, 0)`` - what a new position can actually draw on."""

    risk_to_stop: Decimal | None
    margin_feasibility: MarginFeasibility
    warnings: tuple[RiskWarning, ...]

    @property
    def margin_utilisation_percent(self) -> Decimal | None:
        return as_percent(self.margin_utilisation)

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)


def notional_exposure(entry_price: Decimal, multiplier: Decimal, contracts: int) -> Decimal:
    """``entry × multiplier × contracts`` - the value being controlled.

    Under linear valuation only. This is the number that makes the difference
    between margin and risk visible: a small deposit controlling a large
    notional is leverage, and leverage is what turns a modest adverse move into
    a large loss.
    """
    return entry_price * multiplier * contracts


def effective_leverage(exposure: Decimal, account_equity: Decimal) -> Decimal | None:
    """``notional / equity``. ``None`` for a zero account.

    Denominator is **equity**, not margin. Notional over margin measures the
    contract's margin rate; notional over equity measures how exposed *this
    account* is, which is the question section 44 asks.
    """
    return safe_ratio(exposure, account_equity)


def assess_margin(
    contract: FuturesContract,
    account: AccountState,
    entry_price: Decimal,
    contracts: int,
    policy: RiskPolicy,
    risk_to_stop: Decimal | None = None,
) -> MarginAssessment:
    """Build the section 44 panel for a proposed position.

    ``risk_to_stop`` is the money at risk if the stop is hit - supplied by the
    caller because it comes from the sizing step. When given, it is checked
    against the configured risk budget.
    """
    contract.requires_linear_valuation("margin assessment")
    require_calculable(contract, "margin assessment")

    multiplier = contract.authoritative_multiplier()
    exposure = (
        notional_exposure(entry_price, multiplier, contracts)
        if multiplier is not None and contracts > 0
        else None
    )
    leverage = effective_leverage(exposure, account.equity) if exposure is not None else None

    margin_per_contract = contract.authoritative_initial_margin()
    if contract.initial_margin is None:
        feasibility = MarginFeasibility.MISSING
    elif margin_per_contract is None:
        feasibility = MarginFeasibility.UNVERIFIED
    else:
        feasibility = MarginFeasibility.KNOWN

    required: Decimal | None = None
    resulting_used: Decimal | None = None
    utilisation: Decimal | None = None
    remaining: Decimal | None = None
    if margin_per_contract is not None and contracts > 0:
        required = margin_per_contract * contracts
        resulting_used = account.used_margin + required
        utilisation = safe_ratio(resulting_used, account.equity)
        remaining = account.equity - resulting_used

    warnings = _warnings(
        feasibility=feasibility,
        utilisation=utilisation,
        leverage=leverage,
        risk_to_stop=risk_to_stop,
        account=account,
        policy=policy,
    )

    return MarginAssessment(
        account_equity=account.equity,
        used_margin=account.used_margin,
        free_margin=account.free_margin,
        margin_deficit=account.margin_deficit,
        available_for_new_positions=account.available_for_new_positions,
        contracts=contracts,
        notional_exposure=exposure,
        effective_leverage=leverage,
        required_margin=required,
        resulting_used_margin=resulting_used,
        margin_utilisation=utilisation,
        remaining_free_margin=remaining,
        risk_to_stop=risk_to_stop,
        margin_feasibility=feasibility,
        warnings=warnings,
    )


def _warnings(
    *,
    feasibility: MarginFeasibility,
    utilisation: Decimal | None,
    leverage: Decimal | None,
    risk_to_stop: Decimal | None,
    account: AccountState,
    policy: RiskPolicy,
) -> tuple[RiskWarning, ...]:
    found: list[RiskWarning] = []

    if account.is_in_deficit:
        found.append(
            RiskWarning(
                code=RiskWarningCode.MARGIN_DEFICIT,
                message=(
                    f"committed margin {account.used_margin} exceeds equity "
                    f"{account.equity} by {account.margin_deficit}; the account is past "
                    "its own margin and no new position can be funded"
                ),
                observed=account.margin_deficit,
            )
        )

    if feasibility is not MarginFeasibility.KNOWN:
        found.append(
            RiskWarning(
                code=RiskWarningCode.MARGIN_UNKNOWN,
                message=(
                    "initial margin is "
                    + (
                        "not supplied"
                        if feasibility is MarginFeasibility.MISSING
                        else "not a verified current fact"
                    )
                    + "; margin utilisation and free margin cannot be assessed"
                ),
            )
        )

    if utilisation is not None and utilisation > policy.max_margin_utilisation:
        found.append(
            RiskWarning(
                code=RiskWarningCode.HIGH_MARGIN_UTILIZATION,
                message=(
                    f"margin utilisation {as_percent(utilisation)}% exceeds the configured "
                    f"policy limit of {as_percent(policy.max_margin_utilisation)}% "
                    "(a user setting, not an exchange rule)"
                ),
                observed=utilisation,
                threshold=policy.max_margin_utilisation,
            )
        )

    if leverage is not None and leverage > policy.max_effective_leverage:
        found.append(
            RiskWarning(
                code=RiskWarningCode.EXCESSIVE_EFFECTIVE_LEVERAGE,
                message=(
                    f"effective leverage {leverage} exceeds the configured policy limit of "
                    f"{policy.max_effective_leverage} (a user setting, not an exchange rule)"
                ),
                observed=leverage,
                threshold=policy.max_effective_leverage,
            )
        )

    if risk_to_stop is not None:
        budget = policy.risk_amount(account.equity)
        if risk_to_stop > budget:
            found.append(
                RiskWarning(
                    code=RiskWarningCode.RISK_LIMIT_EXCEEDED,
                    message=(
                        f"risk to stop {risk_to_stop} exceeds the configured risk budget "
                        f"of {budget}"
                    ),
                    observed=risk_to_stop,
                    threshold=budget,
                )
            )

    return tuple(found)
