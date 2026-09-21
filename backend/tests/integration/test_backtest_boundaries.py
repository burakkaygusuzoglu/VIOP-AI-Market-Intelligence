"""The edges the specification names explicitly (Phase 12 Part 1).

Four unrelated edges live here because each is a single sharp question rather
than a theme:

* **Failure at every stage of a run.** The persistence model is bounded
  computation with atomic final publication, so the interesting claim is that
  a failure *anywhere before* publication leaves exactly the same thing behind:
  a terminalised row and no financial record at all. That is tested at four
  stages, not asserted.
* **The coverage boundary to the microsecond.** ``coverage_end <= as_of`` is
  an inclusive comparison. One microsecond either side of it must change what
  is visible, or the comparison is not the one the system claims to make.
* **Dataset identity under identical symbols.** Two datasets for the same
  instrument whose candles differ are different datasets, and a run belongs to
  one of them by digest - never by symbol string.
* **The phase boundary.** The things Phase 12 Part 1 must *not* contain are
  checked mechanically, because "we did not build an optimiser" is otherwise a
  claim that decays silently.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from app.adapters.persistence.backtest_store import SqlAlchemyBacktestStore
from app.adapters.persistence.database import Database
from app.application.backtest.ports import BacktestStoreUnavailableError, StoredRun
from app.application.backtest.service import BacktestServiceError
from app.domain.backtest.policy import StrategyContext, StrategyDecision
from app.domain.backtest.run import RunInterval, RunStatus
from app.domain.common.enums import Direction, Timeframe
from app.domain.replay import coverage_end
from tests.factories_replay import BASE
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
APP_ROOT = Path(__file__).resolve().parents[2] / "app"

SHAPE = bars(
    ("100", "100", "100", "100"),
    ("100", "100", "100", "100"),
    ("100", "104", "100", "104"),
    ("104", "108", "104", "107"),
    ("107", "107", "107", "107"),
    ("107", "107", "107", "107"),
)
ENTRY = StrategyDecision.enter(
    intent(Direction.LONG, entry="100", stop="97", targets=(("106", 1),)),
    "scripted entry",
)


class ExplodesAt(ScriptedStrategy):
    """A strategy that fails at a chosen point in the walk.

    Subclassed rather than mocked so the runner sees an ordinary policy that
    happens to raise, which is what a defective strategy actually looks like.
    """

    def __init__(self, *, at: int, plan: dict[int, StrategyDecision] | None = None) -> None:
        super().__init__(plan=plan or {})
        self._at = at

    def decide(self, context: StrategyContext) -> StrategyDecision:
        decision = super().decide(context)
        if context.bars_available - 1 == self._at:
            raise RuntimeError(f"strategy failed at index {self._at}")
        return decision


async def table_counts(database: Database) -> dict[str, int]:
    found: dict[str, int] = {}
    async with database.engine.connect() as connection:
        for table in (
            "backtest_runs",
            "backtest_positions",
            "backtest_position_events",
            "backtest_decisions",
        ):
            result = await connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
            found[table] = result.scalar_one()
    return found


class TestAFailureAtAnyStageLeavesTheSameThingBehind:
    """Section 26: bounded computation with atomic final publication."""

    @pytest.mark.parametrize(
        ("label", "at"),
        [
            ("at the very beginning", 0),
            ("after an entry intent", 2),
            ("after a fill", 3),
            ("near the end", 5),
        ],
    )
    async def test_nothing_financial_is_persisted(
        self, database: Database, label: str, at: int
    ) -> None:
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        strategy = ExplodesAt(at=at, plan={1: ENTRY})

        with pytest.raises(BacktestServiceError, match="INTERNAL_ERROR"):
            await runner(database).run(
                scripted_request(
                    dataset, strategy, key=f"boundary-fail-{at:04d}-001", last=len(SHAPE) - 1
                )
            )

        counts = await table_counts(database)
        assert counts["backtest_positions"] == 0
        assert counts["backtest_position_events"] == 0
        assert counts["backtest_decisions"] == 0

    @pytest.mark.parametrize("at", [0, 2, 3, 5])
    async def test_the_run_never_reads_as_completed(self, database: Database, at: int) -> None:
        """Terminalised as FAILED, and never with a result.

        Part 1 left such a run PENDING, which was safe but unhelpful: the
        caller got a bare 500 and a row that read as work still in progress.
        Part 2B terminalises it with a sanitized internal code instead. What
        has not changed - and is what this test is really for - is that no
        status other than FAILED is reachable, and no result is attached.
        """
        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        key = f"boundary-status-{at:04d}-1"

        with pytest.raises(BacktestServiceError, match="INTERNAL_ERROR"):
            await runner(database).run(
                scripted_request(
                    dataset,
                    ExplodesAt(at=at, plan={1: ENTRY}),
                    key=key,
                    last=len(SHAPE) - 1,
                )
            )

        stored = await SqlAlchemyBacktestStore(database).find_by_attempt(key)
        assert stored is not None
        assert stored.status is RunStatus.FAILED
        assert stored.failure_code == "INTERNAL_ERROR"
        assert stored.result is None

    async def test_a_failure_before_the_run_row_exists_leaves_no_row(
        self, database: Database
    ) -> None:
        """The earliest stage of all: the store itself is unreachable."""

        class RefusesToCreate(SqlAlchemyBacktestStore):
            async def create_run(self, run: StoredRun) -> StoredRun:
                raise BacktestStoreUnavailableError("the database went away")

        dataset = await seed_dataset(database, SHAPE, timeframes=M5_ONLY)
        service = runner(database)
        service._store = RefusesToCreate(database)  # noqa: SLF001 - injecting the seam

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(
                scripted_request(
                    dataset, ScriptedStrategy(plan={1: ENTRY}), key="boundary-nostore-01", last=5
                )
            )

        assert raised.value.code == "BACKTEST_STORE_UNAVAILABLE"
        assert (await table_counts(database))["backtest_runs"] == 0


class TestTheCoverageBoundaryIsExact:
    """Section 31: ``coverage_end <= as_of``, to the microsecond."""

    async def test_a_candle_is_evaluated_at_exactly_its_coverage_end(
        self, database: Database
    ) -> None:
        dataset = await seed_dataset(database, flat_rows(6), timeframes=M5_ONLY)
        strategy = ScriptedStrategy()
        boundary = BASE + timedelta(minutes=10)
        request = replace(
            scripted_request(dataset, strategy, key="boundary-exact-0001", last=5),
            # Starts exactly at one candle's coverage end and is a hair wide,
            # so that boundary and no other can fall inside it.
            interval=RunInterval(start=boundary, end=boundary + timedelta(microseconds=1)),
        )

        await runner(database).run(request)

        assert [context.as_of for context in strategy.seen] == [boundary]
        assert coverage_end(strategy.seen[0].bar) == boundary

    async def test_one_microsecond_before_the_boundary_excludes_the_candle(
        self, database: Database
    ) -> None:
        """The window end is inclusive, so a hair earlier drops the bar."""
        dataset = await seed_dataset(database, flat_rows(6), timeframes=M5_ONLY)
        service = runner(database)
        request = scripted_request(dataset, ScriptedStrategy(), key="boundary-micro-0001", last=1)
        narrowed = replace(
            request,
            interval=RunInterval(
                start=request.interval.start,
                end=request.interval.end - timedelta(microseconds=1),
            ),
        )

        stored = await service.run(narrowed)

        assert stored.result is not None
        assert stored.result.boundaries_evaluated == 1
        assert stored.result.last_boundary == BASE + timedelta(minutes=5)

    async def test_one_microsecond_after_the_boundary_includes_it(self, database: Database) -> None:
        dataset = await seed_dataset(database, flat_rows(6), timeframes=M5_ONLY)
        service = runner(database)
        request = scripted_request(dataset, ScriptedStrategy(), key="boundary-micro-0002", last=1)
        widened = replace(
            request,
            interval=RunInterval(
                start=request.interval.start,
                end=request.interval.end + timedelta(microseconds=1),
            ),
        )

        stored = await service.run(widened)

        assert stored.result is not None
        assert stored.result.boundaries_evaluated == 2
        assert stored.result.last_boundary == BASE + timedelta(minutes=10)


class TestDatasetIdentityIsNotASymbolString:
    """Section 34: a run belongs to a dataset by digest."""

    async def test_the_same_symbol_with_one_changed_candle_is_a_different_dataset(
        self, database: Database
    ) -> None:
        original = await seed_dataset(database, flat_rows(6), timeframes=M5_ONLY)
        altered_rows = [*flat_rows(5), *bars(("101", "101", "101", "101"), start=5)]
        altered = await seed_dataset(database, altered_rows, timeframes=M5_ONLY)

        assert original.symbol == altered.symbol
        assert original.dataset_id != altered.dataset_id

    async def test_two_runs_over_those_datasets_do_not_share_a_configuration(
        self, database: Database
    ) -> None:
        original = await seed_dataset(database, flat_rows(6), timeframes=M5_ONLY)
        altered_rows = [*flat_rows(5), *bars(("101", "101", "101", "101"), start=5)]
        altered = await seed_dataset(database, altered_rows, timeframes=M5_ONLY)
        service = runner(database)

        first = await service.run(
            scripted_request(original, ScriptedStrategy(), key="boundary-ident-0001", last=5)
        )
        second = await service.run(
            scripted_request(altered, ScriptedStrategy(), key="boundary-ident-0002", last=5)
        )

        assert first.configuration != second.configuration
        assert first.dataset_id != second.dataset_id

    async def test_a_run_records_the_digest_it_actually_read(self, database: Database) -> None:
        dataset = await seed_dataset(database, flat_rows(6), timeframes=M5_ONLY)

        stored = await runner(database).run(
            scripted_request(dataset, ScriptedStrategy(), key="boundary-digest-001", last=5)
        )

        assert stored.dataset_id == dataset.dataset_id
        assert stored.dataset_id.startswith("RD-")


class TestThePhaseBoundaryHoldsMechanically:
    """Section 39: what Part 1 must not contain, checked rather than claimed."""

    @pytest.mark.parametrize(
        "concept",
        [
            "grid_search",
            "gridsearch",
            "walk_forward",
            "walkforward",
            "monte_carlo",
            "montecarlo",
            "optimis",
            "optimiz",
            "strategy_ranking",
            "leaderboard",
        ],
    )
    def test_no_optimisation_concept_exists_in_the_backtest_code(self, concept: str) -> None:
        offenders = [
            path.relative_to(APP_ROOT).as_posix()
            for path in (APP_ROOT / "domain" / "backtest").rglob("*.py")
            if concept in path.read_text(encoding="utf-8").lower()
        ] + [
            path.relative_to(APP_ROOT).as_posix()
            for path in (APP_ROOT / "application" / "backtest").rglob("*.py")
            if concept in path.read_text(encoding="utf-8").lower()
        ]
        assert offenders == []

    @pytest.mark.parametrize(
        "capability",
        [
            "def place_order",
            "def submit_order",
            "def send_order",
            "async def place_order",
            "import midas",
            "class BrokerAdapter",
            "class OrderExecution",
            "BrokerPort",
            "OrderPort",
            "shadow_mode",
        ],
    )
    def test_no_execution_capability_exists_anywhere_in_the_app(self, capability: str) -> None:
        """Names, not prose.

        Several modules mention Midas and brokers precisely in order to forbid
        them - `config.py` says real-money execution is disabled and broker
        credentials must never be stored. Banning the *word* would delete the
        documentation of the prohibition, so what is banned here is the shape
        an actual capability would take: a function that places an order, a
        module that imports a broker, a port that abstracts one.
        """
        offenders = [
            path.relative_to(APP_ROOT).as_posix()
            for path in APP_ROOT.rglob("*.py")
            if capability in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_the_prohibition_is_still_written_down(self) -> None:
        """The counterpart: deleting the warning must not pass as progress."""
        config = (APP_ROOT / "core" / "config.py").read_text(encoding="utf-8").lower()

        assert "no broker configuration" in config
        assert "execution is" in config

    def test_the_http_surface_exists_and_is_the_only_one(self) -> None:
        """Part 2A added the API. It is one router and one schema module.

        This test moved with the phase boundary rather than being deleted: what
        it guards now is that the surface did not sprawl. A second backtest
        router, or a schema module that grew its own financial vocabulary,
        would show up here.
        """
        assert (APP_ROOT / "api" / "routes" / "backtest.py").exists()
        assert (APP_ROOT / "api" / "schemas" / "backtest.py").exists()

        routers = sorted(
            path.name
            for path in (APP_ROOT / "api" / "routes").glob("*.py")
            if "backtest" in path.name
        )
        assert routers == ["backtest.py"]

    def test_the_composition_root_wires_the_runner_through_state_only(self) -> None:
        """The store and the performance factory, and no strategy of its own."""
        main = (APP_ROOT / "main.py").read_text(encoding="utf-8")

        assert "application.state.backtest_store" in main
        assert "application.state.backtest_performance" in main
        # The runner is built per request from app state, never held as a
        # long-lived object carrying a resolver somebody could swap later.
        assert "BacktestRunner(" not in main

    def test_the_api_computes_no_financial_quantity(self) -> None:
        """A route that did arithmetic would be a second engine with a URL."""
        for name in ("routes/backtest.py", "schemas/backtest_projection.py"):
            body = (APP_ROOT / "api" / name).read_text(encoding="utf-8")
            for banned in ("sum(", "/ len(", "* Decimal", "+ Decimal", "- Decimal"):
                assert banned not in body, f"{name} contains {banned}"

    def test_no_llm_dependency_reaches_the_backtest_domain(self) -> None:
        for path in (APP_ROOT / "domain" / "backtest").rglob("*.py"):
            text_of = path.read_text(encoding="utf-8").lower()
            for banned in ("anthropic", "claude", "openai", "httpx", "tavily"):
                assert banned not in text_of, f"{path.name} mentions {banned}"

    def test_the_runner_holds_no_randomness_or_wall_clock_shortcut(self) -> None:
        for folder in ("domain/backtest", "application/backtest"):
            for path in (APP_ROOT / folder).rglob("*.py"):
                body = path.read_text(encoding="utf-8")
                assert "import random" not in body
                assert "datetime.now()" not in body
                assert "utcnow()" not in body


class TestTheReferenceStrategyIsTheOnlyOne:
    def test_exactly_one_strategy_module_ships(self) -> None:
        modules = sorted(
            path.name
            for path in (APP_ROOT / "domain" / "backtest" / "strategies").glob("*.py")
            if path.name != "__init__.py"
        )

        assert modules == ["ema_crossover.py"]

    def test_no_strategy_is_constructed_from_a_string(self) -> None:
        """Section 35: no eval, no dynamic import from a name."""
        for folder in ("domain/backtest", "application/backtest"):
            for path in (APP_ROOT / folder).rglob("*.py"):
                body = path.read_text(encoding="utf-8")
                for banned in ("eval(", "exec(", "importlib", "__import__", "getattr(globals"):
                    assert banned not in body, f"{path.name} contains {banned}"


class TestNothingClaimsProfitability:
    @pytest.mark.parametrize(
        "claim", ["guaranteed profit", "risk-free", "risk free", "certain profit", "will profit"]
    )
    def test_the_backtest_code_makes_no_promise(self, claim: str) -> None:
        offenders = [
            path.name
            for folder in ("domain/backtest", "application/backtest")
            for path in (APP_ROOT / folder).rglob("*.py")
            if claim in path.read_text(encoding="utf-8").lower()
        ]
        assert offenders == []

    def test_the_reference_strategy_says_what_it_is_not(self) -> None:
        body = (
            (APP_ROOT / "domain" / "backtest" / "strategies" / "ema_crossover.py")
            .read_text(encoding="utf-8")
            .lower()
        )

        assert "not a recommendation" in body or "not a claim" in body
