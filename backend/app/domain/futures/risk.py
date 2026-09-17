"""Futures entry points into the product-agnostic risk engine (Phase 8.5).

Until Phase 8.5 these four functions lived in ``app.domain.risk`` and read a
``FuturesContract`` directly. Their signatures and results are unchanged; each
now wraps the contract in ``FuturesProductPolicy`` and calls the generic engine.

The dependency therefore runs **futures -> risk**, which is the right way round:
a product plugs into the core, and the core does not know which products exist.
An import contract forbids ``app.domain.risk`` from importing this package.

Nothing here computes. Every number comes from ``app.domain.risk``, and
``test_futures_parity.py`` holds all four to the Phase 8 baseline.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.common.enums import Direction
from app.domain.futures.contract import FuturesContract
from app.domain.futures.policy import FuturesProductPolicy
from app.domain.risk.margin import MarginAssessment, assess_margin_for_product
from app.domain.risk.pnl import PnLResult, TradeCosts, pnl_for_product
from app.domain.risk.sizing import AccountState, PositionSizing, RiskPolicy, size_for_product
from app.domain.risk.whatif import WhatIfResult, simulate_for_product


def size_position(
    direction: Direction,
    entry_price: Decimal,
    stop_price: Decimal,
    contract: FuturesContract,
    account: AccountState,
    policy: RiskPolicy,
) -> PositionSizing:
    """Master spec section 42 sizing for a futures contract."""
    return size_for_product(
        direction, entry_price, stop_price, FuturesProductPolicy(contract), account, policy
    )


def assess_margin(
    contract: FuturesContract,
    account: AccountState,
    entry_price: Decimal,
    contracts: int,
    policy: RiskPolicy,
    risk_to_stop: Decimal | None = None,
) -> MarginAssessment:
    """Master spec section 44 margin panel for a futures position."""
    return assess_margin_for_product(
        FuturesProductPolicy(contract),
        account,
        entry_price,
        contracts,
        policy,
        risk_to_stop=risk_to_stop,
    )


def calculate_contract_pnl(
    contract: FuturesContract,
    direction: Direction,
    entry_price: Decimal,
    exit_price: Decimal,
    contracts: int,
    costs: TradeCosts | None = None,
) -> PnLResult:
    """Master spec section 46 P&L using the contract's verified multiplier."""
    return pnl_for_product(
        FuturesProductPolicy(contract), direction, entry_price, exit_price, contracts, costs
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
    """Master spec section 47 what-if using the contract's verified multiplier."""
    return simulate_for_product(
        FuturesProductPolicy(contract),
        direction,
        entry_price,
        contracts,
        account_equity,
        prices,
        initial_risk,
    )
