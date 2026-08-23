"""Profit and loss (master spec section 46).

    LONG  : (exit - entry) x multiplier x contracts
    SHORT : (entry - exit) x multiplier x contracts

Every value is ``Decimal``. A P&L figure is not an estimate that a binary float
may round; it is an amount of money the user will compare against their account
statement.

**Gross is always computable from valid inputs. Net is not.** Master spec
section 46 lists fees, commission and slippage, and every one of them is a
broker-specific or execution-specific fact - section 118 territory. This module
therefore never invents a commission. When no costs are supplied, ``net`` is
``None``, not equal to gross: reporting a net that silently assumes zero cost
would tell a user their scalp was profitable when the round trip may not have
been. Slippage is likewise an explicit modelling input, never a number the
engine decides on the user's behalf.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.arithmetic import as_percent, normalise_zero, safe_ratio
from app.domain.common.enums import Direction
from app.domain.futures.contract import FuturesContract
from app.domain.futures.validation import require_calculable


class PnLInputError(ValueError):
    """Raised when a P&L calculation is given inputs it cannot honour."""


@unique
class CostCompleteness(StrEnum):
    """How much of the execution cost is actually known.

    The distinction this enum exists to preserve: a *partial* cost set is not
    a net P&L. Subtracting only the commission from gross and presenting the
    result as "net" understates every remaining cost as zero, which is exactly
    the section 118 failure - a plausible number standing in for one nobody
    measured.
    """

    COMPLETE = "COMPLETE"
    """Commission, fees and slippage were all supplied - explicit zeros
    included. Only this state yields a ``net``."""

    PARTIAL = "PARTIAL"
    """Some supplied, some missing. Yields an upper bound, never a net."""

    UNKNOWN = "UNKNOWN"
    """Nothing supplied. Neither a net nor a bound."""


@dataclass(frozen=True, slots=True)
class TradeCosts:
    """Explicitly supplied execution costs.

    Absent fields are ``None`` and stay ``None``. There is no default
    commission anywhere in this codebase, because a default would be a guess
    about a broker's fee schedule presented as a fact.
    """

    commission: Decimal | None = None
    fees: Decimal | None = None
    slippage: Decimal | None = None
    """Modelled execution slippage, as a money amount. An input to a
    simulation, never an observation the engine makes up."""

    def __post_init__(self) -> None:
        for name in ("commission", "fees", "slippage"):
            value: Decimal | None = getattr(self, name)
            if value is None:
                continue
            if not value.is_finite():
                raise PnLInputError(f"{name} must be finite, got {value}")
            if value < 0:
                raise PnLInputError(f"{name} must not be negative, got {value}")

    @property
    def is_empty(self) -> bool:
        return self.commission is None and self.fees is None and self.slippage is None

    @property
    def completeness(self) -> CostCompleteness:
        """Whether every component was supplied.

        An **explicit** ``Decimal("0")`` counts as supplied - a user stating a
        zero-commission account is giving information. A ``None`` does not.
        That distinction is the whole reason both are representable.
        """
        supplied = [self.commission, self.fees, self.slippage]
        if all(value is not None for value in supplied):
            return CostCompleteness.COMPLETE
        if any(value is not None for value in supplied):
            return CostCompleteness.PARTIAL
        return CostCompleteness.UNKNOWN

    @property
    def known_total(self) -> Decimal | None:
        """Sum of the components that were supplied.

        Under PARTIAL this is a *lower bound on cost*, and therefore produces
        an *upper bound on net* - never a net. It is deliberately not named
        ``total``, which the earlier version was, because that name invited
        exactly the subtraction that misreports a partial figure as complete.
        """
        if self.is_empty:
            return None
        supplied = (self.commission, self.fees, self.slippage)
        return sum((value for value in supplied if value is not None), Decimal(0))

    @property
    def missing_components(self) -> tuple[str, ...]:
        """Names of the components nobody supplied."""
        return tuple(
            name for name in ("commission", "fees", "slippage") if getattr(self, name) is None
        )


@dataclass(frozen=True, slots=True)
class PnLResult:
    """Gross P&L, and net only when costs were actually supplied."""

    direction: Direction
    entry_price: Decimal
    exit_price: Decimal
    contracts: int
    multiplier: Decimal
    gross: Decimal
    costs: TradeCosts | None
    cost_completeness: CostCompleteness
    net: Decimal | None
    """Populated **only** when every cost component was supplied.

    ``None`` under PARTIAL and UNKNOWN. Not a synonym for gross: absent cost
    data is unknown cost, not zero cost - and a partial subtraction is not a
    net either, which is why PARTIAL yields ``net_upper_bound`` instead."""

    net_upper_bound: Decimal | None
    """Gross minus the costs that *are* known.

    Available under COMPLETE (where it equals ``net``) and PARTIAL. The true
    net can only be lower, because the unsupplied components are costs. Named
    as a bound so it can never be read as a settled figure."""

    @property
    def points(self) -> Decimal:
        """Price movement captured, in points, signed by direction."""
        move = self.exit_price - self.entry_price
        return normalise_zero(move if self.direction is Direction.LONG else -move)

    def return_on_account(self, account_equity: Decimal) -> Decimal | None:
        """Gross P&L as a fraction of account equity. ``None`` for a zero account."""
        return safe_ratio(self.gross, account_equity)

    def return_on_account_percent(self, account_equity: Decimal) -> Decimal | None:
        return as_percent(self.return_on_account(account_equity))

    def return_on_margin(self, margin_posted: Decimal) -> Decimal | None:
        """Gross P&L as a fraction of the margin posted.

        A far larger number than return on account, and not a measure of risk:
        margin is a deposit, not a cap on loss. See ``margin.py``.
        """
        return safe_ratio(self.gross, margin_posted)

    def r_multiple(self, initial_risk: Decimal) -> Decimal | None:
        """Gross P&L in units of the risk originally taken.

        ``None`` when the initial risk is zero - a trade that risked nothing
        has no R to be a multiple of.
        """
        return safe_ratio(self.gross, initial_risk)


def gross_pnl(
    direction: Direction,
    entry_price: Decimal,
    exit_price: Decimal,
    multiplier: Decimal,
    contracts: int,
) -> Decimal:
    """The section 46 formula, with the sign decided by direction.

    Rejects rather than guesses: a non-positive contract count, a non-positive
    price, a non-positive multiplier or a ``NEUTRAL`` direction all raise. A
    zero-contract "trade" returning zero P&L would look like a break-even
    result rather than the input error it is.
    """
    _require_positive(entry_price, "entry_price")
    _require_positive(exit_price, "exit_price")
    _require_positive(multiplier, "multiplier")
    if contracts <= 0:
        raise PnLInputError(f"contracts must be positive, got {contracts}")

    if direction is Direction.LONG:
        move = exit_price - entry_price
    elif direction is Direction.SHORT:
        move = entry_price - exit_price
    else:
        raise PnLInputError(f"{direction.value} is not a tradeable direction for a P&L calculation")

    return normalise_zero(move * multiplier * contracts)


def calculate_pnl(
    direction: Direction,
    entry_price: Decimal,
    exit_price: Decimal,
    multiplier: Decimal,
    contracts: int,
    costs: TradeCosts | None = None,
) -> PnLResult:
    """Gross P&L, plus a net **only** when every cost component was supplied.

    A partial cost set yields ``net_upper_bound`` and a ``PARTIAL``
    completeness state, never a ``net``. See ``CostCompleteness``.
    """
    gross = gross_pnl(direction, entry_price, exit_price, multiplier, contracts)

    completeness = costs.completeness if costs is not None else CostCompleteness.UNKNOWN
    known = costs.known_total if costs is not None else None
    bound = None if known is None else normalise_zero(gross - known)
    net = bound if completeness is CostCompleteness.COMPLETE else None

    return PnLResult(
        direction=direction,
        entry_price=entry_price,
        exit_price=exit_price,
        contracts=contracts,
        multiplier=multiplier,
        gross=gross,
        costs=costs,
        cost_completeness=completeness,
        net=net,
        net_upper_bound=bound,
    )


def calculate_contract_pnl(
    contract: FuturesContract,
    direction: Direction,
    entry_price: Decimal,
    exit_price: Decimal,
    contracts: int,
    costs: TradeCosts | None = None,
) -> PnLResult:
    """P&L using the contract's **verified** multiplier.

    Refuses when the multiplier is not a verified current fact. Computing money
    from an unverified multiplier produces a number that looks exactly like a
    real one, which is the failure mode section 118 exists to prevent - so the
    call fails loudly instead.
    """
    contract.requires_linear_valuation("P&L calculation")
    require_calculable(contract, "P&L calculation")
    multiplier = contract.multiplier.require_authoritative(f"P&L for {contract.symbol}")
    return calculate_pnl(direction, entry_price, exit_price, multiplier, contracts, costs)


def _require_positive(value: Decimal, name: str) -> None:
    if not value.is_finite():
        raise PnLInputError(f"{name} must be finite, got {value}")
    if value <= 0:
        raise PnLInputError(f"{name} must be positive, got {value}")
