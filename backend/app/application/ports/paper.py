"""Ports for paper trading (Phase 9).

Three seams, each with one reason to exist:

``ProductResolver``
    Turns a symbol into a ``ProductPolicy`` from server-owned metadata - the
    same trust path Phase 8.5 established for analysis. Optional: when no
    contract metadata provider is composed, it is absent and every new paper
    position is refused rather than opened against defaults.

``ProductSnapshotCodec``
    Freezes a product's metadata into the position when it opens, and restores
    it for every later step. A paper position's P&L must not change because a
    multiplier was re-registered next week; it was simulated under the facts in
    force when it opened, and replay must use exactly those.

``PaperStore``
    Persistence behind a unit of work. The application never sees SQL, a
    session or a row; it sees a locked position, its ledger, and "save these
    new events atomically".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from app.domain.instrument.policy import ProductPolicy
from app.domain.paper import PaperEvent, PaperPosition, PositionSpec, RiskApproval


@runtime_checkable
class ProductResolver(Protocol):
    """Resolves a symbol to a product policy from trusted server-side metadata."""

    async def resolve(self, symbol: str) -> ProductPolicy | None:
        """``None`` when no record exists. Never a policy built from defaults."""
        ...


@runtime_checkable
class ProductSnapshotCodec(Protocol):
    """Freezes and restores the product metadata a position was opened under."""

    def snapshot(self, product: ProductPolicy) -> Mapping[str, Any]:
        """A JSON-compatible record that ``restore`` turns back into an equal policy."""
        ...

    def restore(self, snapshot: Mapping[str, Any]) -> ProductPolicy:
        """Raises ``ProductSnapshotError`` for a snapshot it does not recognise."""
        ...


class ProductSnapshotError(ValueError):
    """A stored product snapshot this build cannot restore."""


class PaperStoreUnavailableError(Exception):
    """The store could not be reached. A system state, never a simulation result.

    Raised by an adapter in place of its driver's connectivity error, so callers
    can answer with a typed "unavailable" without importing the driver.
    """


class DuplicatePositionError(Exception):
    """An insert collided with an existing position or idempotency key.

    Raised by the store when two identical creation requests race past the
    idempotency lookup; the service re-reads and answers from what won.
    """


class ConcurrentModificationError(Exception):
    """A save found the position at a different version than it loaded.

    The row lock makes this unreachable in normal operation; it is the backstop
    that turns a lost update into a refusal instead of a silent overwrite.
    """


@dataclass(frozen=True, slots=True)
class PositionProjection:
    """The queryable current state of a position, written with every save.

    Derived from the ledger - never an independent source of truth. It exists so
    that listing positions does not replay every ledger on the page.
    """

    state: str
    symbol: str
    asset_class: str
    direction: str
    quantity: int
    remaining: int
    intended_entry: Decimal
    entry_fill_price: Decimal | None
    stop: Decimal
    last_mark: Decimal | None
    last_bar_time: datetime | None
    realized_gross: Decimal
    fees_total: Decimal | None
    realized_net: Decimal | None
    unrealized_gross: Decimal | None
    bars_applied: int
    close_pending: bool
    rules_version: str
    event_count: int


@dataclass(frozen=True, slots=True)
class StoredPosition:
    """Everything persisted for one position."""

    position_id: str
    idempotency_key: str
    request_fingerprint: str
    spec: PositionSpec
    approval: RiskApproval
    product_snapshot: Mapping[str, Any]
    events: tuple[PaperEvent, ...]
    projection: PositionProjection
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredEvent:
    event: PaperEvent
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class PositionSummary:
    """One row of a position list: the projection, no ledger."""

    position_id: str
    projection: PositionProjection
    created_at: datetime
    updated_at: datetime


class PaperUnitOfWork(Protocol):
    """One transaction. Anything not committed is rolled back on exit."""

    async def find_by_idempotency_key(self, key: str) -> StoredPosition | None: ...

    async def load_for_update(self, position_id: str) -> StoredPosition | None:
        """Load and lock the position until the unit of work ends."""
        ...

    async def insert(self, stored: StoredPosition, recorded_at: datetime) -> None: ...

    async def append(
        self,
        stored: StoredPosition,
        position: PaperPosition,
        projection: PositionProjection,
        recorded_at: datetime,
    ) -> StoredPosition:
        """Append the events ``position`` has beyond ``stored`` and update the
        projection, in this transaction. Never rewrites an existing event."""
        ...

    async def commit(self) -> None: ...


class PaperStore(Protocol):
    def unit_of_work(self) -> AbstractAsyncContextManager[PaperUnitOfWork]: ...

    async def get(self, position_id: str) -> StoredPosition | None: ...

    async def list_summaries(
        self, *, offset: int, limit: int
    ) -> tuple[Sequence[PositionSummary], int]: ...

    async def events_page(
        self, position_id: str, *, after_sequence: int, limit: int
    ) -> tuple[Sequence[StoredEvent], int]:
        """Events with ``sequence > after_sequence`` in ascending order, plus the
        total event count for the position."""
        ...
