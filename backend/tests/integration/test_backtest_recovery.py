"""What a run leaves behind when it does not finish (Phase 12).

A backtest is long enough that it will sometimes be interrupted - a dropped
connection, a restarted container, a killed process. The rule these tests hold
the runner to is that an interruption may cost you the answer, but it may never
produce a *wrong* one: there is no state in which a run reports COMPLETED while
holding part of a ledger.

That property comes from where the writes happen, not from care taken at each
step. The whole evaluation is computed in memory and published in one
transaction, so the only two outcomes are "nothing" and "everything". The
database enforces the same thing independently, with a check constraint that
refuses a completed run carrying no result.

Persistence semantics, stated plainly rather than implied:

* a run row is created **before** the walk starts, so an interrupted run is
  visible as PENDING rather than invisible;
* positions, events and decisions are written **only** at publication, in one
  transaction;
* a retry with the same attempt key returns the run that key already produced -
  including a FAILED one. Re-running a failed configuration is a new attempt,
  under a new key. The key identifies the *request*, not the intention.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.adapters.persistence.backtest_store import SqlAlchemyBacktestStore
from app.adapters.persistence.database import Database
from app.adapters.persistence.replay_store import SqlAlchemyReplayStore
from app.adapters.products.futures import FuturesSnapshotCodec
from app.application.backtest.ports import (
    BacktestPosition,
    BacktestResult,
    BacktestStore,
    BacktestStoreUnavailableError,
    CompletedRunError,
    LedgerEvent,
    RunSummary,
    RunTotals,
    StoredRun,
)
from app.application.backtest.service import (
    BacktestErrorKind,
    BacktestRunner,
    BacktestServiceError,
)
from app.application.performance.ports import OutcomeFilters
from app.domain.backtest.policy import StrategyDecision
from app.domain.backtest.run import DecisionRecord, RunStatus
from app.domain.common.enums import Direction, Timeframe
from tests.integration.backtest_support import (
    SCRIPTED_RULES,
    ScriptedStrategy,
    bars,
    intent,
    performance_for,
    runner,
    scripted_request,
    seed_dataset,
)
from tests.integration.paper_support import FixedClock

pytestmark = pytest.mark.integration

M5_ONLY = (Timeframe.M5,)
WALL = datetime.fromisoformat("2026-06-01T00:00:00+00:00")

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


class FailsAtPublication:
    """The real store, except that publication never succeeds.

    A failure exactly here is the interesting one: the run has been fully
    computed and is one statement away from being visible. Anything the runner
    had written earlier would be exposed by it.
    """

    def __init__(self, inner: BacktestStore) -> None:
        self._inner = inner
        self.published = 0

    async def create_run(self, run: StoredRun) -> StoredRun:
        return await self._inner.create_run(run)

    async def find_by_attempt(self, attempt_key: str) -> StoredRun | None:
        return await self._inner.find_by_attempt(attempt_key)

    async def publish(self, run_id: str, result: BacktestResult, *, now: datetime) -> StoredRun:
        self.published += 1
        raise BacktestStoreUnavailableError("the connection dropped during publication")

    async def fail(self, run_id: str, *, code: str, reason: str, now: datetime) -> StoredRun:
        return await self._inner.fail(run_id, code=code, reason=reason, now=now)

    async def get(self, run_id: str) -> StoredRun | None:
        return await self._inner.get(run_id)

    async def list_runs(self, *, offset: int, limit: int) -> tuple[tuple[RunSummary, ...], int]:
        return await self._inner.list_runs(offset=offset, limit=limit)

    async def positions_of(self, run_id: str) -> tuple[BacktestPosition, ...]:
        return await self._inner.positions_of(run_id)

    async def ledger_of(
        self, run_id: str
    ) -> dict[str, Sequence[tuple[str, datetime | None, Mapping[str, Any]]]]:
        return await self._inner.ledger_of(run_id)

    async def head(self, run_id: str) -> tuple[StoredRun, RunTotals] | None:
        return await self._inner.head(run_id)

    async def trace_page(
        self, run_id: str, *, offset: int, limit: int
    ) -> tuple[tuple[DecisionRecord, ...], int]:
        return await self._inner.trace_page(run_id, offset=offset, limit=limit)

    async def events_of(
        self, position_id: str, *, after_sequence: int, limit: int
    ) -> tuple[tuple[LedgerEvent, ...], int]:
        return await self._inner.events_of(position_id, after_sequence=after_sequence, limit=limit)


def interrupted_runner(database: Database) -> tuple[BacktestRunner, FailsAtPublication]:
    store = FailsAtPublication(SqlAlchemyBacktestStore(database))
    service = BacktestRunner(
        store=store,
        replay=SqlAlchemyReplayStore(database),
        resolver=runner(database)._resolver,  # noqa: SLF001 - the same composition
        codec=FuturesSnapshotCodec(),
        clock=FixedClock(WALL),
        supported=SCRIPTED_RULES,
    )
    return service, store


async def counts(database: Database) -> dict[str, int]:
    tables = (
        "backtest_runs",
        "backtest_positions",
        "backtest_position_events",
        "backtest_decisions",
    )
    found: dict[str, int] = {}
    async with database.engine.connect() as connection:
        for table in tables:
            result = await connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
            found[table] = result.scalar_one()
    return found


class TestAnInterruptedPublicationLeavesNoHalfRun:
    async def test_no_position_or_event_survives_a_failed_publication(
        self, database: Database
    ) -> None:
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        service, store = interrupted_runner(database)

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(
                scripted_request(
                    dataset, ScriptedStrategy(plan=PLAN), key="recover-publish-001", last=3
                )
            )

        assert raised.value.kind is BacktestErrorKind.UNAVAILABLE
        assert store.published == 1
        after = await counts(database)
        assert after["backtest_positions"] == 0
        assert after["backtest_position_events"] == 0
        assert after["backtest_decisions"] == 0

    async def test_the_run_remains_visibly_unfinished_rather_than_disappearing(
        self, database: Database
    ) -> None:
        """PENDING is the honest state: the request was accepted, the answer
        was never recorded. A missing row would look like it never happened."""
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        service, _ = interrupted_runner(database)

        with pytest.raises(BacktestServiceError):
            await service.run(
                scripted_request(
                    dataset, ScriptedStrategy(plan=PLAN), key="recover-pending-001", last=3
                )
            )

        stored = await SqlAlchemyBacktestStore(database).find_by_attempt("recover-pending-001")
        assert stored is not None
        assert stored.status is RunStatus.PENDING
        assert stored.result is None

    async def test_an_unfinished_run_reports_no_outcomes(self, database: Database) -> None:
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        service, _ = interrupted_runner(database)

        with pytest.raises(BacktestServiceError):
            await service.run(
                scripted_request(
                    dataset, ScriptedStrategy(plan=PLAN), key="recover-nometrics-1", last=3
                )
            )

        stored = await SqlAlchemyBacktestStore(database).find_by_attempt("recover-nometrics-1")
        assert stored is not None
        view = await performance_for(database, stored.run_id).summary(OutcomeFilters())
        assert view.summary.counts.total == 0


class TestTheDatabaseRefusesAnImpossibleRun:
    async def test_a_completed_run_without_a_result_is_rejected(self, database: Database) -> None:
        """The constraint, not the code, is what makes this impossible."""
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        service, _ = interrupted_runner(database)
        with pytest.raises(BacktestServiceError):
            await service.run(
                scripted_request(
                    dataset, ScriptedStrategy(plan=PLAN), key="recover-constraint-1", last=3
                )
            )

        with pytest.raises(IntegrityError):
            async with database.engine.begin() as connection:
                await connection.execute(
                    text("UPDATE backtest_runs SET status = 'COMPLETED' WHERE status = 'PENDING'")
                )

    async def test_a_failed_run_must_state_a_code(self, database: Database) -> None:
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        service, _ = interrupted_runner(database)
        with pytest.raises(BacktestServiceError):
            await service.run(
                scripted_request(
                    dataset, ScriptedStrategy(plan=PLAN), key="recover-failcode-1", last=3
                )
            )

        with pytest.raises(IntegrityError):
            async with database.engine.begin() as connection:
                await connection.execute(
                    text("UPDATE backtest_runs SET status = 'FAILED' WHERE status = 'PENDING'")
                )


class TestRetryingIsExplicitAboutWhatItReturns:
    async def test_the_same_key_after_an_interruption_returns_the_unfinished_run(
        self, database: Database
    ) -> None:
        """Not a silent re-run. The key identifies the request that was made.

        A caller wanting the work done again asks with a new key, which is a
        decision somebody makes rather than a retry loop makes for them.
        """
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        broken, _ = interrupted_runner(database)
        with pytest.raises(BacktestServiceError):
            await broken.run(
                scripted_request(
                    dataset, ScriptedStrategy(plan=PLAN), key="recover-retry-0001", last=3
                )
            )

        again = await runner(database).run(
            scripted_request(dataset, ScriptedStrategy(plan=PLAN), key="recover-retry-0001", last=3)
        )

        assert again.status is RunStatus.PENDING
        assert again.result is None

    async def test_a_new_key_over_the_same_configuration_completes_normally(
        self, database: Database
    ) -> None:
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        broken, _ = interrupted_runner(database)
        with pytest.raises(BacktestServiceError):
            await broken.run(
                scripted_request(
                    dataset, ScriptedStrategy(plan=PLAN), key="recover-fresh-0001", last=3
                )
            )

        fresh = await runner(database).run(
            scripted_request(dataset, ScriptedStrategy(plan=PLAN), key="recover-fresh-0002", last=3)
        )

        assert fresh.status is RunStatus.COMPLETED
        assert fresh.result is not None
        assert len(fresh.result.positions) == 1

    async def test_a_completed_run_is_never_recomputed_by_a_repeat(
        self, database: Database
    ) -> None:
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        service = runner(database)
        first = await service.run(
            scripted_request(dataset, ScriptedStrategy(plan=PLAN), key="recover-once-00001", last=3)
        )

        watcher = ScriptedStrategy(plan=PLAN)
        again = await service.run(
            scripted_request(dataset, watcher, key="recover-once-00001", last=3)
        )

        assert again.run_id == first.run_id
        assert watcher.seen == []


class TestTerminalisingAnInterruptedRun:
    """The honest recovery path: abandon it, then ask again under a new key.

    There is no automatic resumption and no job scheduler. The walk's
    intermediate state is never written anywhere, so there is nothing to resume
    from - and a resumption point invented after the fact would publish a
    result computed partly before the interruption and partly after it.
    """

    async def test_a_pending_run_can_be_terminalised_explicitly(self, database: Database) -> None:
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        broken, _ = interrupted_runner(database)
        with pytest.raises(BacktestServiceError):
            await broken.run(
                scripted_request(
                    dataset, ScriptedStrategy(plan=PLAN), key="recover-abandon-001", last=3
                )
            )
        service = runner(database)
        stuck = await SqlAlchemyBacktestStore(database).find_by_attempt("recover-abandon-001")
        assert stuck is not None
        assert stuck.status is RunStatus.PENDING

        abandoned = await service.abandon(stuck.run_id)

        assert abandoned.status is RunStatus.FAILED
        assert abandoned.failure_code == "INTERRUPTED"
        assert abandoned.result is None

    async def test_an_abandoned_run_never_looks_like_a_result(self, database: Database) -> None:
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        broken, _ = interrupted_runner(database)
        with pytest.raises(BacktestServiceError):
            await broken.run(
                scripted_request(
                    dataset, ScriptedStrategy(plan=PLAN), key="recover-noresult-01", last=3
                )
            )
        service = runner(database)
        stuck = await SqlAlchemyBacktestStore(database).find_by_attempt("recover-noresult-01")
        assert stuck is not None

        abandoned = await service.abandon(stuck.run_id)

        assert abandoned.status is not RunStatus.COMPLETED
        assert abandoned.failure_reason is not None
        assert "no partial result was kept" in abandoned.failure_reason
        view = await performance_for(database, abandoned.run_id).summary(OutcomeFilters())
        assert view.summary.counts.total == 0

    async def test_the_new_attempt_runs_normally_after_terminalisation(
        self, database: Database
    ) -> None:
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        broken, _ = interrupted_runner(database)
        with pytest.raises(BacktestServiceError):
            await broken.run(
                scripted_request(
                    dataset, ScriptedStrategy(plan=PLAN), key="recover-newkey-0001", last=3
                )
            )
        service = runner(database)
        stuck = await SqlAlchemyBacktestStore(database).find_by_attempt("recover-newkey-0001")
        assert stuck is not None
        await service.abandon(stuck.run_id)

        fresh = await service.run(
            scripted_request(
                dataset, ScriptedStrategy(plan=PLAN), key="recover-newkey-0002", last=3
            )
        )

        assert fresh.status is RunStatus.COMPLETED
        assert fresh.result is not None
        assert len(fresh.result.positions) == 1

    async def test_terminalising_twice_is_harmless(self, database: Database) -> None:
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        broken, _ = interrupted_runner(database)
        with pytest.raises(BacktestServiceError):
            await broken.run(
                scripted_request(
                    dataset, ScriptedStrategy(plan=PLAN), key="recover-twice-0001", last=3
                )
            )
        service = runner(database)
        stuck = await SqlAlchemyBacktestStore(database).find_by_attempt("recover-twice-0001")
        assert stuck is not None
        once = await service.abandon(stuck.run_id)

        twice = await service.abandon(stuck.run_id)

        assert twice.status is RunStatus.FAILED
        assert twice.failure_code == once.failure_code

    async def test_a_completed_run_is_never_abandoned(self, database: Database) -> None:
        """Its result is real. Re-labelling it would throw the answer away."""
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        service = runner(database)
        finished = await service.run(
            scripted_request(
                dataset, ScriptedStrategy(plan=PLAN), key="recover-finished-01", last=3
            )
        )

        with pytest.raises(BacktestServiceError) as raised:
            await service.abandon(finished.run_id)

        assert raised.value.code == "RUN_ALREADY_COMPLETED"
        still = await service.get(finished.run_id)
        assert still.status is RunStatus.COMPLETED
        assert still.result is not None

    async def test_the_store_itself_refuses_to_fail_a_completed_run(
        self, database: Database
    ) -> None:
        """A guard at the statement that would actually destroy the result."""
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        service = runner(database)
        finished = await service.run(
            scripted_request(
                dataset, ScriptedStrategy(plan=PLAN), key="recover-guarded-001", last=3
            )
        )

        with pytest.raises(CompletedRunError):
            await SqlAlchemyBacktestStore(database).fail(
                finished.run_id, code="INTERRUPTED", reason="should not be possible", now=WALL
            )

    async def test_abandoning_an_unknown_run_is_a_not_found(self, database: Database) -> None:
        with pytest.raises(BacktestServiceError) as raised:
            await runner(database).abandon("BR-does-not-exist-0000000")

        assert raised.value.code == "RUN_NOT_FOUND"
