"""Deterministic interactive replay routes (Phase 11). Simulation only.

A replay session walks through historical data a person uploaded, one candle at
a time, and asks the engines that already exist what that moment looked like.
No route here reaches a live feed, a broker or an order.

## The server owns the clock

Progression is a server decision. ``POST .../advance`` says *how far*, never
*where to*: there is no way to submit a replay time, a cursor, a revealed count
or a candle, and no request body carries one. Every number a replay shows was
computed as if the session's ``replay_as_of`` were now, and nothing whose
coverage ends after that moment is loaded, let alone returned.

## Nothing here computes anything

* analysis - Phase 8's ``run_analysis``, over the revealed prefix;
* paper trading - Phase 9's service, with a clock reporting replay time;
* performance - Phase 10's engine, narrowed to this session's positions.

## Status codes

* ``201`` a session was created; ``200`` the same idempotency key and request
  were already used, and that session is returned unchanged.
* ``422`` malformed input, an unusable dataset, or an engine's refusal - with a
  typed code. ``404`` no such session, or a position this session did not open.
* ``409`` key reuse with a different request, or a concurrent step.
* ``413`` an upload or an advance over its bound.
* ``503`` the store is unreachable, or a stored row disagreed with its ledger.
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime
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
from app.api.routes.analysis import (
    get_candle_parser,
    get_contract_metadata,
    get_synthesis_settings,
    get_synthesizer,
    synthesise,
)
from app.api.routes.paper import get_product_resolver
from app.api.routes.performance import get_performance_service
from app.api.schemas.analysis_projection import project
from app.api.schemas.paper import PaperPositionResponse
from app.api.schemas.paper_projection import detail
from app.api.schemas.performance_projection import performance as performance_response
from app.api.schemas.replay import (
    AdvanceReplayBody,
    CreateReplaySessionBody,
    OpenReplayPositionBody,
    ReplayAnalysisBody,
    ReplayAnalysisResponse,
    ReplayPerformanceResponse,
    ReplaySessionListResponse,
    ReplaySessionResponse,
    ReplayStepResponse,
)
from app.api.schemas.replay_projection import session as session_response
from app.api.schemas.replay_projection import session_list
from app.api.schemas.replay_projection import step as step_response
from app.application.performance.service import PerformanceService
from app.application.ports.contract_metadata import ContractMetadataProvider
from app.application.ports.market_data import CandleTextParser
from app.application.ports.paper import PaperStore, ProductResolver, ProductSnapshotCodec
from app.application.ports.synthesis import MarketSynthesisProvider
from app.application.ports.system import ClockPort
from app.application.replay.ports import ReplayStore
from app.application.replay.service import (
    CreateReplaySession,
    ReplayClock,
    ReplayErrorKind,
    ReplayService,
    ReplayServiceError,
    TimeframeUpload,
)
from app.application.synthesis.use_case import SynthesisSettings
from app.domain.common.enums import Direction, Timeframe
from app.domain.paper import (
    FeeMode,
    FeePolicy,
    SameBarPolicy,
    SimulationPolicy,
    SimulationPolicyError,
    SlippageMode,
    SlippagePolicy,
    TargetSpec,
)
from app.domain.risk.sizing import AccountState, RiskInputError, RiskMode, RiskPolicy

router = APIRouter(prefix="/replay/sessions", tags=["replay"])

SessionId = Annotated[str, Path(pattern=r"^RS-[0-9a-f]{24}$")]
PositionId = Annotated[str, Path(pattern=r"^PP-[0-9a-f]{24}$")]

_STATUS: dict[ReplayErrorKind, int] = {
    ReplayErrorKind.INVALID: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ReplayErrorKind.REFUSED: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ReplayErrorKind.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ReplayErrorKind.CONFLICT: status.HTTP_409_CONFLICT,
    ReplayErrorKind.TOO_LARGE: status.HTTP_413_CONTENT_TOO_LARGE,
    ReplayErrorKind.UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
}

_TIMEFRAMES = {
    "1D": Timeframe.D1,
    "1H": Timeframe.H1,
    "15M": Timeframe.M15,
    "5M": Timeframe.M5,
}


def get_replay_service(
    request: Request,
    resolver: Annotated[ProductResolver | None, Depends(get_product_resolver)],
    parser: Annotated[CandleTextParser, Depends(get_candle_parser)],
    clock: Annotated[ClockPort, Depends(get_clock)],
    performance: Annotated[PerformanceService, Depends(get_performance_service)],
) -> ReplayService:
    """Assemble the replay service from what the composition root wired.

    The clock here is the *process* clock, used only for audit stamps - when a
    session was created, when a link was recorded. Every market-facing use of
    time inside the service comes from the session's cursor instead.
    """
    store: ReplayStore = request.app.state.replay_store
    paper_store: PaperStore = request.app.state.paper_store
    codec: ProductSnapshotCodec = request.app.state.product_codec
    return ReplayService(
        store=store,
        paper_store=paper_store,
        codec=codec,
        resolver=resolver,
        parser=parser,
        performance=performance,
        clock=clock,
    )


Service = Annotated[ReplayService, Depends(get_replay_service)]
ChartTimeframe = Annotated[str | None, Query(pattern=r"^(5M|15M|1H|1D)$")]


@router.post("", response_model=ReplaySessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateReplaySessionBody,
    service: Service,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> ReplaySessionResponse:
    """Ingest historical data and open a replay over it."""
    command = CreateReplaySession(
        idempotency_key=idempotency_key,
        symbol=body.symbol,
        driver=_TIMEFRAMES[body.driver_timeframe],
        replay_start=body.replay_start,
        datasets=tuple(
            TimeframeUpload(
                timeframe=_TIMEFRAMES[item.timeframe],
                content=item.content,
                source_name=item.source_name,
            )
            for item in body.datasets
        ),
    )
    stored, replayed = await _run(service.create(command))
    if replayed:
        response.status_code = status.HTTP_200_OK
    view = await _run(service.get(stored.session_id))
    return session_response(view, service.limits, replayed=replayed)


@router.get("", response_model=ReplaySessionListResponse)
async def list_sessions(
    service: Service,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=25)] = 20,
) -> ReplaySessionListResponse:
    items, total = await _run(service.list(offset=offset, limit=limit))
    return session_list(items, total, offset=offset, limit=limit)


@router.get("/{session_id}", response_model=ReplaySessionResponse)
async def get_session(
    session_id: SessionId, service: Service, chart: ChartTimeframe = None
) -> ReplaySessionResponse:
    """The session as it stands, with candles for one timeframe's chart.

    ``chart`` defaults to the driver timeframe. Only that timeframe carries
    candles; the rest report their counts, so one screen is one bounded read.
    """
    view = await _run(service.get(session_id, chart=_chart(chart)))
    return session_response(view, service.limits)


@router.post("/{session_id}/advance", response_model=ReplayStepResponse)
async def advance_session(
    session_id: SessionId,
    body: AdvanceReplayBody,
    service: Service,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=16, max_length=128)
    ] = None,
    chart: ChartTimeframe = None,
) -> ReplayStepResponse:
    """Reveal the next ``steps`` driver candles, in order.

    An advance of N is exactly N single steps: each boundary it crosses is fed
    to this session's open paper positions before the next one is. Retrying the
    same ``Idempotency-Key`` returns the cursor that command already produced
    rather than stepping again.
    """
    result = await _run(
        service.step(
            session_id,
            steps=body.steps,
            command_key=idempotency_key,
            expected_version=body.expected_version,
            chart=_chart(chart),
        )
    )
    return step_response(result, service.limits)


@router.post("/{session_id}/analysis", response_model=ReplayAnalysisResponse)
async def analyse_session(
    session_id: SessionId,
    body: ReplayAnalysisBody,
    service: Service,
    contracts: Annotated[ContractMetadataProvider | None, Depends(get_contract_metadata)],
    synthesizer: Annotated[MarketSynthesisProvider | None, Depends(get_synthesizer)],
    synthesis_settings: Annotated[SynthesisSettings, Depends(get_synthesis_settings)],
) -> ReplayAnalysisResponse:
    """Run the existing analysis over exactly what is revealed right now.

    User-triggered, always: no step, no page load and no timer runs an analysis,
    and therefore none of them can run a synthesis either.
    """
    account, risk = _account_and_risk(body)
    session, outcome = await _run(
        service.analyse(
            session_id,
            account=account,
            risk_policy=risk,
            entry_price=_decimal(body.entry_price),
            stop_price=_decimal(body.stop_price),
            contracts=contracts,
        )
    )
    as_of = session.cursor.as_of
    synthesis = await synthesise(outcome, synthesizer, synthesis_settings, ReplayClock(as_of))
    return ReplayAnalysisResponse(
        session_id=session.session_id,
        replay_as_of=_time(as_of),
        analysis=project(outcome, synthesis),
    )


@router.post(
    "/{session_id}/positions",
    response_model=PaperPositionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def open_position(
    session_id: SessionId,
    body: OpenReplayPositionBody,
    service: Service,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> PaperPositionResponse:
    """Record a simulated position decided at *replay* time. Never an order.

    The symbol, the timeframe and the decision time come from the session. A
    client cannot backdate a decision, because there is no field for one.
    """
    policy, risk, account = _simulation(body)
    _session, stored = await _run(
        service.open_paper_position(
            session_id,
            idempotency_key=idempotency_key,
            direction=Direction(body.direction),
            quantity=body.quantity,
            intended_entry=Decimal(body.intended_entry),
            stop=Decimal(body.stop),
            targets=tuple(TargetSpec(Decimal(item.price), item.quantity) for item in body.targets),
            account=account,
            risk=risk,
            policy=policy,
            note=body.note,
        )
    )
    return detail(stored)


@router.get("/{session_id}/positions", response_model=list[PaperPositionResponse])
async def list_positions(session_id: SessionId, service: Service) -> list[PaperPositionResponse]:
    """This session's simulated positions, marked at the current replay time."""
    return [detail(item) for item in await _run(service.positions(session_id))]


