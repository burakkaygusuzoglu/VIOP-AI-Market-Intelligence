"""Deterministic backtesting routes (Phase 12 Part 2A). Simulation only.

A backtest evaluates one fixed strategy against one immutable historical
dataset and records what the existing engines said. No route here reaches a
live feed, a broker or an order, and none of them computes a number.

## The server owns every result

A request says *what to evaluate*. It cannot say what happened: there is no
field for a fill, a P&L, a metric, a decision, a risk approval, a digest or a
status, so a body carrying one is refused because the field does not exist.

## Nothing here computes anything

* the walk - Phase 12's ``BacktestRunner``, which orchestrates Phases 1, 3, 9
  and 11 and adds no arithmetic;
* metrics - Phase 10's engine, over this run's own outcomes;
* positions - replayed through Phase 9 from their stored ledgers.

## Reads are bounded

Opening a run costs a row and two counts. The trace, the positions and a
position's ledger are separate paginated reads, because a run at the
2,500-boundary ceiling would otherwise put its whole history in one response.

## Status codes

* ``201`` a run was created; ``200`` the same idempotency key was already used
  and that run is returned unchanged.
* ``422`` malformed input or an engine's refusal, with a typed code.
* ``404`` no such run, or a position this run did not open.
* ``409`` key reuse with a different configuration, or abandoning a run that
  already completed.
* ``413`` a configuration beyond the runner's resource bounds.
* ``503`` the store is unreachable.
* ``500`` a defect in this software. The body carries a fixed sentence and the
  run id - never an exception, a module name or a path - and the run is left
  FAILED rather than PENDING so it cannot look like work still in progress.
"""

from __future__ import annotations

from collections.abc import Awaitable
from decimal import Decimal
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)

from app.api.dependencies import get_clock
from app.api.routes.paper import get_product_resolver
from app.api.schemas.backtest import (
    MAX_DATASET_PAGE,
    BacktestCapabilityResponse,
    BacktestDatasetListResponse,
    BacktestEventListResponse,
    BacktestPerformanceResponse,
    BacktestPositionListResponse,
    BacktestRunListResponse,
    BacktestRunResponse,
    BacktestTraceResponse,
    CreateBacktestRunBody,
    EventLimit,
    PositionLimit,
    RunLimit,
    RunOffset,
    StrategyCatalogueResponse,
    TraceLimit,
)
from app.api.schemas.backtest_projection import (
    dataset_list,
    event_list,
    position,
    position_list,
    run,
    run_list,
    strategy_catalogue,
    trace,
)
from app.api.schemas.performance_projection import performance as performance_response
from app.application.backtest.ports import BacktestStore, RunPerformanceFactory
from app.application.backtest.service import (
    BacktestErrorKind,
    BacktestRunner,
    BacktestServiceError,
    RunRequest,
)
from app.application.performance.ports import OutcomeFilters
from app.application.performance.service import PerformanceService
from app.application.ports.paper import ProductResolver, ProductSnapshotCodec
from app.application.ports.system import ClockPort
from app.application.replay.ports import ReplayStore
from app.domain.backtest.registry import SUPPORTED_STRATEGIES
from app.domain.backtest.run import BacktestError, RunInterval
from app.domain.backtest.strategies.ema_crossover import IDENTIFIER as EMA_IDENTIFIER
from app.domain.backtest.strategies.ema_crossover import EmaCrossoverStrategy
from app.domain.common.enums import Timeframe
from app.domain.paper import (
    FeeMode,
    FeePolicy,
    SameBarPolicy,
    SimulationPolicy,
    SimulationPolicyError,
    SlippageMode,
    SlippagePolicy,
)
from app.domain.paper.engine import PaperRefusalError, rebuild, unrealized_gross
from app.domain.risk.sizing import AccountState, RiskInputError, RiskMode, RiskPolicy

router = APIRouter(prefix="/backtest", tags=["backtest"])

RunId = Annotated[str, Path(pattern=r"^BR-[0-9a-f]{24}$")]
PositionId = Annotated[str, Path(pattern=r"^BP-[A-Za-z0-9_-]{1,48}$")]

