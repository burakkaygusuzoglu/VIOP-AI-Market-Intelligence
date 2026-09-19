"""Deterministic interactive replay (Phase 11).

Four tables: an immutable dataset, its candles, the session cursor over them,
and the link from a session to the paper positions it created. No financial
ledger is added - replay drives the Phase 9 engine and the money stays there -
and no table caches an analysis or a metric.

Both dataset tables are written once. Nothing in the application updates a
candle, and a trigger refuses it at the storage level: a session that has
walked through a dataset must be able to walk it again and see the same market.

Revision ID: 0004_replay
Revises: 0003_paper_journal
Create Date: Phase 11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_replay"
down_revision: str | None = "0003_paper_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_CANDLES = """
CREATE OR REPLACE FUNCTION replay_candles_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'replay_candles is immutable (% refused)', TG_OP;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "replay_datasets",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("timeframes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("total_rows > 0", name=op.f("ck_replay_datasets_dataset_has_rows")),
    )

    op.create_table(
        "replay_candles",
        sa.Column(
            "dataset_id",
            sa.String(length=64),
            sa.ForeignKey("replay_datasets.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("timeframe", sa.String(length=8), primary_key=True),
        sa.Column("open_time", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("open", sa.Numeric(), nullable=False),
        sa.Column("high", sa.Numeric(), nullable=False),
        sa.Column("low", sa.Numeric(), nullable=False),
        sa.Column("close", sa.Numeric(), nullable=False),
        sa.Column("volume", sa.Numeric(), nullable=False),
        sa.CheckConstraint("high >= low", name=op.f("ck_replay_candles_high_not_below_low")),
        sa.CheckConstraint("sequence >= 0", name=op.f("ck_replay_candles_non_negative_sequence")),
    )
    op.create_index(
        "ix_replay_candles_window", "replay_candles", ["dataset_id", "timeframe", "open_time"]
    )

    op.create_table(
        "replay_sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.String(length=64),
            sa.ForeignKey("replay_datasets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("driver_timeframe", sa.String(length=8), nullable=False),
        sa.Column("replay_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revealed_driver_candles", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_command_key", sa.String(length=128), nullable=True),
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
            "state IN ('READY', 'IN_PROGRESS', 'END_OF_DATASET')",
            name=op.f("ck_replay_sessions_valid_state"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_replay_sessions_positive_version")),
        sa.CheckConstraint(
            "revealed_driver_candles >= 0", name=op.f("ck_replay_sessions_non_negative_revealed")
        ),
    )
    op.create_index("ix_replay_sessions_created_at_id", "replay_sessions", ["created_at", "id"])

    op.create_table(
        "replay_position_links",
        sa.Column(
            "position_id",
            sa.String(length=64),
            sa.ForeignKey("paper_positions.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "session_id",
            sa.String(length=64),
            sa.ForeignKey("replay_sessions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_replay_position_links_session", "replay_position_links", ["session_id", "position_id"]
    )

    # The market a session walked through must still be that market tomorrow.
    op.execute(IMMUTABLE_CANDLES)
    op.execute(
        """
        CREATE TRIGGER replay_candles_no_update_or_delete
        BEFORE UPDATE OR DELETE ON replay_candles
        FOR EACH ROW EXECUTE FUNCTION replay_candles_immutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS replay_candles_no_update_or_delete ON replay_candles")
    op.execute("DROP FUNCTION IF EXISTS replay_candles_immutable()")
    op.drop_index("ix_replay_position_links_session", table_name="replay_position_links")
    op.drop_table("replay_position_links")
    op.drop_index("ix_replay_sessions_created_at_id", table_name="replay_sessions")
    op.drop_table("replay_sessions")
    op.drop_index("ix_replay_candles_window", table_name="replay_candles")
    op.drop_table("replay_candles")
    op.drop_table("replay_datasets")
