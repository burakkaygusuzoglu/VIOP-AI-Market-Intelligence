"""Paper-trading routes (Phase 9). Simulation only.

No route here places, routes or transmits an order. ``POST /positions`` records a
plan for a simulated trade; ``POST /observations`` feeds it closed historical
bars a person uploaded; the rest are requests about that simulation.

## Status codes

* ``201`` a position was created; ``200`` the same idempotency key and payload
  were already used, and the existing position is returned unchanged.
* ``422`` the request is malformed, or the simulation refused it (risk veto,
  unverified product, invalid transition, bar out of order) - with a typed code.
* ``404`` no such position. ``409`` idempotency-key reuse with a different
  payload, or a concurrent modification. ``413`` an upload over its limit.
* ``503`` no stored position could be read back exactly.

Every refusal is a system or simulation statement, never a market opinion.
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
from app.api.routes.analysis import get_candle_parser
from app.api.schemas.paper import (
    CreatePaperPositionBody,
    ObservationsBody,
    PaperEventPageResponse,
    PaperPositionListResponse,
    PaperPositionResponse,
)
from app.api.schemas.paper_projection import detail, stored_event_response, summary
from app.application.paper.service import (
    CreatePaperPosition,
    PaperErrorKind,
    PaperServiceError,
    PaperTradingService,
)
from app.application.ports.market_data import CandleTextParser
from app.application.ports.paper import (
    PaperStore,
    PaperStoreUnavailableError,
    ProductResolver,
    ProductSnapshotCodec,
)
from app.application.ports.system import ClockPort
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

router = APIRouter(prefix="/paper/positions", tags=["paper"])

PositionId = Annotated[str, Path(pattern=r"^PP-[0-9a-f]{24}$")]

_STATUS: dict[PaperErrorKind, int] = {
    PaperErrorKind.INVALID: status.HTTP_422_UNPROCESSABLE_CONTENT,
    PaperErrorKind.REFUSED: status.HTTP_422_UNPROCESSABLE_CONTENT,
    PaperErrorKind.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    PaperErrorKind.CONFLICT: status.HTTP_409_CONFLICT,
    PaperErrorKind.TOO_LARGE: status.HTTP_413_CONTENT_TOO_LARGE,
    PaperErrorKind.UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def get_product_resolver(request: Request) -> ProductResolver | None:
    """The product resolver the composition root provided, or ``None``.

    ``None`` is this deployment's answer: no verified contract metadata provider
    is composed, so no paper position can be opened. That is a refusal, not a
    fallback to assumed specifications.
    """
    resolver = getattr(request.app.state, "product_resolver", None)
    return resolver if isinstance(resolver, ProductResolver) else None


def get_paper_service(
    request: Request,
    resolver: Annotated[ProductResolver | None, Depends(get_product_resolver)],
    parser: Annotated[CandleTextParser, Depends(get_candle_parser)],
    clock: Annotated[ClockPort, Depends(get_clock)],
) -> PaperTradingService:
    store: PaperStore = request.app.state.paper_store
    codec: ProductSnapshotCodec = request.app.state.product_codec
    return PaperTradingService(
        store=store, codec=codec, resolver=resolver, parser=parser, clock=clock
    )


Service = Annotated[PaperTradingService, Depends(get_paper_service)]


@router.post("", response_model=PaperPositionResponse, status_code=status.HTTP_201_CREATED)
async def create_position(
    body: CreatePaperPositionBody,
    service: Service,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
) -> PaperPositionResponse:
    """Record a plan for a simulated trade. Never places an order."""
    command = _command(body, idempotency_key)
    view = await _run(service.create(command))
    if view.replayed:
        response.status_code = status.HTTP_200_OK
    return detail(view.stored, replayed=view.replayed)


@router.get("", response_model=PaperPositionListResponse)
async def list_positions(
    service: Service,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> PaperPositionListResponse:
    page = await _run(service.list(offset=offset, limit=limit))
    return PaperPositionListResponse(
        items=[summary(item) for item in page.items],
        total=page.total,
        offset=page.offset,
        limit=page.limit,
    )


@router.get("/{position_id}", response_model=PaperPositionResponse)
async def get_position(position_id: PositionId, service: Service) -> PaperPositionResponse:
    view = await _run(service.get(position_id))
    return detail(view.stored)


@router.get("/{position_id}/events", response_model=PaperEventPageResponse)
async def list_events(
    position_id: PositionId,
    service: Service,
    after_sequence: Annotated[int, Query(ge=0, le=10_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PaperEventPageResponse:
    page = await _run(service.events(position_id, after_sequence=after_sequence, limit=limit))
    items = [stored_event_response(item) for item in page.items]
    last = items[-1].sequence if items else None
    more = last is not None and last < page.total
    return PaperEventPageResponse(
        position_id=position_id,
        items=items,
        total=page.total,
        after_sequence=page.after_sequence,
        limit=page.limit,
        next_after_sequence=last if more else None,
    )


@router.post("/{position_id}/observations", response_model=PaperPositionResponse)
async def add_observations(
    position_id: PositionId, body: ObservationsBody, service: Service
) -> PaperPositionResponse:
    """Apply closed historical bars, in order. The whole upload applies or none of it does."""
    view = await _run(service.observe(position_id, body.content, body.source_name))
    return detail(view.stored)


@router.post("/{position_id}/close", response_model=PaperPositionResponse)
async def request_close(position_id: PositionId, service: Service) -> PaperPositionResponse:
    """Ask for the remaining units to exit at the next bar's open."""
    view = await _run(service.request_close(position_id))
    return detail(view.stored)