_STATUS: dict[BacktestErrorKind, int] = {
    BacktestErrorKind.INVALID: status.HTTP_422_UNPROCESSABLE_CONTENT,
    BacktestErrorKind.REFUSED: status.HTTP_422_UNPROCESSABLE_CONTENT,
    BacktestErrorKind.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    BacktestErrorKind.CONFLICT: status.HTTP_409_CONFLICT,
    BacktestErrorKind.TOO_LARGE: status.HTTP_413_CONTENT_TOO_LARGE,
    BacktestErrorKind.UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    BacktestErrorKind.INTERNAL: status.HTTP_500_INTERNAL_SERVER_ERROR,
}

_TIMEFRAMES = {
    "1D": Timeframe.D1,
    "1H": Timeframe.H1,
    "15M": Timeframe.M15,
    "5M": Timeframe.M5,
}

_NO_METADATA = (
    "This deployment has no verified contract metadata provider, so a simulated trade's "
    "money could not be computed. Historical candles do not establish a multiplier, a tick "
    "size, a margin or a contract identity, and a run is refused rather than reported with "
    "assumed specifications."
)
_HAS_METADATA = (
    "Verified contract metadata is available, so simulated fills and money can be computed."
)


def get_backtest_runner(
    request: Request,
    resolver: Annotated[ProductResolver | None, Depends(get_product_resolver)],
    clock: Annotated[ClockPort, Depends(get_clock)],
) -> BacktestRunner:
    """Assemble the runner from what the composition root wired.

    ``resolver`` is ``None`` in a default deployment. That is not a degraded
    mode with zero trades - it is a refusal, raised by the runner itself.
    """
    return BacktestRunner(
        store=request.app.state.backtest_store,
        replay=request.app.state.replay_store,
        resolver=resolver,
        codec=request.app.state.product_codec,
        clock=clock,
    )


def get_backtest_performance(request: Request, run_id: str) -> PerformanceService:
    """Phase 10's engine, scoped to one run - built by the composition root."""
    factory: RunPerformanceFactory = request.app.state.backtest_performance
    return factory(run_id)


Runner = Annotated[BacktestRunner, Depends(get_backtest_runner)]


# ----------------------------------------------------------------------
# Capability and catalogue
# ----------------------------------------------------------------------


@router.get("/capability", response_model=BacktestCapabilityResponse)
async def capability(
    runner: Runner,
    resolver: Annotated[ProductResolver | None, Depends(get_product_resolver)],
) -> BacktestCapabilityResponse:
    """Whether this deployment can price a simulated trade, stated up front.

    Read from the **same** dependency the runner refuses on. Reporting
    capability from one source while refusing on another is two answers to one
    question, and they only agree until somebody composes one of them.
    """
    available = resolver is not None
    bounds = runner.bounds
    return BacktestCapabilityResponse(
        financial_execution_available=available,
        reason=_HAS_METADATA if available else _NO_METADATA,
        refusal_code=None if available else "PRODUCT_METADATA_UNAVAILABLE",
        max_boundaries=bounds.max_boundaries,
        max_positions=bounds.max_positions,
        max_warm_up_bars=bounds.max_warm_up_bars,
    )


@router.get("/strategies", response_model=StrategyCatalogueResponse)
async def strategies(runner: Runner) -> StrategyCatalogueResponse:
    """The strategy registry, described. Nothing else may be requested."""
    return strategy_catalogue(runner.strategies())


@router.get("/datasets", response_model=BacktestDatasetListResponse)
async def datasets(
    request: Request,
    offset: RunOffset = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_DATASET_PAGE)] = MAX_DATASET_PAGE,
) -> BacktestDatasetListResponse:
    """The immutable datasets a run may be pointed at."""
    store: ReplayStore = request.app.state.replay_store
    items, total = await store.list_datasets(offset=offset, limit=limit)
    return dataset_list(items, total, offset=offset, limit=limit)


# ----------------------------------------------------------------------
# Runs
# ----------------------------------------------------------------------


