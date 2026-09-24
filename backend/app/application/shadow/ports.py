"""What a shadow run needs from storage (Phase 14 Part 1).

The journal is persisted, because the point of Shadow Mode is to look back at
what the rules decided (master spec section 78) and because Part 2's
evaluation tools have nothing to evaluate if a restart empties the record.
Phase 13's *market state* stays ephemeral; a research journal is not market
state.

Two properties the store must have, both asserted by tests against real
PostgreSQL:

* **append-only.** An entry is never updated or deleted. A later fact that
  changes the picture is a new entry that supersedes an earlier one.
* **idempotent append.** Entries carry a decision key derived from the run,
  the market boundary and the exact policy inputs. Writing the same key twice
  is a no-op, so a reconnect, a retry or a resumed consumer cannot record one
  observation as two decisions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.domain.common.enums import Timeframe
from app.domain.shadow.decision import ShadowDecision
from app.domain.shadow.outcome import ShadowOutcomeRecord
from app.domain.shadow.run import EndReason, ShadowRunStatus

__all__ = [
    "AttemptKeyHeldError",
    "ShadowJournalStore",
    "ShadowStoreUnavailableError",
    "StoredShadowRun",
]


class ShadowStoreUnavailableError(RuntimeError):
    """Storage could not be reached. Never a statement about the market."""


class AttemptKeyHeldError(RuntimeError):
    """A creation attempt key already names a run.

    Raised by :meth:`ShadowJournalStore.create_run` *instead of* creating a
    second run: the run row and the key are written in one transaction, so a
    losing request leaves nothing behind. Carries what the key already holds,
    so the caller can answer a retry with that run and a changed configuration
    with a conflict.
    """

    def __init__(self, run_id: str, configuration: str) -> None:
        super().__init__("this attempt key already created a run")
        self.run_id = run_id
        self.configuration = configuration
        self.requested_configuration: str | None = None
        """The configuration the losing request asked for, stamped by the
        runner, so a caller can tell a retry from a conflict."""


@dataclass(frozen=True, slots=True)
class StoredShadowRun:
    """One observation, its frozen configuration and what it has seen."""

    run_id: str
    configuration: str
    source_id: str
    instrument_label: str
    provenance: str
    market_currency: str
    strategy_id: str
    strategy_version: str
    strategy_parameters: Mapping[str, str]
    driver: Timeframe
    timeframes: tuple[Timeframe, ...]
    required_timeframes: tuple[Timeframe, ...]
    risk: Mapping[str, str]
    account: Mapping[str, str]
    status: ShadowRunStatus
    started_at: datetime
    ended_at: datetime | None = None
    end_reason: EndReason | None = None
    observations: int = 0
    """Confirmed boundaries this run processed, evaluated or unavailable."""

    decisions: int = 0
    entries: int = 0
    first_boundary: datetime | None = None
    last_boundary: datetime | None = None
    failure_code: str | None = None
    notes: Mapping[str, str] = field(default_factory=dict)


class ShadowJournalStore(Protocol):
    async def create_run(
        self, run: StoredShadowRun, *, attempt_key: str | None = None
    ) -> StoredShadowRun:
        """Store a new run. A run id already present is a conflict, never a
        silent merge of two observations.

        With an ``attempt_key``, the key is claimed in the same transaction as
        the run is written. If another request already holds the key, nothing
        is written and :class:`AttemptKeyHeldError` says which run holds it -
        so two racing requests produce exactly one run, never two and never a
        run with no key or a key with no run.
        """
        ...

    async def append(self, run_id: str, entries: Sequence[ShadowDecision]) -> int:
        """Append entries; return how many were genuinely new.

        A duplicate decision key is ignored rather than rejected: the caller
        that retries has already done the work, and the journal states the
        observation exactly once.
        """
        ...

    async def finish_run(
        self,
        run_id: str,
        *,
        status: ShadowRunStatus,
        end_reason: EndReason,
        ended_at: datetime,
        observations: int,
        decisions: int,
        entries: int,
        first_boundary: datetime | None,
        last_boundary: datetime | None,
        failure_code: str | None = None,
    ) -> StoredShadowRun: ...

    async def get_run(self, run_id: str) -> StoredShadowRun | None: ...

    async def list_runs(
        self, *, offset: int, limit: int
    ) -> tuple[tuple[StoredShadowRun, ...], int]:
        """One bounded page of runs, newest first, with the total."""
        ...

    async def read_entries(
        self, run_id: str, *, after_sequence: int, limit: int
    ) -> tuple[tuple[ShadowDecision, ...], int]:
        """A bounded page of journal entries in sequence order, with the total."""
        ...

    async def last_sequence(self, run_id: str) -> int:
        """The highest sequence written for this run, or 0."""
        ...

    async def append_outcomes(self, run_id: str, records: Sequence[ShadowOutcomeRecord]) -> int:
        """Append published developments; return how many were genuinely new.

        Same rules as the journal: append-only and idempotent on the outcome
        key, so re-observing the same development writes nothing.
        """
        ...

    async def read_outcomes(
        self, run_id: str, *, after_sequence: int, limit: int
    ) -> tuple[tuple[ShadowOutcomeRecord, ...], int]:
        """A bounded page of outcome records in sequence order, with the total."""
        ...

    async def latest_outcomes(
        self, run_id: str, decision_keys: Sequence[str]
    ) -> Mapping[str, ShadowOutcomeRecord]:
        """The most recent published development for each named decision.

        Earlier records are kept; this is the current answer, not the only one.
        """
        ...

    async def last_outcome_sequence(self, run_id: str) -> int: ...

    async def find_attempt(self, attempt_key: str) -> tuple[str, str] | None:
        """The run id and configuration an attempt key already holds, if any.

        A read, for answering a retry before any slot is taken. It does not
        replace the atomic claim in :meth:`create_run`: two requests can both
        find nothing here, and exactly one of them will then win there.
        """
        ...

    async def observing_runs(self, *, limit: int) -> tuple[StoredShadowRun, ...]:
        """Runs still marked as observing, oldest first, at most ``limit``."""
        ...

    async def interrupt(
        self, run_id: str, *, ended_at: datetime, end_reason: EndReason
    ) -> StoredShadowRun:
        """Close a run nobody is observing any more, with counts from its journal.

        The counts come from what was actually written, not from a process
        that no longer exists: observations are the decision entries, decisions
        the ones that were not UNAVAILABLE, and the boundaries the first and
        last recorded. Only a run still marked observing is changed.
        """
        ...
