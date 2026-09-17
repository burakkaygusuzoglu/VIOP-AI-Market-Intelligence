"""Position sizing (master spec section 42).

Deterministic domain arithmetic. **No LLM participates in any of it** - master
spec section 1 makes the risk engine independent of Claude, and section 42
makes one of its behaviours mandatory:

> Account 2,500 · Max risk 75 · Entry 105 · Stop 104 · Multiplier 100
> Loss per contract 100 TRY
> **TRADE NOT ALLOWED**, because one minimum contract exceeds configured risk.

That is the case this module is built around. ``floor``, never ``ceil``: 0.75
contracts is **zero** contracts. Rounding up would breach the user's stated
risk limit by a third on the very first trade, and the fact that it produces a
tradeable-looking answer is exactly why it is a tempting bug.

**Risk feasibility and margin feasibility are separate constraints, and the
engine never merges an unknown into a known one.** If risk sizing permits five
contracts and the margin is unverified, the answer is not "five". It is "risk
allows five, margin feasibility unknown, final allowance undetermined". Saying
five would present half an analysis as a whole one.

**Product-agnostic since Phase 8.5.** This module reasons about a risk budget,
a stop, a loss per unit and a margin constraint; it no longer knows what a
futures contract is. Everything product-specific - the point value, the price
grid, the margin per unit, whether quantity is whole units, and what a unit is
called - arrives through a ``ProductPolicy``. ``app.domain.futures.risk`` binds
a ``FuturesContract`` to this engine, and an import contract forbids this
package from importing ``app.domain.futures`` at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.arithmetic import as_percent, floor_divide, safe_ratio
from app.domain.common.enums import Direction
from app.domain.instrument.policy import (
    MarginFeasibility,
    ProductPolicy,
    TickFeasibility,
    require_product_calculable,
)

__all__ = [
    "AccountState",
    "MarginFeasibility",
    "PositionSizing",
    "RiskInputError",
    "RiskMode",
    "RiskPolicy",
    "SizingOutcome",
    "TickFeasibility",
    "size_for_product",
    "stop_distance",
]


class RiskInputError(ValueError):
    """Raised when a sizing request cannot be interpreted at all."""


@unique
class RiskMode(StrEnum):
    """How the risk budget for a trade is expressed."""

    FIXED = "FIXED"
    """A money amount per trade, e.g. 75 TRY."""

    PERCENTAGE = "PERCENTAGE"
    """A fraction of account equity, e.g. 0.03 for 3%."""


@unique
class SizingOutcome(StrEnum):
    """What the engine was able to conclude."""

    ALLOWED = "ALLOWED"
    """A definite count of at least one contract is permitted."""

    NOT_PERMITTED = "NOT_PERMITTED"
    """Zero contracts. Definite regardless of margin - the mandatory section
    42 outcome when a single contract already exceeds the risk budget."""

    UNDETERMINED = "UNDETERMINED"
    """Risk sizing succeeded but margin feasibility is unknown, so no final
    count can be stated. The risk-sized maximum is still reported."""

    INVALID = "INVALID"
    """The request cannot be sized: wrong stop orientation, zero stop distance,
    non-positive account, unverified multiplier."""


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """The user's risk configuration.

    Every threshold here is a **user or project policy**, never an exchange
    rule. Master spec section 45 forbids automatically changing a user's
    configured real-money risk, so nothing in this engine adjusts these values
    - it reports against them.
    """

    mode: RiskMode = RiskMode.FIXED
    fixed_risk: Decimal | None = None
    """Money at risk per trade, when ``mode`` is FIXED."""

    risk_ratio: Decimal | None = None
    """Fraction of equity at risk per trade, when ``mode`` is PERCENTAGE.
    ``0.03`` means 3%; the name says fraction so nobody passes ``3``."""

    max_contracts: int | None = None
    """An explicit user cap, applied on top of risk and margin."""

    max_margin_utilisation: Decimal = Decimal("0.5")
    """Fraction of equity committed as margin above which a warning fires."""

    max_effective_leverage: Decimal = Decimal("5")
    """Notional-to-equity ratio above which a warning fires."""

    def __post_init__(self) -> None:
        if self.mode is RiskMode.FIXED:
            if self.fixed_risk is None:
                raise RiskInputError("FIXED risk mode needs fixed_risk")
            if self.fixed_risk <= 0:
                raise RiskInputError(f"fixed_risk must be positive, got {self.fixed_risk}")
        else:
            if self.risk_ratio is None:
                raise RiskInputError("PERCENTAGE risk mode needs risk_ratio")
            if not 0 < self.risk_ratio <= 1:
                raise RiskInputError(f"risk_ratio is a fraction in (0, 1], got {self.risk_ratio}")
        if self.max_contracts is not None and self.max_contracts < 0:
            raise RiskInputError("max_contracts must not be negative")
        if self.max_margin_utilisation <= 0:
            raise RiskInputError("max_margin_utilisation must be positive")
        if self.max_effective_leverage <= 0:
            raise RiskInputError("max_effective_leverage must be positive")

    def risk_amount(self, account_equity: Decimal) -> Decimal:
        """The money budget for this trade.

        The ``None`` guards are unreachable after ``__post_init__`` and are
        written as real raises rather than assertions, which ``python -O``
        strips - a risk budget silently becoming ``None`` under an optimised
        interpreter is not a failure mode worth leaving open.
        """
        if self.mode is RiskMode.FIXED:
            if self.fixed_risk is None:
                raise RiskInputError("FIXED risk mode needs fixed_risk")
            return self.fixed_risk
        if self.risk_ratio is None:
            raise RiskInputError("PERCENTAGE risk mode needs risk_ratio")
        return account_equity * self.risk_ratio


@dataclass(frozen=True, slots=True)
class AccountState:
    """What the account currently looks like.

    ``used_margin`` is margin already committed to open positions, so free
    margin is what remains for a new one.
    """

    equity: Decimal
    used_margin: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.equity.is_finite():
            raise RiskInputError(f"equity must be finite, got {self.equity}")
        if self.used_margin < 0:
            raise RiskInputError(f"used_margin must not be negative, got {self.used_margin}")

    @property
    def free_margin(self) -> Decimal:
        """``equity - used_margin``, **including when that is negative**.

        Deliberately not clamped. An account with 2,500 of equity against 2,700
        of committed margin is 200 in deficit, and that deficit is the single
        most important fact about it - it is a margin call, not "zero free
        margin". Clamping destroys the magnitude and makes a breached account
        indistinguishable from a fully-committed healthy one.

        Use ``available_for_new_positions`` for the sizing question, which is a
        different question with a different answer.
        """
        return self.equity - self.used_margin

    @property
    def available_for_new_positions(self) -> Decimal:
        """Margin usable for a *new* position: ``max(free_margin, 0)``.

        Floored at zero because a deficit does not fund anything. The economic
        value stays observable on ``free_margin`` and ``margin_deficit``.
        """
        free = self.free_margin
        return free if free > 0 else Decimal(0)

    @property
    def is_in_deficit(self) -> bool:
        return self.free_margin < 0

    @property
    def margin_deficit(self) -> Decimal:
        """How far past its committed margin the account is. Zero when healthy."""
        free = self.free_margin
        return -free if free < 0 else Decimal(0)


@dataclass(frozen=True, slots=True)
class PositionSizing:
    """The full sizing answer, including what could not be determined."""

    outcome: SizingOutcome
    reason: str

    risk_amount: Decimal | None
    stop_distance: Decimal | None
    loss_per_contract: Decimal | None

    maximum_by_risk: int | None
    maximum_by_margin: int | None
    """``None`` whenever margin feasibility is not KNOWN. Never defaulted to a
    large number, which would let an unknown constraint read as a satisfied
    one."""

    margin_feasibility: MarginFeasibility
    tick_feasibility: TickFeasibility
    allowed_contracts: int | None
    """The final count, set only when the outcome is ALLOWED or
    NOT_PERMITTED. ``None`` under UNDETERMINED and INVALID."""

    policy_limit: int | None = None

    @property
    def is_tradeable(self) -> bool:
        """True only for a definite, non-zero allowance."""
        return self.outcome is SizingOutcome.ALLOWED

    def risk_ratio_of_account(self, account_equity: Decimal) -> Decimal | None:
        """Configured risk as a fraction of equity."""
        if self.risk_amount is None:
            return None
        return safe_ratio(self.risk_amount, account_equity)

    def risk_percent_of_account(self, account_equity: Decimal) -> Decimal | None:
        return as_percent(self.risk_ratio_of_account(account_equity))


def stop_distance(direction: Direction, entry: Decimal, stop: Decimal) -> Decimal | None:
    """Distance from entry to stop, **only when the stop is on the right side**.

    A long's stop sits below entry; a short's sits above. Taking ``abs(entry -
    stop)`` would happily size a long whose "stop" is above the entry - a
    position that cannot lose the amount the calculation claims, and whose real
    behaviour is nothing like what the user asked for. Returning ``None``
    forces the caller to notice.
    """
    if direction is Direction.LONG:
        return entry - stop if stop < entry else None
    if direction is Direction.SHORT:
        return stop - entry if stop > entry else None
    return None


def size_for_product(
    direction: Direction,
    entry_price: Decimal,
    stop_price: Decimal,
    product: ProductPolicy,
    account: AccountState,
    policy: RiskPolicy,
) -> PositionSizing:
    """Size a position against risk, margin and the user's own cap.

    Order of work:

    1. Validate the request - the product is implemented, sized in whole units
       and calculable; direction, prices, stop orientation, equity; and a
       **verified** point value. Money arithmetic on an unverified point value
       is refused, not caveated.
    2. ``risk_amount`` from the policy.
    3. ``loss_per_unit = stop_distance x point_value``.
    4. ``maximum_by_risk = floor(risk_amount / loss_per_unit)``. Zero is a
       legitimate, mandatory answer.
    5. ``maximum_by_margin = floor(free_margin / margin_per_unit)``, **only** if
       the margin is a verified current fact.
    6. The final allowance is the minimum of whatever is actually known.
    """
    require_product_calculable(product, "position sizing")
    words = product.vocabulary
    symbol = product.instrument.symbol

    if direction not in (Direction.LONG, Direction.SHORT):
        return _invalid(f"{direction.value} is not a tradeable direction")
    if entry_price <= 0 or not entry_price.is_finite():
        return _invalid(f"entry price must be positive and finite, got {entry_price}")
    if stop_price <= 0 or not stop_price.is_finite():
        return _invalid(f"stop price must be positive and finite, got {stop_price}")
    if account.equity <= 0:
        return _invalid(f"account equity must be positive, got {account.equity}")

    point = product.point_value()
    if not point.is_authoritative:
        return _invalid(
            f"{words.point_value} for {symbol} is {point.status.value}; "
            f"position size cannot be computed from an unverified {words.point_value_qualified}"
        )
    point_value = point.value

    distance = stop_distance(direction, entry_price, stop_price)
    if distance is None:
        expected = "below" if direction is Direction.LONG else "above"
        return _invalid(
            f"a {direction.value} stop must be {expected} the entry; "
            f"got entry {entry_price} and stop {stop_price}"
        )

    risk_amount = policy.risk_amount(account.equity)
    loss_per_contract = distance * point_value
    maximum_by_risk = floor_divide(risk_amount, loss_per_contract)
    if maximum_by_risk is None:
        return _invalid(f"loss per {words.unit} is zero, so risk sizing is undefined")

    grid = product.price_increment_check(entry_price, stop_price)
    tick_state, tick_detail = grid.feasibility, grid.detail
    if tick_state is TickFeasibility.OFF_GRID:
        return _invalid(
            f"{tick_detail}; the levels are preserved and were not rounded to the grid",
            tick=tick_state,
        )

    feasibility, margin_capacity = _margin_capacity(product, account)
    # Reported as ``None`` unless genuinely known, so an unknown constraint can
    # never be mistaken for a satisfied one.
    maximum_by_margin = margin_capacity if feasibility is MarginFeasibility.KNOWN else None

    if maximum_by_risk <= 0:
        return PositionSizing(
            outcome=SizingOutcome.NOT_PERMITTED,
            reason=(
                f"one {words.unit} would risk {loss_per_contract}, which exceeds the configured "
                f"risk of {risk_amount}; no position is permitted"
            ),
            risk_amount=risk_amount,
            stop_distance=distance,
            loss_per_contract=loss_per_contract,
            maximum_by_risk=0,
            maximum_by_margin=maximum_by_margin,
            margin_feasibility=feasibility,
            tick_feasibility=tick_state,
            allowed_contracts=0,
            policy_limit=policy.max_contracts,
        )

    if feasibility is not MarginFeasibility.KNOWN or tick_state is not TickFeasibility.ON_GRID:
        blockers: list[str] = []
        if feasibility is MarginFeasibility.MISSING:
            blockers.append("no initial margin was supplied")
        elif feasibility is MarginFeasibility.UNVERIFIED:
            blockers.append("the initial margin is not a verified current fact")
        if tick_state is not TickFeasibility.ON_GRID:
            blockers.append(tick_detail)

        return PositionSizing(
            outcome=SizingOutcome.UNDETERMINED,
            reason=(
                f"risk sizing permits {maximum_by_risk} {words.unit_counted}, but "
                + " and ".join(blockers)
                + ", so the final allowance cannot be determined"
            ),
            risk_amount=risk_amount,
            stop_distance=distance,
            loss_per_contract=loss_per_contract,
            maximum_by_risk=maximum_by_risk,
            maximum_by_margin=(margin_capacity if feasibility is MarginFeasibility.KNOWN else None),
            margin_feasibility=feasibility,
            tick_feasibility=tick_state,
            allowed_contracts=None,
            policy_limit=policy.max_contracts,
        )

    candidates = [maximum_by_risk, margin_capacity]
    if policy.max_contracts is not None:
        candidates.append(policy.max_contracts)
    allowed = min(candidates)

    if allowed <= 0:
        binding = _binding_constraint(
            maximum_by_risk, margin_capacity, policy.max_contracts, words.unit
        )
        return PositionSizing(
            outcome=SizingOutcome.NOT_PERMITTED,
            reason=f"no position is permitted; the binding constraint is {binding}",
            risk_amount=risk_amount,
            stop_distance=distance,
            loss_per_contract=loss_per_contract,
            maximum_by_risk=maximum_by_risk,
            maximum_by_margin=maximum_by_margin,
            margin_feasibility=feasibility,
            tick_feasibility=tick_state,
            allowed_contracts=0,
            policy_limit=policy.max_contracts,
        )

    binding = _binding_constraint(
        maximum_by_risk, margin_capacity, policy.max_contracts, words.unit
    )
    return PositionSizing(
        outcome=SizingOutcome.ALLOWED,
        reason=f"{allowed} {words.unit_counted} permitted; the binding constraint is {binding}",
        risk_amount=risk_amount,
        stop_distance=distance,
        loss_per_contract=loss_per_contract,
        maximum_by_risk=maximum_by_risk,
        maximum_by_margin=maximum_by_margin,
        margin_feasibility=feasibility,
        tick_feasibility=tick_state,
        allowed_contracts=allowed,
        policy_limit=policy.max_contracts,
    )


def _margin_capacity(
    product: ProductPolicy, account: AccountState
) -> tuple[MarginFeasibility, int]:
    """Units the free margin supports, and how well that is known.

    The integer is meaningless unless the feasibility is ``KNOWN``; the caller
    reports ``None`` in every other case rather than letting a placeholder zero
    read as a real constraint.
    """
    requirement = product.margin_requirement()
    if requirement.per_unit is None:
        return requirement.feasibility, 0
    capacity = floor_divide(account.available_for_new_positions, requirement.per_unit)
    return MarginFeasibility.KNOWN, 0 if capacity is None else max(capacity, 0)


def _binding_constraint(by_risk: int, by_margin: int, cap: int | None, unit: str) -> str:
    options: list[tuple[int, str]] = [(by_risk, "risk"), (by_margin, "margin")]
    if cap is not None:
        options.append((cap, f"the configured {unit} cap"))
    return min(options, key=lambda item: item[0])[1]


def _invalid(reason: str, tick: TickFeasibility = TickFeasibility.MISSING) -> PositionSizing:
    return PositionSizing(
        outcome=SizingOutcome.INVALID,
        reason=reason,
        risk_amount=None,
        stop_distance=None,
        loss_per_contract=None,
        maximum_by_risk=None,
        maximum_by_margin=None,
        margin_feasibility=MarginFeasibility.MISSING,
        tick_feasibility=tick,
        allowed_contracts=None,
    )