@router.post("/runs", response_model=BacktestRunResponse, status_code=status.HTTP_201_CREATED)
async def create_run(
    body: CreateBacktestRunBody,
    runner: Runner,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> BacktestRunResponse:
    """Evaluate one configuration, or refuse it.

    The same key returns the run that key already produced - including a
    PENDING or FAILED one. Re-running is a new attempt under a new key, which
    is a decision a person makes rather than one a retry loop makes for them.
    """
    request_model = RunRequest(
        attempt_key=idempotency_key,
        dataset_id=body.dataset_id,
        driver=_TIMEFRAMES[body.driver_timeframe],
        interval=_interval(body),
        strategy=_strategy(body),
        account=_account(body),
        risk=_risk(body),
        simulation=_simulation(body),
    )
    before = await _existing(runner, idempotency_key)
    stored = await _run(runner.run(request_model))
    if before is not None:
        response.status_code = status.HTTP_200_OK
    found, totals = await _run(runner.head(stored.run_id))
    return run(found, totals)


@router.get("/runs", response_model=BacktestRunListResponse)
async def list_runs(
    runner: Runner, offset: RunOffset = 0, limit: RunLimit = 20
) -> BacktestRunListResponse:
    items, total = await _run(runner.list(offset=offset, limit=limit))
    return run_list(items, total, offset=offset, limit=limit)


@router.get("/runs/{run_id}", response_model=BacktestRunResponse)
async def get_run(run_id: RunId, runner: Runner) -> BacktestRunResponse:
    """Identity, configuration, status and totals. No trace, no ledgers."""
    stored, totals = await _run(runner.head(run_id))
    return run(stored, totals)


@router.post("/runs/{run_id}/abandon", response_model=BacktestRunResponse)
async def abandon_run(run_id: RunId, runner: Runner) -> BacktestRunResponse:
    """Terminalise a run an interruption left unfinished.

    A COMPLETED run is refused with 409: it holds a real answer, and
    re-labelling it would throw that answer away. Abandoning twice is
    harmless, so a repeated request is safe.
    """
    await _run(runner.abandon(run_id))
    stored, totals = await _run(runner.head(run_id))
    return run(stored, totals)


# ----------------------------------------------------------------------
# Trace, positions, events, performance
# ----------------------------------------------------------------------


@router.get("/runs/{run_id}/trace", response_model=BacktestTraceResponse)
async def get_trace(
    run_id: RunId, runner: Runner, offset: RunOffset = 0, limit: TraceLimit = 50
) -> BacktestTraceResponse:
    """One bounded page of why each boundary ended the way it did."""
    records, total = await _run(runner.trace(run_id, offset=offset, limit=limit))
    return trace(run_id, records, total, offset=offset, limit=limit)


@router.get("/runs/{run_id}/positions", response_model=BacktestPositionListResponse)
async def get_positions(
    run_id: RunId,
    request: Request,
    runner: Runner,
    offset: RunOffset = 0,
    limit: PositionLimit = 25,
) -> BacktestPositionListResponse:
    """The simulated positions this run opened, replayed from their ledgers."""
    await _run(runner.head(run_id))
    store: BacktestStore = request.app.state.backtest_store
    codec: ProductSnapshotCodec = request.app.state.product_codec
    stored = await store.positions_of(run_id)
    page = stored[offset : offset + limit]
    items = []
    for item in page:
        try:
            product = codec.restore(item.product_snapshot)
            rebuilt = rebuild(item.spec, item.approval, product, item.events)
            mark = unrealized_gross(rebuilt, product)
        except PaperRefusalError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "LEDGER_UNREPLAYABLE",
                    "kind": "UNAVAILABLE",
                    "detail": f"{item.position_id} could not be replayed from its ledger",
                },
            ) from error
        items.append(position(item.ordinal, rebuilt, event_count=len(item.events), unrealized=mark))
    return position_list(run_id, items, len(stored), offset=offset, limit=limit)


@router.get(
    "/runs/{run_id}/positions/{position_id}/events",
    response_model=BacktestEventListResponse,
)
async def get_events(
    run_id: RunId,
    position_id: PositionId,
    runner: Runner,
    after_sequence: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: EventLimit = 100,
) -> BacktestEventListResponse:
    """One page of a position's ledger, for a position *this run* opened."""
    events, total = await _run(
        runner.events(run_id, position_id, after_sequence=after_sequence, limit=limit)
    )
    return event_list(
        run_id, position_id, events, total, after_sequence=after_sequence, limit=limit
    )


