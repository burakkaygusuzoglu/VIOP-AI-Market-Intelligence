"""Reading external intelligence through the domain's rules (Phase 15 Part 2A).

Each reader asks one port, bounds what comes back, and hands the records to
the deterministic domain function for its category. None of them is composed
into the application: no licensed source exists to put behind the ports, and
none of these answers is wired into a strategy, a risk decision or a Paper
action. They exist so that, when a source does exist, the rules it is read
under are already fixed and tested.

## Failure is "unknown", never "nothing"

A source that raises, or returns more than its bound, yields an explicit
unavailable answer - never an empty list that would read as "no news" or "no
open interest". Failures are logged by source name and exception type only: a
provider's message may carry a URL, an account id or a token. A bound is
enforced by refusal, not truncation.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import partial

from app.application.ports.intelligence import (
    BreadthSource,
    CalendarSource,
    ContractFactSource,
    NewsSource,
    OpenInterestSource,
)
from app.domain.common.verification import VerifiedValue
from app.domain.futures.contract import FuturesContract
from app.domain.sourcing.breadth import BreadthAnswer, BreadthStatus, breadth_at
from app.domain.sourcing.calendar import CalendarAnswer, answer_from, unavailable
from app.domain.sourcing.facts import (
    ContractSourceRecord,
    FactVerdict,
    FactVerdictCode,
    assess_fact_records,
)
from app.domain.sourcing.limits import ProviderLimits
from app.domain.sourcing.news import NewsArticle, news_visible_at
from app.domain.sourcing.open_interest import (
    OpenInterestAnswer,
    OpenInterestScope,
    OpenInterestStatus,
    open_interest_at,
)

__all__ = [
    "BreadthReader",
    "NewsReader",
    "NewsView",
    "OpenInterestReader",
    "RecordedSessionCalendar",
    "VerifiedContractFacts",
]

_LOG = logging.getLogger(__name__)


async def _fetch[T](
    source: str, call: Callable[[], Awaitable[Sequence[T]]], bound: int
) -> tuple[T, ...] | None:
    """The records, or ``None`` when the source failed or exceeded its bound."""
    try:
        records = tuple(await call())
    except Exception as error:  # noqa: BLE001 - reported safely, below
        _LOG.warning(
            "external source failed",
            extra={"source": source, "error_type": type(error).__name__},
        )
        return None
    if len(records) > bound:
        _LOG.warning(
            "external source exceeded its bound",
            extra={"source": source, "returned": len(records), "bound": bound},
        )
        return None
    return records


class VerifiedContractFacts:
    """Implements ``ContractMetadataProvider`` over dated, referenced records.

    ``get_contract`` answers for *now*; :meth:`assess` answers for any moment,
    which is what historical research needs - the record that governed then,
    not the one that governs today.
    """

    def __init__(
        self,
        sources: Sequence[tuple[str, ContractFactSource]],
        *,
        now: Callable[[], datetime],
        max_age: timedelta,
        limits: ProviderLimits,
    ) -> None:
        names = [name for name, _ in sources]
        if len(set(names)) != len(names):
            raise ValueError("every fact source has a distinct name")
        self._sources = tuple(sources)
        self._now = now
        self._max_age = max_age
        self._limits = limits

    async def assess(self, symbol: str, *, applies_at: datetime | None = None) -> FactVerdict:
        now = self._now()
        records: list[ContractSourceRecord] = []
        for name, source in self._sources:
            fetched = await _fetch(
                name,
                partial(source.records_for, symbol),
                self._limits.max_fact_records,
            )
            if fetched is not None:
                records.extend(fetched)
        return assess_fact_records(
            symbol,
            tuple(records),
            applies_at=applies_at if applies_at is not None else now,
            now=now,
            max_age=self._max_age,
        )

    async def get_contract(self, symbol: str) -> FuturesContract | None:
        verdict = await self.assess(symbol)
        return verdict.contract if verdict.code is FactVerdictCode.USABLE else None

    async def list_symbols(self) -> Sequence[str]:
        symbols: set[str] = set()
        for name, source in self._sources:
            fetched = await _fetch(name, source.symbols, self._limits.metadata_cache_entries)
            if fetched is not None:
                symbols.update(fetched)
        return tuple(sorted(symbols))


class RecordedSessionCalendar:
    """Implements ``SessionCalendarProvider`` from verified calendar records.

    Which session category a symbol trades in is itself a verified fact,
    supplied as ``categories``; a symbol without a verified category has no
    answer. Nothing maps a symbol to a category by its spelling.
    """

    def __init__(
        self,
        source: CalendarSource,
        *,
        venue: str,
        categories: Mapping[str, VerifiedValue[str]],
        limits: ProviderLimits,
    ) -> None:
        self._source = source
        self._venue = venue
        self._categories = dict(categories)
        self._limits = limits

    async def session_at(self, symbol: str, at: datetime) -> CalendarAnswer:
        category = self._categories.get(symbol)
        if category is None or not (
            category.is_authoritative and category.source.strip() and category.as_of is not None
        ):
            return unavailable(symbol, at, "no verified session category for this symbol")
        day = at.date()
        # One day either side of the UTC date covers any offset under a day.
        days = await _fetch(
            "calendar",
            lambda: self._source.days(
                self._venue, day - timedelta(days=1), day + timedelta(days=1)
            ),
            self._limits.max_calendar_days,
        )
        if days is None:
            return unavailable(symbol, at, "the calendar source is unavailable")
        return answer_from(
            days, symbol=symbol, venue=self._venue, session_category=category.value, at=at
        )


class OpenInterestReader:
    def __init__(
        self, source: OpenInterestSource, *, limits: ProviderLimits, max_age: timedelta
    ) -> None:
        self._source = source
        self._limits = limits
        self._max_age = max_age

    async def at(
        self,
        instrument: str,
        scope: OpenInterestScope,
        *,
        decision_time: datetime,
        lookback: timedelta,
    ) -> OpenInterestAnswer:
        observations = await _fetch(
            "open_interest",
            lambda: self._source.observations(
                instrument, scope, decision_time - lookback, decision_time
            ),
            self._limits.max_open_interest_points,
        )
        if observations is None:
            return OpenInterestAnswer(
                status=OpenInterestStatus.UNAVAILABLE,
                reason="the open-interest source is unavailable",
            )
        return open_interest_at(
            observations,
            instrument=instrument,
            scope=scope,
            decision_time=decision_time,
            max_age=self._max_age,
        )


@dataclass(frozen=True, slots=True)
class NewsView:
    """Articles a decision could have seen, or an explicit "unknown"."""

    available: bool
    reason: str
    articles: tuple[NewsArticle, ...] = ()


class NewsReader:
    def __init__(self, source: NewsSource, *, limits: ProviderLimits) -> None:
        self._source = source
        self._limits = limits

    async def visible(self, *, since: datetime, decision_time: datetime) -> NewsView:
        articles = await _fetch(
            "news",
            lambda: self._source.articles(since, decision_time),
            self._limits.max_news_articles,
        )
        if articles is None:
            return NewsView(available=False, reason="the news source is unavailable")
        try:
            visible = news_visible_at(articles, decision_time=decision_time)
        except ValueError:
            return NewsView(available=False, reason="the news source sent inconsistent revisions")
        return NewsView(available=True, reason="published and available by then", articles=visible)


class BreadthReader:
    def __init__(
        self, source: BreadthSource, *, limits: ProviderLimits, min_coverage: Decimal
    ) -> None:
        self._source = source
        self._limits = limits
        self._min_coverage = min_coverage

    async def at(
        self, universe_id: str, *, observed_at: datetime, decision_time: datetime
    ) -> BreadthAnswer:
        refused = BreadthAnswer(
            status=BreadthStatus.UNAVAILABLE, reason="the breadth source is unavailable"
        )
        try:
            universe = await self._source.universe(universe_id)
        except Exception as error:  # noqa: BLE001 - reported safely, below
            _LOG.warning(
                "external source failed",
                extra={"source": "breadth", "error_type": type(error).__name__},
            )
            return refused
        if universe is not None and len(universe.constituents) > (
            self._limits.max_universe_constituents
        ):
            return refused
        moves = await _fetch(
            "breadth",
            lambda: self._source.moves(universe_id, observed_at),
            self._limits.max_universe_constituents,
        )
        if moves is None:
            return refused
        return breadth_at(
            universe,
            moves,
            observed_at=observed_at,
            decision_time=decision_time,
            min_coverage=self._min_coverage,
        )
