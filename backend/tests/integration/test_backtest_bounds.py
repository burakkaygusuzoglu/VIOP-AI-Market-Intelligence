"""What one run may consume, and what happens at the edge (Phase 12).

A backtest's cost grows with the range it covers, not with the answer it finds,
so an unbounded request is an unbounded amount of work. Each limit here is
*refused* rather than silently applied: running the first 2,500 boundaries of a
10,000-boundary request and reporting it as the requested range would be a
false statement about what was tested.

Every refusal names the limit and says what to do instead, because "too large"
without a number is not something a person can act on.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.adapters.persistence.database import Database
from app.application.backtest.service import BacktestErrorKind, BacktestServiceError
from app.domain.backtest.policy import StrategyDecision
from app.domain.backtest.run import RunBounds, RunStatus
from app.domain.common.enums import Direction, Timeframe
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


class TestABoundedRunIsRefusedNotTruncated:
    async def test_too_many_boundaries_is_refused_before_any_work_is_done(
        self, database: Database
    ) -> None:
        dataset = await seed_dataset(database, flat_rows(40), timeframes=M5_ONLY)
        strategy = ScriptedStrategy()
        service = runner(database, bounds=RunBounds(max_boundaries=10))

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(
                scripted_request(dataset, strategy, key="bounds-toolong-0001", last=39)
            )

        assert raised.value.kind is BacktestErrorKind.TOO_LARGE
        assert raised.value.code == "RESOURCE_LIMIT"
        assert "at most 10" in raised.value.detail
        assert strategy.seen == []

    async def test_the_refusal_says_what_to_do_instead(self, database: Database) -> None:
        dataset = await seed_dataset(database, flat_rows(40), timeframes=M5_ONLY)
        service = runner(database, bounds=RunBounds(max_boundaries=10))

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(
                scripted_request(dataset, ScriptedStrategy(), key="bounds-advice-0001", last=39)
            )

        assert "Narrow the interval" in raised.value.detail

    async def test_a_run_at_exactly_the_limit_is_allowed(self, database: Database) -> None:
        """The boundary case belongs to the caller, not to the guard."""
        dataset = await seed_dataset(database, flat_rows(20), timeframes=M5_ONLY)
        service = runner(database, bounds=RunBounds(max_boundaries=10))

        stored = await service.run(
            scripted_request(dataset, ScriptedStrategy(), key="bounds-exact-00001", last=9)
        )

        assert stored.status is RunStatus.COMPLETED
        assert stored.result is not None
        assert stored.result.boundaries_evaluated == 10


class TestTooManyPositionsEndsTheRun:
    async def test_a_run_that_would_exceed_its_position_limit_fails(
        self, database: Database
    ) -> None:
        """One position at a time, so each one needs a bar to close on."""
        shapes = []
        for _ in range(6):
            shapes.extend(
                [
                    ("100", "100", "100", "100"),
                    ("100", "100", "96", "97"),
                    ("100", "100", "100", "100"),
                ]
            )
        rows = bars(*shapes)
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        signal = StrategyDecision.enter(
            intent(Direction.LONG, entry="100", stop="97", targets=(("110", 1),)),
            "scripted repeated entry",
        )
        plan = dict.fromkeys(range(0, len(rows), 3), signal)
        service = runner(database, bounds=RunBounds(max_positions=2))

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(
                scripted_request(
                    dataset,
                    ScriptedStrategy(plan=plan),
                    key="bounds-positions-01",
                    last=len(rows) - 1,
                )
            )

        assert raised.value.code == "RESOURCE_LIMIT"
        assert "at most 2" in raised.value.detail

    async def test_that_failure_leaves_no_partial_run_behind(self, database: Database) -> None:
        shapes = []
        for _ in range(6):
            shapes.extend(
                [
                    ("100", "100", "100", "100"),
                    ("100", "100", "96", "97"),
                    ("100", "100", "100", "100"),
                ]
            )
        rows = bars(*shapes)
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        signal = StrategyDecision.enter(
            intent(Direction.LONG, entry="100", stop="97", targets=(("110", 1),)),
            "scripted repeated entry",
        )
        plan = dict.fromkeys(range(0, len(rows), 3), signal)
        service = runner(database, bounds=RunBounds(max_positions=2))

        with pytest.raises(BacktestServiceError):
            await service.run(
                scripted_request(
                    dataset,
                    ScriptedStrategy(plan=plan),
                    key="bounds-nopartial-1",
                    last=len(rows) - 1,
                )
            )

        async with database.engine.connect() as connection:
            positions = await connection.execute(text("SELECT count(*) FROM backtest_positions"))
        assert positions.scalar_one() == 0

        summaries, _ = await service.list(offset=0, limit=10)
        stored = await service.get(summaries[0].run_id)
        assert stored.status is RunStatus.FAILED
        assert stored.failure_code == "RESOURCE_LIMIT"


class TestAStrategyCannotDemandUnboundedHistory:
    async def test_an_excessive_warm_up_requirement_is_refused(self, database: Database) -> None:
        dataset = await seed_dataset(database, flat_rows(20), timeframes=M5_ONLY)
        service = runner(database, bounds=RunBounds(max_warm_up_bars=50))

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(
                scripted_request(
                    dataset, ScriptedStrategy(warm_up=500), key="bounds-warmup-0001", last=19
                )
            )

        assert raised.value.code == "RESOURCE_LIMIT"
        assert "at most 50" in raised.value.detail


class TestTheDefaultsAreStatedNotImplied:
    def test_the_shipped_limits_are_the_documented_ones(self) -> None:
        """A limit nobody can see is a limit nobody can plan around."""
        bounds = RunBounds()

        assert bounds.max_boundaries == 2_500
        assert bounds.max_positions == 200
        assert bounds.max_warm_up_bars == 500

    async def test_a_runner_reports_the_bounds_it_will_apply(self, database: Database) -> None:
        assert runner(database).bounds == RunBounds()
