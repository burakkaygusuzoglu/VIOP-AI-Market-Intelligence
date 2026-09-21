"""Golden scenarios: one run, one known outcome, derived by hand (Phase 12).

Each test states a fixture's OHLC outright, scripts one decision, and asserts
the exact fills that follow. The expectations are arithmetic a person can check
against the bars written a few lines above them - not a recorded output that
would "pass" again after the engine started computing something else.

The scripted strategy is deliberate: an EMA crossover is a poor instrument for
asking whether an entry decided at a boundary fills at the *next* bar's open.
The reference strategy's own rules are proven in ``tests/unit/backtest``.

Every price is on the fixture contract's 0.25 grid, and the fixture contract
is a TEST_FIXTURE - no number here claims to be a current exchange fact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

import pytest

from app.adapters.persistence.database import Database
from app.application.backtest.ports import BacktestPosition, StoredRun
from app.application.backtest.service import BacktestServiceError
from app.domain.backtest.policy import StrategyDecision
from app.domain.backtest.run import DecisionOutcome, RunStatus
from app.domain.common.enums import Direction, Timeframe
from app.domain.common.verification import VerificationStatus, VerifiedValue
from app.domain.paper.model import PaperEventType, PositionOrigin
from app.domain.paper.rules import (
    FeeMode,
    FeePolicy,
    SameBarPolicy,
    SimulationPolicy,
    SlippageMode,
    SlippagePolicy,
)
from app.domain.risk.sizing import AccountState, RiskMode, RiskPolicy
from tests.factories_paper import paper_contract
from tests.factories_replay import Row
from tests.integration.backtest_support import (
    ScriptedStrategy,
    bars,
    flat_rows,
    intent,
    runner,
    scripted_request,
    seed_dataset,
)

pytestmark = pytest.mark.integration

M5_ONLY = (Timeframe.M5,)


def enter(
    direction: Direction,
    *,
    entry: str,
    stop: str,
    targets: Sequence[tuple[str, int]] = (("999999", 1),),
    quantity: int = 1,
) -> StrategyDecision:
    return StrategyDecision.enter(
        intent(direction, entry=entry, stop=stop, targets=targets, quantity=quantity),
        f"scripted {direction.value} entry",
    )


def fills(position: BacktestPosition) -> list[tuple[str, str]]:
    """``(event, fill_price)`` for every event that moved money."""
    return [
        (event.type.value, event.data["fill_price"])
        for event in position.events
        if "fill_price" in event.data
    ]


def only(position: BacktestPosition, kind: PaperEventType) -> Mapping[str, str]:
    """The data of the one event of this kind. Two would be a finding."""
    (found,) = [event for event in position.events if event.type is kind]
    return found.data


async def run_with(
    database: Database,
    rows: Sequence[Row],
    plan: dict[int, StrategyDecision],
    *,
    key: str,
    last: int,
    first: int = 0,
    simulation: SimulationPolicy | None = None,
    risk: RiskPolicy | None = None,
    account: AccountState | None = None,
) -> tuple[StoredRun, ScriptedStrategy]:
    """Seed an M5-only dataset, run one scripted plan over it, return the run."""
    dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
    strategy = ScriptedStrategy(plan=plan)
    stored = await runner(database).run(
        scripted_request(
            dataset,
            strategy,
            key=key,
            first=first,
            last=last,
            simulation=simulation,
            risk=risk,
            account=account,
        )
    )
    return stored, strategy


# ----------------------------------------------------------------------
# A. Nothing happens
# ----------------------------------------------------------------------


class TestAQuietRun:
    async def test_a_run_with_no_signal_completes_with_no_positions(
        self, database: Database
    ) -> None:
        stored, _ = await run_with(database, flat_rows(6), {}, key="golden-a-quiet-000001", last=5)

        assert stored.status is RunStatus.COMPLETED
        assert stored.result is not None
        assert stored.result.positions == ()
        assert {record.outcome for record in stored.result.decisions} == {DecisionOutcome.NO_SIGNAL}

    async def test_every_boundary_in_the_window_is_evaluated_exactly_once(
        self, database: Database
    ) -> None:
        stored, strategy = await run_with(
            database, flat_rows(6), {}, key="golden-a-once-0000001", first=1, last=4
        )

        assert stored.result is not None
        assert stored.result.boundaries_evaluated == 4
        assert len(strategy.seen) == 4
        assert [context.as_of for context in strategy.seen] == [
            record.as_of for record in stored.result.decisions
        ]


# ----------------------------------------------------------------------
# B. The entry is a next-bar entry, and the price is the next bar's open
# ----------------------------------------------------------------------


class TestBNextBarEntry:
    async def test_a_signal_never_fills_on_the_bar_that_produced_it(
        self, database: Database
    ) -> None:
        """Bar 1 closes at 101 and signals; bar 2 opens at 102 and fills there.

        Filling at 101 would be hindsight: at the moment of the decision, the
        101 print was the last thing that had happened.
        """
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "101", "100", "101"),
            ("102", "103", "102", "103"),
            ("103", "103", "103", "103"),
        )
        plan = {1: enter(Direction.LONG, entry="101", stop="98", targets=(("110", 1),))}

        stored, _ = await run_with(database, rows, plan, key="golden-b-nextbar-01", last=3)

        assert stored.result is not None
        (position,) = stored.result.positions
        entry_events = [
            event for event in position.events if event.type is PaperEventType.ENTRY_FILLED
        ]
        (entry_event,) = entry_events
        assert entry_event.data["fill_price"] == "102"
        assert entry_event.market_time == rows[2].open_time

    async def test_the_decision_is_stamped_with_the_boundary_not_the_bar_open(
        self, database: Database
    ) -> None:
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "101", "100", "101"),
            ("102", "103", "102", "103"),
        )
        plan = {1: enter(Direction.LONG, entry="101", stop="98", targets=(("110", 1),))}

        stored, _ = await run_with(database, rows, plan, key="golden-b-stamp-0001", last=2)

        assert stored.result is not None
        (position,) = stored.result.positions
        assert position.spec.decision_time == rows[2].open_time
        assert position.spec.origin is PositionOrigin.STRATEGY_BACKTEST


# ----------------------------------------------------------------------
# C-D. A long that wins, a short that wins
# ----------------------------------------------------------------------


class TestCDWinners:
    async def test_a_long_reaching_its_target_records_that_fill(self, database: Database) -> None:
        """Entry at 100, target 106: the fill is the target, not the bar's high."""
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "101", "100", "101"),
            ("104", "108", "104", "107"),
        )
        plan = {1: enter(Direction.LONG, entry="100", stop="97", targets=(("106", 1),))}

        stored, _ = await run_with(database, rows, plan, key="golden-c-long-0001", last=3)

        assert stored.result is not None
        (position,) = stored.result.positions
        assert fills(position) == [("ENTRY_FILLED", "100"), ("TARGET_FILLED", "106")]

    async def test_a_short_reaching_its_target_records_that_fill(self, database: Database) -> None:
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "100", "99", "99"),
            ("96", "96", "92", "93"),
        )
        plan = {1: enter(Direction.SHORT, entry="100", stop="103", targets=(("94", 1),))}

        stored, _ = await run_with(database, rows, plan, key="golden-d-short-001", last=3)

        assert stored.result is not None
        (position,) = stored.result.positions
        assert fills(position) == [("ENTRY_FILLED", "100"), ("TARGET_FILLED", "94")]


