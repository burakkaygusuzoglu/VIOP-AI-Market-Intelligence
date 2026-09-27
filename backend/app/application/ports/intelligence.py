"""Ports for external intelligence and verified facts (Phase 15 Part 2A).

Master spec section 73 names provider interfaces and Phase 15 names the
categories: contract metadata, open interest, news and market breadth, with
session calendars as a section 118 fact. Each is a port here, answered by an
adapter that speaks one vendor's protocol and returns the vendor-neutral
domain records of ``app.domain.sourcing``.

**No adapter implements these ports in this build**, and nothing composes
them: no licensed source is available to the project (see the Phase 15
report). A port without an implementation is an interface, not an
integration, and is not described as one.

Every method returns *records*, never conclusions: which record governs, what
a decision could have known, and whether breadth has a denominator are
decided by the deterministic domain functions, the same for every source.

The contract-fact review journal has its own port since Part 2B:
:mod:`app.application.ports.fact_verification`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from app.domain.sourcing.breadth import ConstituentMove, Universe
from app.domain.sourcing.calendar import CalendarDay
from app.domain.sourcing.facts import ContractSourceRecord
from app.domain.sourcing.news import NewsArticle
from app.domain.sourcing.open_interest import OpenInterestObservation, OpenInterestScope

__all__ = [
    "BreadthSource",
    "CalendarSource",
    "ContractFactSource",
    "NewsSource",
    "OpenInterestSource",
]


@runtime_checkable
class ContractFactSource(Protocol):
    async def symbols(self) -> Sequence[str]:
        """Every contract this source holds records for."""
        ...

    async def records_for(self, symbol: str) -> Sequence[ContractSourceRecord]:
        """Every record this source holds for ``symbol`` - including
        superseded and inapplicable ones, so the choice stays auditable."""
        ...


@runtime_checkable
class CalendarSource(Protocol):
    async def days(self, venue: str, start: date, end: date) -> Sequence[CalendarDay]:
        """Calendar records for ``[start, end]``. A date absent is unknown."""
        ...


@runtime_checkable
class OpenInterestSource(Protocol):
    async def observations(
        self, instrument: str, scope: OpenInterestScope, start: datetime, end: datetime
    ) -> Sequence[OpenInterestObservation]:
        """Observations measured in ``[start, end]``, every revision included."""
        ...


@runtime_checkable
class NewsSource(Protocol):
    async def articles(self, start: datetime, end: datetime) -> Sequence[NewsArticle]:
        """Article revisions published in ``[start, end]``, every revision included."""
        ...


@runtime_checkable
class BreadthSource(Protocol):
    async def universe(self, universe_id: str) -> Universe | None: ...

    async def moves(self, universe_id: str, observed_at: datetime) -> Sequence[ConstituentMove]:
        """Every constituent observation for ``observed_at``, every revision."""
        ...
