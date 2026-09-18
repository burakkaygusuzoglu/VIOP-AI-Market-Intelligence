"""The one mutable table Phase 10 adds.

A journal annotation hangs off a paper position and holds nothing financial: a
note, some tags, a version for concurrency and two audit stamps. The foreign key
is ``RESTRICT`` for the same reason the ledger is append-only - nothing in this
application deletes simulated history, so nothing may cascade into it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.persistence.base import Base
from app.domain.journal import MAX_NOTE_LENGTH, MAX_TAGS


class PaperJournalAnnotationRow(Base):
    __tablename__ = "paper_journal_annotations"
    __table_args__ = (
        CheckConstraint("version >= 1", name="positive_version"),
        CheckConstraint(f"char_length(note) <= {MAX_NOTE_LENGTH}", name="note_within_bound"),
        CheckConstraint("jsonb_typeof(tags) = 'array'", name="tags_are_an_array"),
        CheckConstraint(f"jsonb_array_length(tags) <= {MAX_TAGS}", name="tags_within_bound"),
        # Tag filtering asks "which positions carry this tag"; a GIN index
        # answers it without reading every annotation.
        Index("ix_paper_journal_annotations_tags", "tags", postgresql_using="gin"),
    )

    position_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("paper_positions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
