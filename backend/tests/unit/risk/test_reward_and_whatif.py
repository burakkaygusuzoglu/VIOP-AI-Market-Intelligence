"""Risk/reward (section 43) and the what-if simulator (section 47).

All values are TEST_FIXTURE data.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.common.enums import Direction
from app.domain.risk.reward import risk_reward, risk_reward_targets
from app.domain.risk.whatif import simulate, simulate_contract
from tests.factories_futures import contract, unverified, verified

# ----------------------------------------------------------------------
# Risk / reward
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_long_risk_reward_hand_calculated() -> None:
    """Entry 105, stop 104, target 108: risk 1, reward 3, ratio 3."""
    result = risk_reward(Direction.LONG, Decimal("105"), Decimal("104"), Decimal("108"))
    assert result.risk_distance == Decimal("1")
    assert result.reward_distance == Decimal("3")
    assert result.ratio == Decimal("3")
    assert result.is_valid


@pytest.mark.unit
def test_short_risk_reward_hand_calculated() -> None:
    """Entry 105, stop 106, target 99: risk 1, reward 6, ratio 6."""
    result = risk_reward(Direction.SHORT, Decimal("105"), Decimal("106"), Decimal("99"))
    assert result.ratio == Decimal("6")


@pytest.mark.unit
def test_a_long_target_below_entry_is_refused_not_absolute_valued() -> None:
    """A "target" below a long's entry is not a target, it is a second stop."""
    result = risk_reward(Direction.LONG, Decimal("105"), Decimal("104"), Decimal("102"))
    assert result.ratio is None
    assert not result.is_valid
    assert "must be above the entry" in result.reason


@pytest.mark.unit
def test_a_short_target_above_entry_is_refused() -> None:
    result = risk_reward(Direction.SHORT, Decimal("105"), Decimal("106"), Decimal("108"))
    assert result.ratio is None
    assert "must be below the entry" in result.reason


@pytest.mark.unit
def test_a_target_equal_to_entry_is_refused() -> None:
    result = risk_reward(Direction.LONG, Decimal("105"), Decimal("104"), Decimal("105"))
    assert result.ratio is None


@pytest.mark.unit
def test_a_wrong_sided_stop_is_refused_before_the_target_is_considered() -> None:
    result = risk_reward(Direction.LONG, Decimal("105"), Decimal("106"), Decimal("110"))
    assert result.ratio is None
    assert "stop must be below" in result.reason


@pytest.mark.unit
def test_a_neutral_direction_is_refused() -> None:
    result = risk_reward(Direction.NEUTRAL, Decimal("105"), Decimal("104"), Decimal("110"))
    assert result.ratio is None


@pytest.mark.unit
def test_multiple_targets_are_evaluated_independently() -> None:
    """One badly placed level must not discard the others."""
    results = risk_reward_targets(
        Direction.LONG,
        Decimal("105"),
        Decimal("104"),
        (Decimal("107"), Decimal("110"), Decimal("103")),
    )
    assert [result.ratio for result in results] == [Decimal("2"), Decimal("5"), None]


@pytest.mark.unit
def test_risk_reward_is_arithmetic_not_a_probability() -> None:
    """Section 43: risk/reward is not setup quality and not a likelihood."""
    from app.domain.risk.reward import RiskReward

    fields = set(RiskReward.__slots__)
    forbidden = {"probability", "confidence", "quality", "score", "win_rate", "edge"}
    assert not (fields & forbidden)


@pytest.mark.unit
def test_risk_reward_is_reproducible() -> None:
    args = (Direction.LONG, Decimal("105"), Decimal("104"), Decimal("108"))
    assert risk_reward(*args) == risk_reward(*args)


# ----------------------------------------------------------------------
# What-if
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_what_if_evaluates_every_supplied_price() -> None:
    """Entry 105, multiplier 100, 1 contract, on 2,500 of equity."""
    result = simulate(
        Direction.LONG,
        Decimal("105"),
        Decimal("100"),
        1,
        Decimal("2500"),
        (Decimal("107"), Decimal("106"), Decimal("104")),
    )
    assert [scenario.gross_pnl for scenario in result.scenarios] == [
        Decimal("200"),
        Decimal("100"),
        Decimal("-100"),
    ]


