"""A run's trades belong to that run and nowhere else (Phase 12).

Two hundred simulated trades from a batch must never be mistaken for trades a
person decided to take. If they leak into the paper population, every figure
Phase 10 reports about that person's judgement becomes meaningless - and it
becomes meaningless quietly, which is worse.

Isolation here is structural, not a filter someone must remember to apply: the
runs live in their own tables, and the performance source is scoped to one run
at construction rather than by an argument.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.adapters.persistence.database import Database
from app.application.backtest.ports import StoredRun
from app.application.backtest.service import BacktestServiceError
from app.application.performance.ports import OutcomeFilters
from app.domain.backtest.policy import StrategyDecision
from app.domain.backtest.run import RunStatus
from app.domain.common.enums import Direction, Timeframe
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
from tests.integration.paper_support import service as paper_service
from tests.integration.performance_support import performance_service

pytestmark = pytest.mark.integration

M5_ONLY = (Timeframe.M5,)

WINNER = bars(
    ("100", "100", "100", "100"),
    ("100", "100", "100", "100"),
    ("100", "104", "100", "104"),
    ("104", "108", "104", "107"),
)
LOSER = bars(
    ("100", "100", "100", "100"),
    ("100", "100", "100", "100"),
    ("100", "100", "96", "97"),
    ("97", "97", "97", "97"),
)


async def one_trade_run(
    database: Database, rows: Sequence[Row], key: str, *, target: str = "106"
) -> StoredRun:
    dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
    strategy = ScriptedStrategy(
        plan={
            1: StrategyDecision.enter(
                intent(Direction.LONG, entry="100", stop="97", targets=((target, 1),)),
                "scripted entry",
            )
        }
    )
    return await runner(database).run(
        scripted_request(dataset, strategy, key=key, last=len(rows) - 1)
    )


class TestOneRunNeverSeesAnother:
    async def test_a_winning_run_and_a_losing_run_report_their_own_outcomes(
        self, database: Database
    ) -> None:
        winner = await one_trade_run(database, WINNER, "isolate-winner-0001")
        loser = await one_trade_run(database, LOSER, "isolate-loser-00001")

        from_winner = await performance_for(database, winner.run_id).summary(OutcomeFilters())
        from_loser = await performance_for(database, loser.run_id).summary(OutcomeFilters())

        assert from_winner.summary.counts.total == 1
        assert from_loser.summary.counts.total == 1
        assert from_winner.summary.wins == 1
        assert from_winner.summary.losses == 0
        assert from_loser.summary.wins == 0
        assert from_loser.summary.losses == 1

    async def test_a_runs_positions_are_only_its_own(self, database: Database) -> None:
        winner = await one_trade_run(database, WINNER, "isolate-pos-a-0001")
        loser = await one_trade_run(database, LOSER, "isolate-pos-b-0001")
        store = runner(database)

        mine = await store.get(winner.run_id)
        theirs = await store.get(loser.run_id)

        assert mine.result is not None
        assert theirs.result is not None
        assert {item.position_id for item in mine.result.positions}.isdisjoint(
            {item.position_id for item in theirs.result.positions}
        )

    async def test_a_second_run_does_not_change_the_first_ones_answer(
        self, database: Database
    ) -> None:
        winner = await one_trade_run(database, WINNER, "isolate-stable-001")
        before = await performance_for(database, winner.run_id).summary(OutcomeFilters())

        await one_trade_run(database, LOSER, "isolate-stable-002")
        after = await performance_for(database, winner.run_id).summary(OutcomeFilters())

        assert before.summary.realized_gross == after.summary.realized_gross
        assert before.summary.counts.total == after.summary.counts.total == 1


class TestARunNeverEntersTheHumanPopulation:
    async def test_the_paper_performance_view_is_empty_after_a_run(
        self, database: Database
    ) -> None:
        """The person took no trades. A batch of simulations does not change that."""
        await one_trade_run(database, WINNER, "isolate-human-0001")

        view = await performance_service(database).summary(OutcomeFilters())

        assert view.summary.counts.total == 0

    async def test_the_paper_position_list_is_empty_after_a_run(self, database: Database) -> None:
        await one_trade_run(database, WINNER, "isolate-list-00001")

        page = await paper_service(database).list(offset=0, limit=50)

        assert page.total == 0

    async def test_a_runs_positions_are_not_in_the_paper_tables_at_all(
        self, database: Database
    ) -> None:
        """Structural, not filtered: they are in different tables."""
        await one_trade_run(database, WINNER, "isolate-tables-001")

        async with database.engine.connect() as connection:
            paper = await connection.execute(text("SELECT count(*) FROM paper_positions"))
            batch = await connection.execute(text("SELECT count(*) FROM backtest_positions"))

        assert paper.scalar_one() == 0
        assert batch.scalar_one() == 1


class TestAFailedRunContributesNothing:
    async def test_a_failed_run_has_no_positions_to_read(self, database: Database) -> None:
        dataset = await seed_dataset(database, WINNER, timeframes=M5_ONLY)
        service = runner(database)
        request = scripted_request(
            dataset, ScriptedStrategy(), key="isolate-failed-0001", first=90, last=99
        )

        with pytest.raises(BacktestServiceError):
            await service.run(request)

        stored = await service.get((await service.list(offset=0, limit=10))[0][0].run_id)
        assert stored.status is RunStatus.FAILED
        assert stored.result is None
        view = await performance_for(database, stored.run_id).summary(OutcomeFilters())
        assert view.summary.counts.total == 0

    async def test_a_failed_run_still_carries_the_reason_it_failed(
        self, database: Database
    ) -> None:
        dataset = await seed_dataset(database, WINNER, timeframes=M5_ONLY)
        service = runner(database)

        with pytest.raises(BacktestServiceError):
            await service.run(
                scripted_request(
                    dataset, ScriptedStrategy(), key="isolate-why-0000001", first=90, last=99
                )
            )

        summaries, _ = await service.list(offset=0, limit=10)
        stored = await service.get(summaries[0].run_id)
        assert stored.failure_code == "INTERVAL_EMPTY"
        assert stored.failure_reason


class TestTheLedgerCannotBeEditedAfterTheFact:
    async def test_an_event_row_cannot_be_updated(self, database: Database) -> None:
        """The same append-only trigger Phase 9 uses, on the run's own table."""
        await one_trade_run(database, WINNER, "isolate-append-001")

        with pytest.raises(DBAPIError):
            async with database.engine.begin() as connection:
                await connection.execute(
                    text("UPDATE backtest_position_events SET event_type = 'TAMPERED'")
                )

    async def test_an_event_row_cannot_be_deleted(self, database: Database) -> None:
        await one_trade_run(database, WINNER, "isolate-nodelete-1")

        with pytest.raises(DBAPIError):
            async with database.engine.begin() as connection:
                await connection.execute(text("DELETE FROM backtest_position_events"))


