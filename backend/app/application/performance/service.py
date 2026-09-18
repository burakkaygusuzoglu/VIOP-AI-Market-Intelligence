"""Performance and journal use cases.

Three responsibilities live here and nothing else: bound the work a request can
ask for, hand authoritative records to the pure engine, and keep a person's
journal writing separate from the financial record it is attached to.

No metric is computed in this module. No amount is adjusted. If a number is
wrong, it is wrong in the ledger or in the engine, and both are tested
independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from app.application.performance.ports import (
    JournalConflictError,
    JournalRow,
    JournalStore,
    JournalStoreUnavailableError,
    OutcomeFilters,
    OutcomePage,
    PerformanceSource,
    PerformanceSourceUnavailableError,
    UnknownPositionError,
)
from app.application.ports.system import ClockPort
from app.domain.journal import JournalAnnotation, JournalInputError, clean_note, clean_tags
from app.domain.performance import (
    GroupPerformance,
    PerformanceSummary,
    by_direction,
    by_instrument,
    by_timeframe,
    summarise,
)


@dataclass(frozen=True, slots=True)
class PerformanceLimits:
    """Project decisions about how much work one request may ask for."""

    max_positions_per_analysis: int = 2_000
    """Measured, not guessed: 1 000 completed positions summarise in well under
    a second, and the ledger read is bounded by event type rather than by bars.
    A range beyond this is refused with its size, not silently truncated."""

    max_timeline_points: int = 2_000
    """One point per completed position, so this follows the analysis bound."""

    max_journal_page: int = 50
    max_breakdown_rows: int = 100
    max_tag_rows: int = 100


@unique
class PerformanceErrorKind(StrEnum):
    INVALID = "INVALID"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    TOO_LARGE = "TOO_LARGE"
    UNAVAILABLE = "UNAVAILABLE"


class PerformanceServiceError(Exception):
    """A typed failure the API maps to a status code."""

    def __init__(self, kind: PerformanceErrorKind, code: str, detail: str) -> None:
        self.kind = kind
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class PerformanceView:
    summary: PerformanceSummary
    filters: OutcomeFilters
    total_matching: int


@dataclass(frozen=True, slots=True)
class GroupSet:
    """One breakdown, and whether it is the whole of it.

    A financial breakdown that quietly shows the first hundred rows reads as a
    complete answer. So the rows travel with their own count: when more groups
    exist than are returned, ``is_complete`` is false and ``omitted`` says how
    many are missing - while the headline totals continue to describe the whole
    population, not the displayed rows.
    """

    rows: tuple[GroupPerformance, ...]
    total: int
    is_complete: bool

    @property
    def omitted(self) -> int:
        return max(0, self.total - len(self.rows))


@dataclass(frozen=True, slots=True)
class BreakdownView:
    by_direction: GroupSet
    by_instrument: GroupSet
    by_timeframe: GroupSet
    filters: OutcomeFilters
    total_matching: int


@dataclass(frozen=True, slots=True)
class JournalPage:
    rows: tuple[JournalRow, ...]
    total: int
    offset: int
    limit: int


class PerformanceService:
    def __init__(
        self,
        *,
        source: PerformanceSource,
        journal: JournalStore,
        clock: ClockPort,
        limits: PerformanceLimits | None = None,
    ) -> None:
        self._source = source
        self._journal = journal
        self._clock = clock
        self._limits = limits or PerformanceLimits()

    @property
    def limits(self) -> PerformanceLimits:
        return self._limits

    # -- analytics ------------------------------------------------------

    async def summary(self, filters: OutcomeFilters) -> PerformanceView:
        page = await self._authoritative(filters)
        return PerformanceView(
            summary=summarise(
                page.records,
                fill_count=page.fill_count,
                start=filters.closed_from,
                end=filters.closed_to,
            ),
            filters=filters,
            total_matching=page.total_matching,
        )

    async def breakdowns(self, filters: OutcomeFilters) -> BreakdownView:
        """The same records, grouped. Identical filters, identical population."""
        page = await self._authoritative(filters)
        bound = self._limits.max_breakdown_rows
        return BreakdownView(
            by_direction=_bounded(by_direction(page.records), bound),
            by_instrument=_bounded(by_instrument(page.records), bound),
            by_timeframe=_bounded(by_timeframe(page.records), bound),
            filters=filters,
            total_matching=page.total_matching,
        )

    async def _authoritative(self, filters: OutcomeFilters) -> OutcomePage:
        """Every matching position, or a refusal to answer at all.

        The whole population is loaded deliberately: an aggregate over the first
        N positions of a larger range is not that range's aggregate, so an
        oversized request is refused instead of answered approximately.
        """
        _validate_range(filters)
        try:
            matching = await self._source.count_matching(filters)
            if matching > self._limits.max_positions_per_analysis:
                raise PerformanceServiceError(
                    PerformanceErrorKind.TOO_LARGE,
                    "ANALYSIS_RANGE_TOO_LARGE",
                    (
                        f"{matching} positions match; at most "
                        f"{self._limits.max_positions_per_analysis} are analysed in one request. "
                        "Narrow the date range or the filters - a partial answer would not be "
                        "this period's performance"
                    ),
                )
            return await self._source.outcomes(
                filters, limit=self._limits.max_positions_per_analysis
            )
        except PerformanceSourceUnavailableError as error:
            raise PerformanceServiceError(
                PerformanceErrorKind.UNAVAILABLE, "PERFORMANCE_SOURCE_UNAVAILABLE", str(error)
            ) from error

    # -- journal --------------------------------------------------------

    async def journal_page(
        self, filters: OutcomeFilters, *, offset: int, limit: int
    ) -> JournalPage:
        _validate_range(filters)
        bounded = max(1, min(limit, self._limits.max_journal_page))
        start = max(0, offset)
        try:
            rows, total = await self._source.journal_rows(filters, offset=start, limit=bounded)
        except PerformanceSourceUnavailableError as error:
            raise PerformanceServiceError(
                PerformanceErrorKind.UNAVAILABLE, "PERFORMANCE_SOURCE_UNAVAILABLE", str(error)
            ) from error
        return JournalPage(rows=rows, total=total, offset=start, limit=bounded)

    async def annotation(self, position_id: str) -> JournalAnnotation:
        try:
            if not await self._journal.position_exists(position_id):
                raise PerformanceServiceError(
                    PerformanceErrorKind.NOT_FOUND,
                    "POSITION_NOT_FOUND",
                    f"no paper position {position_id}",
                )
            stored = await self._journal.get(position_id)
        except JournalStoreUnavailableError as error:
            raise PerformanceServiceError(
                PerformanceErrorKind.UNAVAILABLE, "JOURNAL_STORE_UNAVAILABLE", str(error)
            ) from error
        return stored or JournalAnnotation(position_id=position_id)

    async def write_annotation(
        self,
        position_id: str,
        *,
        note: str | None,
        tags: list[str] | None,
        expected_version: int,
    ) -> JournalAnnotation:
        """Replace the note and tags of one position, if nobody else has.

        Only these two fields move. The paper ledger, its projection, the risk
        approval and the product snapshot are not reachable from here at all.
        """
        try:
            cleaned_note = clean_note(note)
            cleaned_tags = clean_tags(tags)
        except JournalInputError as error:
            raise PerformanceServiceError(
                PerformanceErrorKind.INVALID, "JOURNAL_CONTENT_INVALID", str(error)
            ) from error
        if expected_version < 0:
            raise PerformanceServiceError(
                PerformanceErrorKind.INVALID,
                "JOURNAL_VERSION_INVALID",
                "the expected version cannot be negative",
            )
        try:
            if not await self._journal.position_exists(position_id):
                raise PerformanceServiceError(
                    PerformanceErrorKind.NOT_FOUND,
                    "POSITION_NOT_FOUND",
                    f"no paper position {position_id}",
                )
            annotation = JournalAnnotation(
                position_id=position_id,
                note=cleaned_note,
                tags=cleaned_tags,
                version=expected_version + 1,
            )
            return await self._journal.save(
                annotation, expected_version=expected_version, now=self._clock.now()
            )
        except JournalConflictError as error:
            raise PerformanceServiceError(
                PerformanceErrorKind.CONFLICT,
                "JOURNAL_VERSION_CONFLICT",
                (
                    f"this note was version {error.actual} when the change arrived, not "
                    f"{error.expected}; someone else edited it. Re-read it and apply the "
                    "change again"
                ),
            ) from error
        except JournalStoreUnavailableError as error:
            raise PerformanceServiceError(
                PerformanceErrorKind.UNAVAILABLE, "JOURNAL_STORE_UNAVAILABLE", str(error)
            ) from error
        except UnknownPositionError as error:
            raise PerformanceServiceError(
                PerformanceErrorKind.NOT_FOUND, "POSITION_NOT_FOUND", str(error)
            ) from error

    async def tags_in_use(self) -> tuple[tuple[tuple[str, int], ...], bool]:
        """Tags in use, and whether that is all of them.

        One more than the bound is fetched, so a truncated list can be reported
        as truncated instead of looking complete.
        """
        try:
            found = await self._journal.tag_counts(limit=self._limits.max_tag_rows + 1)
            bound = self._limits.max_tag_rows
            return found[:bound], len(found) <= bound
        except JournalStoreUnavailableError as error:
            raise PerformanceServiceError(
                PerformanceErrorKind.UNAVAILABLE, "JOURNAL_STORE_UNAVAILABLE", str(error)
            ) from error


def _bounded(rows: tuple[GroupPerformance, ...], limit: int) -> GroupSet:
    """Keep at most ``limit`` rows, and say so when some were left out."""
    return GroupSet(rows=rows[:limit], total=len(rows), is_complete=len(rows) <= limit)


def _validate_range(filters: OutcomeFilters) -> None:
    """Both ends must be timezone-aware, and the range must run forwards."""
    for name, value in (("from", filters.closed_from), ("to", filters.closed_to)):
        if value is not None and value.tzinfo is None:
            raise PerformanceServiceError(
                PerformanceErrorKind.INVALID,
                "RANGE_NOT_TIMEZONE_AWARE",
                f"the {name} bound must carry a timezone; market time is never local time",
            )
    if (
        filters.closed_from is not None
        and filters.closed_to is not None
        and filters.closed_from > filters.closed_to
    ):
        raise PerformanceServiceError(
            PerformanceErrorKind.INVALID,
            "RANGE_REVERSED",
            "the from bound is later than the to bound",
        )
