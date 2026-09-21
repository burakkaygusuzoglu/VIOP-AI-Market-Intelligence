"""One engine, two populations (Phase 12).

A backtest is only worth reading if it simulates trades the way the paper
trader does. Two engines that agree today are two engines that will disagree
after the next change to one of them, so the runner reuses Phase 9 outright -
and these tests prove the reuse by driving the *same* trade through both paths
and comparing the ledgers event by event.

The same argument applies one layer up: the metrics come from the Phase 10
engine, fed this run's outcomes through the Phase 9 fold. So a backtest's
summary and a paper population's summary of identical trades must be identical
numbers, not merely similar ones.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal

import pytest

from app.adapters.persistence.database import Database
from app.application.backtest.ports import StoredRun
from app.application.paper.service import CreatePaperPosition
from app.application.performance.ports import OutcomeFilters
from app.domain.backtest.policy import StrategyDecision
from app.domain.common.enums import Direction, Timeframe
from app.domain.paper.model import PaperEvent, PaperEventType, PositionOrigin, TargetSpec
from app.domain.paper.rules import FeeMode, FeePolicy, SimulationPolicy
from tests.factories_replay import BASE, FIXTURE_SYMBOL, Row, csv_of
from tests.integration.backtest_support import (
    ACCOUNT,
    RISK,
    ScriptedStrategy,
    bars,
    intent,
    performance_for,
    runner,
    scripted_request,
    seed_dataset,
)
from tests.integration.paper_support import service as paper_service
from tests.integration.performance_support import performance_service

pytestmark = pytest.mark.integration

M5_ONLY = (Timeframe.M5,)

SHAPE = bars(
    ("100", "100", "100", "100"),
    ("100", "100", "100", "100"),
    ("100", "104", "100", "104"),
    ("104", "104", "96", "97"),
    ("97", "97", "97", "97"),
)
"""Signal at bar 1, entry at bar 2's open, target then stop. Both paths see it."""

ENTRY_INTENT = intent(
    Direction.LONG,
    entry="100",
    stop="97",
    targets=(("103", 1), ("110", 1)),
    quantity=2,
)

DECISION_TIME = BASE + timedelta(minutes=10)
"""The boundary of bar 1 - which is also bar 2's opening moment."""


def financial(events: Sequence[PaperEvent]) -> list[tuple[str, dict[str, str]]]:
    """Ledger entries that moved money, with their data. Inputs are excluded.

    ``POSITION_CREATED`` carries identifiers and a snapshot that legitimately
    differ between a person's trade and a run's, and ``OBSERVATION_APPLIED``
    is the market being handed in rather than anything derived.
    """
    return [
        (event.type.value, dict(event.data))
        for event in events
        if event.type not in (PaperEventType.POSITION_CREATED, PaperEventType.OBSERVATION_APPLIED)
    ]


async def backtest_run(database: Database, key: str) -> StoredRun:
    dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
    strategy = ScriptedStrategy(
        plan={1: StrategyDecision.enter(ENTRY_INTENT, "scripted parity entry")}
    )
    return await runner(database).run(
        scripted_request(dataset, strategy, key=key, last=len(SHAPE) - 1)
    )


async def paper_twin(database: Database, key: str) -> str:
    """The identical trade, opened and observed through the Phase 9 service."""
    service = paper_service(database)
    view = await service.create(
        CreatePaperPosition(
            idempotency_key=key,
            symbol=FIXTURE_SYMBOL,
            direction=Direction.LONG,
            quantity=ENTRY_INTENT.quantity,
            intended_entry=ENTRY_INTENT.intended_entry,
            stop=ENTRY_INTENT.stop,
            targets=tuple(
                TargetSpec(level.price, level.quantity) for level in ENTRY_INTENT.targets
            ),
            timeframe=Timeframe.M5,
            decision_time=DECISION_TIME,
            account=ACCOUNT,
            risk=RISK,
            policy=SimulationPolicy(),
        )
    )
    position_id = view.stored.position_id
    # Bars 2 and 3 only: bar 3 closes the position, and Phase 9 refuses a bar
    # after that. The runner stops feeding a terminal position for the same
    # reason, so both ledgers end at the same market moment.
    await service.observe(position_id, _csv_from(SHAPE[2:4]), "parity.csv")
    return position_id


def _csv_from(rows: list[Row]) -> str:
    return csv_of(rows)


