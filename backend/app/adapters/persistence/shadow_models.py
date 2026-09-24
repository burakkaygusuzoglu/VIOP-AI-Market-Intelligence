"""Shadow Mode tables (Phase 14 Parts 1 and 2A).

Four tables, and the journal is append-only at the storage level: a trigger
refuses UPDATE and DELETE, so a decision that was published cannot be edited
into one the rules never made. A later fact is a new entry that supersedes an
earlier one.

Part 2A adds two more. ``shadow_outcomes`` holds published *price
developments* - where the market traded after a decision, relative to the
levels that decision proposed. It is append-only for the same reason the
journal is: "we did not know yet" is part of the history, so a later answer is
a new row rather than an edit. Nothing in it is money: there is no fill price,
no quantity and no profit column, because a touched level is not a trade.

``shadow_run_attempts`` makes run creation idempotent at the database rather
than in a process's memory. Two concurrent requests carrying one attempt key
cannot become two observers of the same stream.

The source is stored as text rather than a foreign key. Phase 13's only source
happens to be a stored replay dataset, but a shadow run records *what it
observed*, and a later provider that is not a dataset must not require this
table to change.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.adapters.persistence.base import Base


class ShadowRunRow(Base):
    __tablename__ = "shadow_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    configuration: Mapped[str] = mapped_column(String(80), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    instrument_label: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance: Mapped[str] = mapped_column(String(64), nullable=False)
    market_currency: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    driver_timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    timeframes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    required_timeframes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    risk: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    account: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    end_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decisions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_boundary: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_boundary: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Declared here as well as in the migration so a drift check compares
        # the two and stays silent only while they agree.
        CheckConstraint("status <> 'ENDED' OR end_reason IS NOT NULL", name="ended_runs_state_why"),
        CheckConstraint(
            "observations >= 0 AND decisions >= 0 AND entries >= 0",
            name="counts_are_not_negative",
        ),
        Index("ix_shadow_runs_started_at_id", "started_at", "id"),
    )


class ShadowJournalRow(Base):
    """One journal entry. Append-only; see the migration's trigger."""

    __tablename__ = "shadow_journal"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("shadow_runs.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_key: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    operational: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    market_boundary: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    entry: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    financial_state: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    risk_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supersedes: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint(
            "(entry_kind = 'DECISION' AND outcome IS NOT NULL)"
            " OR (entry_kind = 'OPERATIONAL' AND operational IS NOT NULL)",
            name="entries_state_what_they_are",
        ),
        UniqueConstraint("run_id", "decision_key", name="uq_shadow_journal_run_decision"),
        Index("ix_shadow_journal_run_sequence", "run_id", "sequence"),
    )


class ShadowOutcomeRow(Base):
    """One published price development. Append-only; see the trigger.

    Every price here is a level the market reached, never a fill and never an
    amount of money. The columns are deliberately missing a quantity and a
    profit: adding either would make it possible to state something this
    system cannot know.
    """

    __tablename__ = "shadow_outcomes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("shadow_runs.id", ondelete="RESTRICT"), nullable=False
    )
    decision_key: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome_key: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decision_boundary: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    rules: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    candles_observed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    target_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    best_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    worst_price: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    last_close: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    ambiguous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    unresolved_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "state <> 'UNAVAILABLE' OR unresolved_reason IS NOT NULL",
            name="unavailable_outcomes_say_why",
        ),
        CheckConstraint(
            "observed_from IS NULL OR observed_from > decision_boundary",
            name="outcomes_observe_only_what_came_after",
        ),
        CheckConstraint("candles_observed >= 0", name="observed_count_is_not_negative"),
        UniqueConstraint("run_id", "outcome_key", name="uq_shadow_outcome_run_key"),
        Index("ix_shadow_outcomes_run_sequence", "run_id", "sequence"),
        Index("ix_shadow_outcomes_run_decision", "run_id", "decision_key"),
    )


class ShadowRunAttemptRow(Base):
    """One creation attempt key, so a retried request cannot fan out.

    The unique key is the attempt key alone: a second request carrying it gets
    the run it already created when the configuration matches, and an explicit
    conflict when it does not. Deciding that in the database rather than in a
    process makes it true across workers and across a restart.
    """

    __tablename__ = "shadow_run_attempts"

    attempt_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("shadow_runs.id", ondelete="RESTRICT"), nullable=False
    )
    configuration: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
