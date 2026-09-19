"""Replay storage (Phase 11).

Four tables and no financial record among them. The market facts a replay walks
through are immutable once a session refers to them; the money a replay makes
stays where Phase 9 put it, in the paper ledger, and the link table is how a
session knows which of those positions are its own.

``replay_candles`` holds normalised candles, not uploaded bytes: the CSV a
person supplied has served its purpose once it has been parsed and validated,
and keeping it would mean keeping a second, unvalidated copy of the same facts.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.persistence.base import Base

SESSION_STATES = ("READY", "IN_PROGRESS", "END_OF_DATASET")


class ReplayDatasetRow(Base):
    """One immutable set of historical candles, identified by its content."""

    __tablename__ = "replay_datasets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    """``RD-`` plus a digest of the normalised candles. Change one price and
    this changes, which is what makes the identity worth trusting."""

    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    timeframes: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    """Per-timeframe row counts and market-time range, for display and bounds."""

    total_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (CheckConstraint("total_rows > 0", name="dataset_has_rows"),)


class ReplayCandleRow(Base):
    """One normalised candle of one dataset. Never updated, never deleted."""

    __tablename__ = "replay_candles"
    __table_args__ = (
        CheckConstraint("high >= low", name="high_not_below_low"),
        CheckConstraint("sequence >= 0", name="non_negative_sequence"),
        Index("ix_replay_candles_window", "dataset_id", "timeframe", "open_time"),
    )

    dataset_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("replay_datasets.id", ondelete="RESTRICT"), primary_key=True
    )
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric, nullable=False)


class ReplaySessionRow(Base):
    """Where one replay has got to. The cursor, and what it was created for."""

    __tablename__ = "replay_sessions"
    __table_args__ = (
        CheckConstraint("state IN ('READY', 'IN_PROGRESS', 'END_OF_DATASET')", name="valid_state"),
        CheckConstraint("version >= 1", name="positive_version"),
        CheckConstraint("revealed_driver_candles >= 0", name="non_negative_revealed"),
        Index("ix_replay_sessions_created_at_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("replay_datasets.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    driver_timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    replay_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revealed_driver_candles: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_command_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    """The idempotency key of the step command in progress or last completed,
    so a retried step returns the same cursor instead of advancing again."""

    command_target_revealed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """How many driver candles that command was going to reveal in total.

    Written with the key on every boundary it commits, so a retry can tell a
    finished command from one that stopped part way - and can finish the
    remainder rather than advancing the whole request a second time.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReplayPositionLinkRow(Base):
    """Which paper positions belong to which replay session.

    The primary key is the *position*: a simulated position belongs to at most
    one replay, so a session can never claim another session's trade, and
    performance for a session is an exact set rather than a guess from symbols
    or timestamps.
    """

    __tablename__ = "replay_position_links"

    position_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("paper_positions.id", ondelete="RESTRICT"), primary_key=True
    )
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("replay_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_replay_position_links_session", "session_id", "position_id"),)
