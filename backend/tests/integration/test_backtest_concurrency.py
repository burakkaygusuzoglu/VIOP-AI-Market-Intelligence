"""Two things happening at once to one run (Phase 12 Part 2A).

A run is created, computed and published over a measurable stretch of time, and
a person can abandon it while that is happening. The rule is that exactly one
terminal transition wins, and it is the first one to commit:

* a COMPLETED run never becomes FAILED, because its result is real;
* an abandoned run never becomes COMPLETED, because somebody was already told
  it had been abandoned.

Both guards live at the statement that would do the damage - inside the
publishing transaction and inside the failing one - rather than in a check the
caller is trusted to have made first. A guard above the write is a guard two
concurrent callers can both pass.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from app.adapters.persistence.backtest_store import SqlAlchemyBacktestStore
from app.adapters.persistence.database import Database
from app.application.backtest.ports import (
    BacktestResult,
    CompletedRunError,
    StoredRun,
    TerminalRunError,
)
from app.application.backtest.service import BacktestErrorKind, BacktestServiceError
from app.domain.backtest.policy import StrategyDecision
from app.domain.backtest.run import RunStatus
from app.domain.common.enums import Direction, Timeframe
from tests.integration.backtest_support import (
    ScriptedStrategy,
    bars,
    intent,
    runner,
    scripted_request,
    seed_dataset,
)

pytestmark = pytest.mark.integration

M5_ONLY = (Timeframe.M5,)

SHAPE = bars(
    ("100", "100", "100", "100"),
    ("100", "100", "100", "100"),
    ("100", "104", "100", "104"),
    ("104", "108", "104", "107"),
)
PLAN = {
    1: StrategyDecision.enter(
        intent(Direction.LONG, entry="100", stop="97", targets=(("106", 1),)),
        "scripted entry",
    )
}


class AbandonsMidPublication(SqlAlchemyBacktestStore):
    """A store that lets somebody abandon the run just before it publishes.

    This is the race written down. Between the runner deciding it has a result
    and the row being updated, another caller terminalises the run - which is
    exactly the window a slow publication leaves open in production.
    """

    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self.published = 0

    async def publish(self, run_id: str, result: BacktestResult, *, now: datetime) -> StoredRun:
        self.published += 1
        await super().fail(
            run_id, code="INTERRUPTED", reason="abandoned while the run was publishing", now=now
        )
        return await super().publish(run_id, result, now=now)


class TestPublicationRacingAbandon:
    async def test_a_publication_that_loses_the_race_is_refused(self, database: Database) -> None:
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        service = runner(database)
        service._store = AbandonsMidPublication(database)  # noqa: SLF001 - the seam under test

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(
                scripted_request(
                    dataset, ScriptedStrategy(plan=PLAN), key="race-publish-000001", last=3
                )
            )

        assert raised.value.kind is BacktestErrorKind.CONFLICT
        assert raised.value.code == "RUN_ALREADY_TERMINAL"

    async def test_the_abandoned_run_stays_abandoned(self, database: Database) -> None:
        """No result is written, and the status somebody was told stands."""
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        service = runner(database)
        service._store = AbandonsMidPublication(database)  # noqa: SLF001

        with pytest.raises(BacktestServiceError):
            await service.run(
                scripted_request(
                    dataset, ScriptedStrategy(plan=PLAN), key="race-stays-0000001", last=3
                )
            )

        store = SqlAlchemyBacktestStore(database)
        stored = await store.find_by_attempt("race-stays-0000001")
        assert stored is not None
        assert stored.status is RunStatus.FAILED
        assert stored.failure_code == "INTERRUPTED"
        assert stored.result is None

    async def test_a_stale_publication_after_an_abandon_is_refused_by_the_store(
        self, database: Database
    ) -> None:
        """The guard is inside the transaction, not above it."""
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        service = runner(database)
        stored = await service.run(
            scripted_request(dataset, ScriptedStrategy(plan=PLAN), key="race-stale-0000001", last=3)
        )
        assert stored.result is not None
        store = SqlAlchemyBacktestStore(database)

        with pytest.raises(TerminalRunError):
            await store.publish(stored.run_id, stored.result, now=stored.updated_at)

    async def test_a_completed_run_still_refuses_to_be_failed(self, database: Database) -> None:
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        service = runner(database)
        stored = await service.run(
            scripted_request(dataset, ScriptedStrategy(plan=PLAN), key="race-nofail-000001", last=3)
        )

        with pytest.raises(CompletedRunError):
            await SqlAlchemyBacktestStore(database).fail(
                stored.run_id, code="INTERRUPTED", reason="should lose", now=stored.updated_at
            )

        again = await service.get(stored.run_id)
        assert again.status is RunStatus.COMPLETED


class TestConcurrentDuplicateCreation:
    async def test_two_identical_requests_produce_one_run(self, database: Database) -> None:
        """The loser re-reads the winner rather than creating a second run."""
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)

        async def attempt() -> StoredRun:
            return await runner(database).run(
                scripted_request(
                    dataset, ScriptedStrategy(plan=PLAN), key="race-duplicate-0001", last=3
                )
            )

        first, second = await asyncio.gather(attempt(), attempt())

        assert first.run_id == second.run_id
        from sqlalchemy import text

        async with database.engine.connect() as connection:
            runs = await connection.execute(text("SELECT count(*) FROM backtest_runs"))
            positions = await connection.execute(text("SELECT count(*) FROM backtest_positions"))
        assert runs.scalar_one() == 1
        assert positions.scalar_one() == 1

    async def test_three_concurrent_requests_still_produce_one_run(
        self, database: Database
    ) -> None:
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)

        async def attempt() -> StoredRun:
            return await runner(database).run(
                scripted_request(
                    dataset, ScriptedStrategy(plan=PLAN), key="race-triple-000001", last=3
                )
            )

        results = await asyncio.gather(attempt(), attempt(), attempt())

        assert len({item.run_id for item in results}) == 1


class TestRepeatedAbandon:
    async def test_abandoning_repeatedly_is_idempotent(self, database: Database) -> None:
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        service = runner(database)
        service._store = AbandonsMidPublication(database)  # noqa: SLF001
        with pytest.raises(BacktestServiceError):
            await service.run(
                scripted_request(
                    dataset, ScriptedStrategy(plan=PLAN), key="race-repeat-000001", last=3
                )
            )
        clean = runner(database)
        stored = await SqlAlchemyBacktestStore(database).find_by_attempt("race-repeat-000001")
        assert stored is not None

        outcomes = [await clean.abandon(stored.run_id) for _ in range(3)]

        assert {item.status for item in outcomes} == {RunStatus.FAILED}
        assert {item.failure_code for item in outcomes} == {"INTERRUPTED"}
