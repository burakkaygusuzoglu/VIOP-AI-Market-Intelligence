"""The backtest runner: an orchestrator with no arithmetic of its own.

Every number a run produces was computed by an engine that already existed:

* **market time** - Phase 11's ``coverage_end <= as_of`` rule, over Phase 11's
  immutable dataset, read by its digest;
* **indicators** - Phase 1's ``compute_technicals``, unchanged;
* **risk** - Phase 3's ``size_for_product``, which may refuse;
* **fills and P&L** - Phase 9's ``open_position`` and ``apply_observation``;
* **metrics** - Phase 10's engine, fed the run's own outcomes.

The runner decides only *when* to ask each of them, and in what order.

## The causal order, which is the whole point

At each boundary ``T``:

1. reveal the driver candle whose coverage ended at ``T``;
2. give it to the positions already open, through the Phase 9 engine;
3. let those fills and transitions complete;
4. read the confirmed indicator values at ``T``;
5. ask the strategy, which sees scalars at ``T`` and nothing later;
6. align an entry intent's derived levels onto the product's price grid, away
   from the entry - the strategy may not know a verified product fact, and a
   level computed from an ATR reading is not yet a placeable price;
7. run risk approval on that intent - server side, and it may refuse;
8. create the simulated position with ``decision_time = T``.

Step 8 is why a signal cannot enter on the candle that produced it: Phase 9
fills an entry on the first bar opening *at or after* the decision time, and the
bar that closed at ``T`` opened before it. The next bar opens exactly at ``T``,
so the entry is a genuine next-bar entry - enforced by the paper engine, not by
a rule this module remembers to apply.

## Indicators are computed once, and that is proven rather than assumed

Phase 1 documents that value ``i`` derives from candles ``0..i`` only, so
computing over the whole driver series and indexing by boundary is identical to
recomputing over each prefix - and turns an O(n2) walk into one pass.
``tests/unit/backtest/test_indicator_causality.py`` proves the equality across
every indicator this strategy reads; if an indicator ever stopped being causal,
that test fails rather than this runner silently leaking the future.

What keeps the strategy honest is not that property but the
:class:`StrategyContext`: it carries scalars at the current index, never the
series, so there is no index for a policy to read past.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique

from app.application.backtest.ports import (
    BacktestPosition,
    BacktestResult,
    BacktestStore,
    BacktestStoreUnavailableError,
    CompletedRunError,
    DuplicateRunError,
    LedgerEvent,
    RunSummary,
    RunTotals,
    StoredRun,
    TerminalRunError,
)
from app.application.ports.paper import ProductResolver, ProductSnapshotCodec
from app.application.ports.system import ClockPort
from app.application.replay.ports import (
    ReplayStore,
    ReplayStoreUnavailableError,
    StoredDataset,
)
from app.domain.backtest.fingerprint import (
    canonical_decimal,
    canonical_time,
    configuration_fingerprint,
    result_digest,
)
from app.domain.backtest.fingerprint import (
    run_id as derive_run_id,
)
from app.domain.backtest.levels import Alignment, align_intent, on_grid
from app.domain.backtest.policy import (
    DecisionKind,
    EntryIntent,
    Readings,
    StrategyContext,
    StrategyDecision,
    StrategyPolicy,
)
from app.domain.backtest.registry import SUPPORTED_STRATEGIES, require_supported_rules
from app.domain.backtest.run import (
    RUNNER_RULES_VERSION,
    BacktestError,
    DecisionOutcome,
    DecisionRecord,
    RunBounds,
    RunInterval,
    RunStatus,
)
from app.domain.common.enums import Timeframe
from app.domain.instrument import ProductPolicy
from app.domain.market.candle import Candle
from app.domain.market.quality import DataQualityEngine
from app.domain.market.series import CandleSeries
from app.domain.paper import SimulationPolicy
from app.domain.paper.engine import (
    PaperRefusalError,
    apply_observation,
    open_position,
    request_close,
)
from app.domain.paper.model import (
    PaperPosition,
    PositionOrigin,
    PositionSpec,
    RiskApproval,
    TargetSpec,
)
from app.domain.replay import coverage_end
from app.domain.risk.sizing import AccountState, RiskPolicy, SizingOutcome, size_for_product
from app.domain.technical.engine import TechnicalSnapshot, compute_technicals

_LOG = logging.getLogger(__name__)

INTERNAL_FAILURE_CODE = "INTERNAL_ERROR"
INTERNAL_FAILURE_REASON = (
    "this run stopped on an internal error in the backtesting software; no partial result "
    "was kept, and nothing about the request needs changing. Re-running the same "
    "configuration requires a new attempt key"
)
"""A fixed sentence. The exception's own text is never used: it may name a
module, a file path or a connection string, and none of that is a client's
business."""

SUPPORTED_TIMEFRAMES: tuple[Timeframe, ...] = (
    Timeframe.D1,
    Timeframe.H1,
    Timeframe.M15,
    Timeframe.M5,
)


@unique
class BacktestErrorKind(StrEnum):
    INVALID = "INVALID"
    REFUSED = "REFUSED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    TOO_LARGE = "TOO_LARGE"
    UNAVAILABLE = "UNAVAILABLE"
    INTERNAL = "INTERNAL"
    """A defect in this software, not a fault in the request.

    Kept distinct from every other kind on purpose. Reporting a programming
    bug as a refusal would teach a person to go and fix their configuration,
    and reporting it as PRODUCT_METADATA_UNAVAILABLE would send them looking
    for contract facts that were never the problem.
    """


class BacktestServiceError(Exception):
    """A typed failure a caller can act on."""

    def __init__(self, kind: BacktestErrorKind, code: str, detail: str) -> None:
        self.kind = kind
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class RunRequest:
    """Everything one run is created from. No server-derived state."""

    attempt_key: str
    dataset_id: str
    driver: Timeframe
    interval: RunInterval
    strategy: StrategyPolicy
    account: AccountState
    risk: RiskPolicy
    simulation: SimulationPolicy


class BacktestRunner:
    """One run, computed in memory and published in one transaction."""

    def __init__(
        self,
        *,
        store: BacktestStore,
        replay: ReplayStore,
        resolver: ProductResolver | None,
        codec: ProductSnapshotCodec,
        clock: ClockPort,
        bounds: RunBounds | None = None,
        supported: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        self._store = store
        self._replay = replay
        self._resolver = resolver
        self._codec = codec
        self._clock = clock
        self._bounds = bounds or RunBounds()
        self._supported = supported
        """The strategy rules this runner accepts. ``None`` means the shipped
        registry, which is what production composes."""

    @property
    def bounds(self) -> RunBounds:
        return self._bounds

    # ------------------------------------------------------------------

    async def run(self, request: RunRequest) -> StoredRun:
        """Evaluate one configuration, or refuse it. Never a partial result."""
        key = _validated_key(request.attempt_key)
        # The rules must be ones this build implements, before any market is
        # read. An unknown version is refused, never re-interpreted.
        try:
            require_supported_rules(
                request.strategy.identifier,
                request.strategy.version,
                supported=self._supported,
            )
        except BacktestError as error:
            raise BacktestServiceError(_kind_for(error.code), error.code, error.reason) from error

        dataset, driver_candles, higher = await self._load_market(request)
        product = await self._product(dataset.symbol)
        self._require_dataset_on_grid(driver_candles, product)
        self._require_slippage_on_grid(request.simulation, product)
        snapshot = dict(self._codec.snapshot(product))

        configuration = configuration_fingerprint(
            dataset_id=dataset.dataset_id,
            symbol=dataset.symbol,
            driver=request.driver.value,
            interval_start=request.interval.start,
            interval_end=request.interval.end,
            strategy_id=request.strategy.identifier,
            strategy_version=request.strategy.version,
            strategy_parameters=request.strategy.parameters(),
            simulation=_simulation_facts(request.simulation),
            risk=_risk_facts(request.risk, request.account),
            product_snapshot={k: str(v) for k, v in sorted(snapshot.items())},
            runner_version=RUNNER_RULES_VERSION,
        )
        # Idempotency is decided against the *fingerprint*, not the key alone.
        # Returning the earlier run for a key that now carries a different
        # question would answer a question nobody asked - so the same key with
        # a different configuration is a conflict, and the same key with the
        # same configuration is the safe retry it looks like.
        try:
            existing = await self._store.find_by_attempt(key)
        except BacktestStoreUnavailableError as error:
            raise _unavailable(error) from error
        if existing is not None:
            if existing.configuration != configuration:
                raise BacktestServiceError(
                    BacktestErrorKind.CONFLICT,
                    "ATTEMPT_KEY_REUSED",
                    (
                        f"attempt key {key!r} already ran configuration "
                        f"{existing.configuration}, and this request is "
                        f"{configuration}; use a new key for a different question"
                    ),
                )
            return existing

        now = self._clock.now()
        pending = StoredRun(
            run_id=derive_run_id(configuration, key),
            configuration=configuration,
            attempt_key=key,
            dataset_id=dataset.dataset_id,
            symbol=dataset.symbol,
            driver=request.driver,
            interval_start=request.interval.start,
            interval_end=request.interval.end,
            strategy_id=request.strategy.identifier,
            strategy_version=request.strategy.version,
            strategy_parameters=request.strategy.parameters(),
            simulation=_simulation_facts(request.simulation),
            risk=_risk_facts(request.risk, request.account),
            product_snapshot=snapshot,
            status=RunStatus.PENDING,
            failure_code=None,
            failure_reason=None,
            result=None,
            created_at=now,
            updated_at=now,
        )
        try:
            created = await self._store.create_run(pending)
        except DuplicateRunError:
            found = await self._store.find_by_attempt(key)
            if found is None:  # pragma: no cover - the winner exists by definition
                raise
            return found
        except BacktestStoreUnavailableError as error:
            raise _unavailable(error) from error

        try:
            result = self._evaluate(request, driver_candles, higher, product, snapshot)
        except BacktestError as error:
            await self._store.fail(
                created.run_id,
                code=error.code,
                reason=error.reason,
                now=self._clock.now(),
            )
            raise BacktestServiceError(_kind_for(error.code), error.code, error.reason) from error
        except PaperRefusalError as error:
            await self._store.fail(
                created.run_id,
                code=error.code.value,
                reason=error.reason,
                now=self._clock.now(),
            )
            raise BacktestServiceError(
                BacktestErrorKind.REFUSED, error.code.value, error.reason
            ) from error
        except Exception as error:
            # A defect in this software. The run is terminalised so it cannot
            # sit PENDING forever looking like work in progress.
            #
            # Neither the exception's message nor its traceback is logged here,
            # and the chain is cut with `from None`. An earlier version used
            # `logger.exception`, and a captured-log probe showed the result: a
            # connection string, its password and the full traceback written
            # to the server log, while the HTTP body stayed clean. A log is a
            # disclosure channel too - it is shipped, retained and read by
            # people who were never meant to hold a database credential.
            #
            # What is logged is what an operator can act on without exposing
            # anything: the code, the run, the strategy, and the *type* of the
            # failure. The run id is the handle for reproducing it.
            _LOG.error(
                "backtest run failed unexpectedly",
                extra={
                    "code": INTERNAL_FAILURE_CODE,
                    "run_id": created.run_id,
                    "strategy": request.strategy.identifier,
                    "strategy_version": request.strategy.version,
                    "error_type": type(error).__name__,
                },
            )
            with suppress(BacktestStoreUnavailableError, CompletedRunError):
                await self._store.fail(
                    created.run_id,
                    code=INTERNAL_FAILURE_CODE,
                    reason=INTERNAL_FAILURE_REASON,
                    now=self._clock.now(),
                )
            raise BacktestServiceError(
                BacktestErrorKind.INTERNAL,
                INTERNAL_FAILURE_CODE,
                f"{INTERNAL_FAILURE_REASON} (run {created.run_id})",
            ) from None

        try:
            return await self._store.publish(created.run_id, result, now=self._clock.now())
        except TerminalRunError as error:
            # Somebody abandoned this run while it was being computed. The
            # terminal state they were told about stands; this result is
            # discarded rather than quietly overwriting it.
            raise BacktestServiceError(
                BacktestErrorKind.CONFLICT, "RUN_ALREADY_TERMINAL", str(error)
            ) from error
        except BacktestStoreUnavailableError as error:
            # Publication is the only thing that makes a run COMPLETED, so a
            # failure here leaves it RUNNING/PENDING with no result - never a
            # completed run holding half a ledger.
            raise _unavailable(error) from error

    # ------------------------------------------------------------------

    async def _load_market(
        self, request: RunRequest
    ) -> tuple[StoredDataset, tuple[Candle, ...], dict[Timeframe, tuple[Candle, ...]]]:
        """The immutable dataset, read by digest. Nothing is copied or repaired."""
        try:
            dataset = await self._replay.get_dataset(request.dataset_id)
            if dataset is None:
                raise BacktestServiceError(
                    BacktestErrorKind.NOT_FOUND,
                    "DATASET_NOT_FOUND",
                    f"no historical dataset {request.dataset_id}",
                )
            driver = await self._replay.candles(request.dataset_id, request.driver)
            if not driver:
                raise BacktestServiceError(
                    BacktestErrorKind.INVALID,
                    "DRIVER_TIMEFRAME_MISSING",
                    (
                        f"the dataset holds no {request.driver.value} candles, so there is "
                        "nothing to step through"
                    ),
                )
            higher: dict[Timeframe, tuple[Candle, ...]] = {}
            for summary in dataset.timeframes:
                if summary.timeframe is request.driver:
                    continue
                higher[summary.timeframe] = await self._replay.candles(
                    request.dataset_id, summary.timeframe
                )
        except ReplayStoreUnavailableError as error:
            raise _unavailable(error) from error
        return (
            dataset,
            _named(driver, dataset.symbol),
            {timeframe: _named(bars, dataset.symbol) for timeframe, bars in higher.items()},
        )

    async def _product(self, symbol: str) -> ProductPolicy:
        """Verified contract facts, or a refusal. Never a default.

        Resolved once and frozen for the whole run: changing the metadata
        provider afterwards cannot alter a result that has already been
        computed, because nothing re-reads it.
        """
        if self._resolver is None:
            raise BacktestServiceError(
                BacktestErrorKind.REFUSED,
                "PRODUCT_METADATA_UNAVAILABLE",
                (
                    "no verified contract metadata provider is configured, so a simulated "
                    "trade's money could not be computed; this run is refused rather than "
                    "reported with assumed specifications"
                ),
            )
        try:
            product = await self._resolver.resolve(symbol)
        except Exception as error:  # noqa: BLE001 - adapter failures are unavailability
            raise _unavailable(error) from error
        if product is None:
            raise BacktestServiceError(
                BacktestErrorKind.REFUSED,
                "PRODUCT_METADATA_UNAVAILABLE",
                f"no verified contract metadata for {symbol}",
            )
        return product

    # ------------------------------------------------------------------

    def _evaluate(
        self,
        request: RunRequest,
        driver: Sequence[Candle],
        higher: dict[Timeframe, tuple[Candle, ...]],
        product: ProductPolicy,
        snapshot: dict[str, object],
    ) -> BacktestResult:
        """The causal walk. Pure with respect to storage: nothing is written."""
        boundaries = _boundaries_in(driver, request.interval)
        if not boundaries:
            raise BacktestError(
                "INTERVAL_EMPTY",
                (
                    f"no {request.driver.value} candle closes between "
                    f"{request.interval.start.isoformat()} and "
                    f"{request.interval.end.isoformat()}"
                ),
            )
        self._bounds.check_boundaries(len(boundaries))
        if request.strategy.warm_up_bars > self._bounds.max_warm_up_bars:
            raise BacktestError(
                "RESOURCE_LIMIT",
                f"a strategy may require at most {self._bounds.max_warm_up_bars} warm-up candles",
            )

        driver_readings = _readings_series(driver)
        higher_readings = {
            timeframe: _readings_series(candles) for timeframe, candles in higher.items()
        }

        decisions: list[DecisionRecord] = []
        positions: list[BacktestPosition] = []
        open_position_state: PaperPosition | None = None
        open_ordinal = 0
        sequence = 0

        for index, boundary in boundaries:
            sequence += 1
            bar = driver[index]

            # 1-3. The bar that just closed goes to the open position first, so
            #      a fill decided by this candle happens before anything is
            #      asked about it.
            if open_position_state is not None:
                open_position_state = apply_observation(open_position_state, bar, product)
                if open_position_state.state.is_terminal:
                    positions[open_ordinal - 1] = _frozen(
                        positions[open_ordinal - 1], open_position_state
                    )
                    open_position_state = None

            # 4-5. Confirmed readings at this boundary, and the rule's answer.
            context = StrategyContext(
                as_of=boundary,
                symbol=bar.symbol,
                driver=request.driver,
                bar=bar,
                current=driver_readings[index],
                previous=driver_readings[index - 1] if index else Readings(),
                higher=_confirmed_higher(higher, higher_readings, boundary),
                bars_available=index + 1,
                has_open_position=open_position_state is not None,
            )
            decision = request.strategy.decide(context)

            if open_position_state is not None:
                record, open_position_state = _handle_open(
                    sequence, boundary, context, decision, open_position_state
                )
                decisions.append(record)
                if open_position_state is not None and open_position_state.state.is_terminal:
                    positions[open_ordinal - 1] = _frozen(
                        positions[open_ordinal - 1], open_position_state
                    )
                    open_position_state = None
                continue

            if decision.kind is DecisionKind.WAIT:
                decisions.append(
                    _record(sequence, boundary, DecisionOutcome.WAIT, decision.reason, context)
                )
                continue
            if decision.kind in (DecisionKind.NO_SIGNAL, DecisionKind.EXIT_INTENT):
                decisions.append(
                    _record(sequence, boundary, DecisionOutcome.NO_SIGNAL, decision.reason, context)
                )
                continue

            assert decision.entry is not None  # noqa: S101 - narrowed by the decision type
            opened, record = self._attempt_entry(
                sequence=sequence,
                boundary=boundary,
                context=context,
                intent=decision.entry,
                reason=decision.reason,
                request=request,
                product=product,
                ordinal=len(positions) + 1,
            )
            decisions.append(record)
            if opened is not None:
                self._bounds.check_positions(len(positions) + 1)
                positions.append(
                    BacktestPosition(
                        position_id=opened.spec.position_id,
                        ordinal=len(positions) + 1,
                        spec=opened.spec,
                        approval=opened.approval,
                        product_snapshot=snapshot,
                        events=tuple(opened.events),
                    )
                )
                open_position_state = opened
                open_ordinal = len(positions)

        if open_position_state is not None:
            # The dataset ended while a position was open. It stays open: no
            # exit is manufactured from missing data, and an open position is
            # never counted as a completed win or loss.
            positions[open_ordinal - 1] = _frozen(positions[open_ordinal - 1], open_position_state)

        return BacktestResult(
            boundaries_evaluated=len(boundaries),
            first_boundary=boundaries[0][1],
            last_boundary=boundaries[-1][1],
            decisions=tuple(decisions),
            positions=tuple(positions),
            result_digest=result_digest(
                boundaries=len(boundaries),
                decisions=_decision_counts(decisions),
                positions={f"{item.ordinal:04d}": _position_facts(item) for item in positions},
            ),
        )

    def _attempt_entry(
        self,
        *,
        sequence: int,
        boundary: datetime,
        context: StrategyContext,
        intent: EntryIntent,
        reason: str,
        request: RunRequest,
        product: ProductPolicy,
        ordinal: int,
    ) -> tuple[PaperPosition | None, DecisionRecord]:
        """Grid, then risk, then the engine. A refusal is recorded, never a trade.

        Alignment happens here, in the one place that holds the frozen product,
        and not in the strategy: a policy that knew this contract's tick size
        would be a policy carrying a verified exchange fact it cannot vouch
        for, and would produce different levels for the same reading on a
        different instrument.
        """
        intent, alignment = self._aligned(intent, product)
        if alignment is not None and alignment.moved:
            reason = f"{reason}; {alignment.note}"
        sizing = size_for_product(
            direction=intent.direction,
            entry_price=intent.intended_entry,
            stop_price=intent.stop,
            product=product,
            account=request.account,
            policy=request.risk,
        )
        approval = RiskApproval.from_sizing(sizing)
        if sizing.outcome is not SizingOutcome.ALLOWED or not sizing.allowed_contracts:
            return None, DecisionRecord(
                sequence=sequence,
                as_of=boundary,
                outcome=DecisionOutcome.REFUSED_BY_RISK,
                reason=reason,
                bars_available=context.bars_available,
                risk_outcome=sizing.outcome.value,
                risk_reason=sizing.reason,
                direction=intent.direction.value,
            )

        units = min(intent.quantity, sizing.allowed_contracts)
        spec = PositionSpec(
            position_id=f"BP-{_position_suffix(request.attempt_key, ordinal)}",
            symbol=context.symbol,
            direction=intent.direction,
            quantity=units,
            intended_entry=intent.intended_entry,
            stop=intent.stop,
            targets=tuple(
                TargetSpec(price=level.price, quantity=min(level.quantity, units))
                for level in intent.targets
            ),
            timeframe=request.driver,
            # The decision is stamped with market time, so Phase 9 fills the
            # entry on the first bar opening at or after it - never the bar
            # that produced the signal.
            decision_time=boundary,
            policy=request.simulation,
            origin=PositionOrigin.STRATEGY_BACKTEST,
        )
        try:
            position = open_position(spec, approval, product)
        except PaperRefusalError as error:
            return None, DecisionRecord(
                sequence=sequence,
                as_of=boundary,
                outcome=DecisionOutcome.REFUSED_BY_ENGINE,
                reason=f"{error.code.value}: {error.reason}",
                bars_available=context.bars_available,
                risk_outcome=sizing.outcome.value,
                risk_reason=sizing.reason,
                direction=intent.direction.value,
            )
        return position, DecisionRecord(
            sequence=sequence,
            as_of=boundary,
            outcome=DecisionOutcome.ENTERED,
            reason=reason,
            bars_available=context.bars_available,
            position_id=spec.position_id,
            risk_outcome=sizing.outcome.value,
            risk_reason=sizing.reason,
            direction=intent.direction.value,
        )

    async def find(self, attempt_key: str) -> StoredRun | None:
        """The run this attempt key already produced, if any.

        A read, not a decision: idempotency is still owned by ``run``. The API
        uses this only to tell a fresh creation from a replayed one, which is
        the difference between 201 and 200.
        """
        try:
            return await self._store.find_by_attempt(_validated_key(attempt_key))
        except BacktestStoreUnavailableError as error:
            raise _unavailable(error) from error

    async def head(self, run_id: str) -> tuple[StoredRun, RunTotals]:
        """A run's identity, configuration, status and totals - no payload.

        What a detail screen needs. The trace and the ledgers are separate,
        paginated reads, so opening a 2,500-boundary run costs a row and two
        counts rather than the whole run.
        """
        try:
            found = await self._store.head(run_id)
        except BacktestStoreUnavailableError as error:
            raise _unavailable(error) from error
        if found is None:
            raise BacktestServiceError(
                BacktestErrorKind.NOT_FOUND, "RUN_NOT_FOUND", f"no backtest run {run_id}"
            )
        return found

    async def trace(
        self, run_id: str, *, offset: int, limit: int
    ) -> tuple[tuple[DecisionRecord, ...], int]:
        """One page of why each boundary ended the way it did."""
        await self.head(run_id)
        try:
            return await self._store.trace_page(run_id, offset=offset, limit=limit)
        except BacktestStoreUnavailableError as error:
            raise _unavailable(error) from error

    async def events(
        self, run_id: str, position_id: str, *, after_sequence: int, limit: int
    ) -> tuple[tuple[LedgerEvent, ...], int]:
        """One page of a position's ledger, for a position *this run* opened.

        Ownership is checked against the run's own positions rather than
        inferred from the identifier's shape, so one run cannot read another's
        ledger by guessing a plausible id.
        """
        await self.head(run_id)
        try:
            positions = await self._store.positions_of(run_id)
        except BacktestStoreUnavailableError as error:
            raise _unavailable(error) from error
        if not any(item.position_id == position_id for item in positions):
            raise BacktestServiceError(
                BacktestErrorKind.NOT_FOUND,
                "POSITION_NOT_FOUND",
                f"run {run_id} did not open position {position_id}",
            )
        try:
            return await self._store.events_of(
                position_id, after_sequence=after_sequence, limit=limit
            )
        except BacktestStoreUnavailableError as error:
            raise _unavailable(error) from error

    def strategies(self) -> Mapping[str, frozenset[str]]:
        """The rules this runner will actually accept. The registry, unedited.

        The API publishes this rather than a list of its own: a second
        allow-list is a second thing to keep in step, and the one that drifts
        is always the one nobody runs.
        """
        return SUPPORTED_STRATEGIES if self._supported is None else self._supported

    async def abandon(self, run_id: str) -> StoredRun:
        """Terminalise a run an interruption left unfinished.

        A run row is created before the walk begins, so a process killed
        half-way leaves a PENDING row with no result. Nothing recovers it
        automatically, and nothing should: the walk's intermediate state was
        never written anywhere, so there is nothing to resume from, and
        inventing a resumption point would mean publishing a result computed
        partly before the interruption and partly after.

        There is no job scheduler here and no retry daemon. The workflow is
        explicit and has exactly two steps:

        1. **Terminalise the stuck run** with this method. It becomes FAILED
           with the code ``INTERRUPTED``, so it can never be mistaken for a
           result, and its attempt key stops shadowing new work.
        2. **Ask again under a new attempt key.** A new key is a new request,
           which is a decision a person makes - not one a retry loop makes on
           their behalf, using financial output nobody checked.

        A COMPLETED run is refused outright: it holds a real answer, and
        re-labelling it would throw that answer away. An already FAILED run is
        returned unchanged, so cleaning up twice is harmless.
        """
        try:
            stored = await self._store.get(run_id)
        except BacktestStoreUnavailableError as error:
            raise _unavailable(error) from error
        if stored is None:
            raise BacktestServiceError(
                BacktestErrorKind.NOT_FOUND, "RUN_NOT_FOUND", f"no backtest run {run_id}"
            )
        if stored.status is RunStatus.COMPLETED:
            raise BacktestServiceError(
                BacktestErrorKind.CONFLICT,
                "RUN_ALREADY_COMPLETED",
                (
                    f"run {run_id} completed and holds a result; a finished run is not "
                    "abandoned, and its result is not discarded"
                ),
            )
        if stored.status is RunStatus.FAILED:
            return stored
        try:
            return await self._store.fail(
                run_id,
                code="INTERRUPTED",
                reason=(
                    "this run was left unfinished by an interruption and was terminalised "
                    "explicitly; no partial result was kept, and re-running the same "
                    "configuration requires a new attempt key"
                ),
                now=self._clock.now(),
            )
        except CompletedRunError as error:  # pragma: no cover - guarded above
            raise BacktestServiceError(
                BacktestErrorKind.CONFLICT, "RUN_ALREADY_COMPLETED", str(error)
            ) from error
        except BacktestStoreUnavailableError as error:
            raise _unavailable(error) from error

    def _require_dataset_on_grid(self, driver: Sequence[Candle], product: ProductPolicy) -> None:
        """Refuse a dataset whose prices this product could not have traded.

        An entry does not fill at the price the strategy proposed; Phase 9
        fills it at the *next bar's open*, which is a price the market printed.
        Phase 3 checks the proposed entry, so an off-grid plan is already
        refused - but nothing was checking the price the fill would actually
        use. A dataset one tick out of step with the product would therefore
        have produced positions opened, stopped and closed at prices that
        product cannot quote, and every figure derived from them would be
        fiction wearing a Decimal.

        The answer is not to round the candle. A historical open is an
        authoritative market price and the dataset is immutable; moving it
        would fabricate a trade at a price nobody paid. So the run is refused,
        naming the first offending candle, and the disagreement is left for a
        person to resolve - the data is wrong for this product, or the product
        is wrong for this data.

        Only the driver series is checked, because only the driver fills
        anything. Higher timeframes feed indicators, which are floats and make
        no claim of executability. The check runs only against a *verified*
        increment: an unverified grid cannot convict a price.
        """
        increment = product.price_increment()
        if increment is None or not increment.is_authoritative:
            return
        tick = increment.value
        for candle in driver:
            for name, price in (
                ("open", candle.open),
                ("high", candle.high),
                ("low", candle.low),
                ("close", candle.close),
            ):
                if not on_grid(price, tick):
                    raise BacktestServiceError(
                        BacktestErrorKind.REFUSED,
                        "DATASET_OFF_PRODUCT_GRID",
                        (
                            f"the candle opening at {candle.open_time.isoformat()} has "
                            f"{name} {price}, which is not a whole number of {tick} ticks "
                            f"for {product.instrument.symbol}; a fill at that price could "
                            "not have happened, and the candle is not rounded to make one "
                            "possible. Either the dataset is not this product's, or the "
                            "verified tick size is wrong"
                        ),
                    )

    def _require_slippage_on_grid(
        self, simulation: SimulationPolicy, product: ProductPolicy
    ) -> None:
        """Refuse a fixed slippage that would move a fill off the grid.

        Phase 9's rules are untouched: slippage is still an adverse amount in
        price points, applied the same way, and the paper trader still accepts
        whatever a person states. What a *backtest* additionally refuses is a
        figure that is not a whole number of ticks - because it is applied to
        an on-grid market price and would land every market-style fill on a
        price the product cannot quote.

        A person watching one paper trade can see and discount that. A batch of
        two hundred cannot be inspected, and the resulting P&L would carry a
        systematic error nobody chose.
        """
        slippage = simulation.slippage
        points = slippage.points
        if points is None or points == 0:
            return
        increment = product.price_increment()
        if increment is None or not increment.is_authoritative:
            return
        if not on_grid(points, increment.value):
            raise BacktestServiceError(
                BacktestErrorKind.INVALID,
                "SLIPPAGE_OFF_PRODUCT_GRID",
                (
                    f"a slippage of {points} points is not a whole number of "
                    f"{increment.value} ticks, so every market-style fill would land on a "
                    "price this product cannot quote; state the slippage in whole ticks"
                ),
            )

    @staticmethod
    def _aligned(
        intent: EntryIntent, product: ProductPolicy
    ) -> tuple[EntryIntent, Alignment | None]:
        """The intent with executable protective levels, when the grid is known.

        An absent or unverified increment leaves the intent exactly as the
        strategy computed it. That is the honest outcome, not a degraded one:
        Phase 3 then reports the feasibility as MISSING or UNVERIFIED and the
        decision is recorded with that reason, rather than the run rounding to
        a grid nobody confirmed and presenting the result as executable.
        """
        increment = product.price_increment()
        if increment is None or not increment.is_authoritative:
            return intent, None
        alignment = align_intent(intent, increment.value)
        return alignment.intent, alignment

    # ------------------------------------------------------------------

    async def get(self, run_id: str) -> StoredRun:
        try:
            run = await self._store.get(run_id)
        except BacktestStoreUnavailableError as error:
            raise _unavailable(error) from error
        if run is None:
            raise BacktestServiceError(
                BacktestErrorKind.NOT_FOUND, "RUN_NOT_FOUND", f"no backtest run {run_id}"
            )
        return run

    async def list(self, *, offset: int, limit: int) -> tuple[tuple[RunSummary, ...], int]:
        try:
            return await self._store.list_runs(offset=max(0, offset), limit=max(1, min(limit, 25)))
        except BacktestStoreUnavailableError as error:
            raise _unavailable(error) from error


# ----------------------------------------------------------------------


def _handle_open(
    sequence: int,
    boundary: datetime,
    context: StrategyContext,
    decision: StrategyDecision,
    position: PaperPosition,
) -> tuple[DecisionRecord, PaperPosition]:
    """What an open position does with a decision. Only an exit acts."""
    kind = decision.kind
    reason = decision.reason
    if kind is DecisionKind.EXIT_INTENT and not position.close_pending:
        return (
            _record(
                sequence,
                boundary,
                DecisionOutcome.EXIT_REQUESTED,
                reason,
                context,
                position_id=position.spec.position_id,
            ),
            request_close(position),
        )
    return (
        _record(
            sequence,
            boundary,
            DecisionOutcome.HOLDING,
            reason,
            context,
            position_id=position.spec.position_id,
        ),
        position,
    )


def _record(
    sequence: int,
    boundary: datetime,
    outcome: DecisionOutcome,
    reason: str,
    context: StrategyContext,
    *,
    position_id: str | None = None,
) -> DecisionRecord:
    return DecisionRecord(
        sequence=sequence,
        as_of=boundary,
        outcome=outcome,
        reason=reason,
        bars_available=context.bars_available,
        position_id=position_id,
    )


def _frozen(record: BacktestPosition, position: PaperPosition) -> BacktestPosition:
    """The position as it now stands, with its whole ledger."""
    return BacktestPosition(
        position_id=record.position_id,
        ordinal=record.ordinal,
        spec=record.spec,
        approval=record.approval,
        product_snapshot=record.product_snapshot,
        events=tuple(position.events),
    )


def _named(candles: Sequence[Candle], symbol: str) -> tuple[Candle, ...]:
    """Stored candles carrying the dataset's own symbol.

    Phase 11 stores a dataset's candle rows without repeating the instrument on
    every one - the dataset row holds it - and returns them with an empty
    symbol for the caller's context to fill. Replay does that by re-parsing
    through the CSV parser; a backtest does it here. The name comes from the
    dataset being read, so nothing invents an instrument identity, and the
    Phase 9 engine's symbol check still has a real value to check against.
    """
    return tuple(replace(candle, symbol=symbol) for candle in candles)


def _boundaries_in(candles: Sequence[Candle], interval: RunInterval) -> list[tuple[int, datetime]]:
    """Every driver boundary inside the requested window, with its index.

    A boundary is a candle's coverage end - the moment it became a confirmed
    fact - so the window is expressed in the same market time the rest of the
    system uses, and nothing before ``start`` is ever evaluated.
    """
    found: list[tuple[int, datetime]] = []
    for index, candle in enumerate(candles):
        end = coverage_end(candle)
        if interval.start <= end <= interval.end:
            found.append((index, end))
    return found


def _readings_series(candles: Sequence[Candle]) -> list[Readings]:
    """Indicator readings per candle, or empty readings when unusable.

    Computed once over the whole series. Phase 1 guarantees value ``i`` derives
    from candles ``0..i`` only, and a unit test proves that guarantee holds for
    every indicator read here - so indexing is identical to recomputing over
    each prefix, at a fraction of the cost.
    """
    if not candles:
        return []
    assessment = DataQualityEngine().assess(CandleSeries.of(tuple(candles)))
    series = assessment.series
    if series is None:
        return [Readings() for _ in candles]
    snapshot = compute_technicals(series)
    return [_readings_at(snapshot, index) for index in range(len(candles))]


def _readings_at(snapshot: TechnicalSnapshot, index: int) -> Readings:
    return Readings(
        ema_fast=_value(snapshot.ema.get(9), index),
        ema_slow=_value(snapshot.ema.get(20), index),
        rsi=_value(snapshot.rsi, index),
        atr=_value(snapshot.atr, index),
        adx=_value(snapshot.adx, index),
    )


def _value(values: Sequence[float | None] | None, index: int) -> float | None:
    if values is None or index < 0 or index >= len(values):
        return None
    return values[index]


def _confirmed_higher(
    candles: dict[Timeframe, tuple[Candle, ...]],
    readings: dict[Timeframe, list[Readings]],
    boundary: datetime,
) -> dict[Timeframe, Readings]:
    """Higher-timeframe readings, and only for candles that have closed.

    A forming 1H bar is *absent*, not present with partial values: the last
    index whose coverage ended at or before the boundary is the only one a
    strategy may see.
    """
    confirmed: dict[Timeframe, Readings] = {}
    for timeframe, series in candles.items():
        last = -1
        for index, candle in enumerate(series):
            if coverage_end(candle) <= boundary:
                last = index
            else:
                break
        if last >= 0:
            confirmed[timeframe] = readings[timeframe][last]
    return confirmed


def _decision_counts(decisions: Sequence[DecisionRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in decisions:
        counts[record.outcome.value] = counts.get(record.outcome.value, 0) + 1
    return counts


def _position_facts(position: BacktestPosition) -> dict[str, str]:
    """What a position concluded, without its generated id.

    Two runs of one configuration produce different position ids - they are
    derived from the attempt key - so identity here is the ordinal and the
    ledger, which is what "the same result" actually means.
    """
    return {
        "direction": position.spec.direction.value,
        "quantity": str(position.spec.quantity),
        "decision_time": canonical_time(position.spec.decision_time),
        "intended_entry": canonical_decimal(position.spec.intended_entry),
        "stop": canonical_decimal(position.spec.stop),
        "targets": "|".join(
            f"{canonical_decimal(t.price)}x{t.quantity}" for t in position.spec.targets
        ),
        "risk_outcome": position.approval.outcome.value,
        "events": "|".join(
            f"{event.type.value}@"
            f"{'' if event.market_time is None else canonical_time(event.market_time)}:"
            + ",".join(f"{k}={v}" for k, v in sorted(event.data.items()))
            for event in position.events
        ),
    }


def _simulation_facts(policy: SimulationPolicy) -> dict[str, str]:
    """Every simulation choice that can move a fill, as canonical text."""
    return {
        "rules_version": policy.rules_version,
        "same_bar": policy.same_bar.value,
        "slippage_mode": policy.slippage.mode.value,
        "slippage_points": canonical_decimal(policy.slippage.points),
        "fee_mode": policy.fees.mode.value,
        "fee_per_unit": canonical_decimal(policy.fees.per_unit),
    }


def _risk_facts(policy: RiskPolicy, account: AccountState) -> dict[str, str]:
    return {
        "mode": policy.mode.value,
        "fixed_risk": canonical_decimal(policy.fixed_risk),
        "risk_ratio": canonical_decimal(policy.risk_ratio),
        "max_contracts": "" if policy.max_contracts is None else str(policy.max_contracts),
        "equity": canonical_decimal(account.equity),
        "used_margin": canonical_decimal(account.used_margin),
    }


def _position_suffix(attempt_key: str, ordinal: int) -> str:
    import hashlib

    digest = hashlib.sha256(f"{attempt_key}|{ordinal}".encode()).hexdigest()
    return digest[:24]


def _validated_key(key: str) -> str:
    stripped = key.strip()
    if not 16 <= len(stripped) <= 128 or not all(c.isalnum() or c in "-_" for c in stripped):
        raise BacktestServiceError(
            BacktestErrorKind.INVALID,
            "ATTEMPT_KEY_INVALID",
            "an attempt key is 16-128 characters of letters, digits, '-' or '_'",
        )
    return stripped


def _kind_for(code: str) -> BacktestErrorKind:
    if code == "RESOURCE_LIMIT":
        return BacktestErrorKind.TOO_LARGE
    if code in ("INTERVAL_EMPTY", "INTERVAL_INVALID", "TRACE_INVALID"):
        return BacktestErrorKind.INVALID
    return BacktestErrorKind.REFUSED


def _unavailable(error: object) -> BacktestServiceError:
    return BacktestServiceError(
        BacktestErrorKind.UNAVAILABLE, "BACKTEST_STORE_UNAVAILABLE", str(error)
    )


__all__ = [
    "SUPPORTED_TIMEFRAMES",
    "BacktestErrorKind",
    "BacktestRunner",
    "BacktestServiceError",
    "RunRequest",
    "Decimal",
]
