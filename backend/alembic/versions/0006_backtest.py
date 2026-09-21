"""Deterministic backtesting (Phase 12).

Four tables: the run, its decision trace, the positions it produced and their
ledgers. **No candles.** A run names a Phase 11 dataset by its digest and reads
that one immutable copy, so there is no second market to keep honest.

Two constraints carry design decisions rather than hygiene:

* ``completed_runs_carry_a_result`` - a run cannot be stored as COMPLETED
  without a result digest. Publication writes the status and the whole result in
  one transaction, and the database refuses the other combination outright;
* ``failed_runs_state_why`` - a FAILED run carries a code. A failure without a
  reason is a result nobody can act on.

``backtest_position_events`` is append-only, enforced by a trigger as in
Phase 9: a simulated ledger that could be edited afterwards is not evidence of
anything.

Revision ID: 0006_backtest
Revises: 0005_replay_command_target
Create Date: Phase 12 Part 1
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_backtest"
down_revision: str | None = "0005_replay_command_target"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUN_STATUSES = "'PENDING', 'RUNNING', 'COMPLETED', 'FAILED'"
DECISION_OUTCOMES = (
    "'NO_SIGNAL', 'WAIT', 'ENTERED', 'REFUSED_BY_RISK', "
    "'REFUSED_BY_ENGINE', 'EXIT_REQUESTED', 'HOLDING'"
)

APPEND_ONLY = """
CREATE OR REPLACE FUNCTION backtest_events_append_only() RETURNS trigger AS $BODY$
BEGIN
    RAISE EXCEPTION 'backtest_position_events is append-only (% refused)', TG_OP;
END;
$BODY$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("configuration", sa.String(length=64), nullable=False),
        sa.Column("attempt_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column(
            "dataset_id",
            sa.String(length=64),
            sa.ForeignKey("replay_datasets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("driver_timeframe", sa.String(length=8), nullable=False),
        sa.Column("interval_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_version", sa.String(length=32), nullable=False),
        sa.Column("strategy_parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("simulation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("product_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column(
            "boundaries_evaluated", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("first_boundary", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_boundary", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_digest", sa.String(length=64), nullable=True),
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
            f"status IN ({RUN_STATUSES})", name=op.f("ck_backtest_runs_valid_status")
        ),
        sa.CheckConstraint(
            "boundaries_evaluated >= 0", name=op.f("ck_backtest_runs_non_negative_boundaries")
        ),
        sa.CheckConstraint(
            "interval_end > interval_start", name=op.f("ck_backtest_runs_interval_is_forward")
        ),
        sa.CheckConstraint(
            "(status <> 'COMPLETED') OR (result_digest IS NOT NULL)",
            name=op.f("ck_backtest_runs_completed_runs_carry_a_result"),
        ),
        sa.CheckConstraint(
            "(status <> 'FAILED') OR (failure_code IS NOT NULL)",
            name=op.f("ck_backtest_runs_failed_runs_state_why"),
        ),
    )
    op.create_index("ix_backtest_runs_created_at_id", "backtest_runs", ["created_at", "id"])
    op.create_index("ix_backtest_runs_configuration", "backtest_runs", ["configuration"])

    op.create_table(
        "backtest_decisions",
        sa.Column(
            "run_id",
            sa.String(length=64),
            sa.ForeignKey("backtest_runs.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("bars_available", sa.Integer(), nullable=False),
        sa.Column("position_id", sa.String(length=64), nullable=True),
        sa.Column("risk_outcome", sa.String(length=24), nullable=True),
        sa.Column("risk_reason", sa.String(length=500), nullable=True),
        sa.Column("direction", sa.String(length=8), nullable=True),
        sa.CheckConstraint("sequence >= 1", name=op.f("ck_backtest_decisions_positive_sequence")),
        sa.CheckConstraint(
            f"outcome IN ({DECISION_OUTCOMES})",
            name=op.f("ck_backtest_decisions_valid_outcome"),
        ),
        sa.CheckConstraint(
            "bars_available >= 0", name=op.f("ck_backtest_decisions_non_negative_bars")
        ),
    )

    op.create_table(
        "backtest_positions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=64),
            sa.ForeignKey("backtest_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("approval", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("product_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("ordinal >= 1", name=op.f("ck_backtest_positions_positive_ordinal")),
        # The shared `uq` naming convention is keyed on the first column, not on
        # a constraint name, so this is what the model's declaration resolves
        # to. Naming it anything else here would drift from the models silently.
        sa.UniqueConstraint("run_id", "ordinal", name="uq_backtest_positions_run_id"),
    )
    op.create_index("ix_backtest_positions_run", "backtest_positions", ["run_id", "ordinal"])

    op.create_table(
        "backtest_position_events",
        sa.Column(
            "position_id",
            sa.String(length=64),
            sa.ForeignKey("backtest_positions.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("market_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "sequence >= 1", name=op.f("ck_backtest_position_events_positive_sequence")
        ),
    )

    op.execute(APPEND_ONLY)
    op.execute(
        """
        CREATE TRIGGER backtest_events_no_update_or_delete
        BEFORE UPDATE OR DELETE ON backtest_position_events
        FOR EACH ROW EXECUTE FUNCTION backtest_events_append_only();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS backtest_events_no_update_or_delete ON backtest_position_events"
    )
    op.execute("DROP FUNCTION IF EXISTS backtest_events_append_only()")
    op.drop_table("backtest_position_events")
    op.drop_index("ix_backtest_positions_run", table_name="backtest_positions")
    op.drop_table("backtest_positions")
    op.drop_table("backtest_decisions")
    op.drop_index("ix_backtest_runs_configuration", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_created_at_id", table_name="backtest_runs")
    op.drop_table("backtest_runs")
