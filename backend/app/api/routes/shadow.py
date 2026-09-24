"""Shadow Mode routes (Phase 14 Part 2A). Observation only.

A shadow run watches a live session - which plays a *stored historical
dataset* - and records what the registered rules decided, plus what price did
afterwards. No route here reaches an exchange, a broker, an order or a
position, and none creates a paper position or a backtest run.

## What a client may send

The id of a session that already exists, a registered strategy id and version,
which timeframes to watch, and optionally an account and risk policy for
sizing. Nothing else: there is no way to submit a decision, an outcome, a
price, a fill, contract metadata, an approval or a run status, because the
request models have no such fields and forbid extras.

## Scope: the same local boundary as Phase 13

Shadow is composed only where the live workspace is - `LIVE_SIMULATION_ENABLED`
with an `APP_ENV` of development or test. Production composes neither, and
every route here then answers ``SHADOW_DISABLED`` (503). It reuses the live
router's ``require_local_request`` dependency, so the Host and Origin checks
that protect Phase 13 protect this identically; Shadow adds no new door.

## Status codes

``201`` created · ``200`` read or cancelled · ``404`` no such run or session ·
``409`` capacity, an ended session, or an attempt key reused with different
rules · ``422`` malformed input or unregistered rules · ``503`` disabled, or
the journal is unreachable.
"""

from __future__ import annotations

from collections.abc import Awaitable
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from app.api.routes.live import require_local_request
from app.api.schemas.shadow import (
    RUN_ID_PATTERN,
    CreateShadowRunBody,
    ShadowCapabilityResponse,
    ShadowJournalPageResponse,
    ShadowOutcomePageResponse,
    ShadowRunListResponse,
    ShadowRunResponse,
)
from app.api.schemas.shadow_projection import (
    capability_response,
    journal_page,
    outcome_page,
    run_list,
    run_response,
)
from app.application.ports.system import ClockPort
from app.application.shadow.ports import ShadowStoreUnavailableError
from app.application.shadow.workspace import (
    ShadowCapability,
    ShadowWorkspace,
    ShadowWorkspaceError,
    WorkspaceErrorKind,
)
from app.domain.backtest.registry import SUPPORTED_STRATEGIES
from app.domain.common.enums import Timeframe
from app.domain.risk.sizing import (
    AccountState,
    RiskInputError,
    RiskMode,
    RiskPolicy,
)
from app.domain.shadow.run import ShadowLimits

router = APIRouter(prefix="/shadow", tags=["shadow"], dependencies=[Depends(require_local_request)])

RunId = Annotated[str, Path(pattern=RUN_ID_PATTERN)]

MAX_SEQUENCE = 2_147_483_647
"""The largest journal or outcome sequence the database can hold (a 32-bit
integer). A cursor beyond it is malformed input, refused here as a 422 - found
in Part 2B, where it reached PostgreSQL and came back as a false
"journal unreachable" 503."""

_DISABLED_DETAIL = (
    "Gölge modu kapalı. Yalnızca yerel geliştirme ortamında, LIVE_SIMULATION_ENABLED=true "
    "ile açıkça etkinleştirildiğinde çalışır; üretimde hiçbir zaman açılmaz."
)

_STATUS: dict[WorkspaceErrorKind, int] = {
    WorkspaceErrorKind.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    WorkspaceErrorKind.INVALID: status.HTTP_422_UNPROCESSABLE_CONTENT,
    WorkspaceErrorKind.CONFLICT: status.HTTP_409_CONFLICT,
    WorkspaceErrorKind.CAPACITY: status.HTTP_409_CONFLICT,
    WorkspaceErrorKind.UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
}

_TIMEFRAMES = {"5M": Timeframe.M5, "15M": Timeframe.M15, "1H": Timeframe.H1, "1D": Timeframe.D1}


def get_shadow_workspace(request: Request) -> ShadowWorkspace | None:
    workspace = getattr(request.app.state, "shadow_workspace", None)
    return workspace if isinstance(workspace, ShadowWorkspace) else None


OptionalShadow = Annotated[ShadowWorkspace | None, Depends(get_shadow_workspace)]


def _require(workspace: ShadowWorkspace | None) -> ShadowWorkspace:
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "SHADOW_DISABLED", "kind": "DISABLED", "detail": _DISABLED_DETAIL},
        )
    return workspace


@router.get("/capability", response_model=ShadowCapabilityResponse)
async def capability(request: Request, workspace: OptionalShadow) -> ShadowCapabilityResponse:
    """Whether shadow mode is composed here, and what it can honestly do."""
    clock = _clock(request)
    if workspace is None:
        # Disabled is an answer, not an error: a screen needs to say why
        # rather than show an empty workspace that looks broken.
        return capability_response(
            ShadowCapability(
                available=False,
                reason=_DISABLED_DETAIL,
                provenance="SIMULATED_HISTORICAL_STREAM",
                market_currency="HISTORICAL",
                financial_metadata=False,
                strategies={k: tuple(sorted(v)) for k, v in SUPPORTED_STRATEGIES.items()},
                limits=ShadowLimits(),
            ),
            server_time=clock.now(),
        )
    return capability_response(workspace.capability(), server_time=clock.now())