class TestTheLedgersAreTheSameLedger:
    async def test_a_run_and_a_paper_trade_produce_identical_financial_events(
        self, database: Database
    ) -> None:
        stored = await backtest_run(database, "parity-ledger-00001")
        twin = await paper_twin(database, "parity-paper-000001")

        assert stored.result is not None
        (position,) = stored.result.positions
        page = await paper_service(database).events(twin, after_sequence=0, limit=200)

        assert financial(position.events) == financial([row.event for row in page.items])

    async def test_the_run_version_is_marked_as_a_strategy_simulation(
        self, database: Database
    ) -> None:
        """Identical arithmetic, distinguishable provenance. Both matter."""
        stored = await backtest_run(database, "parity-origin-00001")

        assert stored.result is not None
        (position,) = stored.result.positions
        assert position.spec.origin is PositionOrigin.STRATEGY_BACKTEST

    async def test_the_same_quantity_is_approved_by_the_same_risk_engine(
        self, database: Database
    ) -> None:
        stored = await backtest_run(database, "parity-size-000001")
        twin = await paper_twin(database, "parity-size-paper-1")

        assert stored.result is not None
        (position,) = stored.result.positions
        view = await paper_service(database).get(twin)
        assert position.spec.quantity == view.stored.spec.quantity


class TestTheMetricsAreTheSameMetrics:
    async def test_a_runs_summary_equals_the_paper_summary_of_the_same_trade(
        self, database: Database
    ) -> None:
        stored = await backtest_run(database, "parity-metrics-0001")
        await paper_twin(database, "parity-metrics-pap1")

        from_run = await performance_for(database, stored.run_id).summary(OutcomeFilters())
        from_paper = await performance_service(database).summary(OutcomeFilters())

        assert from_run.summary.counts.closed == from_paper.summary.counts.closed == 1
        assert from_run.summary.realized_gross == from_paper.summary.realized_gross
        assert from_run.summary.wins == from_paper.summary.wins
        assert from_run.summary.losses == from_paper.summary.losses
        assert from_run.summary.expectancy == from_paper.summary.expectancy

    async def test_an_open_position_is_marked_by_the_engine_not_by_the_reader(
        self, database: Database
    ) -> None:
        """The mark of an open position is obtained by replaying its ledger.

        Nothing in the performance source multiplies a price by a quantity, so
        an open exposure cannot be valued by a second, divergent rule.
        """
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("102", "102", "102", "102"),
        )
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        strategy = ScriptedStrategy(
            plan={
                1: StrategyDecision.enter(
                    intent(Direction.LONG, entry="100", stop="97", targets=(("110", 1),)),
                    "scripted open-at-end entry",
                )
            }
        )
        stored = await runner(database).run(
            scripted_request(dataset, strategy, key="parity-openmark-001", last=3)
        )

        view = await performance_for(database, stored.run_id).summary(OutcomeFilters())

        assert view.summary.counts.open_positions == 1
        assert view.summary.counts.closed == 0
        assert view.summary.unrealized_gross_open.value == Decimal("20")


class TestTheRunIsReadThroughPhase10sOwnVocabulary:
    async def test_filters_apply_to_a_runs_population(self, database: Database) -> None:
        stored = await backtest_run(database, "parity-filters-0001")
        service = performance_for(database, stored.run_id)

        longs = await service.summary(OutcomeFilters(direction=Direction.LONG))
        shorts = await service.summary(OutcomeFilters(direction=Direction.SHORT))

        assert longs.summary.counts.total == 1
        assert shorts.summary.counts.total == 0

    async def test_a_run_has_no_journal_and_says_so_as_an_empty_population(
        self, database: Database
    ) -> None:
        """Nobody wrote a note about a simulated trade. That is empty, not broken."""
        stored = await backtest_run(database, "parity-journal-0001")

        page = await performance_for(database, stored.run_id).journal_page(
            OutcomeFilters(), offset=0, limit=50
        )

        assert page.total == 0
        assert page.rows == ()


