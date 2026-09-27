"""The durable verification journal, as the application sees it (Phase 15 Part 2B).

Three append-only kinds of entry, each written in one statement:

* a **submission** - a claim that a document states a fact;
* a **decision** - what a named reviewer decided about one submission, and what
  the review boundary made of it (``APPROVED``, ``REJECTED`` or ``REFUSED``
  with its code). One per submission, ever;
* a **record** - a contract's facts published from approved decisions of one
  document, carrying when this system first held it (``known_at``).

The store guarantees what the application cannot guarantee by itself: that a
published record references only decisions whose stored result is
``APPROVED``, that nothing is updated or deleted, and that a retried or
concurrent write either lands once or is refused - never twice, never
half-way.

Repeating an identical write is idempotent and reports that it was already
held. Writing *different* content under an identity already taken raises
:class:`VerificationConflictError`. An unreachable store raises
:class:`VerificationStoreUnavailableError` whose message names no host, path
or credential.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.domain.sourcing.facts import ContractSourceRecord
from app.domain.sourcing.review import (
    FactSubmission,
    ReviewDecision,
    ReviewResult,
    ReviewVerdict,
)

__all__ = [
    "FactVerificationStore",
    "JournalledDecision",
    "RecordEvidence",
    "ReviewEntry",
    "VerificationConflictError",
    "VerificationCounts",
    "VerificationStoreUnavailableError",
]


class VerificationConflictError(RuntimeError):
    """An identity is already held with different content, or a publish
    references evidence the journal does not hold as approved."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class VerificationStoreUnavailableError(RuntimeError):
    """The journal could not be reached. Carries no connection detail."""


@dataclass(frozen=True, slots=True)
class JournalledDecision:
    decision: ReviewDecision
    result: ReviewResult
    refusal_code: str | None
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewEntry:
    """One submission and, if it has one, its decision. Read in one query."""

    sequence: int
    submission: FactSubmission
    submitted_recorded_at: datetime
    decision: JournalledDecision | None


@dataclass(frozen=True, slots=True)
class RecordEvidence:
    """Which journalled decisions a record was published from."""

    multiplier: str
    tick_size: str
    expiry: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationCounts:
    submissions: int
    approved: int
    rejected: int
    refused: int
    records: int


@runtime_checkable
class FactVerificationStore(Protocol):
    async def append_submission(self, submission: FactSubmission, *, recorded_at: datetime) -> bool:
        """``True`` if written now, ``False`` if the identical entry was held."""
        ...

    async def append_decision(
        self, decision: ReviewDecision, verdict: ReviewVerdict, *, recorded_at: datetime
    ) -> tuple[JournalledDecision, bool]:
        """The journalled decision, and whether it was written by this call."""
        ...

    async def entry(self, submission_id: str) -> ReviewEntry | None: ...

    async def publish_record(
        self, record: ContractSourceRecord, evidence: RecordEvidence, *, recorded_at: datetime
    ) -> bool:
        """``True`` if written now. ``record.known_at`` is ignored: the
        journal's own ``recorded_at`` is when the system first held it."""
        ...

    async def records_for(self, symbol: str, *, limit: int) -> tuple[ContractSourceRecord, ...]:
        """Every record for exactly this canonical symbol, oldest first,
        ``known_at`` filled from the journal. At most ``limit``."""
        ...

    async def review_page(self, *, after: int, limit: int) -> tuple[tuple[ReviewEntry, ...], int]:
        """Entries with ``sequence > after``, oldest first, and the total."""
        ...

    async def counts(self) -> VerificationCounts: ...
