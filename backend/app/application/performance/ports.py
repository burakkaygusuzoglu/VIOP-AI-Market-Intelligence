"""What the performance use cases need from the world outside them.

The engine computes; something else has to find the positions. That something
is a :class:`PerformanceSource`, which promises *authoritative* outcomes -
records folded from the append-only ledger, never from the mutable
``paper_positions`` projection. A future replay or backtest could implement the
same port and reuse every metric without another financial calculation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.common.enums import Direction, Timeframe
from app.domain.journal import JournalAnnotation
from app.domain.performance import PositionOutcome


class PerformanceSourceUnavailableError(RuntimeError):
    """The store could not be reached. Never a market opinion, never a zero."""


class JournalStoreUnavailableError(RuntimeError):
    """The journal store could not be reached."""


class JournalConflictError(RuntimeError):
    """Someone else edited this annotation since it was read."""

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected version {expected}, found {actual}")


class UnknownPositionError(LookupError):
    """A journal was requested for a position that does not exist."""


@dataclass(frozen=True, slots=True)
class OutcomeFilters:
    """Which positions an answer is about.

    ``opened_from``/``closed_to`` are *market* times: a trade belongs to a range
    by when it finished in the market, never by when a row was written. The
    range rule is applied identically to the summary, the breakdowns and the
    timeline, so a filtered dashboard cannot mix populations.
    """

    closed_from: datetime | None = None
    closed_to: datetime | None = None
    direction: Direction | None = None
    symbol: str | None = None
    timeframe: Timeframe | None = None
    tag: str | None = None
    outcomes_only: bool = False
    """True to restrict to completed positions - used by the journal list."""

    def describes_everything(self) -> bool:
        return not any(
            (
                self.closed_from,
                self.closed_to,
                self.direction,
                self.symbol,
                self.timeframe,
                self.tag,
            )
        )


@dataclass(frozen=True, slots=True)
class OutcomePage:
    """Authoritative outcomes plus what was needed to bound the request."""

    records: tuple[PositionOutcome, ...]
    fill_count: int
    """Exit fills behind these positions. Reported separately so a partial exit
    can never inflate a trade count."""

    total_matching: int
    """How many positions the filters matched, before any limit was applied."""


@dataclass(frozen=True, slots=True)
class JournalRow:
    """One journal line: immutable trade facts beside the person's own writing."""

    outcome: PositionOutcome
    annotation: JournalAnnotation


class PerformanceSource(Protocol):
    """Authoritative outcomes for analytics."""

    async def count_matching(self, filters: OutcomeFilters) -> int:
        """How many positions match, so an oversized range can be refused."""

    async def outcomes(self, filters: OutcomeFilters, *, limit: int) -> OutcomePage:
        """Every matching position as an authoritative outcome record."""

    async def journal_rows(
        self, filters: OutcomeFilters, *, offset: int, limit: int
    ) -> tuple[tuple[JournalRow, ...], int]:
        """One page of positions with their annotations, and the total."""


class JournalStore(Protocol):
    """Where a person's notes and tags live. Never financial authority."""

    async def get(self, position_id: str) -> JournalAnnotation | None:
        """The stored annotation, or None when nothing has been written yet."""

    async def save(
        self, annotation: JournalAnnotation, *, expected_version: int, now: datetime
    ) -> JournalAnnotation:
        """Write the annotation if it is still at ``expected_version``.

        Raises :class:`JournalConflictError` otherwise, so a second editor is
        told rather than silently overwriting the first.
        """

    async def position_exists(self, position_id: str) -> bool:
        """Whether the paper position exists at all."""

    async def tag_counts(self, *, limit: int) -> tuple[tuple[str, int], ...]:
        """Tags in use with their counts, most used first, bounded."""
