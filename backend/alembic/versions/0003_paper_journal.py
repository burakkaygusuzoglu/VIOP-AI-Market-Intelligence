"""Journal annotations (Phase 10).

One table, holding only what a person writes: a note, some tags, a version and
two audit stamps. Nothing financial is stored here and nothing in Phase 9
changes - the ledger, its trigger and the positions table are untouched.

There is deliberately no table for cached metrics. Performance is computed from
the ledger on demand, so a second store of financial numbers cannot drift away
from the first.

Revision ID: 0003_paper_journal
Revises: 0002_paper_trading
Create Date: Phase 10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_paper_journal"
down_revision: str | None = "0002_paper_trading"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_NOTE_LENGTH = 4000
MAX_TAGS = 12


def upgrade() -> None:
    op.create_table(
        "paper_journal_annotations",
        sa.Column(
            "position_id",
            sa.String(length=64),
            sa.ForeignKey("paper_positions.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
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
            "version >= 1", name=op.f("ck_paper_journal_annotations_positive_version")
        ),
        sa.CheckConstraint(
            f"char_length(note) <= {MAX_NOTE_LENGTH}",
            name=op.f("ck_paper_journal_annotations_note_within_bound"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(tags) = 'array'",
            name=op.f("ck_paper_journal_annotations_tags_are_an_array"),
        ),
        sa.CheckConstraint(
            f"jsonb_array_length(tags) <= {MAX_TAGS}",
            name=op.f("ck_paper_journal_annotations_tags_within_bound"),
        ),
    )
    # Tag filtering asks "which positions carry this tag"; a GIN index answers it
    # without reading every annotation.
    op.create_index(
        "ix_paper_journal_annotations_tags",
        "paper_journal_annotations",
        ["tags"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_paper_journal_annotations_tags", table_name="paper_journal_annotations")
    op.drop_table("paper_journal_annotations")
