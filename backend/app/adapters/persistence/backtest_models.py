"""Backtest storage (Phase 12).

Four tables, and **no historical candles among them**. A run names a Phase 11
dataset by its digest and reads that; copying the market into a second table
would mean two copies of the same facts and two things to keep immutable.

The position and event tables deliberately mirror the Phase 9 shapes, because
the rows hold Phase 9 objects - the same spec, the same approval, the same
frozen product snapshot, the same ledger. What differs is *where* they live: a
batch of two hundred historical simulations belongs to its run, not to the list
of trades a person actually thought about.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.persistence.base import Base

RUN_STATUSES = ("PENDING", "RUNNING", "COMPLETED", "FAILED")
DECISION_OUTCOMES = (
    "NO_SIGNAL",
    "WAIT",
    "ENTERED",
    "REFUSED_BY_RISK",
    "REFUSED_BY_ENGINE",
    "EXIT_REQUESTED",
    "HOLDING",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    listed = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({listed})"


class BacktestRunRow(Base):
    """One evaluation of one frozen configuration against one dataset."""

    __tablename__ = "backtest_runs"
    __table_args__ = (
        CheckConstraint(_in("status", RUN_STATUSES), name="valid_status"),
        CheckConstraint("boundaries_evaluated >= 0", name="non_negative_boundaries"),
        CheckConstraint("interval_end > interval_start", name="interval_is_forward"),
        CheckConstraint(
            "(status <> 'COMPLETED') OR (result_digest IS NOT NULL)",
            name="completed_runs_carry_a_result",
        ),
        CheckConstraint(
            "(status <> 'FAILED') OR (failure_code IS NOT NULL)",
            name="failed_runs_state_why",
        ),
        Index("ix_backtest_runs_created_at_id", "created_at", "id"),
        Index("ix_backtest_runs_configuration", "configuration"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    configuration: Mapped[str] = mapped_column(String(64), nullable=False)
    """Semantic fingerprint. Two runs sharing it were asked the same question."""

    attempt_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    dataset_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("replay_datasets.id", ondelete="RESTRICT"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    driver_timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    interval_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interval_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    simulation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    product_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    """Frozen at the start of the run. Nothing re-reads the current metadata,
    so changing the resolver cannot alter a result already computed."""

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    boundaries_evaluated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_boundary: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_boundary: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Present only for a COMPLETED run, and enforced by a check constraint:
    the database itself refuses a completed run with nothing to show."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BacktestDecisionRow(Base):
    """One evaluated boundary and why it ended the way it did."""

    __tablename__ = "backtest_decisions"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="positive_sequence"),
        CheckConstraint(_in("outcome", DECISION_OUTCOMES), name="valid_outcome"),
        CheckConstraint("bars_available >= 0", name="non_negative_bars"),
    )

    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("backtest_runs.id", ondelete="RESTRICT"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    bars_available: Mapped[int] = mapped_column(Integer, nullable=False)
    position_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    risk_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)


class BacktestPositionRow(Base):
    """A Phase 9 position that belongs to a run rather than to a person."""

    __tablename__ = "backtest_positions"
    __table_args__ = (
        CheckConstraint("ordinal >= 1", name="positive_ordinal"),
        # Unnamed on purpose: the shared naming convention derives `uq` names
        # from the table and first column, so a name given here would be
        # discarded and the migration would look as if it disagreed.
        UniqueConstraint("run_id", "ordinal"),
        Index("ix_backtest_positions_run", "run_id", "ordinal"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("backtest_runs.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    approval: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    product_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BacktestEventRow(Base):
    """One ledger entry. Append-only, enforced by a trigger as in Phase 9."""

    __tablename__ = "backtest_position_events"
    __table_args__ = (CheckConstraint("sequence >= 1", name="positive_sequence"),)

    position_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("backtest_positions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    market_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
