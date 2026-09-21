"""A run must be able to report a bad outcome (Phase 12).

The throughput measurement in the phase report used a sawtooth fixture and
produced 62 wins and no losses. That is a property of the fixture, not of the
strategy, and a suite that only ever exercised fixtures like it would be unable
to tell the difference. So the outcomes that make a backtest uncomfortable are
tested explicitly here: a loss, a breakeven, an unknown cost, and a win that is
smaller than it looks once a stated fee is charged.

Breakeven is kept separate from a win throughout. Collapsing it into one would
inflate a win rate by exactly the trades that made no money.

No number in this file is evidence about the reference strategy. Simulated
historical results do not guarantee future returns.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import pytest

from app.adapters.persistence.database import Database
from app.application.backtest.ports import StoredRun
from app.application.performance.ports import OutcomeFilters
from app.domain.backtest.policy import StrategyDecision
from app.domain.common.enums import Direction, Timeframe
from app.domain.paper.model import PaperEventType
from app.domain.paper.rules import FeeMode, FeePolicy, SimulationPolicy
from app.domain.performance.model import MetricStatus
from tests.factories_replay import Row
from tests.integration.backtest_support import (
    ScriptedStrategy,
    bars,
    intent,
    performance_for,
    runner,
    scripted_request,
    seed_dataset,
)

pytestmark = pytest.mark.integration

M5_ONLY = (Timeframe.M5,)


def long_entry(*, stop: str, target: str) -> StrategyDecision:
    return StrategyDecision.enter(
        intent(Direction.LONG, entry="100", stop=stop, targets=((target, 1),)),
        "scripted long entry",
    )


async def run_plan(
    database: Database,
    rows: Sequence[Row],
    plan: dict[int, StrategyDecision],
    *,
    key: str,
    simulation: SimulationPolicy | None = None,
) -> StoredRun:
    dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
    return await runner(database).run(
        scripted_request(
            dataset,
            ScriptedStrategy(plan=plan),
            key=key,
            last=len(rows) - 1,
            simulation=simulation,
        )
    )


class TestALossIsReportedAsALoss:
    async def test_a_stopped_trade_counts_against_the_run(self, database: Database) -> None:
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "100", "96", "97"),
            ("97", "97", "97", "97"),
        )

        stored = await run_plan(
            database, rows, {1: long_entry(stop="97", target="130")}, key="honesty-loss-00001"
        )

        assert stored.result is not None
        view = await performance_for(database, stored.run_id).summary(OutcomeFilters())
        assert view.summary.losses == 1
        assert view.summary.wins == 0
        assert view.summary.breakevens == 0
        assert view.summary.realized_gross.value == Decimal("-30")


class TestABreakevenIsNotAWin:
    async def test_a_trade_closed_at_its_entry_is_a_breakeven(self, database: Database) -> None:
        """Entered at 100, exited at 100. Gross is exactly zero.

        Counting this as a win would inflate the win rate by the trades that
        earned nothing, which is the most common way a backtest flatters
        itself without stating a single false number.
        """
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "101", "99", "100"),
            ("100", "101", "99", "100"),
            ("100", "100", "100", "100"),
        )
        plan = {
            1: long_entry(stop="95", target="130"),
            3: StrategyDecision.exit_now("scripted flat exit"),
        }

        stored = await run_plan(database, rows, plan, key="honesty-breakeven-01")

        assert stored.result is not None
        (position,) = stored.result.positions
        (closed,) = [
            event for event in position.events if event.type is PaperEventType.POSITION_CLOSED
        ]
        assert closed.data["realized_gross"] == "0"

        view = await performance_for(database, stored.run_id).summary(OutcomeFilters())
        assert view.summary.breakevens == 1
        assert view.summary.wins == 0
        assert view.summary.losses == 0


class TestAnUnknownCostIsNotAZeroCost:
    async def test_a_run_without_a_stated_fee_reports_no_net_result(
        self, database: Database
    ) -> None:
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "107", "100", "107"),
        )

        stored = await run_plan(
            database, rows, {1: long_entry(stop="97", target="106")}, key="honesty-nofee-0001"
        )

        assert stored.result is not None
        view = await performance_for(database, stored.run_id).summary(OutcomeFilters())
        assert view.summary.realized_gross.value == Decimal("60")
        assert view.summary.realized_net.status is not MetricStatus.AVAILABLE
        assert view.summary.realized_net.value is None
        assert view.summary.realized_net.reason

    async def test_a_stated_fee_makes_the_win_smaller_not_larger(self, database: Database) -> None:
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "107", "100", "107"),
        )

        stored = await run_plan(
            database,
            rows,
            {1: long_entry(stop="97", target="106")},
            key="honesty-fee-0000001",
            simulation=SimulationPolicy(
                fees=FeePolicy(mode=FeeMode.USER_DEFINED_PER_UNIT, per_unit=Decimal("2.50"))
            ),
        )

        assert stored.result is not None
        view = await performance_for(database, stored.run_id).summary(OutcomeFilters())
        assert view.summary.realized_gross.value == Decimal("60")
        assert view.summary.realized_net.value == Decimal("55.00")
        assert view.summary.realized_net.value < view.summary.realized_gross.value


class TestAMixedRunReportsBothSides:
    async def test_wins_losses_and_breakevens_are_counted_separately(
        self, database: Database
    ) -> None:
        """One winner, one loser, in one run. Neither cancels the other out."""
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "107", "100", "104"),
            ("104", "104", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "100", "96", "97"),
            ("97", "97", "97", "97"),
        )
        plan = {
            1: long_entry(stop="97", target="106"),
            4: long_entry(stop="97", target="130"),
        }

        stored = await run_plan(database, rows, plan, key="honesty-mixed-00001")

        assert stored.result is not None
        assert len(stored.result.positions) == 2
        view = await performance_for(database, stored.run_id).summary(OutcomeFilters())
        assert view.summary.wins == 1
        assert view.summary.losses == 1
        assert view.summary.realized_gross.value == Decimal("30")