@router.post("/runs", response_model=ShadowRunResponse, status_code=status.HTTP_201_CREATED)
async def create_run(body: CreateShadowRunBody, workspace: OptionalShadow) -> ShadowRunResponse:
    account, risk = _account_and_risk(body)
    created = await _run(
        _require(workspace).create(
            session_id=body.session_id,
            strategy_id=body.strategy_id,
            strategy_version=body.strategy_version,
            driver=_TIMEFRAMES[body.driver],
            timeframes=tuple(_TIMEFRAMES[value] for value in body.timeframes),
            required=tuple(_TIMEFRAMES[value] for value in body.required),
            account=account,
            risk=risk,
            analysis_evidence=body.analysis_evidence,
            attempt_key=body.attempt_key,
        )
    )
    return run_response(created)


@router.get("/runs", response_model=ShadowRunListResponse)
async def list_runs(
    workspace: OptionalShadow,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ShadowRunListResponse:
    runs, total = await _run(_require(workspace).list(offset=offset, limit=limit))
    return run_list(runs, total)


@router.get("/runs/{run_id}", response_model=ShadowRunResponse)
async def get_run(run_id: RunId, workspace: OptionalShadow) -> ShadowRunResponse:
    return run_response(await _run(_require(workspace).get(run_id)))


@router.post("/runs/{run_id}/cancel", response_model=ShadowRunResponse)
async def cancel_run(run_id: RunId, workspace: OptionalShadow) -> ShadowRunResponse:
    """Stop observing. Everything already recorded stays recorded."""
    return run_response(await _run(_require(workspace).cancel(run_id)))


@router.get("/runs/{run_id}/journal", response_model=ShadowJournalPageResponse)
async def read_journal(
    run_id: RunId,
    request: Request,
    workspace: OptionalShadow,
    after: Annotated[int, Query(ge=0, le=MAX_SEQUENCE)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ShadowJournalPageResponse:
    """One bounded page of entries, each with its current development."""
    entries, total, developments = await _run(
        _require(workspace).journal(run_id, after=after, limit=limit)
    )
    return journal_page(
        run_id, entries, total, dict(developments), server_time=_clock(request).now()
    )


@router.get("/runs/{run_id}/outcomes", response_model=ShadowOutcomePageResponse)
async def read_outcomes(
    run_id: RunId,
    request: Request,
    workspace: OptionalShadow,
    after: Annotated[int, Query(ge=0, le=MAX_SEQUENCE)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ShadowOutcomePageResponse:
    """Every published development, including superseded ones, in order."""
    records, total = await _run(_require(workspace).outcomes(run_id, after=after, limit=limit))
    return outcome_page(run_id, records, total, server_time=_clock(request).now())


# ----------------------------------------------------------------------


def _clock(request: Request) -> ClockPort:
    return request.app.state.clock  # type: ignore[no-any-return]


async def _run[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except ShadowWorkspaceError as error:
        raise _error(error) from None
    except ShadowStoreUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SHADOW_STORE_UNAVAILABLE",
                "kind": "UNAVAILABLE",
                "detail": "the shadow journal is unreachable; this says nothing about the market",
            },
        ) from None


def _error(error: ShadowWorkspaceError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS.get(error.kind, status.HTTP_400_BAD_REQUEST),
        detail={"code": error.code, "kind": error.kind.value, "detail": error.detail},
    )


def _account_and_risk(
    body: CreateShadowRunBody,
) -> tuple[AccountState | None, RiskPolicy | None]:
    """Both or neither: a half-configured risk setup is refused, not guessed."""
    if (body.account is None) != (body.risk is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INCOMPLETE_RISK_CONFIGURATION",
                "kind": "REFUSED",
                "detail": "give both an account and a risk policy, or neither",
            },
        )
    if body.account is None or body.risk is None:
        return None, None
    try:
        account = AccountState(
            equity=Decimal(body.account.equity),
            used_margin=Decimal(body.account.used_margin),
        )
        risk = RiskPolicy(
            mode=RiskMode(body.risk.mode),
            fixed_risk=None if body.risk.fixed_risk is None else Decimal(body.risk.fixed_risk),
            risk_ratio=None if body.risk.risk_ratio is None else Decimal(body.risk.risk_ratio),
            max_contracts=body.risk.max_contracts,
        )
    except (RiskInputError, ValueError, ArithmeticError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_RISK_SETTINGS",
                "kind": "INVALID",
                "detail": "the account or risk settings are not valid",
            },
        ) from None
    return account, risk
