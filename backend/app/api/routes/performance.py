"""Performance and journal routes (Phase 10). Simulation only.

Five surfaces, all over the same authoritative ledger:

* ``GET /paper/performance`` - the metrics for one filtered selection;
* ``GET /paper/performance/breakdowns`` - the same selection, grouped;
* ``GET /paper/journal`` - positions with their notes and tags;
* ``GET``/``PUT /paper/positions/{id}/journal`` - read and write one annotation;
* ``GET /paper/journal/tags`` - the tags in use.

The filters are parsed once, in one place, so the summary, the breakdowns, the
timeline and the journal list are always about the same population. A client can
send a note, some tags and the version it read; it cannot send a number.

## Status codes

* ``422`` malformed filters, a reversed range, a naive timestamp, or journal
  content over its bound. ``404`` no such position. ``409`` the annotation
  changed since it was read. ``413`` the selection is too large to analyse in
  one request - narrowed filters, never a partial answer. ``503`` the store is
  unreachable, or a position's stored row disagrees with its ledger.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from app.api.dependencies import get_clock
from app.api.schemas.performance import (
    BreakdownsResponse,
    JournalAnnotationResponse,
    JournalPageResponse,
    JournalUpdateBody,
    PerformanceResponse,
    TagListResponse,
)
from app.api.schemas.performance_projection import (
    annotation,
    breakdowns,
    journal_page,
    performance,
    tag_list,
)
from app.application.performance.ports import (
    JournalStore,
    OutcomeFilters,
    PerformanceSource,
)
from app.application.performance.service import (
    PerformanceErrorKind,
    PerformanceService,
    PerformanceServiceError,
)
from app.application.ports.system import ClockPort
from app.domain.common.enums import Direction, Timeframe
from app.domain.journal import JournalInputError, normalise_tag

router = APIRouter(prefix="/paper", tags=["performance"])

PositionId = Annotated[str, Path(pattern=r"^PP-[0-9a-f]{24}$")]

_STATUS: dict[PerformanceErrorKind, int] = {
    PerformanceErrorKind.INVALID: status.HTTP_422_UNPROCESSABLE_CONTENT,
    PerformanceErrorKind.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    PerformanceErrorKind.CONFLICT: status.HTTP_409_CONFLICT,
    PerformanceErrorKind.TOO_LARGE: status.HTTP_413_CONTENT_TOO_LARGE,
    PerformanceErrorKind.UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def get_performance_service(
    request: Request,
    clock: Annotated[ClockPort, Depends(get_clock)],
) -> PerformanceService:
    source: PerformanceSource = request.app.state.performance_source
    journal: JournalStore = request.app.state.journal_store
    return PerformanceService(source=source, journal=journal, clock=clock)


Service = Annotated[PerformanceService, Depends(get_performance_service)]


def _fail(error: PerformanceServiceError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS[error.kind],
        detail={"code": error.code, "kind": error.kind.value, "detail": error.detail},
    )


def parse_filters(
    closed_from: Annotated[datetime | None, Query(alias="from")] = None,
    closed_to: Annotated[datetime | None, Query(alias="to")] = None,
    direction: Annotated[Direction | None, Query()] = None,
    symbol: Annotated[str | None, Query(max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")] = None,
    timeframe: Annotated[Timeframe | None, Query()] = None,
    tag: Annotated[str | None, Query(max_length=32)] = None,
) -> OutcomeFilters:
    """One parser for every surface, so no two of them can disagree.

    A tag is normalised the same way it was when stored, otherwise "Breakout"
    would silently match nothing.
    """
    normalised_tag: str | None = None
    if tag is not None:
        try:
            normalised_tag = normalise_tag(tag)
        except JournalInputError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "TAG_INVALID", "kind": "INVALID", "detail": str(error)},
            ) from error
    return OutcomeFilters(
        closed_from=closed_from,
        closed_to=closed_to,
        direction=direction,
        symbol=symbol,
        timeframe=timeframe,
        tag=normalised_tag,
    )


Filters = Annotated[OutcomeFilters, Depends(parse_filters)]


@router.get("/performance", response_model=PerformanceResponse)
async def read_performance(service: Service, filters: Filters) -> PerformanceResponse:
    try:
        return performance(await service.summary(filters))
    except PerformanceServiceError as error:
        raise _fail(error) from error


@router.get("/performance/breakdowns", response_model=BreakdownsResponse)
async def read_breakdowns(service: Service, filters: Filters) -> BreakdownsResponse:
    try:
        return breakdowns(await service.breakdowns(filters))
    except PerformanceServiceError as error:
        raise _fail(error) from error


@router.get("/journal", response_model=JournalPageResponse)
async def read_journal(
    service: Service,
    filters: Filters,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> JournalPageResponse:
    try:
        page = await service.journal_page(filters, offset=offset, limit=limit)
    except PerformanceServiceError as error:
        raise _fail(error) from error
    return journal_page(page, filters)


@router.get("/journal/tags", response_model=TagListResponse)
async def read_tags(service: Service) -> TagListResponse:
    try:
        items, is_complete = await service.tags_in_use()
    except PerformanceServiceError as error:
        raise _fail(error) from error
    return tag_list(items, service.limits.max_tag_rows, is_complete=is_complete)


@router.get("/positions/{position_id}/journal", response_model=JournalAnnotationResponse)
async def read_annotation(service: Service, position_id: PositionId) -> JournalAnnotationResponse:
    try:
        return annotation(await service.annotation(position_id))
    except PerformanceServiceError as error:
        raise _fail(error) from error


@router.put("/positions/{position_id}/journal", response_model=JournalAnnotationResponse)
async def write_annotation(
    service: Service, position_id: PositionId, body: JournalUpdateBody
) -> JournalAnnotationResponse:
    """Replace this position's note and tags. Nothing financial is reachable."""
    try:
        saved = await service.write_annotation(
            position_id,
            note=body.note,
            tags=body.tags,
            expected_version=body.expected_version,
        )
    except PerformanceServiceError as error:
        raise _fail(error) from error
    return annotation(saved)
