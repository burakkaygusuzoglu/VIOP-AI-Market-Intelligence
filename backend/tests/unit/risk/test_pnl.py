"""P&L (master spec section 46), verified by hand calculation.

Every figure below is checkable on paper. All values are TEST_FIXTURE data.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.common.enums import Direction
from app.domain.futures.risk import calculate_contract_pnl
from app.domain.risk.pnl import (
    CostCompleteness,
    PnLInputError,
    TradeCosts,
    calculate_pnl,
    gross_pnl,
)
from tests.factories_futures import contract, unverified, verified

MULTIPLIER = Decimal("100")


# ----------------------------------------------------------------------
# The two formulas
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_long_profit_hand_calculated() -> None:
    """(110 - 105) x 100 x 1 = 500."""
    assert gross_pnl(Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, 1) == Decimal(
        "500"
    )


@pytest.mark.unit
def test_long_loss_hand_calculated() -> None:
    """(104 - 105) x 100 x 1 = -100."""
    assert gross_pnl(Direction.LONG, Decimal("105"), Decimal("104"), MULTIPLIER, 1) == Decimal(
        "-100"
    )


@pytest.mark.unit
def test_short_profit_hand_calculated() -> None:
    """(105 - 100) x 100 x 1 = 500. The sign flip is the whole point."""
    assert gross_pnl(Direction.SHORT, Decimal("105"), Decimal("100"), MULTIPLIER, 1) == Decimal(
        "500"
    )


@pytest.mark.unit
def test_short_loss_hand_calculated() -> None:
    """(105 - 110) x 100 x 1 = -500."""
    assert gross_pnl(Direction.SHORT, Decimal("105"), Decimal("110"), MULTIPLIER, 1) == Decimal(
        "-500"
    )


@pytest.mark.unit
def test_long_and_short_are_exact_mirrors() -> None:
    """The same move must not be profitable in both directions."""
    long_pnl = gross_pnl(Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, 1)
    short_pnl = gross_pnl(Direction.SHORT, Decimal("105"), Decimal("110"), MULTIPLIER, 1)
    assert long_pnl == -short_pnl


@pytest.mark.unit
def test_break_even_is_zero_and_not_negative_zero() -> None:
    """``-0.00`` in a column of numbers reads as a loss."""
    result = gross_pnl(Direction.SHORT, Decimal("105"), Decimal("105"), MULTIPLIER, 3)
    assert result == Decimal("0")
    assert str(result) != "-0"


@pytest.mark.unit
@pytest.mark.parametrize(("contracts", "expected"), ((1, "500"), (3, "1500"), (7, "3500")))
def test_multiple_contracts_scale_linearly(contracts: int, expected: str) -> None:
    assert gross_pnl(
        Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, contracts
    ) == Decimal(expected)


@pytest.mark.unit
def test_the_multiplier_is_applied_not_ignored() -> None:
    """A five-point move on a 100x contract is 500, not 5."""
    five_points = gross_pnl(Direction.LONG, Decimal("105"), Decimal("110"), Decimal("1"), 1)
    assert five_points == Decimal("5")
    assert gross_pnl(Direction.LONG, Decimal("105"), Decimal("110"), Decimal("100"), 1) == Decimal(
        "500"
    )


@pytest.mark.unit
def test_decimal_arithmetic_is_exact() -> None:
    """0.1 + 0.2 problems have no place in a P&L figure."""
    result = gross_pnl(Direction.LONG, Decimal("105.10"), Decimal("105.40"), Decimal("100"), 1)
    assert result == Decimal("30.00")


# ----------------------------------------------------------------------
# Rejected inputs
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("contracts", (0, -1))
def test_a_non_positive_contract_count_is_refused(contracts: int) -> None:
    """Zero contracts returning zero P&L would look like a break-even trade."""
    with pytest.raises(PnLInputError, match="contracts"):
        gross_pnl(Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, contracts)


@pytest.mark.unit
@pytest.mark.parametrize(("entry", "exit_price"), (("0", "110"), ("-5", "110"), ("105", "0")))
def test_a_non_positive_price_is_refused(entry: str, exit_price: str) -> None:
    with pytest.raises(PnLInputError):
        gross_pnl(Direction.LONG, Decimal(entry), Decimal(exit_price), MULTIPLIER, 1)


@pytest.mark.unit
@pytest.mark.parametrize("multiplier", ("0", "-100"))
def test_a_non_positive_multiplier_is_refused(multiplier: str) -> None:
    with pytest.raises(PnLInputError, match="multiplier"):
        gross_pnl(Direction.LONG, Decimal("105"), Decimal("110"), Decimal(multiplier), 1)


@pytest.mark.unit
def test_a_neutral_direction_is_refused() -> None:
    with pytest.raises(PnLInputError, match="not a tradeable direction"):
        gross_pnl(Direction.NEUTRAL, Decimal("105"), Decimal("110"), MULTIPLIER, 1)


@pytest.mark.unit
def test_a_non_finite_price_is_refused() -> None:
    with pytest.raises(PnLInputError, match="finite"):
        gross_pnl(Direction.LONG, Decimal("NaN"), Decimal("110"), MULTIPLIER, 1)


# ----------------------------------------------------------------------
# Costs: net only when they are supplied
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_without_costs_net_is_none_and_not_equal_to_gross() -> None:
    """The most important assertion in this module.

    Reporting net == gross would tell a user their scalp was profitable when
    the round trip may not have been. Missing cost data is unknown cost.
    """
    result = calculate_pnl(Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, 1)
    assert result.gross == Decimal("500")
    assert result.net is None
    assert result.net_upper_bound is None
    assert result.cost_completeness is CostCompleteness.UNKNOWN


@pytest.mark.unit
def test_an_empty_cost_object_still_yields_no_net() -> None:
    result = calculate_pnl(
        Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, 1, TradeCosts()
    )
    assert result.net is None


@pytest.mark.unit
def test_supplied_costs_produce_a_net() -> None:
    result = calculate_pnl(
        Direction.LONG,
        Decimal("105"),
        Decimal("110"),
        MULTIPLIER,
        1,
        TradeCosts(commission=Decimal("12"), fees=Decimal("3"), slippage=Decimal("5")),
    )
    assert result.gross == Decimal("500")
    assert result.net == Decimal("480")


@pytest.mark.unit
def test_a_partial_cost_set_never_produces_a_net() -> None:
    """The hardening fix. Subtracting only the commission and calling the
    result "net" understates every unsupplied component as zero."""
    costs = TradeCosts(commission=Decimal("12"))
    assert costs.completeness is CostCompleteness.PARTIAL
    assert costs.known_total == Decimal("12")
    assert costs.missing_components == ("fees", "slippage")

    result = calculate_pnl(Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, 1, costs)
    assert result.cost_completeness is CostCompleteness.PARTIAL
    assert result.net is None
    assert result.net_upper_bound == Decimal("488")


@pytest.mark.unit
def test_negative_costs_are_refused() -> None:
    with pytest.raises(PnLInputError, match="negative"):
        TradeCosts(commission=Decimal("-1"))


@pytest.mark.unit
def test_no_default_commission_exists_anywhere() -> None:
    """A default fee schedule would be a section 118 guess presented as fact."""
    costs = TradeCosts()
    assert costs.commission is None
    assert costs.fees is None
    assert costs.slippage is None
    assert costs.known_total is None
    assert costs.completeness is CostCompleteness.UNKNOWN


@pytest.mark.unit
def test_only_a_complete_cost_set_produces_a_net() -> None:
    costs = TradeCosts(commission=Decimal("12"), fees=Decimal("3"), slippage=Decimal("5"))
    assert costs.completeness is CostCompleteness.COMPLETE
    result = calculate_pnl(Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, 1, costs)
    assert result.net == Decimal("480")
    assert result.net_upper_bound == Decimal("480")


@pytest.mark.unit
def test_an_explicit_zero_is_supplied_information_and_a_missing_value_is_not() -> None:
    """The distinction the two representations exist for.

    A user stating a zero-commission account is telling us something; a user
    who said nothing is not. Only the first can complete a cost set.
    """
    explicit = TradeCosts(commission=Decimal("0"), fees=Decimal("0"), slippage=Decimal("0"))
    assert explicit.completeness is CostCompleteness.COMPLETE
    assert explicit.known_total == Decimal("0")

    result = calculate_pnl(Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, 1, explicit)
    assert result.net == Decimal("500")

    silent = TradeCosts()
    assert silent.completeness is CostCompleteness.UNKNOWN


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kwargs", "missing"),
    (
        ({"commission": Decimal("12")}, ("fees", "slippage")),
        ({"slippage": Decimal("5")}, ("commission", "fees")),
        ({"commission": Decimal("12"), "fees": Decimal("3")}, ("slippage",)),
        ({"fees": Decimal("0"), "slippage": Decimal("0")}, ("commission",)),
    ),
)
def test_every_partial_shape_stays_partial(
    kwargs: dict[str, Decimal], missing: tuple[str, ...]
) -> None:
    costs = TradeCosts(**kwargs)
    assert costs.completeness is CostCompleteness.PARTIAL
    assert costs.missing_components == missing
    result = calculate_pnl(Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, 1, costs)
    assert result.net is None
    assert result.net_upper_bound is not None


@pytest.mark.unit
def test_the_upper_bound_is_never_below_the_true_net() -> None:
    """Unsupplied components are costs, so they can only lower the net."""
    partial = TradeCosts(commission=Decimal("12"))
    complete = TradeCosts(commission=Decimal("12"), fees=Decimal("3"), slippage=Decimal("5"))
    bound = calculate_pnl(
        Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, 1, partial
    ).net_upper_bound
    actual = calculate_pnl(
        Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, 1, complete
    ).net
    assert bound is not None and actual is not None
    assert bound >= actual


# ----------------------------------------------------------------------
# Derived measures
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_return_on_account_is_a_fraction_and_percent_is_points() -> None:
    result = calculate_pnl(Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, 1)
    assert result.return_on_account(Decimal("2500")) == Decimal("0.2")
    assert result.return_on_account_percent(Decimal("2500")) == Decimal("20.0")


@pytest.mark.unit
def test_return_on_margin_uses_the_margin_posted() -> None:
    result = calculate_pnl(Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, 1)
    assert result.return_on_margin(Decimal("250")) == Decimal("2")


@pytest.mark.unit
def test_r_multiple_expresses_pnl_in_units_of_risk() -> None:
    result = calculate_pnl(Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, 1)
    assert result.r_multiple(Decimal("100")) == Decimal("5")


@pytest.mark.unit
def test_zero_denominators_give_none_rather_than_infinity() -> None:
    result = calculate_pnl(Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, 1)
    assert result.return_on_account(Decimal("0")) is None
    assert result.return_on_margin(Decimal("0")) is None
    assert result.r_multiple(Decimal("0")) is None


@pytest.mark.unit
def test_points_captured_are_signed_by_direction() -> None:
    long_result = calculate_pnl(Direction.LONG, Decimal("105"), Decimal("110"), MULTIPLIER, 1)
    short_result = calculate_pnl(Direction.SHORT, Decimal("105"), Decimal("110"), MULTIPLIER, 1)
    assert long_result.points == Decimal("5")
    assert short_result.points == Decimal("-5")


# ----------------------------------------------------------------------
# Contract-aware entry point
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_contract_pnl_uses_the_verified_multiplier() -> None:
    result = calculate_contract_pnl(
        contract(multiplier=verified(100)),
        Direction.LONG,
        Decimal("105"),
        Decimal("110"),
        1,
    )
    assert result.gross == Decimal("500")


@pytest.mark.unit
def test_contract_pnl_refuses_an_unverified_multiplier() -> None:
    """A P&L from a guessed multiplier looks exactly like a real one."""
    from app.domain.common.verification import UnverifiedFinancialFactError

    with pytest.raises(UnverifiedFinancialFactError):
        calculate_contract_pnl(
            contract(multiplier=unverified(100)),
            Direction.LONG,
            Decimal("105"),
            Decimal("110"),
            1,
        )
