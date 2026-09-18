"""Golden paper-trading scenarios, with every expected number derived by hand.

These expectations were **not** produced by running the engine. Each one is the
master spec section 46 formula applied on paper to the fixture - one point is
worth 10 per unit - and written next to its scenario so a reviewer can check it
without trusting any code:

    LONG  : (fill - entry) x 10 x units
    SHORT : (entry - fill) x 10 x units

Every financial behaviour is exercised for LONG and for its exact SHORT mirror,
so a formula that silently assumed LONG fails on the second half.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.market.candle import Candle
from app.domain.paper import (
    FeeMode,
    FeePolicy,
    PaperEventType,
    PaperPosition,
    PositionSpec,
    PositionState,
    SameBarPolicy,
    SimulationPolicy,
    SlippageMode,
    SlippagePolicy,
    apply_observation,
    rebuild,
    request_close,
    unrealized_gross,
)
from tests.factories_paper import approval_for, bar, long_spec, product, run, short_spec

D = Decimal


def events_of(position: PaperPosition, kind: PaperEventType) -> list[dict[str, str]]:
    return [dict(event.data) for event in position.events if event.type is kind]


def assert_replays(position: PaperPosition) -> None:
    """Every golden scenario must rebuild exactly from its own ledger."""
    policy = product()
    rebuilt = rebuild(position.spec, position.approval, policy, position.events)
    assert rebuilt == position


# ======================================================================
# LONG
# ======================================================================


class TestLong:
    def test_entry_fills_at_the_next_open_not_at_the_intended_price(self) -> None:
        position = run(long_spec(), [bar(0, "100.50", "101", "99.50", "100.75")])

        entry = events_of(position, PaperEventType.ENTRY_FILLED)[0]
        assert entry["intended_entry"] == "100.00"
        assert entry["fill_price"] == "100.50"
        assert entry["difference_from_intended"] == "0.50"
        assert position.entry_fill_price == D("100.50")
        assert position.state is PositionState.OPEN
        assert position.remaining == 4
        # (100.75 - 100.50) x 10 x 4 = 10
        assert unrealized_gross(position, product()) == D("10.00")

    def test_both_targets(self) -> None:
        position = run(
            long_spec(),
            [
                bar(0, "100", "101", "99.50", "100.50"),  # entry 100
                bar(1, "101", "104.50", "100.75", "104.25"),  # T1 104 x2
                bar(2, "104.50", "106.25", "104", "106"),  # T2 106 x2
            ],
        )
        targets = events_of(position, PaperEventType.TARGET_FILLED)

        # (104 - 100) x 10 x 2 = 80 ; (106 - 100) x 10 x 2 = 120
        assert [t["gross_pnl"] for t in targets] == ["80.00", "120.00"]
        assert [t["remaining"] for t in targets] == ["2", "0"]
        assert position.realized_gross == D("200.00")
        assert position.state is PositionState.CLOSED
        assert position.remaining == 0
        assert unrealized_gross(position, product()) == D("0")
        assert_replays(position)

    def test_partial_state_and_unrealized_on_remaining_units_only(self) -> None:
        position = run(
            long_spec(),
            [bar(0, "100", "101", "99.50", "100.50"), bar(1, "101", "104.50", "100.75", "104.25")],
        )

        assert position.state is PositionState.PARTIALLY_CLOSED
        assert position.remaining == 2
        assert position.realized_gross == D("80.00")
        # (104.25 - 100) x 10 x 2 = 85, on the 2 open units - never on all 4
        assert unrealized_gross(position, product()) == D("85.00")

    def test_stop(self) -> None:
        position = run(
            long_spec(),
            [bar(0, "100", "101", "99.50", "100.50"), bar(1, "99.50", "100", "97.75", "98")],
        )
        stop = events_of(position, PaperEventType.STOP_FILLED)[0]

        assert stop["fill_price"] == "98.00"
        assert stop["gap"] == "false"
        assert stop["quantity"] == "4"
        # (98 - 100) x 10 x 4 = -80
        assert position.realized_gross == D("-80.00")
        assert position.state is PositionState.CLOSED
        assert_replays(position)

    def test_partial_target_then_stop_closes_only_what_remains(self) -> None:
        position = run(
            long_spec(),
            [
                bar(0, "100", "101", "99.50", "100.50"),
                bar(1, "101", "104.50", "100.75", "104.25"),  # T1: +80, 2 remain
                bar(2, "103", "103.50", "97.50", "98"),  # stop on the remaining 2
            ],
        )
        stop = events_of(position, PaperEventType.STOP_FILLED)[0]

        assert stop["quantity"] == "2"
        # (98 - 100) x 10 x 2 = -40 ; total 80 - 40 = 40
        assert stop["gross_pnl"] == "-40.00"
        assert position.realized_gross == D("40.00")
        assert position.closed_quantity == 4
        assert_replays(position)

    def test_gap_through_the_stop_fills_at_the_open(self) -> None:
        position = run(
            long_spec(),
            [bar(0, "100", "101", "99.50", "100.50"), bar(1, "95", "96", "94", "95.50")],
        )
        stop = events_of(position, PaperEventType.STOP_FILLED)[0]

        assert stop["trigger_price"] == "98.00"
        assert stop["reference_price"] == "95"
        assert stop["fill_price"] == "95"
        assert stop["gap"] == "true"
        # (95 - 100) x 10 x 4 = -200, not the -80 a fill at the skipped stop would claim
        assert position.realized_gross == D("-200")
        assert_replays(position)

    def test_gap_through_a_target_credits_no_price_improvement(self) -> None:
        position = run(
            long_spec(),
            [bar(0, "100", "101", "99.50", "100.50"), bar(1, "105", "105.50", "104.75", "105")],
        )
        target = events_of(position, PaperEventType.TARGET_FILLED)[0]

        assert target["gap"] == "true"
        assert target["fill_price"] == "104.00"
        # (104 - 100) x 10 x 2 = 80, not (105 - 100) x 10 x 2 = 100
        assert target["gross_pnl"] == "80.00"

    def test_same_bar_stop_and_target_is_resolved_stop_first_and_says_so(self) -> None:
        position = run(
            long_spec(),
            [bar(0, "100", "101", "99.50", "100.50"), bar(1, "100", "104.50", "97.50", "100")],
        )
        ambiguity = events_of(position, PaperEventType.SAME_BAR_AMBIGUITY)[0]
        stop = events_of(position, PaperEventType.STOP_FILLED)[0]

        assert ambiguity["policy"] == "STOP_FIRST"
        assert ambiguity["targets_touched"] == "1"
        assert stop["ambiguous"] == "true"
        assert stop["targets_also_touched"] == "1"
        assert events_of(position, PaperEventType.TARGET_FILLED) == []
        # (98 - 100) x 10 x 4 = -80 - the pessimistic reading, never +80
        assert position.realized_gross == D("-80.00")
        assert_replays(position)

    def test_same_bar_ambiguity_can_halt_instead(self) -> None:
        spec = long_spec(policy=SimulationPolicy(same_bar=SameBarPolicy.HALT))
        position = run(
            spec,
            [bar(0, "100", "101", "99.50", "100.50"), bar(1, "100", "104.50", "97.50", "100")],
        )

        assert position.state is PositionState.AMBIGUOUS_HALTED
        assert position.remaining == 4
        assert position.realized_gross == D("0")
        assert events_of(position, PaperEventType.STOP_FILLED) == []
        assert events_of(position, PaperEventType.TARGET_FILLED) == []

        # A halted position ignores later levels and only exits by request.
        policy = product()
        position = apply_observation(position, bar(2, "101", "107", "96", "101"), policy)
        assert position.state is PositionState.AMBIGUOUS_HALTED
        position = request_close(position)
        position = apply_observation(position, bar(3, "102", "103", "101", "102.50"), policy)

        exit_fill = events_of(position, PaperEventType.MANUAL_EXIT_FILLED)[0]
        assert exit_fill["fill_price"] == "102"
        # (102 - 100) x 10 x 4 = 80
        assert position.realized_gross == D("80")
        assert position.state is PositionState.CLOSED
        rebuilt = rebuild(position.spec, position.approval, policy, position.events)
        assert rebuilt == position

    def test_entry_bar_opening_beyond_the_stop_rejects_the_plan(self) -> None:
        position = run(long_spec(), [bar(0, "97.50", "98", "97", "97.75")])

        assert position.state is PositionState.REJECTED
        assert position.entry_fill_price is None
        assert position.filled_quantity == 0
        assert events_of(position, PaperEventType.ENTRY_FILLED) == []
        assert position.realized_gross == D("0")

    def test_manual_close_fills_at_the_next_open(self) -> None:
        policy = product()
        position = run(long_spec(), [bar(0, "100", "101", "99.50", "100.50")], policy)
        position = request_close(position)
        position = apply_observation(position, bar(1, "101.25", "102", "101", "101.50"), policy)

        # (101.25 - 100) x 10 x 4 = 50
        assert position.realized_gross == D("50.00")
        assert position.state is PositionState.CLOSED


# ======================================================================
# SHORT - the exact mirror
# ======================================================================


class TestShort:
    def test_both_targets(self) -> None:
        position = run(
            short_spec(),
            [
                bar(0, "100", "100.50", "99", "99.50"),  # entry 100
                bar(1, "99", "99.25", "95.50", "95.75"),  # T1 96 x2
                bar(2, "95.50", "96", "93.75", "94"),  # T2 94 x2
            ],
        )
        targets = events_of(position, PaperEventType.TARGET_FILLED)

        # (100 - 96) x 10 x 2 = 80 ; (100 - 94) x 10 x 2 = 120
        assert [t["gross_pnl"] for t in targets] == ["80.00", "120.00"]
        assert position.realized_gross == D("200.00")
        assert position.state is PositionState.CLOSED
        assert_replays(position)

    def test_unrealized_is_signed_for_a_short(self) -> None:
        position = run(short_spec(), [bar(0, "100", "100.50", "99", "99.50")])

        # (100 - 99.50) x 10 x 4 = 20: a short gains as the price falls
        assert unrealized_gross(position, product()) == D("20.00")

    def test_stop(self) -> None:
        position = run(
            short_spec(),
            [bar(0, "100", "100.50", "99", "99.50"), bar(1, "100.50", "102.25", "100", "102")],
        )

        # (100 - 102) x 10 x 4 = -80
        assert position.realized_gross == D("-80.00")
        assert position.state is PositionState.CLOSED
        assert_replays(position)

    def test_partial_target_then_stop_closes_only_what_remains(self) -> None:
        position = run(
            short_spec(),
            [
                bar(0, "100", "100.50", "99", "99.50"),
                bar(1, "99", "99.25", "95.50", "95.75"),  # T1: +80
                bar(2, "97", "102.50", "96.50", "102"),  # stop on 2
            ],
        )
        stop = events_of(position, PaperEventType.STOP_FILLED)[0]

        assert stop["quantity"] == "2"
        # (100 - 102) x 10 x 2 = -40 ; total 40
        assert position.realized_gross == D("40.00")
        assert_replays(position)

    def test_gap_through_the_stop_fills_at_the_open(self) -> None:
        position = run(
            short_spec(),
            [bar(0, "100", "100.50", "99", "99.50"), bar(1, "105", "106", "104", "105.50")],
        )
        stop = events_of(position, PaperEventType.STOP_FILLED)[0]

        assert stop["gap"] == "true"
        assert stop["fill_price"] == "105"
        # (100 - 105) x 10 x 4 = -200
        assert position.realized_gross == D("-200")
        assert_replays(position)

    def test_same_bar_stop_and_target_is_resolved_stop_first(self) -> None:
        position = run(
            short_spec(),
            [bar(0, "100", "100.50", "99", "99.50"), bar(1, "100", "102.50", "95.50", "100")],
        )

        assert events_of(position, PaperEventType.SAME_BAR_AMBIGUITY)[0]["targets_touched"] == "1"
        # (100 - 102) x 10 x 4 = -80
        assert position.realized_gross == D("-80.00")
        assert_replays(position)

    def test_entry_bar_opening_beyond_the_stop_rejects_the_plan(self) -> None:
        position = run(short_spec(), [bar(0, "102.50", "103", "102", "102.75")])

        assert position.state is PositionState.REJECTED


# ======================================================================
# Slippage and fees
# ======================================================================


class TestSlippageAndFees:
    @pytest.mark.parametrize(
        ("spec_factory", "bars", "expected"),
        [
            (
                long_spec,
                [
                    bar(0, "100", "101", "99.50", "100.50"),
                    bar(1, "101", "104.50", "100.75", "104.25"),
                    bar(2, "97", "97.50", "96", "97"),
                ],
                # entry 100 + 0.25 = 100.25 ; T1 at 104 (no slippage on a target):
                #   (104 - 100.25) x 10 x 2 = 75
                # gap stop at open 97 - 0.25 = 96.75 : (96.75 - 100.25) x 10 x 2 = -70
                # gross 5 ; fees 2 x (4 + 2 + 2) = 16 ; net -11
                {"entry": "100.25", "gross": "5.00", "fees": "16", "net": "-11.00"},
            ),
            (
                short_spec,
                [
                    bar(0, "100", "100.50", "99", "99.50"),
                    bar(1, "99", "99.25", "95.50", "95.75"),
                    bar(2, "103", "104", "102.50", "103"),
                ],
                # entry 100 - 0.25 = 99.75 ; T1 at 96 : (99.75 - 96) x 10 x 2 = 75
                # gap stop at open 103 + 0.25 = 103.25 : (99.75 - 103.25) x 10 x 2 = -70
                {"entry": "99.75", "gross": "5.00", "fees": "16", "net": "-11.00"},
            ),
        ],
        ids=["long", "short"],
    )
    def test_adverse_slippage_and_user_fees(
        self,
        spec_factory: Callable[..., PositionSpec],
        bars: list[Candle],
        expected: dict[str, str],
    ) -> None:
        policy = SimulationPolicy(
            slippage=SlippagePolicy(SlippageMode.FIXED_POINTS, D("0.25")),
            fees=FeePolicy(FeeMode.USER_DEFINED_PER_UNIT, D("2")),
        )
        spec = spec_factory(policy=policy)
        position = run(spec, bars)

        assert position.entry_fill_price == D(expected["entry"])
        assert position.realized_gross == D(expected["gross"])
        assert position.fees_total == D(expected["fees"])
        assert position.realized_net == D(expected["net"])
        closed = events_of(position, PaperEventType.POSITION_CLOSED)[0]
        assert closed["realized_net"] == expected["net"]
        assert_replays(position)

    def test_fees_not_modelled_means_no_net_rather_than_zero_cost(self) -> None:
        position = run(
            long_spec(),
            [bar(0, "100", "101", "99.50", "100.50"), bar(1, "99.50", "100", "97.75", "98")],
        )

        assert position.fees_total is None
        assert position.realized_net is None
        closed = events_of(position, PaperEventType.POSITION_CLOSED)[0]
        assert closed["fees_total"] == ""
        assert closed["realized_net"] == ""

    def test_a_zero_user_fee_is_stated_not_assumed(self) -> None:
        spec = long_spec(
            policy=SimulationPolicy(fees=FeePolicy(FeeMode.USER_DEFINED_PER_UNIT, D("0")))
        )
        position = run(
            spec,
            [bar(0, "100", "101", "99.50", "100.50"), bar(1, "99.50", "100", "97.75", "98")],
        )

        assert position.fees_total == D("0")
        assert position.realized_net == position.realized_gross
        created = events_of(position, PaperEventType.POSITION_CREATED)[0]
        assert created["fee_mode"] == "USER_DEFINED_PER_UNIT"

    def test_each_fill_charges_its_own_fee_exactly_once(self) -> None:
        spec = long_spec(
            policy=SimulationPolicy(fees=FeePolicy(FeeMode.USER_DEFINED_PER_UNIT, D("3")))
        )
        position = run(
            spec,
            [
                bar(0, "100", "101", "99.50", "100.50"),
                bar(1, "101", "104.50", "100.75", "104.25"),
                bar(2, "104.50", "106.25", "104", "106"),
            ],
        )
        fees = [
            event.data["fee"]
            for event in position.events
            if event.type
            in (
                PaperEventType.ENTRY_FILLED,
                PaperEventType.TARGET_FILLED,
                PaperEventType.STOP_FILLED,
            )
        ]

        # entry 3 x 4 = 12, T1 3 x 2 = 6, T2 3 x 2 = 6
        assert fees == ["12", "6", "6"]
        assert position.fees_total == D("24")


# ======================================================================
# Determinism
# ======================================================================


def test_identical_inputs_give_identical_results() -> None:
    bars = [
        bar(0, "100", "101", "99.50", "100.50"),
        bar(1, "101", "104.50", "100.75", "104.25"),
        bar(2, "103", "103.50", "97.50", "98"),
    ]
    first = run(long_spec(), bars)
    second = run(long_spec(), bars)

    assert first == second
    assert [event.data for event in first.events] == [event.data for event in second.events]


def test_the_position_id_does_not_influence_any_financial_result() -> None:
    bars = [bar(0, "100", "101", "99.50", "100.50"), bar(1, "95", "96", "94", "95.50")]
    first = run(long_spec(position_id="PP-A"), bars)
    second = run(long_spec(position_id="PP-B"), bars)

    assert first.realized_gross == second.realized_gross
    assert [(e.type, dict(e.data)) for e in first.events[1:]] == [
        (e.type, dict(e.data)) for e in second.events[1:]
    ]


def test_approval_is_what_the_real_risk_engine_returned() -> None:
    """The fixture is large enough for the golden quantity to be permitted."""
    approval = approval_for(long_spec())

    assert approval.outcome.value == "ALLOWED"
    assert approval.allowed_units is not None
    assert approval.allowed_units >= 4
    assert replace(approval) == approval
