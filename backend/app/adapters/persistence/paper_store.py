"""PostgreSQL implementation of ``PaperStore`` (Phase 9).

## Concurrency: a row lock, and a version as the backstop

Every write loads the position with ``SELECT ... FOR UPDATE`` inside its
transaction. A second request for the same position waits for the first to
commit, then rebuilds from what the first wrote - so two requests racing to
close the same remaining units cannot both close them. That is the whole
strategy; no distributed lock is needed for one database.

The ``version`` column is the backstop: the projection update is conditional on
the version that was loaded, and the event primary key ``(position_id,
sequence)`` refuses a second event at the same position in the ledger. Either
tripping raises ``ConcurrentModificationError`` rather than overwriting.

## Idempotent creation

``idempotency_key`` is unique. Two identical creates that race past the lookup
both try to insert; the second hits the constraint and gets
``DuplicatePositionError``, and the service answers from the row that won.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.persistence.database import Database
from app.adapters.persistence.paper_models import PaperEventRow, PaperPositionRow
from app.application.paper.codec import (
    decode_approval,
    decode_spec,
    encode_approval,
    encode_spec,
)
from app.application.ports.paper import (
    ConcurrentModificationError,
    DuplicatePositionError,
    PaperStoreUnavailableError,
    PositionProjection,
    PositionSummary,
    StoredEvent,
    StoredPosition,
)
from app.domain.paper import PaperEvent, PaperEventType, PaperPosition

_UNREACHABLE = (OperationalError, InterfaceError)
"""Driver errors that mean "the database could not be used", as opposed to a
constraint or a query that was wrong."""


@asynccontextmanager
async def _reachable() -> AsyncIterator[None]:
    try:
        yield
    except _UNREACHABLE as error:
        raise PaperStoreUnavailableError("the paper-trading store is unavailable") from error


class SqlAlchemyPaperStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    @asynccontextmanager
    async def unit_of_work(self) -> AsyncIterator[_UnitOfWork]:
        async with _reachable(), self._database.session() as session:
            yield _UnitOfWork(session)

    async def get(self, position_id: str) -> StoredPosition | None:
        async with _reachable(), self._database.session() as session:
            row = await session.get(PaperPositionRow, position_id)
            if row is None:
                return None
            return await _load(session, row)

    async def list_summaries(
        self, *, offset: int, limit: int
    ) -> tuple[Sequence[PositionSummary], int]:
        async with _reachable(), self._database.session() as session:
            total = await session.scalar(select(func.count()).select_from(PaperPositionRow))
            rows = (
                await session.scalars(
                    select(PaperPositionRow)
                    .order_by(PaperPositionRow.created_at.desc(), PaperPositionRow.id)
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        items = [
            PositionSummary(
                position_id=row.id,
                projection=_projection(row),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]
        return items, int(total or 0)

    async def events_page(
        self, position_id: str, *, after_sequence: int, limit: int
    ) -> tuple[Sequence[StoredEvent], int]:
        async with _reachable(), self._database.session() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(PaperEventRow)
                .where(PaperEventRow.position_id == position_id)
            )
            rows = (
                await session.scalars(
                    select(PaperEventRow)
                    .where(
                        PaperEventRow.position_id == position_id,
                        PaperEventRow.sequence > after_sequence,
                    )
                    .order_by(PaperEventRow.sequence)
                    .limit(limit)
                )
            ).all()
        return [StoredEvent(event=_event(row), recorded_at=row.recorded_at) for row in rows], int(
            total or 0
        )


class _UnitOfWork:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_idempotency_key(self, key: str) -> StoredPosition | None:
        row = await self._session.scalar(
            select(PaperPositionRow).where(PaperPositionRow.idempotency_key == key)
        )
        return None if row is None else await _load(self._session, row)

    async def load_for_update(self, position_id: str) -> StoredPosition | None:
        row = await self._session.scalar(
            select(PaperPositionRow)
            .where(PaperPositionRow.id == position_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return None if row is None else await _load(self._session, row)

    async def insert(self, stored: StoredPosition, recorded_at: datetime) -> None:
        projection = stored.projection
        self._session.add(
            PaperPositionRow(
                id=stored.position_id,
                idempotency_key=stored.idempotency_key,
                request_fingerprint=stored.request_fingerprint,
                version=stored.version,
                spec=encode_spec(stored.spec),
                approval=encode_approval(stored.approval),
                product_snapshot=dict(stored.product_snapshot),
                created_at=stored.created_at,
                updated_at=stored.updated_at,
                **_projection_columns(projection),
            )
        )
        try:
            await self._session.flush()
            for event in stored.events:
                self._session.add(_event_row(stored.position_id, event, recorded_at))
            await self._session.flush()
        except IntegrityError as error:
            await self._session.rollback()
            raise DuplicatePositionError(str(error.orig)) from error

    async def append(
        self,
        stored: StoredPosition,
        position: PaperPosition,
        projection: PositionProjection,
        recorded_at: datetime,
    ) -> StoredPosition:
        new_events = position.events[len(stored.events) :]
        if tuple(position.events[: len(stored.events)]) != tuple(stored.events):
            raise ConcurrentModificationError("the ledger prefix does not match what was loaded")
        result = await self._session.execute(
            update(PaperPositionRow)
            .where(
                PaperPositionRow.id == stored.position_id,
                PaperPositionRow.version == stored.version,
            )
            .values(
                version=stored.version + 1,
                updated_at=recorded_at,
                **_projection_columns(projection),
            )
        )
        if getattr(result, "rowcount", 0) != 1:
            await self._session.rollback()
            raise ConcurrentModificationError("the position version changed")
        try:
            for event in new_events:
                self._session.add(_event_row(stored.position_id, event, recorded_at))
            await self._session.flush()
        except IntegrityError as error:
            await self._session.rollback()
            raise ConcurrentModificationError(str(error.orig)) from error
        return StoredPosition(
            position_id=stored.position_id,
            idempotency_key=stored.idempotency_key,
            request_fingerprint=stored.request_fingerprint,
            spec=stored.spec,
            approval=stored.approval,
            product_snapshot=stored.product_snapshot,
            events=tuple(position.events),
            projection=projection,
            version=stored.version + 1,
            created_at=stored.created_at,
            updated_at=recorded_at,
        )

    async def commit(self) -> None:
        await self._session.commit()


async def _load(session: AsyncSession, row: PaperPositionRow) -> StoredPosition:
    events = (
        await session.scalars(
            select(PaperEventRow)
            .where(PaperEventRow.position_id == row.id)
            .order_by(PaperEventRow.sequence)
        )
    ).all()
    return StoredPosition(
        position_id=row.id,
        idempotency_key=row.idempotency_key,
        request_fingerprint=row.request_fingerprint,
        spec=decode_spec(row.spec),
        approval=decode_approval(row.approval),
        product_snapshot=row.product_snapshot,
        events=tuple(_event(event) for event in events),
        projection=_projection(row),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _event(row: PaperEventRow) -> PaperEvent:
    return PaperEvent(
        sequence=row.sequence,
        type=PaperEventType(row.event_type),
        market_time=row.market_time,
        data={str(key): str(value) for key, value in row.data.items()},
    )


def _event_row(position_id: str, event: PaperEvent, recorded_at: datetime) -> PaperEventRow:
    return PaperEventRow(
        position_id=position_id,
        sequence=event.sequence,
        event_type=event.type.value,
        market_time=event.market_time,
        data=dict(event.data),
        recorded_at=recorded_at,
    )


def _projection(row: PaperPositionRow) -> PositionProjection:
    return PositionProjection(
        state=row.state,
        symbol=row.symbol,
        asset_class=row.asset_class,
        direction=row.direction,
        quantity=row.quantity,
        remaining=row.remaining,
        intended_entry=row.intended_entry,
        entry_fill_price=row.entry_fill_price,
        stop=row.stop,
        last_mark=row.last_mark,
        last_bar_time=row.last_bar_time,
        realized_gross=row.realized_gross,
        fees_total=row.fees_total,
        realized_net=row.realized_net,
        unrealized_gross=row.unrealized_gross,
        bars_applied=row.bars_applied,
        close_pending=row.close_pending,
        rules_version=row.rules_version,
        event_count=row.event_count,
    )


def _projection_columns(projection: PositionProjection) -> dict[str, object]:
    return {
        "state": projection.state,
        "symbol": projection.symbol,
        "asset_class": projection.asset_class,
        "direction": projection.direction,
        "quantity": projection.quantity,
        "remaining": projection.remaining,
        "intended_entry": projection.intended_entry,
        "entry_fill_price": projection.entry_fill_price,
        "stop": projection.stop,
        "last_mark": projection.last_mark,
        "last_bar_time": projection.last_bar_time,
        "realized_gross": projection.realized_gross,
        "fees_total": projection.fees_total,
        "realized_net": projection.realized_net,
        "unrealized_gross": projection.unrealized_gross,
        "bars_applied": projection.bars_applied,
        "close_pending": projection.close_pending,
        "rules_version": projection.rules_version,
        "event_count": projection.event_count,
    }