@router.get("/runs/{run_id}/performance", response_model=BacktestPerformanceResponse)
async def get_performance(
    run_id: RunId, request: Request, runner: Runner
) -> BacktestPerformanceResponse:
    """Phase 10's metrics over this run's own outcomes, and nobody else's."""
    stored, _ = await _run(runner.head(run_id))
    service = get_backtest_performance(request, run_id)
    view = await service.summary(OutcomeFilters())
    return BacktestPerformanceResponse(
        run_id=run_id,
        status=stored.status.value,
        performance=performance_response(view),
    )


# ----------------------------------------------------------------------


async def _run[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except BacktestServiceError as error:
        raise HTTPException(
            status_code=_STATUS[error.kind],
            detail={"code": error.code, "kind": error.kind.value, "detail": error.detail},
        ) from error


async def _existing(runner: BacktestRunner, key: str) -> object | None:
    """Whether this key already produced a run, read before the attempt.

    Used only to choose 201 against 200. The runner still owns idempotency; this
    does not decide it.
    """
    return await _run(runner.find(key))


def _interval(body: CreateBacktestRunBody) -> RunInterval:
    try:
        return RunInterval(start=body.start, end=body.end)
    except BacktestError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": error.code, "kind": "INVALID", "detail": error.reason},
        ) from error


def _strategy(body: CreateBacktestRunBody) -> EmaCrossoverStrategy:
    """The registered implementation for the requested rules.

    A static mapping, checked against the registry. There is no import by name,
    no ``eval`` and no user-supplied Python: an unregistered pair never reaches
    a constructor, and the runner checks the pair again before it reads a
    dataset.
    """
    if body.strategy_id != EMA_IDENTIFIER or body.strategy_version not in SUPPORTED_STRATEGIES.get(
        EMA_IDENTIFIER, frozenset()
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "STRATEGY_UNSUPPORTED",
                "kind": "REFUSED",
                "detail": (
                    f"{body.strategy_id!r} version {body.strategy_version!r} is not a strategy "
                    f"this build implements; available: {sorted(SUPPORTED_STRATEGIES)}"
                ),
            },
        )
    return EmaCrossoverStrategy()


def _account(body: CreateBacktestRunBody) -> AccountState:
    try:
        return AccountState(
            equity=Decimal(body.account.equity),
            used_margin=Decimal(body.account.used_margin),
        )
    except (ArithmeticError, ValueError, RiskInputError) as error:
        raise _invalid("ACCOUNT_INVALID", str(error)) from error


def _risk(body: CreateBacktestRunBody) -> RiskPolicy:
    settings = body.risk
    try:
        return RiskPolicy(
            mode=RiskMode(settings.mode),
            fixed_risk=None if settings.fixed_risk is None else Decimal(settings.fixed_risk),
            risk_ratio=None if settings.risk_ratio is None else Decimal(settings.risk_ratio),
            max_contracts=settings.max_contracts,
        )
    except (ArithmeticError, ValueError, RiskInputError) as error:
        raise _invalid("RISK_CONFIGURATION_INVALID", str(error)) from error


def _simulation(body: CreateBacktestRunBody) -> SimulationPolicy:
    policy = body.simulation
    try:
        return SimulationPolicy(
            rules_version=policy.rules_version,
            same_bar=SameBarPolicy(policy.same_bar),
            slippage=SlippagePolicy(
                mode=SlippageMode(policy.slippage_mode),
                points=None if policy.slippage_points is None else Decimal(policy.slippage_points),
            ),
            fees=FeePolicy(
                mode=FeeMode(policy.fee_mode),
                per_unit=None if policy.fee_per_unit is None else Decimal(policy.fee_per_unit),
            ),
        )
    except (ArithmeticError, ValueError, SimulationPolicyError) as error:
        raise _invalid("SIMULATION_POLICY_INVALID", str(error)) from error


def _invalid(code: str, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "kind": "INVALID", "detail": detail},
    )
