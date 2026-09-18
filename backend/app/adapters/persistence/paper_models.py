"""Paper-trading tables (Phase 9).

Two tables, and the only tables Phase 9 creates:

``paper_positions``
    One row per position: its immutable opening (specification, risk approval,
    product snapshot, idempotency key) plus a *projection* of its current state
    for listing. The projection is rewritten on every save; the opening never is.

``paper_position_events``
    The ledger. One row per event, keyed by ``(position_id, sequence)`` so an
    event can be written exactly once. A database trigger (in the migration)
    refuses ``UPDATE`` and ``DELETE`` on this table, so append-only is a property
    of the storage, not a promise of the code that happens to use it.

Every money and price column is ``NUMERIC`` with no declared precision -
PostgreSQL's exact, arbitrary-precision decimal - so nothing is rounded on the
way in or out. No float column exists.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.persistence.base import Base

POSITION_STATES = (
    "PENDING_ENTRY",
    "OPEN",
    "PARTIALLY_CLOSED",
    "AMBIGUOUS_HALTED",
    "CLOSED",
    "CANCELLED",
    "REJECTED",
)

EVENT_TYPES = (
    "POSITION_CREATED",
    "OBSERVATION_APPLIED",
    "ENTRY_FILLED",
    "ENTRY_REJECTED",
    "TARGET_FILLED",
    "STOP_FILLED",
    "SAME_BAR_AMBIGUITY",
    "CLOSE_REQUESTED",
    "MANUAL_EXIT_FILLED",
    "STOP_MOVED_TO_BREAKEVEN",
    "POSITION_CANCELLED",
    "POSITION_CLOSED",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


EXACT = Numeric(asdecimal=True)


class PaperPositionRow(Base):
    __tablename__ = "paper_positions"
    __table_args__ = (
        CheckConstraint(_in("state", POSITION_STATES), name="valid_state"),
        CheckConstraint("direction IN ('LONG', 'SHORT')", name="valid_direction"),
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint("remaining >= 0 AND remaining <= quantity", name="remaining_in_range"),
        CheckConstraint("version >= 1", name="positive_version"),
        CheckConstraint("bars_applied >= 0", name="non_negative_bars"),
        CheckConstraint("event_count >= 1", name="has_creation_event"),
        Index("ix_paper_positions_created_at_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    approval: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    product_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    state: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    intended_entry: Mapped[Decimal] = mapped_column(EXACT, nullable=False)
    entry_fill_price: Mapped[Decimal | None] = mapped_column(EXACT, nullable=True)
    stop: Mapped[Decimal] = mapped_column(EXACT, nullable=False)
    last_mark: Mapped[Decimal | None] = mapped_column(EXACT, nullable=True)
    last_bar_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    realized_gross: Mapped[Decimal] = mapped_column(EXACT, nullable=False)
    fees_total: Mapped[Decimal | None] = mapped_column(EXACT, nullable=True)
    realized_net: Mapped[Decimal | None] = mapped_column(EXACT, nullable=True)
    unrealized_gross: Mapped[Decimal | None] = mapped_column(EXACT, nullable=True)
    bars_applied: Mapped[int] = mapped_column(Integer, nullable=False)
    close_pending: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rules_version: Mapped[str] = mapped_column(String(32), nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperEventRow(Base):
    __tablename__ = "paper_position_events"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="positive_sequence"),
        CheckConstraint(_in("event_type", EVENT_TYPES), name="valid_event_type"),
    )

    position_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("paper_positions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    market_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
