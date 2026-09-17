"""Tick-grid participation in sizing safety.

A stop that cannot be placed where the risk calculation assumed it is not a
stop. Before the hardening pass, `check_tick_value` was computed at provider
registration and read by nothing: a contract whose tick value contradicted its
own tick size and multiplier flowed straight into P&L, sizing and what-if.

All values are TEST_FIXTURE data.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.common.enums import Direction
from app.domain.futures.contract import ContractValidationError
from app.domain.futures.risk import calculate_contract_pnl, simulate_contract, size_position
from app.domain.risk.reward import risk_reward
from app.domain.risk.sizing import (
    AccountState,
    RiskMode,
    RiskPolicy,
    SizingOutcome,
    TickFeasibility,
)
from tests.factories_futures import contract, unverified, verified

POLICY = RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("500"))
ACCOUNT = AccountState(equity=Decimal("10000"))


def size(entry: str, stop: str, subject=None):  # type: ignore[no-untyped-def]
    return size_position(
        Direction.LONG,
        Decimal(entry),
        Decimal(stop),
        subject
        if subject is not None
        else contract(
            multiplier=verified(10), tick_size=verified("0.05"), initial_margin=verified(100)
        ),
        ACCOUNT,
        POLICY,
    )


# ----------------------------------------------------------------------
# On-grid levels size normally
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_levels_on_the_grid_size_normally() -> None:
    result = size("105.00", "104.50")
    assert result.tick_feasibility is TickFeasibility.ON_GRID
    assert result.outcome is SizingOutcome.ALLOWED


@pytest.mark.unit
@pytest.mark.parametrize(
    ("entry", "stop"),
    (("105.00", "104.95"), ("105.05", "104.00"), ("105.10", "104.60"), ("100.00", "99.05")),
)
def test_exact_decimal_boundaries_on_a_0_05_grid(entry: str, stop: str) -> None:
    assert size(entry, stop).tick_feasibility is TickFeasibility.ON_GRID


# ----------------------------------------------------------------------
# Off-grid levels are refused, never rounded
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_an_off_grid_entry_blocks_sizing() -> None:
    result = size("105.037", "104.50")
    assert result.tick_feasibility is TickFeasibility.OFF_GRID
    assert result.outcome is SizingOutcome.INVALID
    assert result.allowed_contracts is None


@pytest.mark.unit
def test_an_off_grid_stop_blocks_sizing() -> None:
    result = size("105.00", "104.512")
    assert result.tick_feasibility is TickFeasibility.OFF_GRID
    assert result.outcome is SizingOutcome.INVALID


@pytest.mark.unit
def test_the_off_grid_values_are_preserved_and_never_snapped() -> None:
    """The engine reports; it does not repair.

    A stop silently moved by a tick is a stop in a different place than the one
    the risk calculation used.
    """
    result = size("105.037", "104.50")
    assert "105.037" in result.reason
    assert "were not rounded" in result.reason
    assert result.stop_distance is None


@pytest.mark.unit
@pytest.mark.parametrize("bad", ("105.01", "105.02", "105.03", "105.04", "105.049"))
def test_every_off_grid_remainder_on_a_0_05_grid_is_caught(bad: str) -> None:
    assert size(bad, "104.50").tick_feasibility is TickFeasibility.OFF_GRID


@pytest.mark.unit
def test_a_finer_grid_accepts_what_a_coarser_one_refuses() -> None:
    fine = contract(
        multiplier=verified(10), tick_size=verified("0.001"), initial_margin=verified(100)
    )
    assert size("105.037", "104.500", fine).tick_feasibility is TickFeasibility.ON_GRID


# ----------------------------------------------------------------------
# Unknown tick size: execution feasibility is undetermined
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_an_unverified_tick_size_leaves_the_allowance_undetermined() -> None:
    """The arithmetic is sound but execution feasibility is unknown.

    Claiming a full allowance would assert that levels are placeable when a
    critical execution constraint has not been verified.
    """
    subject = contract(
        multiplier=verified(10), tick_size=unverified("0.05"), initial_margin=verified(100)
    )
    result = size("105.00", "104.50", subject)
    assert result.tick_feasibility is TickFeasibility.UNVERIFIED
    assert result.outcome is SizingOutcome.UNDETERMINED
    assert result.allowed_contracts is None
    assert result.maximum_by_risk == 100
    assert "tick size" in result.reason


@pytest.mark.unit
def test_the_risk_sized_maximum_is_still_reported_when_the_tick_is_unknown() -> None:
    subject = contract(
        multiplier=verified(10), tick_size=unverified("0.05"), initial_margin=verified(100)
    )
    result = size("105.00", "104.50", subject)
    assert result.loss_per_contract == Decimal("5.00")
    assert result.maximum_by_margin == 100


@pytest.mark.unit
def test_zero_from_risk_stays_definite_even_when_the_tick_is_unknown() -> None:
    """Zero is permitted whatever the grid, so the answer remains knowable."""
    subject = contract(multiplier=verified(10000), tick_size=unverified("0.05"))
    result = size("105.00", "104.50", subject)
    assert result.outcome is SizingOutcome.NOT_PERMITTED
    assert result.allowed_contracts == 0


# ----------------------------------------------------------------------
# A contradictory tick value must not reach any money calculation
# ----------------------------------------------------------------------


BROKEN = contract(
    multiplier=verified(100),
    tick_size=verified("0.05"),
    tick_value=verified("7"),  # 0.05 x 100 = 5, not 7
    initial_margin=verified(250),
)


@pytest.mark.unit
def test_a_contradictory_tick_value_blocks_sizing() -> None:
    with pytest.raises(ContractValidationError, match="tick_value"):
        size_position(Direction.LONG, Decimal("105"), Decimal("104"), BROKEN, ACCOUNT, POLICY)


@pytest.mark.unit
def test_a_contradictory_tick_value_blocks_pnl() -> None:
    with pytest.raises(ContractValidationError, match="tick_value"):
        calculate_contract_pnl(BROKEN, Direction.LONG, Decimal("105"), Decimal("110"), 1)


@pytest.mark.unit
def test_a_contradictory_tick_value_blocks_what_if() -> None:
    with pytest.raises(ContractValidationError, match="tick_value"):
        simulate_contract(
            BROKEN, Direction.LONG, Decimal("105"), 1, Decimal("10000"), (Decimal("110"),)
        )


@pytest.mark.unit
def test_a_contradictory_tick_value_blocks_the_margin_panel() -> None:
    from app.domain.futures.risk import assess_margin

    with pytest.raises(ContractValidationError, match="tick_value"):
        assess_margin(BROKEN, ACCOUNT, Decimal("105"), 1, POLICY)


@pytest.mark.unit
def test_a_consistent_tick_value_flows_through_normally() -> None:
    consistent = contract(
        multiplier=verified(100),
        tick_size=verified("0.05"),
        tick_value=verified("5"),
        initial_margin=verified(250),
    )
    result = calculate_contract_pnl(consistent, Direction.LONG, Decimal("105"), Decimal("110"), 1)
    assert result.gross == Decimal("500")


# ----------------------------------------------------------------------
# Targets
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_risk_reward_remains_pure_geometry_and_is_grid_agnostic() -> None:
    """Risk/reward takes no contract, so it applies no grid.

    That is deliberate rather than an oversight: the ratio of two distances is
    true whether or not the levels are placeable, and the contract-aware
    sizing path is where execution feasibility is decided. Recorded here so the
    boundary is explicit.
    """
    result = risk_reward(Direction.LONG, Decimal("105.037"), Decimal("104.50"), Decimal("106.00"))
    assert result.ratio is not None
