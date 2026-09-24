"""Shadow Mode observation journal (Phase 14 Part 1).

Two tables: the run, with its frozen configuration, and the journal of what
was observed. **No candles and no positions.** A shadow run records what the
existing rules decided about evidence that lives in the Phase 13 stream; it
opens nothing, so there is no ledger to keep.

Two constraints carry design decisions rather than hygiene:

* ``uq_shadow_journal_run_decision`` - one entry per (run, decision key). The
  key is derived from the run, the market boundary and the exact policy
  inputs, so a reconnect, a retry or a resumed consumer writes the observation
  once. Deduplication lives in the database, not in the caller's memory;
* ``ended_runs_state_why`` - a run that is no longer observing carries the
  reason it stopped. A stream has no promised end, so "ended" without a reason
  would be indistinguishable from a run that silently died.

``shadow_journal`` is append-only, enforced by a trigger as in Phases 9 and
12: a published decision that could be edited afterwards would be evidence of
nothing. An invalidated decision is superseded by a new entry.

Revision ID: 0007_shadow_mode
Revises: 0006_backtest
Create Date: Phase 14 Part 1
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_shadow_mode"
down_revision: str | None = "0006_backtest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shadow_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("configuration", sa.String(length=80), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("instrument_label", sa.String(length=64), nullable=False),
        sa.Column("provenance", sa.String(length=64), nullable=False),
        sa.Column("market_currency", sa.String(length=32), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_version", sa.String(length=32), nullable=False),
        sa.Column("strategy_parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("driver_timeframe", sa.String(length=8), nullable=False),
        sa.Column("timeframes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_timeframes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("account", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("end_reason", sa.String(length=32), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("observations", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("decisions", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("entries", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("first_boundary", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_boundary", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status <> 'ENDED' OR end_reason IS NOT NULL", name="ended_runs_state_why"
        ),
        sa.CheckConstraint(
            "observations >= 0 AND decisions >= 0 AND entries >= 0", name="counts_are_not_negative"
        ),
    )
    op.create_index("ix_shadow_runs_started_at_id", "shadow_runs", ["started_at", "id"])

    op.create_table(
        "shadow_journal",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.String(length=64),
            sa.ForeignKey("shadow_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("decision_key", sa.String(length=64), nullable=False),
        sa.Column("entry_kind", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=True),
        sa.Column("operational", sa.String(length=32), nullable=True),
        sa.Column("strategy_kind", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("market_boundary", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=True),
        sa.Column("entry", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("financial_state", sa.String(length=32), nullable=False),
        sa.Column("risk_outcome", sa.String(length=24), nullable=True),
        sa.Column("risk_reason", sa.String(length=500), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("supersedes", sa.String(length=64), nullable=True),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.UniqueConstraint("run_id", "decision_key", name="uq_shadow_journal_run_decision"),
        sa.CheckConstraint(
            "(entry_kind = 'DECISION' AND outcome IS NOT NULL)"
            " OR (entry_kind = 'OPERATIONAL' AND operational IS NOT NULL)",
            name="entries_state_what_they_are",
        ),
    )
    op.create_index("ix_shadow_journal_run_sequence", "shadow_journal", ["run_id", "sequence"])

    op.execute(
        """
        CREATE FUNCTION shadow_journal_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'shadow_journal is append-only (% refused)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER shadow_journal_no_update_or_delete
        BEFORE UPDATE OR DELETE ON shadow_journal
        FOR EACH ROW EXECUTE FUNCTION shadow_journal_append_only();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS shadow_journal_no_update_or_delete ON shadow_journal")
    op.execute("DROP FUNCTION IF EXISTS shadow_journal_append_only()")
    op.drop_index("ix_shadow_journal_run_sequence", table_name="shadow_journal")
    op.drop_table("shadow_journal")
    op.drop_index("ix_shadow_runs_started_at_id", table_name="shadow_runs")
    op.drop_table("shadow_runs")