# ----------------------------------------------------------------------
# E-F. Losers, and the gap that fills worse than the stop
# ----------------------------------------------------------------------


class TestEFLosers:
    async def test_a_long_stopped_inside_a_bar_fills_at_the_stop(self, database: Database) -> None:
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "100", "96", "97"),
        )
        plan = {1: enter(Direction.LONG, entry="100", stop="97", targets=(("110", 1),))}

        stored, _ = await run_with(database, rows, plan, key="golden-e-stop-0001", last=2)

        assert stored.result is not None
        (position,) = stored.result.positions
        assert fills(position) == [("ENTRY_FILLED", "100"), ("STOP_FILLED", "97")]

    async def test_a_gap_through_the_stop_fills_at_the_open_and_says_so(
        self, database: Database
    ) -> None:
        """The stop is 97; the market opens at 94. Nobody was filled at 97.

        Crediting the stop price here is the single most flattering error a
        backtest can make, so the fill is the open and the event is flagged.
        """
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("94", "94", "90", "91"),
        )
        plan = {1: enter(Direction.LONG, entry="100", stop="97", targets=(("110", 1),))}

        stored, _ = await run_with(database, rows, plan, key="golden-f-gap-00001", last=3)

        assert stored.result is not None
        (position,) = stored.result.positions
        assert fills(position) == [("ENTRY_FILLED", "100"), ("STOP_FILLED", "94")]
        assert only(position, PaperEventType.STOP_FILLED)["gap"] == "true"


