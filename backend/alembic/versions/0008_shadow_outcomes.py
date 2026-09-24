"""Shadow outcome observation and run-attempt identity (Phase 14 Part 2A).

A forward migration rather than an edit of 0007. The evidence for that choice
is recorded rather than assumed: ``alembic current`` reports ``0007_shadow_mode``
as the applied head on the development database, so 0007 has run somewhere and
rewriting it would leave that database disagreeing with its own history.

Two tables:

``shadow_outcomes`` - where price went after a decision, relative to the levels
that decision proposed. Append-only, like the journal: a development that was
pending and later resolved is a new row, not an edited one. It has no quantity
column and no profit column, deliberately - a level the market touched is not a
filled order and not money, and a column would invite the claim.

``shadow_run_attempts`` - one row per creation attempt key, so a retried or
concurrent HTTP request cannot produce two observers of the same stream. The
uniqueness lives here rather than in a process, so it survives a restart and
holds across workers.

Revision ID: 0008_shadow_outcomes
Revises: 0007_shadow_mode
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_shadow_outcomes"
down_revision = "0007_shadow_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shadow_outcomes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.String(length=64),
            sa.ForeignKey("shadow_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("decision_key", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("outcome_key", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_boundary", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column("rules", sa.String(length=32), nullable=False),
        sa.Column("observed_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("candles_observed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_ordinal", sa.Integer(), nullable=True),
        sa.Column("best_price", sa.Numeric(24, 8), nullable=True),
        sa.Column("worst_price", sa.Numeric(24, 8), nullable=True),
        sa.Column("last_close", sa.Numeric(24, 8), nullable=True),
        sa.Column("ambiguous", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("unresolved_reason", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "state <> 'UNAVAILABLE' OR unresolved_reason IS NOT NULL",
            name="unavailable_outcomes_say_why",
        ),
        # Forward-only causality, enforced by the database and not only by the
        # code that writes it: an outcome may never cite evidence from at or
        # before the boundary its decision was made at.
        sa.CheckConstraint(
            "observed_from IS NULL OR observed_from > decision_boundary",
            name="outcomes_observe_only_what_came_after",
        ),
        sa.CheckConstraint("candles_observed >= 0", name="observed_count_is_not_negative"),
        sa.UniqueConstraint("run_id", "outcome_key", name="uq_shadow_outcome_run_key"),
    )
    op.create_index("ix_shadow_outcomes_run_sequence", "shadow_outcomes", ["run_id", "sequence"])
    op.create_index(
        "ix_shadow_outcomes_run_decision", "shadow_outcomes", ["run_id", "decision_key"]
    )

    op.create_table(
        "shadow_run_attempts",
        sa.Column("attempt_key", sa.String(length=80), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=64),
            sa.ForeignKey("shadow_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("configuration", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.execute(
        """
        CREATE FUNCTION shadow_outcomes_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'shadow_outcomes is append-only (% refused)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER shadow_outcomes_no_update_or_delete
        BEFORE UPDATE OR DELETE ON shadow_outcomes
        FOR EACH ROW EXECUTE FUNCTION shadow_outcomes_append_only();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS shadow_outcomes_no_update_or_delete ON shadow_outcomes")
    op.execute("DROP FUNCTION IF EXISTS shadow_outcomes_append_only()")
    op.drop_table("shadow_run_attempts")
    op.drop_index("ix_shadow_outcomes_run_decision", table_name="shadow_outcomes")
    op.drop_index("ix_shadow_outcomes_run_sequence", table_name="shadow_outcomes")
    op.drop_table("shadow_outcomes")
