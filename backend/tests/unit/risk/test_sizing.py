"""Position sizing (master spec section 42).

The mandatory case first: when one contract already exceeds the configured
risk, the answer is **zero contracts and no trade**. Every number here is
TEST_FIXTURE data.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.common.enums import Direction
from app.domain.risk.sizing import (
    AccountState,
    MarginFeasibility,
    RiskInputError,
    RiskMode,
    RiskPolicy,
    SizingOutcome,
    size_position,
    stop_distance,
)
from tests.factories_futures import contract, unverified, verified

FIXED_75 = RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("75"))
ACCOUNT_2500 = AccountState(equity=Decimal("2500"))


# ----------------------------------------------------------------------
# The mandatory section 42 example
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_one_contract_exceeding_the_risk_budget_permits_no_trade() -> None:
    """Master spec section 42, verbatim, and it is marked *mandatory* there.

    Account 2,500 · max risk 75 · entry 105 · stop 104 · multiplier 100.
    Loss per contract is 1.00 x 100 = 100 TRY, which exceeds the 75 budget.
    The only correct answer is zero contracts.
    """
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(100)),
        ACCOUNT_2500,
        FIXED_75,
    )

    assert result.loss_per_contract == Decimal("100")
    assert result.maximum_by_risk == 0
    assert result.allowed_contracts == 0
    assert result.outcome is SizingOutcome.NOT_PERMITTED
    assert not result.is_tradeable
    assert "exceeds the configured risk" in result.reason


@pytest.mark.unit
def test_the_answer_is_floored_never_rounded_up() -> None:
    """0.75 contracts is zero, not one.

    Rounding up would breach the user's stated risk limit by a third on the
    very first trade - and it produces a tradeable-looking answer, which is
    what makes it a tempting bug.
    """
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(100)),
        ACCOUNT_2500,
        FIXED_75,
    )
    exact = Decimal("75") / Decimal("100")
    assert exact == Decimal("0.75")
    assert result.maximum_by_risk == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("risk", "loss_per_contract_stop", "expected"),
    (
        ("100", "104", 1),  # exactly one fits
        ("199", "104", 1),  # 1.99 floors to 1
        ("200", "104", 2),
        ("99", "104", 0),
    ),
)
def test_risk_sizing_floors_at_every_boundary(
    risk: str, loss_per_contract_stop: str, expected: int
) -> None:
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal(loss_per_contract_stop),
        contract(multiplier=verified(100)),
        ACCOUNT_2500,
        RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal(risk)),
    )
    assert result.maximum_by_risk == expected


# ----------------------------------------------------------------------
# Risk modes
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_fixed_risk_uses_the_configured_amount() -> None:
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10)),
        ACCOUNT_2500,
        RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("75")),
    )
    assert result.risk_amount == Decimal("75")
    assert result.loss_per_contract == Decimal("10")
    assert result.maximum_by_risk == 7  # 7.5 floors to 7


@pytest.mark.unit
def test_percentage_risk_is_a_fraction_of_equity() -> None:
    """``risk_ratio`` is a fraction: 0.03 is 3%, and 3 would be 300%."""
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10)),
        ACCOUNT_2500,
        RiskPolicy(mode=RiskMode.PERCENTAGE, risk_ratio=Decimal("0.03")),
    )
    assert result.risk_amount == Decimal("75.00")
    assert result.maximum_by_risk == 7


@pytest.mark.unit
def test_risk_percent_of_account_is_reported_back() -> None:
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10)),
        ACCOUNT_2500,
        FIXED_75,
    )
    assert result.risk_percent_of_account(Decimal("2500")) == Decimal("3")


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    (
        {"mode": RiskMode.FIXED},
        {"mode": RiskMode.FIXED, "fixed_risk": Decimal("0")},
        {"mode": RiskMode.PERCENTAGE},
        {"mode": RiskMode.PERCENTAGE, "risk_ratio": Decimal("0")},
        {"mode": RiskMode.PERCENTAGE, "risk_ratio": Decimal("3")},
    ),
)
def test_incoherent_risk_policy_is_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(RiskInputError):
        RiskPolicy(**kwargs)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Stop orientation
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_stop_distance_requires_the_stop_on_the_correct_side() -> None:
    assert stop_distance(Direction.LONG, Decimal("105"), Decimal("104")) == Decimal("1")
    assert stop_distance(Direction.SHORT, Decimal("105"), Decimal("106")) == Decimal("1")
    assert stop_distance(Direction.LONG, Decimal("105"), Decimal("106")) is None
    assert stop_distance(Direction.SHORT, Decimal("105"), Decimal("104")) is None


@pytest.mark.unit
def test_a_long_stop_above_the_entry_is_refused_not_absolute_valued() -> None:
    """``abs(entry - stop)`` would size a position that cannot lose that amount."""
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("106"),
        contract(multiplier=verified(10)),
        ACCOUNT_2500,
        FIXED_75,
    )
    assert result.outcome is SizingOutcome.INVALID
    assert result.allowed_contracts is None
    assert "must be below the entry" in result.reason


@pytest.mark.unit
def test_a_short_stop_below_the_entry_is_refused() -> None:
    result = size_position(
        Direction.SHORT,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10)),
        ACCOUNT_2500,
        FIXED_75,
    )
    assert result.outcome is SizingOutcome.INVALID
    assert "must be above the entry" in result.reason


@pytest.mark.unit
def test_a_stop_equal_to_the_entry_is_refused() -> None:
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("105"),
        contract(multiplier=verified(10)),
        ACCOUNT_2500,
        FIXED_75,
    )
    assert result.outcome is SizingOutcome.INVALID


@pytest.mark.unit
def test_a_short_is_sized_from_the_upward_stop_distance() -> None:
    result = size_position(
        Direction.SHORT,
        Decimal("105"),
        Decimal("106"),
        contract(multiplier=verified(10)),
        ACCOUNT_2500,
        FIXED_75,
    )
    assert result.stop_distance == Decimal("1")
    assert result.loss_per_contract == Decimal("10")
    assert result.maximum_by_risk == 7


# ----------------------------------------------------------------------
# Invalid inputs
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("equity", ("0", "-100"))
def test_a_non_positive_account_cannot_be_sized(equity: str) -> None:
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10)),
        AccountState(equity=Decimal(equity)),
        FIXED_75,
    )
    assert result.outcome is SizingOutcome.INVALID


@pytest.mark.unit
def test_a_neutral_direction_cannot_be_sized() -> None:
    result = size_position(
        Direction.NEUTRAL,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10)),
        ACCOUNT_2500,
        FIXED_75,
    )
    assert result.outcome is SizingOutcome.INVALID


@pytest.mark.unit
def test_an_unverified_multiplier_refuses_to_size() -> None:
    """Money arithmetic on an unverified multiplier is refused, not caveated."""
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=unverified(10)),
        ACCOUNT_2500,
        FIXED_75,
    )
    assert result.outcome is SizingOutcome.INVALID
    assert "UNVERIFIED" in result.reason


@pytest.mark.unit
def test_sizing_is_reproducible() -> None:
    args = (
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10)),
        ACCOUNT_2500,
        FIXED_75,
    )
    assert size_position(*args) == size_position(*args)


@pytest.mark.unit
def test_a_margin_deficit_is_preserved_not_clamped_away() -> None:
    """The hardening fix.

    2,500 of equity against 2,700 of committed margin is 200 in deficit, and
    that magnitude is the most important fact about the account. Clamping made
    a breached account indistinguishable from a fully committed healthy one.
    """
    account = AccountState(equity=Decimal("2500"), used_margin=Decimal("2700"))
    assert account.free_margin == Decimal("-200")
    assert account.margin_deficit == Decimal("200")
    assert account.is_in_deficit


@pytest.mark.unit
def test_a_deficit_funds_no_new_position() -> None:
    """The economic value stays observable; sizing asks a different question."""
    account = AccountState(equity=Decimal("2500"), used_margin=Decimal("2700"))
    assert account.available_for_new_positions == Decimal("0")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("equity", "used", "free", "deficit", "available"),
    (
        ("2500", "500", "2000", "0", "2000"),
        ("2500", "2500", "0", "0", "0"),
        ("2500", "2700", "-200", "200", "0"),
    ),
)
def test_free_margin_boundaries(
    equity: str, used: str, free: str, deficit: str, available: str
) -> None:
    account = AccountState(equity=Decimal(equity), used_margin=Decimal(used))
    assert account.free_margin == Decimal(free)
    assert account.margin_deficit == Decimal(deficit)
    assert account.available_for_new_positions == Decimal(available)


@pytest.mark.unit
def test_no_contracts_are_permitted_while_in_deficit() -> None:
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10), initial_margin=verified(100)),
        AccountState(equity=Decimal("2500"), used_margin=Decimal("2700")),
        FIXED_75,
    )
    assert result.maximum_by_margin == 0
    assert result.allowed_contracts == 0
    assert result.outcome is SizingOutcome.NOT_PERMITTED


@pytest.mark.unit
def test_missing_margin_leaves_the_margin_maximum_unset() -> None:
    result = size_position(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        contract(multiplier=verified(10)),
        ACCOUNT_2500,
        FIXED_75,
    )
    assert result.margin_feasibility is MarginFeasibility.MISSING
    assert result.maximum_by_margin is None