# ----------------------------------------------------------------------
# G. One bar touching both levels is not resolved in the run's favour
# ----------------------------------------------------------------------


class TestGSameBar:
    async def test_a_bar_reaching_both_levels_is_taken_as_the_stop(
        self, database: Database
    ) -> None:
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "107", "96", "101"),
        )
        plan = {1: enter(Direction.LONG, entry="100", stop="97", targets=(("106", 1),))}

        stored, _ = await run_with(database, rows, plan, key="golden-g-ambig-001", last=2)

        assert stored.result is not None
        (position,) = stored.result.positions
        assert fills(position) == [("ENTRY_FILLED", "100"), ("STOP_FILLED", "97")]
        stop_event = only(position, PaperEventType.STOP_FILLED)
        assert stop_event["ambiguous"] == "true"
        assert stop_event["targets_also_touched"] == "1"

    async def test_halting_on_an_ambiguous_bar_decides_nothing(self, database: Database) -> None:
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "107", "96", "101"),
        )
        plan = {1: enter(Direction.LONG, entry="100", stop="97", targets=(("106", 1),))}

        stored, _ = await run_with(
            database,
            rows,
            plan,
            key="golden-g-halt-00001",
            last=2,
            simulation=SimulationPolicy(same_bar=SameBarPolicy.HALT),
        )

        assert stored.result is not None
        (position,) = stored.result.positions
        assert fills(position) == [("ENTRY_FILLED", "100")]


# ----------------------------------------------------------------------
# H. A partial exit leaves the rest running
# ----------------------------------------------------------------------


class TestHPartialExit:
    async def test_the_first_target_closes_part_and_the_stop_takes_the_rest(
        self, database: Database
    ) -> None:
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "104", "100", "104"),
            ("104", "104", "96", "97"),
        )
        plan = {
            1: enter(
                Direction.LONG,
                entry="100",
                stop="97",
                targets=(("103", 1), ("110", 1)),
                quantity=2,
            )
        }

        stored, _ = await run_with(database, rows, plan, key="golden-h-partial-1", last=3)

        assert stored.result is not None
        (position,) = stored.result.positions
        assert position.spec.quantity == 2
        assert fills(position) == [
            ("ENTRY_FILLED", "100"),
            ("TARGET_FILLED", "103"),
            ("STOP_FILLED", "97"),
        ]


# ----------------------------------------------------------------------
# I-J. Costs are modelled only when the user stated them
# ----------------------------------------------------------------------


class TestIJCosts:
    async def test_an_unmodelled_fee_reports_no_net_figure(self, database: Database) -> None:
        """Absent cost data is unknown cost, never zero cost."""
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "107", "100", "107"),
        )
        plan = {1: enter(Direction.LONG, entry="100", stop="97", targets=(("106", 1),))}

        stored, _ = await run_with(database, rows, plan, key="golden-i-nofee-001", last=2)

        assert stored.result is not None
        (position,) = stored.result.positions
        assert only(position, PaperEventType.TARGET_FILLED)["fee"] == ""
        assert only(position, PaperEventType.POSITION_CLOSED)["realized_net"] == ""

    async def test_a_user_defined_fee_is_charged_on_every_fill(self, database: Database) -> None:
        """Gross 6 points x 10 multiplier = 60; two fills at 2.50 = 5 charged."""
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "107", "100", "107"),
        )
        plan = {1: enter(Direction.LONG, entry="100", stop="97", targets=(("106", 1),))}

        stored, _ = await run_with(
            database,
            rows,
            plan,
            key="golden-j-fee-000001",
            last=2,
            simulation=SimulationPolicy(
                fees=FeePolicy(mode=FeeMode.USER_DEFINED_PER_UNIT, per_unit=Decimal("2.50"))
            ),
        )

        assert stored.result is not None
        (position,) = stored.result.positions
        assert only(position, PaperEventType.TARGET_FILLED)["gross_pnl"] == "60"
        closed = only(position, PaperEventType.POSITION_CLOSED)
        assert closed["realized_gross"] == "60"
        assert closed["fees_total"] == "5.00"
        assert closed["realized_net"] == "55.00"


