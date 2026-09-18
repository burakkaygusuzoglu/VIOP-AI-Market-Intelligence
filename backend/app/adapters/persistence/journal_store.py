"""PostgreSQL implementation of ``JournalStore`` (Phase 10).

Journal writing is ordinary mutable content, so it uses ordinary optimistic
concurrency: a write carries the version it was read at, and the UPDATE only
applies to a row still at that version. If it applies to nothing, someone else
edited it first and the caller is told - nobody's paragraph disappears silently.

There is no event sourcing here, and no edit history: a note has one current
value plus its audit stamps. The financial ledger next door remains append-only
and is not reachable from this module.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.persistence.database import Database
from app.adapters.persistence.journal_models import PaperJournalAnnotationRow
from app.adapters.persistence.paper_models import PaperPositionRow
from app.application.performance.ports import (
    JournalConflictError,
    JournalStoreUnavailableError,
    UnknownPositionError,
)
from app.domain.journal import JournalAnnotation

_UNREACHABLE = (OperationalError, InterfaceError)


@asynccontextmanager
async def _reachable() -> AsyncIterator[None]:
    try:
        yield
    except _UNREACHABLE as error:
        raise JournalStoreUnavailableError(str(error)) from error


class SqlAlchemyJournalStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, position_id: str) -> JournalAnnotation | None:
        async with _reachable(), self._database.session() as session:
            row = await session.get(PaperJournalAnnotationRow, position_id)
            return None if row is None else _to_annotation(row)

    async def position_exists(self, position_id: str) -> bool:
        async with _reachable(), self._database.session() as session:
            found = await session.scalar(
                select(PaperPositionRow.id).where(PaperPositionRow.id == position_id)
            )
            return found is not None

    async def save(
        self, annotation: JournalAnnotation, *, expected_version: int, now: datetime
    ) -> JournalAnnotation:
        """Insert at version 1, or update a row still at ``expected_version``."""
        async with _reachable(), self._database.session() as session:
            if expected_version == 0:
                return await self._insert(session, annotation, now)
            updated = await session.execute(
                text(
                    """
                    UPDATE paper_journal_annotations
                    SET note = :note,
                        tags = CAST(:tags AS jsonb),
                        version = version + 1,
                        updated_at = :now
                    WHERE position_id = :position_id AND version = :expected
                    RETURNING position_id, note, tags, version, created_at, updated_at
                    """
                ),
                {
                    "note": annotation.note,
                    "tags": _json_tags(annotation.tags),
                    "now": now,
                    "position_id": annotation.position_id,
                    "expected": expected_version,
                },
            )
            row = updated.first()
            if row is None:
                await session.rollback()
                raise JournalConflictError(
                    expected_version, await self._current_version(annotation.position_id)
                )
            await session.commit()
            return _row_to_annotation(row)

    async def _insert(
        self, session: AsyncSession, annotation: JournalAnnotation, now: datetime
    ) -> JournalAnnotation:
        try:
            inserted = await session.execute(
                text(
                    """
                    INSERT INTO paper_journal_annotations
                        (position_id, note, tags, version, created_at, updated_at)
                    VALUES (:position_id, :note, CAST(:tags AS jsonb), 1, :now, :now)
                    RETURNING position_id, note, tags, version, created_at, updated_at
                    """
                ),
                {
                    "position_id": annotation.position_id,
                    "note": annotation.note,
                    "tags": _json_tags(annotation.tags),
                    "now": now,
                },
            )
        except IntegrityError as error:
            await session.rollback()
            # The SQLSTATE, not the constraint name: names differ by naming
            # convention and a race must never be reported as a missing position.
            sqlstate = getattr(error.orig, "sqlstate", None)
            if sqlstate == "23505":  # unique_violation: someone inserted first
                raise JournalConflictError(
                    0, await self._current_version(annotation.position_id)
                ) from error
            if sqlstate == "23503":  # foreign_key_violation: no such position
                raise UnknownPositionError(annotation.position_id) from error
            raise
        row = inserted.first()
        await session.commit()
        return _row_to_annotation(row)

    async def _current_version(self, position_id: str) -> int:
        async with _reachable(), self._database.session() as session:
            version = await session.scalar(
                select(PaperJournalAnnotationRow.version).where(
                    PaperJournalAnnotationRow.position_id == position_id
                )
            )
            return int(version or 0)

    async def tag_counts(self, *, limit: int) -> tuple[tuple[str, int], ...]:
        """Every tag in use with how many positions carry it, most used first."""
        async with _reachable(), self._database.session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT tag, count(*) AS uses
                    FROM paper_journal_annotations,
                         LATERAL jsonb_array_elements_text(tags) AS tag
                    GROUP BY tag
                    ORDER BY uses DESC, tag ASC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
            return tuple((row.tag, int(row.uses)) for row in result)

    async def count(self) -> int:
        async with _reachable(), self._database.session() as session:
            total = await session.scalar(
                select(func.count()).select_from(PaperJournalAnnotationRow)
            )
            return int(total or 0)


def _json_tags(tags: tuple[str, ...]) -> str:
    import json

    return json.dumps(list(tags))


def _to_annotation(row: PaperJournalAnnotationRow) -> JournalAnnotation:
    return JournalAnnotation(
        position_id=row.position_id,
        note=row.note,
        tags=tuple(row.tags or ()),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_annotation(row: Any) -> JournalAnnotation:
    return JournalAnnotation(
        position_id=row.position_id,
        note=row.note,
        tags=tuple(row.tags or ()),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
