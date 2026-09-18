"""Paper trading tables (Phase 9).

Creates exactly the two tables Phase 9 needs - positions and their event ledger -
and a trigger that makes the ledger append-only at the storage level. No table
for a journal, a replay, a backtest or an account exists here, because none of
those exist yet.

Revision ID: 0002_paper_trading
Revises: 0001_baseline
Create Date: Phase 9
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_paper_trading"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    exact = sa.Numeric(asdecimal=True)
    op.create_table(
        "paper_positions",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("approval", postgresql.JSONB(), nullable=False),
        sa.Column("product_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("asset_class", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("remaining", sa.Integer(), nullable=False),
        sa.Column("intended_entry", exact, nullable=False),
        sa.Column("entry_fill_price", exact, nullable=True),
        sa.Column("stop", exact, nullable=False),
        sa.Column("last_mark", exact, nullable=True),
        sa.Column("last_bar_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("realized_gross", exact, nullable=False),
        sa.Column("fees_total", exact, nullable=True),
        sa.Column("realized_net", exact, nullable=True),
        sa.Column("unrealized_gross", exact, nullable=True),
        sa.Column("bars_applied", sa.Integer(), nullable=False),
        sa.Column("close_pending", sa.Boolean(), nullable=False),
        sa.Column("rules_version", sa.String(32), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_paper_positions"),
        sa.UniqueConstraint("idempotency_key", name="uq_paper_positions_idempotency_key"),
        sa.CheckConstraint(
            _in("state", POSITION_STATES), name=op.f("ck_paper_positions_valid_state")
        ),
        sa.CheckConstraint(
            "direction IN ('LONG', 'SHORT')", name=op.f("ck_paper_positions_valid_direction")
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_paper_positions_positive_quantity")),
        sa.CheckConstraint(
            "remaining >= 0 AND remaining <= quantity",
            name=op.f("ck_paper_positions_remaining_in_range"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_paper_positions_positive_version")),
        sa.CheckConstraint("bars_applied >= 0", name=op.f("ck_paper_positions_non_negative_bars")),
        sa.CheckConstraint("event_count >= 1", name=op.f("ck_paper_positions_has_creation_event")),
    )
    op.create_index("ix_paper_positions_created_at_id", "paper_positions", ["created_at", "id"])

    op.create_table(
        "paper_position_events",
        sa.Column("position_id", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("market_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("position_id", "sequence", name="pk_paper_position_events"),
        sa.ForeignKeyConstraint(
            ["position_id"],
            ["paper_positions.id"],
            name="fk_paper_position_events_position_id_paper_positions",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "sequence >= 1", name=op.f("ck_paper_position_events_positive_sequence")
        ),
        sa.CheckConstraint(
            _in("event_type", EVENT_TYPES), name=op.f("ck_paper_position_events_valid_event_type")
        ),
    )

    # Append-only at the storage level. An event is a fact about what the
    # simulation did; correcting one in place would rewrite history that a replay
    # is required to reproduce.
    op.execute(
        """
        CREATE FUNCTION paper_position_events_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'paper_position_events is append-only (% refused)', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER paper_position_events_no_update_or_delete
        BEFORE UPDATE OR DELETE ON paper_position_events
        FOR EACH ROW EXECUTE FUNCTION paper_position_events_append_only();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS paper_position_events_no_update_or_delete ON paper_position_events"
    )
    op.execute("DROP FUNCTION IF EXISTS paper_position_events_append_only()")
    op.drop_table("paper_position_events")
    op.drop_index("ix_paper_positions_created_at_id", table_name="paper_positions")
    op.drop_table("paper_positions")