# ----------------------------------------------------------------------
# K. Slippage moves a market-style fill against the position
# ----------------------------------------------------------------------


class TestKSlippage:
    async def test_the_entry_is_filled_worse_than_the_open(self, database: Database) -> None:
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
        )
        plan = {1: enter(Direction.LONG, entry="100", stop="97", targets=(("110", 1),))}

        stored, _ = await run_with(
            database,
            rows,
            plan,
            key="golden-k-slip-00001",
            last=2,
            simulation=SimulationPolicy(
                slippage=SlippagePolicy(mode=SlippageMode.FIXED_POINTS, points=Decimal("0.25"))
            ),
        )

        assert stored.result is not None
        (position,) = stored.result.positions
        assert fills(position) == [("ENTRY_FILLED", "100.25")]


# ----------------------------------------------------------------------
# L-M. Risk decides the size, and may refuse outright
# ----------------------------------------------------------------------


class TestLMRisk:
    async def test_risk_may_allow_fewer_units_than_the_rule_asked_for(
        self, database: Database
    ) -> None:
        """3 points x 10 = 30 per unit; 100 of risk buys 3, not the 10 asked."""
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
        )
        plan = {
            1: enter(Direction.LONG, entry="100", stop="97", targets=(("110", 10),), quantity=10)
        }

        stored, _ = await run_with(
            database,
            rows,
            plan,
            key="golden-l-size-00001",
            last=2,
            risk=RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("100")),
        )

        assert stored.result is not None
        (position,) = stored.result.positions
        assert position.spec.quantity == 3

    async def test_a_refusal_is_recorded_and_no_position_is_created(
        self, database: Database
    ) -> None:
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
        )
        plan = {1: enter(Direction.LONG, entry="100", stop="97", targets=(("110", 1),))}

        stored, _ = await run_with(
            database,
            rows,
            plan,
            key="golden-m-veto-00001",
            last=2,
            risk=RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("1")),
        )

        assert stored.result is not None
        assert stored.result.positions == ()
        (refusal,) = [
            record
            for record in stored.result.decisions
            if record.outcome is DecisionOutcome.REFUSED_BY_RISK
        ]
        assert refusal.risk_reason


# ----------------------------------------------------------------------
# N. One position at a time
# ----------------------------------------------------------------------


class TestNExposure:
    async def test_a_second_signal_while_exposed_does_not_open_a_second_position(
        self, database: Database
    ) -> None:
        rows = flat_rows(6)
        signal = enter(Direction.LONG, entry="100", stop="97", targets=(("110", 1),))
        plan = {1: signal, 2: signal, 3: signal}

        stored, _ = await run_with(database, rows, plan, key="golden-n-expose-01", last=5)

        assert stored.result is not None
        assert len(stored.result.positions) == 1
        assert [record.outcome for record in stored.result.decisions][2:4] == [
            DecisionOutcome.HOLDING,
            DecisionOutcome.HOLDING,
        ]


# ----------------------------------------------------------------------
# O-P. What the end of the dataset does not manufacture
# ----------------------------------------------------------------------