@router.post("/{session_id}/positions/{position_id}/close", response_model=PaperPositionResponse)
async def close_position(
    session_id: SessionId, position_id: PositionId, service: Service
) -> PaperPositionResponse:
    """Ask for the remaining units to exit at the next revealed bar's open."""
    return detail(await _run(service.close_position(session_id, position_id)))


@router.post(
    "/{session_id}/positions/{position_id}/stop/breakeven",
    response_model=PaperPositionResponse,
)
async def move_stop_to_breakeven(
    session_id: SessionId, position_id: PositionId, service: Service
) -> PaperPositionResponse:
    return detail(await _run(service.move_stop_to_breakeven(session_id, position_id)))


@router.post("/{session_id}/positions/{position_id}/cancel", response_model=PaperPositionResponse)
async def cancel_position(
    session_id: SessionId, position_id: PositionId, service: Service
) -> PaperPositionResponse:
    """Cancel a replay position whose entry has not filled."""
    return detail(await _run(service.cancel_position(session_id, position_id)))


@router.get("/{session_id}/performance", response_model=ReplayPerformanceResponse)
async def read_performance(session_id: SessionId, service: Service) -> ReplayPerformanceResponse:
    """Phase 10's metrics over exactly the positions this session opened."""
    session, position_ids, result = await _run(service.performance(session_id))
    return ReplayPerformanceResponse(
        session_id=session.session_id,
        replay_as_of=_time(session.cursor.as_of),
        position_ids=list(position_ids),
        performance=performance_response(result),
    )


