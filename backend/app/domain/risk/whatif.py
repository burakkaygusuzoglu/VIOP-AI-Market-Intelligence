"""What-if simulator (master spec section 47).

"What happens to my account if price goes to X?" - answered for a list of
hypothetical prices the **caller** supplies.

Section 44 happens to use -1%, -2%, -3% and -5% as illustrative moves. Those
are examples in a document, not a market fact, and they are not baked in here:
the caller passes whatever prices it wants to see, and a UI in a later phase
can offer the section 44 set as a preset. Hard-coding them would make the
engine quietly opinionated about which adverse moves matter.

**No trade is placed and no position is created.** This is arithmetic on
hypothetical prices. Master spec section 120 disables real execution, and paper
trading is Phase 9; nothing here writes state anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.common.arithmetic import as_percent
from app.domain.common.enums import Direction
from app.domain.futures.contract import FuturesContract
from app.domain.futures.validation import require_calculable
from app.domain.risk.pnl import gross_pnl


@dataclass(frozen=True, slots=True)
class WhatIfScenario:
    """One hypothetical exit price and what it would mean."""

    price: Decimal
    gross_pnl: Decimal
    account_ratio: Decimal | None
    """Gross P&L as a fraction of account equity; ``None`` for a zero account."""

    r_multiple: Decimal | None
    """P&L in units of the initial risk; ``None`` when that risk is unknown or
    zero. Absent rather than assumed - a scenario cannot be expressed in R if
    nobody said what R was."""

    @property
    def account_percent(self) -> Decimal | None:
        return as_percent(self.account_ratio)


@dataclass(frozen=True, slots=True)
class WhatIfResult:
    """The scenarios, with the position they were computed for."""

    direction: Direction
    entry_price: Decimal
    contracts: int
    multiplier: Decimal
    account_equity: Decimal
    initial_risk: Decimal | None
    scenarios: tuple[WhatIfScenario, ...]


def simulate(
    direction: Direction,
    entry_price: Decimal,
    multiplier: Decimal,
    contracts: int,
    account_equity: Decimal,
    prices: tuple[Decimal, ...],
    initial_risk: Decimal | None = None,
) -> WhatIfResult:
    """Evaluate a position against a list of hypothetical prices.

    Deterministic and side-effect free: the same inputs always give the same
    scenarios, in the order the prices were supplied.
    """
    scenarios = tuple(
        _scenario(
            direction, entry_price, multiplier, contracts, account_equity, price, initial_risk
        )
        for price in prices
    )
    return WhatIfResult(
        direction=direction,
        entry_price=entry_price,
        contracts=contracts,
        multiplier=multiplier,
        account_equity=account_equity,
        initial_risk=initial_risk,
        scenarios=scenarios,
    )


def simulate_contract(
    contract: FuturesContract,
    direction: Direction,
    entry_price: Decimal,
    contracts: int,
    account_equity: Decimal,
    prices: tuple[Decimal, ...],
    initial_risk: Decimal | None = None,
) -> WhatIfResult:
    """Simulate using the contract's **verified** multiplier.

    Refuses on an unverified multiplier for the same reason ``calculate_pnl``
    does: a what-if built on a guessed multiplier looks exactly like one built
    on a real fact.
    """
    contract.requires_linear_valuation("what-if simulation")
    require_calculable(contract, "what-if simulation")
    multiplier = contract.multiplier.require_authoritative(
        f"what-if simulation for {contract.symbol}"
    )
    return simulate(
        direction, entry_price, multiplier, contracts, account_equity, prices, initial_risk
    )


def _scenario(
    direction: Direction,
    entry_price: Decimal,
    multiplier: Decimal,
    contracts: int,
    account_equity: Decimal,
    price: Decimal,
    initial_risk: Decimal | None,
) -> WhatIfScenario:
    from app.domain.common.arithmetic import safe_ratio

    pnl = gross_pnl(direction, entry_price, price, multiplier, contracts)
    return WhatIfScenario(
        price=price,
        gross_pnl=pnl,
        account_ratio=safe_ratio(pnl, account_equity),
        r_multiple=None if initial_risk is None else safe_ratio(pnl, initial_risk),
    )