class TestOPDatasetEnd:
    async def test_a_position_still_open_at_the_end_stays_open(self, database: Database) -> None:
        """No exit is invented from bars that do not exist."""
        rows = flat_rows(4)
        plan = {1: enter(Direction.LONG, entry="100", stop="97", targets=(("110", 1),))}

        stored, _ = await run_with(database, rows, plan, key="golden-o-openend-1", last=3)

        assert stored.result is not None
        (position,) = stored.result.positions
        assert not any(event.type is PaperEventType.POSITION_CLOSED for event in position.events)

    async def test_an_entry_decided_on_the_last_boundary_never_fills(
        self, database: Database
    ) -> None:
        """There is no next bar, so there is no opening price to fill at."""
        rows = flat_rows(3)
        plan = {2: enter(Direction.LONG, entry="100", stop="97", targets=(("110", 1),))}

        stored, _ = await run_with(database, rows, plan, key="golden-p-lastbar-1", last=2)

        assert stored.result is not None
        (position,) = stored.result.positions
        assert fills(position) == []


# ----------------------------------------------------------------------
# Q. An exit intent closes at the next open, not at the signal price
# ----------------------------------------------------------------------


class TestQExitIntent:
    async def test_a_requested_exit_fills_at_the_following_open(self, database: Database) -> None:
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("103", "103", "103", "103"),
        )
        plan = {
            1: enter(Direction.LONG, entry="100", stop="97", targets=(("110", 1),)),
            3: StrategyDecision.exit_now("scripted exit"),
        }

        stored, _ = await run_with(database, rows, plan, key="golden-q-exit-0001", last=4)

        assert stored.result is not None
        (position,) = stored.result.positions
        assert fills(position) == [("ENTRY_FILLED", "100"), ("MANUAL_EXIT_FILLED", "103")]
        assert DecisionOutcome.EXIT_REQUESTED in [
            record.outcome for record in stored.result.decisions
        ]


# ----------------------------------------------------------------------
# R-S. Refusals that stop a run before any money is imagined
# ----------------------------------------------------------------------


class TestRSRefusedRuns:
    async def test_a_run_without_verified_product_metadata_is_refused(
        self, database: Database
    ) -> None:
        dataset = await seed_dataset(database, flat_rows(4), timeframes=M5_ONLY)
        service = runner(database, with_products=False)

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(
                scripted_request(dataset, ScriptedStrategy(), key="golden-r-nometa-01", last=3)
            )

        assert raised.value.code == "PRODUCT_METADATA_UNAVAILABLE"

    async def test_an_empty_window_is_refused_rather_than_reported_as_zero_trades(
        self, database: Database
    ) -> None:
        """Nothing evaluated is not the same answer as nothing found."""
        dataset = await seed_dataset(database, flat_rows(4), timeframes=M5_ONLY)
        service = runner(database)
        request = scripted_request(
            dataset, ScriptedStrategy(), key="golden-s-empty-0001", first=50, last=60
        )

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(request)

        assert raised.value.code == "INTERVAL_EMPTY"


# ----------------------------------------------------------------------
# T. The same question, asked twice
# ----------------------------------------------------------------------


class TestTRepeatability:
    async def test_two_runs_of_one_configuration_agree_completely(self, database: Database) -> None:
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "104", "100", "104"),
            ("104", "108", "104", "107"),
        )
        plan = {1: enter(Direction.LONG, entry="100", stop="97", targets=(("106", 1),))}
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        service = runner(database)

        first = await service.run(
            scripted_request(dataset, ScriptedStrategy(plan=plan), key="golden-t-repeat-01", last=3)
        )
        second = await service.run(
            scripted_request(dataset, ScriptedStrategy(plan=plan), key="golden-t-repeat-02", last=3)
        )

        assert first.run_id != second.run_id
        assert first.configuration == second.configuration
        assert first.result is not None
        assert second.result is not None
        assert first.result.result_digest == second.result.result_digest

    async def test_the_same_attempt_key_returns_the_first_run_untouched(
        self, database: Database
    ) -> None:
        rows = flat_rows(4)
        plan = {1: enter(Direction.LONG, entry="100", stop="97", targets=(("110", 1),))}
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        service = runner(database)
        request = scripted_request(
            dataset, ScriptedStrategy(plan=plan), key="golden-t-idem-0001", last=3
        )

        first = await service.run(request)
        again = await service.run(
            scripted_request(dataset, ScriptedStrategy(plan=plan), key="golden-t-idem-0001", last=3)
        )

        assert again.run_id == first.run_id
        assert again.result is not None
        assert first.result is not None
        assert again.result.result_digest == first.result.result_digest

    async def test_a_changed_account_changes_the_configuration(self, database: Database) -> None:
        """The fingerprint covers what was asked, not only the candles."""
        rows = flat_rows(4)
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        service = runner(database)

        first = await service.run(
            scripted_request(dataset, ScriptedStrategy(), key="golden-t-acct-0001", last=3)
        )
        second = await service.run(
            scripted_request(
                dataset,
                ScriptedStrategy(),
                key="golden-t-acct-0002",
                last=3,
                account=AccountState(equity=Decimal("50000")),
            )
        )

        assert first.configuration != second.configuration