# ----------------------------------------------------------------------


async def _run[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except ReplayServiceError as error:
        raise HTTPException(
            status_code=_STATUS[error.kind],
            detail={"code": error.code, "kind": error.kind.value, "detail": error.detail},
        ) from error


def _chart(value: str | None) -> Timeframe | None:
    return None if value is None else _TIMEFRAMES[value]


def _time(value: datetime) -> str:
    return value.isoformat()


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _account_and_risk(body: ReplayAnalysisBody) -> tuple[AccountState | None, RiskPolicy | None]:
    try:
        account = (
            None
            if body.account is None
            else AccountState(
                equity=Decimal(body.account.equity),
                used_margin=Decimal(body.account.used_margin),
            )
        )
        risk = (
            None
            if body.risk is None
            else RiskPolicy(
                mode=RiskMode(body.risk.mode),
                fixed_risk=_decimal(body.risk.fixed_risk),
                risk_ratio=_decimal(body.risk.risk_ratio),
                max_contracts=body.risk.max_contracts,
            )
        )
    except (RiskInputError, ValueError, ArithmeticError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_RISK_SETTINGS", "kind": "INVALID", "detail": str(error)},
        ) from error
    return account, risk


def _simulation(
    body: OpenReplayPositionBody,
) -> tuple[SimulationPolicy, RiskPolicy, AccountState]:
    try:
        policy = SimulationPolicy(
            rules_version=body.simulation.rules_version,
            same_bar=SameBarPolicy(body.simulation.same_bar),
            slippage=SlippagePolicy(
                SlippageMode(body.simulation.slippage_mode),
                _decimal(body.simulation.slippage_points),
            ),
            fees=FeePolicy(
                FeeMode(body.simulation.fee_mode), _decimal(body.simulation.fee_per_unit)
            ),
        )
        risk = RiskPolicy(
            mode=RiskMode(body.risk.mode),
            fixed_risk=_decimal(body.risk.fixed_risk),
            risk_ratio=_decimal(body.risk.risk_ratio),
            max_contracts=body.risk.max_contracts,
        )
        account = AccountState(
            equity=Decimal(body.account.equity), used_margin=Decimal(body.account.used_margin)
        )
    except (SimulationPolicyError, RiskInputError, ValueError, ArithmeticError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_POSITION", "kind": "INVALID", "detail": str(error)},
        ) from error
    return policy, risk, account
