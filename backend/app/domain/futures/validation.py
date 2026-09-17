"""Contract-level checks that report rather than repair.

Two jobs, and the second is the important one.

**Tick-grid validation reports; it never rounds.** A price of ``105.037`` on a
``0.05`` grid is wrong, and the engine says so. Quietly turning it into
``105.05`` would hand the caller a number they never supplied, and a stop
silently moved by a tick is a stop in a different place than the one the risk
calculation used.

**Tick value is cross-checked only where the identity actually holds.** Under
linear valuation ``tick_value == tick_size × multiplier``. That is not a
universal truth about futures - an inverse contract's tick value depends on
price - so the check is gated on ``ValuationModel.LINEAR`` and anything else
fails closed rather than being waved through.

Expiry decisions use an injected ``ClockPort`` and refuse to invent a
last-trading time. See ``contract_state``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.arithmetic import safe_ratio
from app.domain.common.verification import VerifiedValue
from app.domain.futures.contract import (
    ContractState,
    ContractValidationError,
    FuturesContract,
    ValuationModel,
)


@unique
class ContractIssueCode(StrEnum):
    """Every condition contract validation can report."""

    PRICE_OFF_TICK_GRID = "PRICE_OFF_TICK_GRID"
    TICK_VALUE_INCONSISTENT = "TICK_VALUE_INCONSISTENT"
    TICK_VALUE_UNCHECKABLE = "TICK_VALUE_UNCHECKABLE"
    UNVERIFIED_MULTIPLIER = "UNVERIFIED_MULTIPLIER"
    UNVERIFIED_TICK_SIZE = "UNVERIFIED_TICK_SIZE"
    UNVERIFIED_MARGIN = "UNVERIFIED_MARGIN"
    MISSING_MARGIN = "MISSING_MARGIN"
    UNVERIFIED_EXPIRY = "UNVERIFIED_EXPIRY"
    MISSING_EXPIRY = "MISSING_EXPIRY"
    UNSOURCED_VERIFIED_FACT = "UNSOURCED_VERIFIED_FACT"
    """A fact claims VERIFIED_CURRENT_FACT but names no source."""

    UNDATED_VERIFIED_FACT = "UNDATED_VERIFIED_FACT"
    """A fact claims VERIFIED_CURRENT_FACT but carries no ``as_of``."""


@dataclass(frozen=True, slots=True)
class ContractIssue:
    """One finding, with enough context to act on it."""

    code: ContractIssueCode
    message: str
    blocking: bool = False
    """True when the finding makes a real-money calculation unsafe rather than
    merely caveated."""


@dataclass(frozen=True, slots=True)
class TickGridResult:
    """Whether a price sits on the contract's tick grid.

    ``remainder`` is the distance past the last valid tick, so a caller that
    genuinely wants to snap can do so **explicitly**, having been told.
    """

    price: Decimal
    tick_size: Decimal
    on_grid: bool
    remainder: Decimal
    nearest_lower: Decimal
    nearest_upper: Decimal


def check_tick_grid(price: Decimal, tick_size: Decimal) -> TickGridResult:
    """Report whether ``price`` is a whole number of ticks.

    Never mutates the price. The caller is told the remainder and both
    neighbouring ticks and decides for itself.
    """
    if tick_size <= 0:
        raise ValueError(f"tick_size must be positive, got {tick_size}")

    remainder = price % tick_size
    lower = price - remainder
    return TickGridResult(
        price=price,
        tick_size=tick_size,
        on_grid=remainder == 0,
        remainder=remainder,
        nearest_lower=lower,
        nearest_upper=lower if remainder == 0 else lower + tick_size,
    )


def check_tick_value(contract: FuturesContract) -> ContractIssue | None:
    """Cross-check tick value against tick size and multiplier.

    Only meaningful under linear valuation, where one tick of price movement is
    worth ``tick_size × multiplier`` per contract. For any other valuation the
    identity does not hold and the check reports
    ``TICK_VALUE_UNCHECKABLE`` rather than pretending it does.
    """
    if contract.tick_value is None:
        return None

    if contract.valuation is not ValuationModel.LINEAR:
        return ContractIssue(
            code=ContractIssueCode.TICK_VALUE_UNCHECKABLE,
            message=(
                f"tick value cannot be cross-checked for {contract.valuation.value} "
                "valuation; the linear identity does not apply"
            ),
        )

    expected = contract.tick_size.value * contract.multiplier.value
    if contract.tick_value.value != expected:
        return ContractIssue(
            code=ContractIssueCode.TICK_VALUE_INCONSISTENT,
            message=(
                f"tick_value {contract.tick_value.value} does not equal "
                f"tick_size {contract.tick_size.value} x multiplier "
                f"{contract.multiplier.value} = {expected}"
            ),
            blocking=True,
        )
    return None


def contract_issues(contract: FuturesContract) -> tuple[ContractIssue, ...]:
    """Everything worth telling a caller about this contract's metadata.

    Provenance gaps are reported as issues rather than raised, because a
    contract with an unverified margin is still perfectly usable for the
    calculations that do not need margin. The caller decides what it can live
    without.
    """
    issues: list[ContractIssue] = []

    if not contract.multiplier.is_authoritative:
        issues.append(
            ContractIssue(
                code=ContractIssueCode.UNVERIFIED_MULTIPLIER,
                message=(
                    f"multiplier is {contract.multiplier.status.value} from "
                    f"'{contract.multiplier.source}'; contract arithmetic would be unreliable"
                ),
                blocking=True,
            )
        )
    if not contract.tick_size.is_authoritative:
        issues.append(
            ContractIssue(
                code=ContractIssueCode.UNVERIFIED_TICK_SIZE,
                message=(
                    f"tick_size is {contract.tick_size.status.value} from "
                    f"'{contract.tick_size.source}'"
                ),
            )
        )

    if contract.initial_margin is None:
        issues.append(
            ContractIssue(
                code=ContractIssueCode.MISSING_MARGIN,
                message="no initial margin supplied; margin feasibility cannot be decided",
            )
        )
    elif not contract.initial_margin.is_authoritative:
        issues.append(
            ContractIssue(
                code=ContractIssueCode.UNVERIFIED_MARGIN,
                message=(
                    f"initial_margin is {contract.initial_margin.status.value} from "
                    f"'{contract.initial_margin.source}'; margin feasibility is unknown"
                ),
            )
        )

    if contract.expiry is None:
        issues.append(
            ContractIssue(
                code=ContractIssueCode.MISSING_EXPIRY,
                message="no expiry supplied; the contract state cannot be decided",
            )
        )
    elif not contract.expiry.expiry_date.is_authoritative:
        issues.append(
            ContractIssue(
                code=ContractIssueCode.UNVERIFIED_EXPIRY,
                message=(
                    f"expiry date is {contract.expiry.expiry_date.status.value} from "
                    f"'{contract.expiry.expiry_date.source}'"
                ),
            )
        )

    tick_issue = check_tick_value(contract)
    if tick_issue is not None:
        issues.append(tick_issue)

    issues.extend(provenance_issues(contract))
    return tuple(issues)


def provenance_issues(contract: FuturesContract) -> tuple[ContractIssue, ...]:
    """Check that facts claiming to be *current* carry enough to justify it.

    ``VERIFIED_CURRENT_FACT`` is a claim about the present, and the claim is
    only checkable if the value says where it came from and when it was true.
    Margin in particular is revised by exchanges; a verified margin with no
    ``as_of`` cannot be distinguished from one verified two years ago.

    **No freshness duration is invented.** This does not decide that a fact
    older than N days is stale - that would need an exchange revision schedule,
    which is itself a section 118 fact. It only requires that the provenance
    exists, so a human or a later phase can judge it.

    * A blank source on a verified fact is **blocking**: an unattributable
      claim of currency is indistinguishable from a guess wearing the right
      status.
    * A missing ``as_of`` is a **warning**: the value may be perfectly good,
      but "current" cannot be evaluated without knowing when it was current.
    """
    issues: list[ContractIssue] = []
    named: list[tuple[str, VerifiedValue[object] | None]] = [
        ("multiplier", contract.multiplier),
        ("tick_size", contract.tick_size),
        ("tick_value", contract.tick_value),
        ("initial_margin", contract.initial_margin),
        ("maintenance_margin", contract.maintenance_margin),
        ("settlement", contract.settlement),
        ("trading_session", contract.trading_session),
        ("classification", contract.classification),
    ]
    if contract.expiry is not None:
        named.append(("expiry_date", contract.expiry.expiry_date))
        named.append(("last_trading_time", contract.expiry.last_trading_time))

    for name, value in named:
        if value is None or not value.is_authoritative:
            continue
        if not value.source.strip():
            issues.append(
                ContractIssue(
                    code=ContractIssueCode.UNSOURCED_VERIFIED_FACT,
                    message=(
                        f"{name} claims VERIFIED_CURRENT_FACT but names no source; "
                        "an unattributable claim of currency cannot be checked"
                    ),
                    blocking=True,
                )
            )
        if value.as_of is None:
            issues.append(
                ContractIssue(
                    code=ContractIssueCode.UNDATED_VERIFIED_FACT,
                    message=(
                        f"{name} claims VERIFIED_CURRENT_FACT but carries no as_of; "
                        "'current' cannot be evaluated without knowing when it was current"
                    ),
                )
            )

    return tuple(issues)


def blocking_issues(contract: FuturesContract) -> tuple[ContractIssue, ...]:
    """Only the findings that make a money calculation unsafe."""
    return tuple(issue for issue in contract_issues(contract) if issue.blocking)


def require_calculable(contract: FuturesContract, operation: str) -> None:
    """Refuse a contract whose metadata is internally inconsistent.

    Guards every money calculation. Before this existed the tick-value
    consistency check was computed at provider registration and **read by
    nothing**: a contract whose tick value contradicted its own tick size and
    multiplier flowed straight into P&L, sizing and what-if as though the
    finding had never been made.

    Provenance gaps are *not* refused here - the individual engines already
    decline to release an unverified multiplier or margin, and a contract with
    an unverified margin is still perfectly usable for everything that does not
    need margin. What this refuses is metadata that cannot all be true at once.
    """
    contradictions = [
        issue for issue in (check_tick_value(contract),) if issue is not None and issue.blocking
    ]
    if contradictions:
        detail = "; ".join(issue.message for issue in contradictions)
        raise ContractValidationError(f"{operation} refused for {contract.symbol}: {detail}")


@dataclass(frozen=True, slots=True)
class ContractStateResult:
    """The expiry verdict together with why it was reached."""

    state: ContractState
    reason: str
    evaluated_at: datetime
    days_to_expiry: int | None
    """Whole days from the evaluation date to the expiry date.

    Negative once past, ``None`` when the expiry is unknown or unverified.
    Derived on demand rather than stored, so it cannot go stale."""


def contract_state(contract: FuturesContract, now: datetime) -> ContractStateResult:
    """Decide whether the contract has expired, or admit that it cannot be decided.

    ``now`` comes from a ``ClockPort`` - never ``datetime.now()`` - so replay
    and backtest evaluate expiry at the historical moment rather than today.

    The rules, and the one that matters:

    * expiry missing or unverified -> ``UNKNOWN``.
    * evaluation date **after** the expiry date -> ``EXPIRED``. The whole day
      has passed; no session hour is needed to know that.
    * evaluation date **before** the expiry date -> ``ACTIVE``, meaning "not
      known to have expired".
    * evaluation date **equal to** the expiry date -> ``UNKNOWN``, unless a
      verified last-trading timestamp says otherwise. A contract stops trading
      at a specific moment on its final day, and that moment is a section 118
      exchange fact this project does not hold. Assuming a close would be
      inventing a session hour to make the answer look decisive.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware; take it from a ClockPort")

    if contract.expiry is None:
        return ContractStateResult(
            state=ContractState.UNKNOWN,
            reason="no expiry metadata was supplied",
            evaluated_at=now,
            days_to_expiry=None,
        )

    expiry_fact = contract.expiry.expiry_date
    if not expiry_fact.is_authoritative:
        return ContractStateResult(
            state=ContractState.UNKNOWN,
            reason=(
                f"expiry date is {expiry_fact.status.value} from '{expiry_fact.source}', "
                "which cannot support a definitive expiration judgement"
            ),
            evaluated_at=now,
            days_to_expiry=None,
        )

    expiry_date = expiry_fact.value
    today = now.date()
    days = (expiry_date - today).days

    last_trading = contract.expiry.last_trading_time
    if last_trading is not None and last_trading.is_authoritative:
        expired = now >= last_trading.value
        return ContractStateResult(
            state=ContractState.EXPIRED if expired else ContractState.ACTIVE,
            reason=(
                f"verified last trading time {last_trading.value.isoformat()} "
                f"{'has passed' if expired else 'is still ahead'}"
            ),
            evaluated_at=now,
            days_to_expiry=days,
        )

    if today > expiry_date:
        return ContractStateResult(
            state=ContractState.EXPIRED,
            reason=f"the expiry date {expiry_date.isoformat()} has fully passed",
            evaluated_at=now,
            days_to_expiry=days,
        )
    if today < expiry_date:
        return ContractStateResult(
            state=ContractState.ACTIVE,
            reason=f"the expiry date {expiry_date.isoformat()} is {days} day(s) ahead",
            evaluated_at=now,
            days_to_expiry=days,
        )

    return ContractStateResult(
        state=ContractState.UNKNOWN,
        reason=(
            f"today is the expiry date {expiry_date.isoformat()} and no verified last "
            "trading time is available; whether trading has ceased cannot be determined "
            "without inventing a session hour"
        ),
        evaluated_at=now,
        days_to_expiry=days,
    )


def implied_tick_value(contract: FuturesContract) -> Decimal | None:
    """``tick_size × multiplier`` when both are verified, else ``None``.

    Offered so a caller can display a tick value it does not have, clearly
    derived, rather than the engine inventing one and storing it as metadata.
    """
    contract.requires_linear_valuation("implied tick value")
    tick = contract.authoritative_tick_size()
    multiplier = contract.authoritative_multiplier()
    if tick is None or multiplier is None:
        return None
    return tick * multiplier


def ticks_between(price_a: Decimal, price_b: Decimal, tick_size: Decimal) -> Decimal | None:
    """Distance between two prices measured in ticks."""
    return safe_ratio(abs(price_a - price_b), tick_size)