# ----------------------------------------------------------------------
# U. A derived level is made executable, and only ever made worse
# ----------------------------------------------------------------------


class TestUGridAlignment:
    async def test_an_off_grid_stop_is_widened_onto_the_grid(self, database: Database) -> None:
        """97.10 is not a 0.25 tick. The stop becomes 97.00, never 97.25.

        97.25 would be the nearer tick - and would shrink the risk distance the
        position is sized from, which is the rounding that flatters a backtest.
        """
        rows = flat_rows(4)
        plan = {1: enter(Direction.LONG, entry="100", stop="97.10", targets=(("106.10", 1),))}

        stored, _ = await run_with(database, rows, plan, key="golden-u-align-001", last=3)

        assert stored.result is not None
        (position,) = stored.result.positions
        assert position.spec.stop == Decimal("97.00")
        assert position.spec.targets[0].price == Decimal("106.25")

    async def test_the_alignment_is_recorded_in_the_decision_trace(
        self, database: Database
    ) -> None:
        """A level the runner moved is never moved silently."""
        rows = flat_rows(4)
        plan = {1: enter(Direction.LONG, entry="100", stop="97.10", targets=(("106.10", 1),))}

        stored, _ = await run_with(database, rows, plan, key="golden-u-trace-001", last=3)

        assert stored.result is not None
        (entered,) = [
            record
            for record in stored.result.decisions
            if record.outcome is DecisionOutcome.ENTERED
        ]
        assert "stop, target 1 moved away from the entry onto the 0.25 grid" in entered.reason

    async def test_a_short_is_widened_in_the_other_direction(self, database: Database) -> None:
        rows = flat_rows(4)
        plan = {1: enter(Direction.SHORT, entry="100", stop="102.10", targets=(("93.90", 1),))}

        stored, _ = await run_with(database, rows, plan, key="golden-u-short-001", last=3)

        assert stored.result is not None
        (position,) = stored.result.positions
        assert position.spec.stop == Decimal("102.25")
        assert position.spec.targets[0].price == Decimal("93.75")

    async def test_a_run_on_an_unverified_grid_aligns_nothing_and_refuses(
        self, database: Database
    ) -> None:
        """Without a confirmed tick there is no grid to round to.

        The levels are left exactly as the rule computed them, and Phase 3 then
        declines the sizing because it cannot confirm they are placeable. The
        run reports a refusal rather than a trade taken at invented levels -
        which is stricter than aligning would have been, and correct: rounding
        to a grid nobody verified would manufacture the very fact that is
        missing.
        """
        dataset = await seed_dataset(database, flat_rows(4), timeframes=M5_ONLY)
        strategy = ScriptedStrategy(
            plan={1: enter(Direction.LONG, entry="100", stop="97.10", targets=(("106.10", 1),))}
        )
        service = runner(
            database,
            contract=paper_contract(
                tick_size=VerifiedValue(
                    value=Decimal("0.25"),
                    status=VerificationStatus.DEVELOPMENT_DEFAULT,
                    source="test",
                )
            ),
        )

        stored = await service.run(
            scripted_request(dataset, strategy, key="golden-u-unverif-1", last=3)
        )

        assert stored.result is not None
        assert stored.result.positions == ()
        (refused,) = [
            record
            for record in stored.result.decisions
            if record.outcome is DecisionOutcome.REFUSED_BY_RISK
        ]
        assert refused.risk_reason is not None
        assert "tick size" in refused.risk_reason
        assert "moved away from the entry" not in refused.reason
