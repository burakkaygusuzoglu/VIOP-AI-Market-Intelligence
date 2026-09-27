"""Source status routes (Phase 15 Part 2B). Read-only, GET only.

What this deployment's external sources are, which contract facts have been
verified and from which documents, whether a session calendar can answer, and
the review history. Nothing here writes.

## Why there is no approval route

This application has no authentication. A reviewer name in a request body is
not an identity, a Host or Origin check is not an authorization, and a URL that
looks official is not a reviewed document. An HTTP route that created a
``VERIFIED_CURRENT_FACT`` would therefore let anyone who can reach the port
declare a multiplier. So reviews are written only by the local operator
command, :mod:`app.operator.fact_review`, which needs shell and database access
on the host; the HTTP surface can only read what that command journalled. No
route fetches a URL, reads a file path or echoes a stored credential.

## Status codes

``200`` answered · ``422`` malformed symbol, time or page · ``503`` the
verification journal is unreachable - which says nothing about the market.
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status

from app.api.schemas.sources import (
    SYMBOL_PATTERN,
    CalendarStatusResponse,
    MetadataStatusResponse,
    ReviewPageResponse,
    SourceCapabilitiesResponse,
)
from app.api.schemas.sources_projection import (
    calendar_status,
    capabilities,
    metadata_status,
    review_page,
)
from app.application.ports.fact_verification import (
    VerificationConflictError,
    VerificationStoreUnavailableError,
)
from app.application.ports.system import ClockPort
from app.application.sourcing.source_status import SourceStatusService

router = APIRouter(prefix="/sources", tags=["sources"])

Symbol = Annotated[str, Path(pattern=SYMBOL_PATTERN)]

MAX_SEQUENCE = 9_223_372_036_854_775_807
"""The largest journal sequence PostgreSQL can hold (a 64-bit identity)."""


def _service(request: Request) -> SourceStatusService:
    service = getattr(request.app.state, "source_status", None)
    if not isinstance(service, SourceStatusService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SOURCE_STATUS_NOT_COMPOSED",
                "kind": "UNAVAILABLE",
                "detail": "Kaynak durumu bu süreçte oluşturulmadı.",
            },
        )
    return service


def _now(request: Request) -> datetime:
    clock: ClockPort = request.app.state.clock
    return clock.now()


def _aware(name: str, moment: datetime | None) -> datetime | None:
    if moment is not None and moment.utcoffset() is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "NAIVE_TIMESTAMP",
                "kind": "INVALID",
                "detail": f"{name} bir saat dilimi içermelidir (ör. 2026-03-02T09:00:00Z).",
            },
        )
    return moment


async def _read[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except VerificationStoreUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SOURCE_JOURNAL_UNAVAILABLE",
                "kind": "UNAVAILABLE",
                "detail": "Doğrulama günlüğüne ulaşılamıyor; bu piyasa hakkında bir şey söylemez.",
            },
        ) from None
    except VerificationConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": error.code,
                "kind": "CONFLICT",
                "detail": "Doğrulama günlüğündeki bir kayıt inceleme sınırıyla artık uyuşmuyor.",
            },
        ) from None


@router.get("/capabilities", response_model=SourceCapabilitiesResponse)
async def read_capabilities(request: Request) -> SourceCapabilitiesResponse:
    """Per-category capability of what this process actually composed."""
    service = _service(request)
    rows, counts = await _read(service.capabilities())
    return capabilities(service.composition, rows, counts, server_time=_now(request))


@router.get("/metadata/{symbol}", response_model=MetadataStatusResponse)
async def read_metadata(
    symbol: Symbol,
    request: Request,
    applies_at: Annotated[datetime | None, Query()] = None,
    known_by: Annotated[datetime | None, Query()] = None,
    retrospective: Annotated[bool, Query()] = False,
) -> MetadataStatusResponse:
    """Verified facts for exactly this contract, as known at ``known_by``."""
    if retrospective and known_by is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "KNOWLEDGE_BOUNDARY_AMBIGUOUS",
                "kind": "INVALID",
                "detail": "Geriye dönük soru bir bilgi sınırı almaz; ikisinden birini seçin.",
            },
        )
    report = await _read(
        _service(request).metadata(
            symbol,
            applies_at=_aware("applies_at", applies_at),
            known_by=_aware("known_by", known_by),
            retrospective=retrospective,
        )
    )
    return metadata_status(report, server_time=_now(request))


@router.get("/calendar/{symbol}", response_model=CalendarStatusResponse)
async def read_calendar(
    symbol: Symbol,
    request: Request,
    at: Annotated[datetime | None, Query()] = None,
) -> CalendarStatusResponse:
    """Whether a verified calendar can say if ``at`` is in session."""
    service = _service(request)
    answer = await service.calendar(symbol, at=_aware("at", at))
    return calendar_status(
        answer,
        composed=service.composition.calendar_source_composed,
        server_time=_now(request),
    )


@router.get("/reviews", response_model=ReviewPageResponse)
async def read_reviews(
    request: Request,
    after: Annotated[int, Query(ge=0, le=MAX_SEQUENCE)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ReviewPageResponse:
    """One bounded page of submissions, each with its decision if it has one."""
    entries, total = await _read(_service(request).reviews(after=after, limit=limit))
    return review_page(entries, total, limit=limit, server_time=_now(request))