class TestParityHoldsForTheHarderPopulations:
    """Section 33: the shapes where a second implementation would diverge first."""

    async def test_a_partial_exit_is_one_trade_sample_in_both(self, database: Database) -> None:
        """Two fills, one position, one sample. Not two half-trades."""
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "104", "100", "104"),
            ("104", "104", "96", "97"),
            ("97", "97", "97", "97"),
        )
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        strategy = ScriptedStrategy(
            plan={
                1: StrategyDecision.enter(
                    intent(
                        Direction.LONG,
                        entry="100",
                        stop="97",
                        targets=(("103", 1), ("110", 1)),
                        quantity=2,
                    ),
                    "scripted partial-exit entry",
                )
            }
        )
        stored = await runner(database).run(
            scripted_request(dataset, strategy, key="parity-partial-0001", last=4)
        )

        assert stored.result is not None
        view = await performance_for(database, stored.run_id).summary(OutcomeFilters())
        assert view.summary.counts.total == 1
        assert view.summary.counts.closed == 1
        # Two *exit* fills - the target and the stop. The entry is not a fill
        # in Phase 10's vocabulary, and the position is still one sample.
        assert view.summary.fill_count == 2
        # 3 points on the first unit, -3 on the second: exactly zero, and a
        # breakeven rather than a win.
        assert view.summary.realized_gross.value == Decimal("0")
        assert view.summary.breakevens == 1

    async def test_a_mixed_fee_population_reports_partial_coverage(
        self, database: Database
    ) -> None:
        """One run with a stated fee, one without. Net is not invented for both."""
        priced = SimulationPolicy(
            fees=FeePolicy(mode=FeeMode.USER_DEFINED_PER_UNIT, per_unit=Decimal("2.50"))
        )
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "107", "100", "107"),
        )
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        entry = StrategyDecision.enter(
            intent(Direction.LONG, entry="100", stop="97", targets=(("106", 1),)),
            "scripted entry",
        )

        with_fee = await runner(database).run(
            scripted_request(
                dataset,
                ScriptedStrategy(plan={1: entry}),
                key="parity-mixedfee-001",
                last=2,
                simulation=priced,
            )
        )
        without_fee = await runner(database).run(
            scripted_request(
                dataset, ScriptedStrategy(plan={1: entry}), key="parity-mixedfee-002", last=2
            )
        )

        priced_view = await performance_for(database, with_fee.run_id).summary(OutcomeFilters())
        bare_view = await performance_for(database, without_fee.run_id).summary(OutcomeFilters())

        assert priced_view.summary.realized_gross == bare_view.summary.realized_gross
        assert priced_view.summary.realized_net.value == Decimal("55.00")
        assert bare_view.summary.realized_net.value is None
        assert priced_view.summary.fee_coverage != bare_view.summary.fee_coverage

    async def test_drawdown_is_measured_across_a_runs_own_curve(self, database: Database) -> None:
        """Win +60 then lose -30: the curve peaks at 60 and falls to 30.

        Phase 10 measures the largest peak-to-trough fall of the cumulative
        realized curve, with the peak starting at the first point - so a single
        trade has no drawdown by definition, and two are needed to see one.
        """
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "107", "100", "104"),
            ("104", "104", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "100", "96", "97"),
            ("97", "97", "97", "97"),
        )
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        winner = StrategyDecision.enter(
            intent(Direction.LONG, entry="100", stop="97", targets=(("106", 1),)),
            "scripted winner",
        )
        loser = StrategyDecision.enter(
            intent(Direction.LONG, entry="100", stop="97", targets=(("130", 1),)),
            "scripted loser",
        )
        stored = await runner(database).run(
            scripted_request(
                dataset,
                ScriptedStrategy(plan={1: winner, 4: loser}),
                key="parity-drawdown-001",
                last=6,
            )
        )

        assert stored.result is not None
        view = await performance_for(database, stored.run_id).summary(OutcomeFilters())
        assert view.summary.counts.closed == 2
        assert view.summary.realized_gross.value == Decimal("30")
        assert view.summary.max_drawdown_absolute.value == Decimal("30")

    async def test_one_runs_curve_is_not_extended_by_another_run(self, database: Database) -> None:
        """Section 33's session isolation, asked of two runs at once."""
        winner_rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "107", "100", "107"),
        )
        loser_rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "100", "96", "97"),
        )
        entry = StrategyDecision.enter(
            intent(Direction.LONG, entry="100", stop="97", targets=(("106", 1),)),
            "scripted entry",
        )
        winner = await runner(database).run(
            scripted_request(
                await seed_dataset(database, winner_rows, timeframes=M5_ONLY),
                ScriptedStrategy(plan={1: entry}),
                key="parity-isolated-001",
                last=2,
            )
        )
        loser = await runner(database).run(
            scripted_request(
                await seed_dataset(database, loser_rows, timeframes=M5_ONLY),
                ScriptedStrategy(plan={1: entry}),
                key="parity-isolated-002",
                last=2,
            )
        )

        won = await performance_for(database, winner.run_id).summary(OutcomeFilters())
        lost = await performance_for(database, loser.run_id).summary(OutcomeFilters())

        assert won.summary.realized_gross.value == Decimal("60")
        assert lost.summary.realized_gross.value == Decimal("-30")
        assert won.summary.counts.closed == lost.summary.counts.closed == 1
        # Each curve has one point, so neither run can show the other's fall.
        assert won.summary.max_drawdown_absolute.value == Decimal("0")
        assert lost.summary.max_drawdown_absolute.value == Decimal("0")