@pytest.mark.unit
def test_what_if_reports_account_percentage() -> None:
    result = simulate(
        Direction.LONG, Decimal("105"), Decimal("100"), 1, Decimal("2500"), (Decimal("107"),)
    )
    scenario = result.scenarios[0]
    assert scenario.account_ratio == Decimal("0.08")
    assert scenario.account_percent == Decimal("8.00")


@pytest.mark.unit
def test_what_if_mirrors_for_a_short() -> None:
    long_result = simulate(
        Direction.LONG, Decimal("105"), Decimal("100"), 1, Decimal("2500"), (Decimal("107"),)
    )
    short_result = simulate(
        Direction.SHORT, Decimal("105"), Decimal("100"), 1, Decimal("2500"), (Decimal("107"),)
    )
    assert long_result.scenarios[0].gross_pnl == -short_result.scenarios[0].gross_pnl


@pytest.mark.unit
def test_r_multiple_appears_only_when_the_initial_risk_is_known() -> None:
    """A scenario cannot be expressed in R if nobody said what R was."""
    without = simulate(
        Direction.LONG, Decimal("105"), Decimal("100"), 1, Decimal("2500"), (Decimal("107"),)
    )
    assert without.scenarios[0].r_multiple is None

    with_risk = simulate(
        Direction.LONG,
        Decimal("105"),
        Decimal("100"),
        1,
        Decimal("2500"),
        (Decimal("107"),),
        initial_risk=Decimal("100"),
    )
    assert with_risk.scenarios[0].r_multiple == Decimal("2")


@pytest.mark.unit
def test_a_zero_account_leaves_the_percentage_undefined() -> None:
    result = simulate(
        Direction.LONG, Decimal("105"), Decimal("100"), 1, Decimal("0"), (Decimal("107"),)
    )
    assert result.scenarios[0].gross_pnl == Decimal("200")
    assert result.scenarios[0].account_ratio is None


@pytest.mark.unit
def test_no_market_scenario_is_hard_coded() -> None:
    """Section 44's -1/-2/-3/-5% are examples in a document, not defaults here."""
    result = simulate(Direction.LONG, Decimal("105"), Decimal("100"), 1, Decimal("2500"), ())
    assert result.scenarios == ()


@pytest.mark.unit
def test_scenarios_keep_the_order_they_were_supplied_in() -> None:
    prices = (Decimal("110"), Decimal("100"), Decimal("105"))
    result = simulate(Direction.LONG, Decimal("105"), Decimal("100"), 1, Decimal("2500"), prices)
    assert tuple(scenario.price for scenario in result.scenarios) == prices


@pytest.mark.unit
def test_what_if_is_deterministic() -> None:
    args = (
        Direction.LONG,
        Decimal("105"),
        Decimal("100"),
        1,
        Decimal("2500"),
        (Decimal("107"), Decimal("103")),
    )
    assert simulate(*args) == simulate(*args)


@pytest.mark.unit
def test_contract_what_if_refuses_an_unverified_multiplier() -> None:
    from app.domain.common.verification import UnverifiedFinancialFactError

    with pytest.raises(UnverifiedFinancialFactError):
        simulate_contract(
            contract(multiplier=unverified(100)),
            Direction.LONG,
            Decimal("105"),
            1,
            Decimal("2500"),
            (Decimal("107"),),
        )


@pytest.mark.unit
def test_contract_what_if_uses_the_verified_multiplier() -> None:
    result = simulate_contract(
        contract(multiplier=verified(100)),
        Direction.LONG,
        Decimal("105"),
        1,
        Decimal("2500"),
        (Decimal("107"),),
    )
    assert result.scenarios[0].gross_pnl == Decimal("200")
