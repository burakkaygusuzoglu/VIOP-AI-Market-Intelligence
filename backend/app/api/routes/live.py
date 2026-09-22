"""Live Intelligence routes (Phase 13 Part 2A). Simulated history only.

A live session plays a *stored historical dataset* through the Part 1
streaming machinery. The provenance of every response is
``SIMULATED_HISTORICAL_STREAM`` and its market currency ``HISTORICAL``: the
candles are real history that somebody uploaded, arriving now, and they are
never a current exchange quotation. No route here reaches an exchange, a
broker, an order or a position.

## Transport

REST for control and reads; Server-Sent Events for one-way updates. The
browser never sends market data, so a bidirectional socket would add a
surface with nothing to carry - and SSE passes through the existing nginx
``/api/`` proxy with buffering disabled by the ``X-Accel-Buffering`` header
this module sets, with no proxy change.

## Scope: local development, opted into

There is no authentication in this application, and sessions consume memory
and a task each. The workspace is composed only with
``LIVE_SIMULATION_ENABLED=true`` *and* ``APP_ENV`` of ``development`` or
``test``; production never composes it, and a missing variable leaves it off.
Everywhere else every route here answers ``LIVE_DISABLED`` (503) and the
capability endpoint says why. A public deployment needs authorisation and
per-user resource isolation first.

Where it is composed, every route also refuses a request whose ``Host`` is
not a loopback name or a configured origin's host (DNS rebinding), and every
state-changing request whose ``Origin`` is present and not a configured CORS
origin (cross-site requests a browser would send without a preflight). A
request with no ``Origin`` - a script, a test client - is not a browser
forgery and is allowed; this is a browser boundary, not authentication.

## Status codes

``201`` created · ``204`` removed · ``404`` no such session or source (sessions
do not survive a restart) · ``409`` capacity, or no timeframe available for an
analysis · ``422`` malformed input or an unusable source · ``429`` too many
creations · ``503`` disabled, or the dataset store is unreachable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated
from urllib.parse import urlsplit

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
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_clock
from app.api.schemas.live import (
    SESSION_ID_PATTERN,
    CreateLiveSessionBody,
    LiveAnalysisBody,
    LiveAnalysisResponse,
    LiveCapabilityResponse,
    LiveLimitsResponse,
    LiveSessionListResponse,
    LiveSessionResponse,
    LiveSourceListResponse,
    LiveTimelinePageResponse,
)
from app.api.schemas.live_projection import (
    analysis_response,
    envelope,
    heartbeat,
    session,
    session_list,
    source_list,
    sse_frame,
    timeline,
)
from app.application.live.catalog import PlaybackPace
from app.application.live.workspace import (
    LiveWorkspace,
    LiveWorkspaceError,
    NotificationKind,
    WorkspaceErrorKind,
)
from app.application.ports.system import ClockPort
from app.domain.common.enums import Timeframe
from app.domain.risk.sizing import AccountState, RiskInputError, RiskMode, RiskPolicy


@dataclass(frozen=True, slots=True)
class LocalRequestPolicy:
    hosts: frozenset[str]
    origins: frozenset[str]


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def local_request_policy(cors_origins: Iterable[str]) -> LocalRequestPolicy:
    """Loopback names plus the hosts of the configured CORS origins."""
    origins = frozenset(cors_origins)
    hosts = set(_LOOPBACK_HOSTS)
    for origin in origins:
        name = urlsplit(origin).hostname
        if name:
            hosts.add(name.lower())
    return LocalRequestPolicy(hosts=frozenset(hosts), origins=origins)


def _host_name(value: str) -> str:
    return (urlsplit(f"//{value}").hostname or "").lower()


def require_local_request(request: Request) -> None:
    """The browser boundary for a local, unauthenticated API. Not a login."""
    policy = getattr(request.app.state, "live_request_policy", None)
    if not isinstance(policy, LocalRequestPolicy):
        return  # nothing composed: every route answers LIVE_DISABLED
    if _host_name(request.headers.get("host", "")) not in policy.hosts:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "LIVE_HOST_REFUSED",
                "kind": "FORBIDDEN",
                "detail": "the live workspace answers only on this machine's own host names",
            },
        )
    origin = request.headers.get("origin")
    if request.method in _STATE_CHANGING and origin is not None and origin not in policy.origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "LIVE_ORIGIN_REFUSED",
                "kind": "FORBIDDEN",
                "detail": "a request from another site cannot change a live session",
            },
        )


router = APIRouter(prefix="/live", tags=["live"], dependencies=[Depends(require_local_request)])

SessionId = Annotated[str, Path(pattern=SESSION_ID_PATTERN)]

DEFAULT_HEARTBEAT_SECONDS = 15.0

_DISABLED_DETAIL = (
    "Canlı akış çalışma alanı kapalı. Yalnızca yerel geliştirme ortamında, açıkça "
    "LIVE_SIMULATION_ENABLED=true ile etkinleştirildiğinde açılır; üretimde hiçbir zaman "
    "açılmaz. Bu uygulamada kimlik doğrulama ve kullanıcı başına kaynak yalıtımı yok."
)
_AVAILABLE_DETAIL = (
    "Saklanan geçmiş veri kümeleri canlı akış altyapısından oynatılır. Borsaya bağlı "
    "değildir; fiyatlar güncel piyasa fiyatı değildir. Emir oluşturulmaz."
)

_STATUS: dict[WorkspaceErrorKind, int] = {
    WorkspaceErrorKind.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    WorkspaceErrorKind.INVALID: status.HTTP_422_UNPROCESSABLE_CONTENT,
    WorkspaceErrorKind.CAPACITY: status.HTTP_409_CONFLICT,
    WorkspaceErrorKind.RATE_LIMITED: status.HTTP_429_TOO_MANY_REQUESTS,
    WorkspaceErrorKind.UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    WorkspaceErrorKind.ANALYSIS_UNAVAILABLE: status.HTTP_409_CONFLICT,
}

_TIMEFRAMES = {"5M": Timeframe.M5, "15M": Timeframe.M15, "1H": Timeframe.H1, "1D": Timeframe.D1}


def get_live_workspace(request: Request) -> LiveWorkspace | None:
    """The workspace composed at startup, or ``None`` where it is disabled."""
    workspace = getattr(request.app.state, "live_workspace", None)
    return workspace if isinstance(workspace, LiveWorkspace) else None


OptionalWorkspace = Annotated[LiveWorkspace | None, Depends(get_live_workspace)]


def _require(workspace: LiveWorkspace | None) -> LiveWorkspace:
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "LIVE_DISABLED", "kind": "DISABLED", "detail": _DISABLED_DETAIL},
        )
    return workspace


def _heartbeat_seconds(request: Request) -> float:
    value = getattr(request.app.state, "live_heartbeat_seconds", DEFAULT_HEARTBEAT_SECONDS)
    return float(value)


@router.get("/capability", response_model=LiveCapabilityResponse)
async def capability(request: Request, workspace: OptionalWorkspace) -> LiveCapabilityResponse:
    """What this server can stream, and what it never claims."""
    paces = [pace.value for pace in PlaybackPace]
    if workspace is None:
        return LiveCapabilityResponse(
            state="DISABLED", detail=_DISABLED_DETAIL, paces=paces, limits=None
        )
    limits, live = workspace.limits, workspace.live_limits
    return LiveCapabilityResponse(
        state="AVAILABLE",
        detail=_AVAILABLE_DETAIL,
        paces=paces,
        limits=LiveLimitsResponse(
            max_sessions=limits.max_sessions,
            max_subscribers_per_session=limits.max_subscribers_per_session,
            max_subscribers_total=limits.max_subscribers_total,
            subscriber_queue=limits.subscriber_queue,
            timeline_retention=limits.timeline_retention,
            timeline_page_max=limits.timeline_page_max,
            max_session_seconds=limits.max_session_seconds,
            creations_per_minute=limits.creations_per_minute,
            max_concurrent_analyses=limits.max_concurrent_analyses,
            min_window_candles=limits.min_window_candles,
            max_window_candles=limits.max_window_candles,
            max_closed_candles=live.max_closed_candles,
            max_missing_sequences=live.max_missing_sequences,
            max_recorded_issues=live.max_recorded_issues,
            max_reconnects=live.max_reconnects,
            heartbeat_seconds=_heartbeat_seconds(request),
        ),
    )


@router.get("/sources", response_model=LiveSourceListResponse)
async def list_sources(
    workspace: OptionalWorkspace,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=25)] = 20,
) -> LiveSourceListResponse:
    """Stored historical datasets that could be played. No candles."""
    items, total = await _run(_require(workspace).sources(offset=offset, limit=limit))
    return source_list(items, total, offset=offset, limit=limit)


@router.post("/sessions", response_model=LiveSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateLiveSessionBody, workspace: OptionalWorkspace
) -> LiveSessionResponse:
    """Start playing a stored dataset. Opening the page never does this."""
    view = await _run(
        _require(workspace).create(
            source_id=body.source_id,
            timeframes=tuple(_TIMEFRAMES[code] for code in body.timeframes),
            window_candles=body.window_candles,
            pace=PlaybackPace(body.pace),
        )
    )
    return session(view)


@router.get("/sessions", response_model=LiveSessionListResponse)
async def list_sessions(workspace: OptionalWorkspace) -> LiveSessionListResponse:
    active = _require(workspace)
    return session_list(active.sessions(), active.limits.max_sessions)


@router.get("/sessions/{session_id}", response_model=LiveSessionResponse)
async def get_session(session_id: SessionId, workspace: OptionalWorkspace) -> LiveSessionResponse:
    """The authoritative snapshot. What a client resynchronises from."""
    return session(_call(lambda: _require(workspace).get(session_id)))


@router.post("/sessions/{session_id}/cancel", response_model=LiveSessionResponse)
async def cancel_session(
    session_id: SessionId, workspace: OptionalWorkspace
) -> LiveSessionResponse:
    """Stop the stream and keep the session for inspection. Repeatable."""
    return session(await _run(_require(workspace).cancel(session_id)))


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_session(session_id: SessionId, workspace: OptionalWorkspace) -> Response:
    """Stop the stream if needed and release the session's slot."""
    await _run(_require(workspace).remove(session_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions/{session_id}/timeline", response_model=LiveTimelinePageResponse)
async def read_timeline(
    session_id: SessionId,
    workspace: OptionalWorkspace,
    after: Annotated[int | None, Query(ge=0, le=1_000_000_000)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> LiveTimelinePageResponse:
    """A bounded page of what actually happened, in order."""
    active = _require(workspace)
    page = _call(lambda: active.timeline(session_id, after=after, limit=limit))
    return timeline(session_id, page, min(limit, active.limits.timeline_page_max))


@router.post("/sessions/{session_id}/analysis", response_model=LiveAnalysisResponse)
async def analyse_session(
    session_id: SessionId, body: LiveAnalysisBody, workspace: OptionalWorkspace
) -> LiveAnalysisResponse:
    """The existing analysis over confirmed, available candles. On request only.

    No event, heartbeat or page load runs this, and it never calls a language
    model. A second request while one runs waits for it and is served from
    the Part 1 cache when nothing changed.
    """
    account, risk = _account_and_risk(body)
    result = await _run(_require(workspace).analyse(session_id, account=account, risk_policy=risk))
    return analysis_response(result)


@router.get("/sessions/{session_id}/events")
async def stream_events(
    session_id: SessionId,
    request: Request,
    workspace: OptionalWorkspace,
    clock: Annotated[ClockPort, Depends(get_clock)],
    after: Annotated[int | None, Query(ge=0, le=1_000_000_000)] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", max_length=12)] = None,
) -> StreamingResponse:
    """Server-Sent Events for one session.

    ``after`` (or the browser's ``Last-Event-ID``) is the last cursor the
    client holds. What follows is: missed entries still retained, or a
    ``RESYNC_REQUIRED``; then the authoritative ``STATE``; then each new
    ``TIMELINE`` entry as it happens; ``HEARTBEAT`` when idle; ``END`` when the
    session ends, after which the response closes.

    A client disconnecting closes its own subscription and nothing else.
    """
    active = _require(workspace)
    cursor = after if after is not None else _cursor(last_event_id)
    subscriber = _call(lambda: active.subscribe(session_id, after=cursor))
    interval = _heartbeat_seconds(request)

    async def frames() -> AsyncIterator[str]:
        try:
            yield "retry: 3000\n\n"
            while True:
                notice = await subscriber.next(timeout=interval)
                if notice is None:
                    if subscriber.closed:
                        return
                    try:
                        current = active.poll(session_id)
                    except LiveWorkspaceError:
                        return
                    if len(subscriber):
                        continue  # polling revealed a change: deliver that first
                    yield sse_frame(heartbeat(session_id, current, clock.now()))
                    continue
                yield sse_frame(envelope(notice, clock.now()))
                if notice.kind is NotificationKind.END:
                    return
        finally:
            subscriber.close()

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ----------------------------------------------------------------------


def _cursor(value: str | None) -> int | None:
    if value is None or not value.isdigit():
        return None
    return int(value)


def _error(error: LiveWorkspaceError) -> HTTPException:
    detail: dict[str, object] = {
        "code": error.code,
        "kind": error.kind.value,
        "detail": error.detail,
    }
    if error.reasons:
        detail["reasons"] = {tf.value: list(items) for tf, items in error.reasons.items()}
    return HTTPException(status_code=_STATUS[error.kind], detail=detail)


async def _run[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except LiveWorkspaceError as error:
        raise _error(error) from None


def _call[T](function: Callable[[], T]) -> T:
    try:
        return function()
    except LiveWorkspaceError as error:
        raise _error(error) from None


def _account_and_risk(body: LiveAnalysisBody) -> tuple[AccountState | None, RiskPolicy | None]:
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
                fixed_risk=None if body.risk.fixed_risk is None else Decimal(body.risk.fixed_risk),
                risk_ratio=None if body.risk.risk_ratio is None else Decimal(body.risk.risk_ratio),
                max_contracts=body.risk.max_contracts,
            )
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
