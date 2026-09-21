"""Shared support for Phase 12 backtest tests against real PostgreSQL.

The runner is composed here exactly as an API would compose it, with one
deliberate difference: a **test-only** contract metadata provider. Production
composes none, so a production run is refused for want of verified product
facts - which a separate test proves, with no provider in place.

Datasets come from Phase 11's own store and factories: a backtest reads the
same immutable candles a replay would, by digest.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.adapters.contract_metadata.manual_provider import ManualContractMetadataProvider
from app.adapters.market_data.csv_provider import CsvCandleTextParser
from app.adapters.performance.backtest_source import BacktestRunPerformanceSource
from app.adapters.persistence.backtest_store import SqlAlchemyBacktestStore
from app.adapters.persistence.database import Database
from app.adapters.persistence.journal_store import SqlAlchemyJournalStore
from app.adapters.persistence.replay_store import SqlAlchemyReplayStore
from app.adapters.products.futures import FuturesProductResolver, FuturesSnapshotCodec
from app.application.backtest.service import BacktestRunner, RunRequest
from app.application.performance.service import PerformanceService
from app.application.replay.ports import StoredDataset
from app.domain.backtest.policy import (
    EntryIntent,
    StrategyContext,
    StrategyDecision,
    StrategyPolicy,
    TargetLevel,
)
from app.domain.backtest.registry import SUPPORTED_STRATEGIES
from app.domain.backtest.run import RunBounds, RunInterval
from app.domain.backtest.strategies.ema_crossover import (
    EmaCrossoverSettings,
    EmaCrossoverStrategy,
)
from app.domain.common.enums import Direction, Timeframe
from app.domain.futures.contract import FuturesContract
from app.domain.market.candle import Candle
from app.domain.paper import SimulationPolicy
from app.domain.risk.sizing import AccountState, RiskMode, RiskPolicy
from tests.factories_paper import paper_contract
from tests.factories_replay import BASE, FIXTURE_SYMBOL, Row, aggregate, csv_of, five_minute
from tests.integration.paper_support import FixedClock

WALL = datetime(2026, 6, 1, tzinfo=UTC)
"""Well after every fixture bar. A number that moved with this would be visible."""

ACCOUNT = AccountState(equity=Decimal("100000"))
RISK = RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("5000"))
"""Generous on purpose: these tests are about causality and reuse, so risk
should approve unless a test is specifically about it refusing."""


SCRIPTED_RULES: Mapping[str, frozenset[str]] = {
    **SUPPORTED_STRATEGIES,
    "scripted-test": frozenset({"1.0.0"}),
}
"""The shipped registry plus the scripted policy these tests drive."""


def runner(
    database: Database,
    *,
    with_products: bool = True,
    contract: FuturesContract | None = None,
    bounds: RunBounds | None = None,
    clock: FixedClock | None = None,
    supported: Mapping[str, frozenset[str]] | None = None,
) -> BacktestRunner:
    """The runner as an API would compose it, plus the scripted test rules.

    ``SCRIPTED_RULES`` widens the strategy registry for this process only. It
    is the same shape of test-only composition as the contract metadata
    provider above, and ``test_metadata_trust`` asserts that nothing in
    ``app/`` ever passes a table of its own.
    """

    resolver = (
        FuturesProductResolver(
            ManualContractMetadataProvider([contract if contract is not None else paper_contract()])
        )
        if with_products
        else None
    )
    return BacktestRunner(
        store=SqlAlchemyBacktestStore(database),
        replay=SqlAlchemyReplayStore(database),
        resolver=resolver,
        codec=FuturesSnapshotCodec(),
        clock=clock or FixedClock(WALL),
        bounds=bounds,
        supported=supported if supported is not None else SCRIPTED_RULES,
    )


def performance_for(database: Database, run_id: str) -> PerformanceService:
    """Phase 10's engine, reading one run's own outcomes."""
    return PerformanceService(
        source=BacktestRunPerformanceSource(
            SqlAlchemyBacktestStore(database), FuturesSnapshotCodec(), run_id
        ),
        journal=SqlAlchemyJournalStore(database),
        clock=FixedClock(WALL),
    )


@dataclass
class ScriptedStrategy:
    """A ``StrategyPolicy`` the test writes out bar by bar.

    The reference strategy has its own unit tests; these integration tests are
    about the *runner*, and an EMA crossover is a poor instrument for asking
    "does an entry decided at 10:05 fill at the 10:05 open". So the decision at
    each driver index is stated directly, and the expected fills can then be
    derived by hand from the fixture's own OHLC.

    ``seen`` records the contexts the runner offered. It is inspection, not
    state: :meth:`decide` reads only the script and the context, so the same
    run produces the same decisions whether or not anyone looks.
    """

    plan: dict[int, StrategyDecision] = field(default_factory=dict)
    """Keyed by driver index - ``bars_available - 1`` - not by wall time."""

    warm_up: int = 0
    label: str = "scripted-test"
    seen: list[StrategyContext] = field(default_factory=list)

    @property
    def identifier(self) -> str:
        return self.label

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def warm_up_bars(self) -> int:
        return self.warm_up

    def parameters(self) -> dict[str, str]:
        return {"plan": ",".join(str(index) for index in sorted(self.plan))}

    def decide(self, context: StrategyContext) -> StrategyDecision:
        self.seen.append(context)
        return self.plan.get(
            context.bars_available - 1, StrategyDecision.no_signal("no scripted decision here")
        )


def intent(
    direction: Direction,
    *,
    entry: str,
    stop: str,
    targets: Sequence[tuple[str, int]] = (("999999", 1),),
    quantity: int = 1,
) -> EntryIntent:
    """An entry intent with levels the test states outright.

    The default target is deliberately unreachable, so a scenario that is about
    a stop is not quietly decided by a target nobody meant to set.
    """
    return EntryIntent(
        direction=direction,
        intended_entry=Decimal(entry),
        stop=Decimal(stop),
        targets=tuple(TargetLevel(price=Decimal(price), quantity=size) for price, size in targets),
        quantity=quantity,
    )


def bars(*shapes: tuple[str, str, str, str], start: int = 0) -> list[Row]:
    """Rows from explicit ``(open, high, low, close)`` prices, 5 minutes apart.

    Every fixture price here is written on the 0.25 grid by hand, because an
    off-grid entry is a refusal about the *data*, not about the scenario the
    test means to exercise.
    """
    return [
        Row(
            open_time=BASE + timedelta(minutes=5 * (start + index)),
            open=Decimal(shape[0]),
            high=Decimal(shape[1]),
            low=Decimal(shape[2]),
            close=Decimal(shape[3]),
            volume=Decimal("1000"),
        )
        for index, shape in enumerate(shapes)
    ]


def flat_rows(count: int, *, start: int = 0, price: str = "100") -> list[Row]:
    """Filler bars that touch nothing: no level can be reached inside them."""
    return bars(*(((price, price, price, price),) * count), start=start)


def candles_of(rows: Sequence[Row], timeframe: Timeframe) -> tuple[Candle, ...]:
    """Fixture rows as domain candles, parsed the way an upload would be."""
    parser = CsvCandleTextParser()
    fetch = parser.parse(
        csv_of(rows), symbol=FIXTURE_SYMBOL, timeframe=timeframe, source_name="fixture.csv"
    )
    return tuple(fetch.candles)


async def seed_dataset(
    database: Database,
    rows: Sequence[Row],
    *,
    timeframes: Sequence[Timeframe] = (Timeframe.M5, Timeframe.M15, Timeframe.H1),
    symbol: str = FIXTURE_SYMBOL,
) -> StoredDataset:
    """Store one immutable Phase 11 dataset and return its identity."""
    from app.application.replay.service import _describe

    candles: dict[Timeframe, tuple[Candle, ...]] = {}
    for timeframe in timeframes:
        series = rows if timeframe is Timeframe.M5 else aggregate(rows, timeframe)
        candles[timeframe] = candles_of(series, timeframe)
    store = SqlAlchemyReplayStore(database)
    return await store.save_dataset(_describe(symbol, candles), candles)


def request_for(
    dataset: StoredDataset,
    *,
    key: str,
    start_minutes: int = 5 * 30,
    end_minutes: int = 5 * 200,
    settings: EmaCrossoverSettings | None = None,
    driver: Timeframe = Timeframe.M5,
    simulation: SimulationPolicy | None = None,
    risk: RiskPolicy | None = None,
    account: AccountState | None = None,
) -> RunRequest:
    """One run over the seeded dataset, with the reference strategy."""
    return RunRequest(
        attempt_key=key,
        dataset_id=dataset.dataset_id,
        driver=driver,
        interval=RunInterval(
            start=BASE + timedelta(minutes=start_minutes),
            end=BASE + timedelta(minutes=end_minutes),
        ),
        strategy=EmaCrossoverStrategy(settings or EmaCrossoverSettings()),
        account=account or ACCOUNT,
        risk=risk or RISK,
        simulation=simulation or SimulationPolicy(),
    )


def scripted_request(
    dataset: StoredDataset,
    strategy: StrategyPolicy,
    *,
    key: str,
    first: int = 0,
    last: int | None = None,
    simulation: SimulationPolicy | None = None,
    risk: RiskPolicy | None = None,
    account: AccountState | None = None,
) -> RunRequest:
    """A run over driver bars ``first..last`` inclusive, by index.

    Indices rather than clock times, because every expectation in these tests
    is written against a bar's own OHLC. Bar ``i`` covers ``BASE + 5i`` to
    ``BASE + 5(i+1)``, and its boundary - the moment it became a confirmed
    fact - is the later of the two.
    """
    stop = first if last is None else last
    return RunRequest(
        attempt_key=key,
        dataset_id=dataset.dataset_id,
        driver=Timeframe.M5,
        interval=RunInterval(
            start=BASE + timedelta(minutes=5 * (first + 1)),
            end=BASE + timedelta(minutes=5 * (stop + 1)),
        ),
        strategy=strategy,
        account=account or ACCOUNT,
        risk=risk or RISK,
        simulation=simulation or SimulationPolicy(),
    )


def trending(count: int, *, swing: int = 40) -> list[Row]:
    """A series that actually crosses, so entries happen at all.

    The fixture walk in ``factories_replay`` oscillates tightly and rarely
    produces an EMA crossover with a usable ADX. This alternates long rising and
    falling legs, which is what a crossover rule needs in order to be exercised
    - and, being deterministic arithmetic on ``Decimal``, it is still a fixture
    a golden expectation can be derived from by hand.

    Every price is a multiple of ``0.25``, the fixture contract's tick size.
    Real market prices sit on their own product's grid by construction; a
    fixture that did not would make every entry fail the executability check
    for a reason that says nothing about the strategy.
    """
    rows: list[Row] = []
    close = Decimal("100")
    for index in range(count):
        opened = close
        leg = (index // swing) % 2
        step = Decimal("0.50") if leg == 0 else Decimal("-0.50")
        close = opened + step
        rows.append(
            Row(
                open_time=BASE + timedelta(minutes=5 * index),
                open=opened,
                high=max(opened, close) + Decimal("0.25"),
                low=min(opened, close) - Decimal("0.25"),
                close=close,
                volume=Decimal(1000 + index),
            )
        )
    return rows


def flat(count: int) -> list[Row]:
    """A series with no crossover at all: every candle identical."""
    return [
        Row(
            open_time=BASE + timedelta(minutes=5 * index),
            open=Decimal("100"),
            high=Decimal("100.5"),
            low=Decimal("99.5"),
            close=Decimal("100"),
            volume=Decimal("1000"),
        )
        for index in range(count)
    ]


def ordinary_rows(count: int = 288) -> list[Row]:
    """The Phase 11 fixture walk, unchanged."""
    return five_minute(count)