@router.post("/{position_id}/stop/breakeven", response_model=PaperPositionResponse)
async def move_stop_to_breakeven(
    position_id: PositionId, service: Service
) -> PaperPositionResponse:
    view = await _run(service.move_stop_to_breakeven(position_id))
    return detail(view.stored)


@router.post("/{position_id}/cancel", response_model=PaperPositionResponse)
async def cancel_position(position_id: PositionId, service: Service) -> PaperPositionResponse:
    """Cancel a position whose entry has not filled."""
    view = await _run(service.cancel(position_id))
    return detail(view.stored)


# ----------------------------------------------------------------------


async def _run[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except PaperServiceError as error:
        raise HTTPException(
            status_code=_STATUS[error.kind],
            detail={"code": error.code, "kind": error.kind.value, "detail": error.detail},
        ) from error
    except PaperStoreUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PAPER_STORE_UNAVAILABLE",
                "kind": "UNAVAILABLE",
                "detail": "paper trading is temporarily unavailable; nothing was recorded",
            },
        ) from error


def _command(body: CreatePaperPositionBody, idempotency_key: str) -> CreatePaperPosition:
    try:
        simulation = body.simulation
        policy = SimulationPolicy(
            rules_version=simulation.rules_version,
            same_bar=SameBarPolicy(simulation.same_bar),
            slippage=SlippagePolicy(
                SlippageMode(simulation.slippage_mode), _opt(simulation.slippage_points)
            ),
            fees=FeePolicy(FeeMode(simulation.fee_mode), _opt(simulation.fee_per_unit)),
        )
        risk = RiskPolicy(
            mode=RiskMode(body.risk.mode),
            fixed_risk=_opt(body.risk.fixed_risk),
            risk_ratio=_opt(body.risk.risk_ratio),
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
    return CreatePaperPosition(
        idempotency_key=idempotency_key,
        symbol=body.symbol,
        direction=Direction(body.direction),
        quantity=body.quantity,
        intended_entry=Decimal(body.intended_entry),
        stop=Decimal(body.stop),
        targets=tuple(TargetSpec(Decimal(t.price), t.quantity) for t in body.targets),
        timeframe=Timeframe(body.timeframe),
        decision_time=body.decision_time,
        account=account,
        risk=risk,
        policy=policy,
        note=body.note,
    )


def _opt(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)
