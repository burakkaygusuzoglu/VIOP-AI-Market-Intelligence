"""What a backtest needs from storage, and the shapes it hands back.

The runner computes a whole run in memory and publishes it in one call. That
shapes this port: there is a ``publish`` that writes everything or nothing, and
there is no ``append_event``, no ``save_position`` and no checkpoint - because a
half-written financial result is not a state this design can reach.

Historical candles are **not** re-stored. A run names a Phase 11 dataset by its
digest and reads it through the replay store, so there is one immutable copy of
any market and one rule for what had happened by a given moment.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.application.performance.service import PerformanceService
from app.domain.backtest.run import DecisionRecord, RunStatus
from app.domain.common.enums import Timeframe
from app.domain.paper.model import PaperEvent, PositionSpec, RiskApproval


class BacktestStoreUnavailableError(RuntimeError):
    """Storage could not be reached. Never a market statement, never a zero."""


class DuplicateRunError(RuntimeError):
    """Two requests raced on one attempt key; the loser re-reads the winner."""


class UnknownRunError(LookupError):
    """No such backtest run."""


class TerminalRunError(RuntimeError):
    """A run that already reached a terminal state may not be published.

    An interruption terminalises a run as FAILED. If the process that was
    computing it then comes back and publishes, that run would turn COMPLETED
    after somebody was told it had been abandoned - so publication is refused
    instead. Only one terminal transition wins, and it is the first one.
    """


class CompletedRunError(RuntimeError):
    """A run that already carries a result may not be re-labelled.

    Marking a COMPLETED run as FAILED would discard a real answer and leave a
    ``result_digest`` attached to a run that claims to have none. Terminalising
    an interrupted run must therefore never be able to touch a finished one.
    """


@dataclass(frozen=True, slots=True)
class BacktestPosition:
    """One simulated position a run produced, with its whole ledger.

    The same Phase 9 shapes - spec, approval, product snapshot, events - because
    it *is* a Phase 9 position. It lives in the run's own tables rather than the
    person's paper journal, so a batch of two hundred historical simulations
    never lands in the list of trades they actually thought about.
    """

    position_id: str
    ordinal: int
    """Its place in the run, from one. Stable across identical runs, which is
    what lets two runs be compared without comparing generated ids."""

    spec: PositionSpec
    approval: RiskApproval
    product_snapshot: Mapping[str, Any]
    events: tuple[PaperEvent, ...]


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Everything one completed run concluded."""

    boundaries_evaluated: int
    first_boundary: datetime | None
    last_boundary: datetime | None
    decisions: tuple[DecisionRecord, ...]
    positions: tuple[BacktestPosition, ...]
    result_digest: str
    """Canonical digest of the conclusions, excluding non-semantic ids."""


@dataclass(frozen=True, slots=True)
class StoredRun:
    """A persisted run: what it was asked to do, and how it ended."""

    run_id: str
    configuration: str
    attempt_key: str
    dataset_id: str
    symbol: str
    driver: Timeframe
    interval_start: datetime
    interval_end: datetime
    strategy_id: str
    strategy_version: str
    strategy_parameters: Mapping[str, str]
    simulation: Mapping[str, str]
    risk: Mapping[str, str]
    product_snapshot: Mapping[str, Any] | None
    status: RunStatus
    failure_code: str | None
    failure_reason: str | None
    result: BacktestResult | None
    """Present only for a COMPLETED run. A FAILED run carries no metrics,
    because it has none - not zeros."""

    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RunSummary:
    """One row of a run listing. No trace, no ledger, no metrics."""

    run_id: str
    configuration: str
    dataset_id: str
    symbol: str
    driver: Timeframe
    strategy_id: str
    strategy_version: str
    status: RunStatus
    boundaries_evaluated: int
    position_count: int
    created_at: datetime
    updated_at: datetime


class RunPerformanceFactory(Protocol):
    """Builds Phase 10's service scoped to one run.

    A run's outcome source is scoped at construction rather than by a filter,
    so something has to construct one per run. That something is an adapter,
    and a route may not reach an adapter - so the composition root supplies
    this, and the route asks it for a service it cannot build itself.
    """

    def __call__(self, run_id: str) -> PerformanceService: ...


@dataclass(frozen=True, slots=True)
class RunTotals:
    """What a completed run recorded, without loading what it recorded.

    ``get`` hydrates every decision and every position's whole ledger, which is
    right for the runner and wrong for a screen: a run at the 2,500-boundary
    ceiling would put 2,500 trace rows and 200 ledgers into one response. These
    are the counts and digests a detail view actually needs, read from the run
    row plus two ``count(*)`` queries.
    """

    boundaries_evaluated: int
    first_boundary: datetime | None
    last_boundary: datetime | None
    result_digest: str | None
    decision_count: int
    position_count: int


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    """One stored ledger entry, as a paginated read returns it."""

    sequence: int
    type: str
    market_time: datetime | None
    data: Mapping[str, str]


class BacktestStore(Protocol):
    """Runs and their results. Candles belong to the replay store."""

    async def create_run(self, run: StoredRun) -> StoredRun:
        """Record an accepted run as PENDING. Raises on a duplicate attempt key."""

    async def find_by_attempt(self, attempt_key: str) -> StoredRun | None:
        """The run created with this attempt key, with its configuration."""

    async def publish(self, run_id: str, result: BacktestResult, *, now: datetime) -> StoredRun:
        """Write the whole result and mark the run COMPLETED, atomically.

        Raises ``TerminalRunError`` when the run already ended: an abandoned
        run does not become COMPLETED because a stale worker finished.

        Every decision, every position and every ledger event lands in one
        transaction with the status change. A run therefore cannot be read as
        COMPLETED while part of its result is missing.
        """

    async def fail(self, run_id: str, *, code: str, reason: str, now: datetime) -> StoredRun:
        """Mark an unfinished run FAILED with a truthful reason, storing no result.

        Raises ``CompletedRunError`` for a run that already carries one: a
        finished answer is never re-labelled as a failure.
        """

    async def get(self, run_id: str) -> StoredRun | None: ...

    async def list_runs(self, *, offset: int, limit: int) -> tuple[tuple[RunSummary, ...], int]:
        """One bounded page of runs, newest first, with the total."""

    async def positions_of(self, run_id: str) -> tuple[BacktestPosition, ...]:
        """Every position the run produced, in ordinal order."""

    async def head(self, run_id: str) -> tuple[StoredRun, RunTotals] | None:
        """A run's identity, configuration, status and totals - and no payload.

        The returned :class:`StoredRun` always carries ``result=None``, even for
        a COMPLETED run: this read deliberately does not load one. A caller
        reports completeness from ``status``, never from ``result is not None``,
        and takes the totals from :class:`RunTotals`.
        """
        ...

    async def trace_page(
        self, run_id: str, *, offset: int, limit: int
    ) -> tuple[tuple[DecisionRecord, ...], int]:
        """One bounded page of the decision trace, in evaluation order."""
        ...

    async def events_of(
        self, position_id: str, *, after_sequence: int, limit: int
    ) -> tuple[tuple[LedgerEvent, ...], int]:
        """One bounded page of a position's ledger, with the total it has."""
        ...

    async def ledger_of(
        self, run_id: str
    ) -> dict[str, Sequence[tuple[str, datetime | None, Mapping[str, Any]]]]:
        """Each position's events as the performance fold reads them."""