class TestTheDatasetItselfIsNeverTouched:
    async def test_a_run_writes_no_candles_of_its_own(self, database: Database) -> None:
        """A run names the dataset it read. Copying it would be a second truth."""
        dataset = await seed_dataset(database, WINNER, timeframes=M5_ONLY)
        before = dataset.total_rows

        await runner(database).run(
            scripted_request(dataset, ScriptedStrategy(), key="isolate-nocopy-001", last=3)
        )

        async with database.engine.connect() as connection:
            rows = await connection.execute(text("SELECT count(*) FROM replay_candles"))
        assert rows.scalar_one() == before

    async def test_two_runs_of_the_same_dataset_share_it(self, database: Database) -> None:
        dataset = await seed_dataset(database, WINNER, timeframes=M5_ONLY)
        service = runner(database)

        first = await service.run(
            scripted_request(dataset, ScriptedStrategy(), key="isolate-share-0001", last=3)
        )
        second = await service.run(
            scripted_request(dataset, ScriptedStrategy(), key="isolate-share-0002", last=3)
        )

        assert first.dataset_id == second.dataset_id == dataset.dataset_id
        assert first.run_id != second.run_id


class TestTheRunIsScopedAtConstructionNotByArgument:
    async def test_a_source_built_for_one_run_cannot_report_another(
        self, database: Database
    ) -> None:
        winner = await one_trade_run(database, WINNER, "isolate-scope-0001")
        loser = await one_trade_run(database, LOSER, "isolate-scope-0002")

        both = await performance_for(database, winner.run_id).summary(
            OutcomeFilters(closed_from=None, closed_to=None)
        )

        assert both.summary.counts.total == 1
        assert both.summary.realized_gross.value == Decimal("60")
        assert loser.run_id != winner.run_id
