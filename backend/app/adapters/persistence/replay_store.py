"""PostgreSQL implementation of ``ReplayStore`` (Phase 11).

Two ideas run through this module.

**The dataset is written once.** ``save_dataset`` inserts the candles of a new
identity and returns the existing row for an identity already stored - identical
market data is the same dataset, not a second copy - and a trigger refuses any
later update or delete, so a session can walk the same market again tomorrow.

**The cursor moves under a version.** ``advance_session`` updates conditionally
on the version it read; two tabs stepping at once mean one update applies and
the other is told, rather than the replay quietly jumping two candles.

Candle reads are always bounded by *coverage end*, never by open time, so an
unrevealed bar is not merely hidden later - it is never loaded.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError

from app.adapters.persistence.database import Database
from app.adapters.persistence.replay_models import (
    ReplayCandleRow,
    ReplayDatasetRow,
    ReplayPositionLinkRow,
    ReplaySessionRow,
)
from app.application.replay.ports import (
    DatasetIdentityConflictError,
    DuplicateSessionError,
    ReplayStoreUnavailableError,
    SessionConflictError,
    SessionSummary,
    StoredDataset,
    StoredSession,
    TimeframeSummary,
)
from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from app.domain.replay import ReplayCursor, ReplayPlan, ReplayStatus

_UNREACHABLE = (OperationalError, InterfaceError)


@asynccontextmanager
async def _reachable() -> AsyncIterator[None]:
    try:
        yield
    except _UNREACHABLE as error:
        raise ReplayStoreUnavailableError(str(error)) from error


class SqlAlchemyReplayStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    # -- datasets -------------------------------------------------------

    async def save_dataset(
        self, dataset: StoredDataset, candles: Mapping[Timeframe, Sequence[Candle]]
    ) -> StoredDataset:
        async with _reachable(), self._database.session() as session:
            existing = await session.get(ReplayDatasetRow, dataset.dataset_id)
            if existing is not None:
                # The digest already commits to the symbol, the timeframe
                # partition and every candle, so a match should mean identical
                # market data. This checks it rather than assuming it: if a
                # stored row ever disagreed with what it is being asked to
                # stand for, reusing it would silently serve one market's
                # candles under another market's name.
                stored_identity = _to_dataset(existing)
                if _identity_facts(stored_identity) != _identity_facts(dataset):
                    raise DatasetIdentityConflictError(
                        f"dataset {dataset.dataset_id} is stored for "
                        f"{stored_identity.symbol!r} with "
                        f"{[item.timeframe.value for item in stored_identity.timeframes]} and "
                        f"{stored_identity.total_rows} rows, which is not what this request "
                        "describes"
                    )
                return stored_identity
            rows = [
                {
                    "dataset_id": dataset.dataset_id,
                    "timeframe": timeframe.value,
                    "open_time": candle.open_time,
                    "sequence": index,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                }
                for timeframe, series in candles.items()
                for index, candle in enumerate(series)
            ]
            try:
                session.add(
                    ReplayDatasetRow(
                        id=dataset.dataset_id,
                        symbol=dataset.symbol,
                        timeframes=[_summary_json(item) for item in dataset.timeframes],
                        total_rows=dataset.total_rows,
                    )
                )
                # The parent row must exist before its candles: a bulk insert is
                # a Core statement and does not flush the pending dataset row.
                await session.flush()
                if rows:
                    await session.execute(insert(ReplayCandleRow), rows)
                await session.commit()
            except IntegrityError:
                # Another request stored the same identity between the read and
                # the write. Identical content, so the winner's rows stand.
                await session.rollback()
                stored = await session.get(ReplayDatasetRow, dataset.dataset_id)
                if stored is None:  # pragma: no cover - the winner exists
                    raise
                return _to_dataset(stored)
            stored = await session.get(ReplayDatasetRow, dataset.dataset_id)
            assert stored is not None  # noqa: S101 - just written in this transaction
            return _to_dataset(stored)

    async def get_dataset(self, dataset_id: str) -> StoredDataset | None:
        async with _reachable(), self._database.session() as session:
            row = await session.get(ReplayDatasetRow, dataset_id)
            return None if row is None else _to_dataset(row)

    async def candles(
        self,
        dataset_id: str,
        timeframe: Timeframe,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> tuple[Candle, ...]:
        """Candles in chronological order, bounded by availability and count.

        ``until`` is compared against *coverage end* - open time plus the
        timeframe's duration - which is the availability rule itself, expressed
        in SQL so an unrevealed row never leaves the database.
        """
        statement = select(ReplayCandleRow).where(
            ReplayCandleRow.dataset_id == dataset_id,
            ReplayCandleRow.timeframe == timeframe.value,
        )
        if until is not None:
            statement = statement.where(
                ReplayCandleRow.open_time <= until - timedelta(minutes=timeframe.minutes)
            )
        if since is not None:
            statement = statement.where(ReplayCandleRow.open_time >= since)
        statement = statement.order_by(
            ReplayCandleRow.open_time.desc() if newest_first else ReplayCandleRow.open_time.asc()
        )
        if limit is not None:
            statement = statement.limit(limit)
        async with _reachable(), self._database.session() as session:
            rows = (await session.execute(statement)).scalars().all()
        candles = [_to_candle(row, timeframe) for row in rows]
        if newest_first:
            candles.reverse()
        return tuple(candles)

    # -- sessions -------------------------------------------------------

    async def create_session(
        self, session_state: StoredSession, *, idempotency_key: str, fingerprint: str, now: datetime
    ) -> StoredSession:
        async with _reachable(), self._database.session() as session:
            row = ReplaySessionRow(
                id=session_state.session_id,
                dataset_id=session_state.plan.dataset_id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                symbol=session_state.plan.symbol,
                driver_timeframe=session_state.plan.driver.value,
                replay_start=session_state.plan.replay_start,
                as_of=session_state.cursor.as_of,
                revealed_driver_candles=session_state.cursor.revealed_driver_candles,
                state=session_state.cursor.status.value,
                version=session_state.cursor.version,
                last_command_key=None,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise DuplicateSessionError(str(error)) from error
            dataset = await session.get(ReplayDatasetRow, session_state.plan.dataset_id)
            assert dataset is not None  # noqa: S101 - the foreign key guarantees it
            return _to_session(row, dataset)

    async def find_session_by_key(self, idempotency_key: str) -> tuple[StoredSession, str] | None:
        async with _reachable(), self._database.session() as session:
            row = (
                await session.execute(
                    select(ReplaySessionRow).where(
                        ReplaySessionRow.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            dataset = await session.get(ReplayDatasetRow, row.dataset_id)
            assert dataset is not None  # noqa: S101
            return _to_session(row, dataset), row.request_fingerprint

    async def get_session(self, session_id: str) -> StoredSession | None:
        async with _reachable(), self._database.session() as session:
            row = await session.get(ReplaySessionRow, session_id)
            if row is None:
                return None
            dataset = await session.get(ReplayDatasetRow, row.dataset_id)
            assert dataset is not None  # noqa: S101
            return _to_session(row, dataset)

    async def list_sessions(
        self, *, offset: int, limit: int
    ) -> tuple[tuple[SessionSummary, ...], int]:
        """One page of sessions with the driver's total, in two statements."""
        async with _reachable(), self._database.session() as session:
            total = await session.scalar(select(func.count()).select_from(ReplaySessionRow))
            rows = (
                (
                    await session.execute(
                        select(ReplaySessionRow)
                        .order_by(ReplaySessionRow.created_at.desc(), ReplaySessionRow.id)
                        .offset(offset)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                return (), int(total or 0)
            counts = await session.execute(
                text(
                    """
                    SELECT s.id, count(c.open_time) AS driver_rows
                    FROM replay_sessions s
                    LEFT JOIN replay_candles c
                      ON c.dataset_id = s.dataset_id AND c.timeframe = s.driver_timeframe
                    WHERE s.id = ANY(CAST(:ids AS text[]))
                    GROUP BY s.id
                    """
                ),
                {"ids": [row.id for row in rows]},
            )
            driver_rows = {item.id: int(item.driver_rows) for item in counts}
        return (
            tuple(
                SessionSummary(
                    session_id=row.id,
                    dataset_id=row.dataset_id,
                    symbol=row.symbol,
                    driver=Timeframe(row.driver_timeframe),
                    replay_start=row.replay_start,
                    as_of=row.as_of,
                    revealed_driver_candles=row.revealed_driver_candles,
                    driver_total_candles=driver_rows.get(row.id, 0),
                    state=row.state,
                    version=row.version,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in rows
            ),
            int(total or 0),
        )

    async def advance_session(
        self,
        session_id: str,
        cursor: ReplayCursor,
        *,
        expected_version: int,
        command_key: str | None,
        command_target: int | None,
        now: datetime,
    ) -> StoredSession:
        async with _reachable(), self._database.session() as session:
            result = await session.execute(
                text(
                    """
                    UPDATE replay_sessions
                    SET as_of = :as_of,
                        revealed_driver_candles = :revealed,
                        state = :state,
                        version = :version,
                        last_command_key = :command_key,
                        command_target_revealed = :command_target,
                        updated_at = :now
                    WHERE id = :id AND version = :expected
                    RETURNING id
                    """
                ),
                {
                    "as_of": cursor.as_of,
                    "revealed": cursor.revealed_driver_candles,
                    "state": cursor.status.value,
                    "version": cursor.version,
                    "command_key": command_key,
                    "command_target": command_target,
                    "now": now,
                    "id": session_id,
                    "expected": expected_version,
                },
            )
            if result.first() is None:
                await session.rollback()
                current = await session.get(ReplaySessionRow, session_id)
                raise SessionConflictError(
                    expected_version, current.version if current is not None else -1
                )
            await session.commit()
            row = await session.get(ReplaySessionRow, session_id)
            assert row is not None  # noqa: S101 - just updated
            dataset = await session.get(ReplayDatasetRow, row.dataset_id)
            assert dataset is not None  # noqa: S101
            return _to_session(row, dataset)

    # -- links ----------------------------------------------------------

    async def link_position(self, session_id: str, position_id: str, now: datetime) -> None:
        async with _reachable(), self._database.session() as session:
            session.add(
                ReplayPositionLinkRow(
                    position_id=position_id, session_id=session_id, created_at=now
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                # A position belongs to at most one session; a repeat of the same
                # link is harmless, a different session is refused by the key.
                await session.rollback()
                raise

    async def linked_positions(self, session_id: str) -> tuple[str, ...]:
        async with _reachable(), self._database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(ReplayPositionLinkRow.position_id)
                        .where(ReplayPositionLinkRow.session_id == session_id)
                        .order_by(
                            ReplayPositionLinkRow.created_at, ReplayPositionLinkRow.position_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return tuple(rows)

    async def session_of_position(self, position_id: str) -> str | None:
        async with _reachable(), self._database.session() as session:
            row = await session.get(ReplayPositionLinkRow, position_id)
            return None if row is None else row.session_id


def _summary_json(item: TimeframeSummary) -> dict[str, object]:
    return {
        "timeframe": item.timeframe.value,
        "rows": item.rows,
        "first_open_time": item.first_open_time.isoformat(),
        "last_open_time": item.last_open_time.isoformat(),
        "last_coverage_end": item.last_coverage_end.isoformat(),
    }


def _identity_facts(dataset: StoredDataset) -> tuple[object, ...]:
    """What a dataset id is supposed to stand for, as a comparable tuple."""
    return (
        dataset.symbol,
        dataset.total_rows,
        tuple(
            (
                item.timeframe.value,
                item.rows,
                item.first_open_time,
                item.last_open_time,
                item.last_coverage_end,
            )
            for item in dataset.timeframes
        ),
    )


def _to_summary(item: Mapping[str, object]) -> TimeframeSummary:
    """One stored timeframe summary, with its JSON checked rather than assumed.

    JSONB carries no types of its own, so a row written by anything other than
    this module could hold anything. A summary that does not read back as one is
    a storage fault - reported as such, never patched into a plausible value.
    """
    return TimeframeSummary(
        timeframe=Timeframe(_text(item, "timeframe")),
        rows=_count(item, "rows"),
        first_open_time=datetime.fromisoformat(_text(item, "first_open_time")),
        last_open_time=datetime.fromisoformat(_text(item, "last_open_time")),
        last_coverage_end=datetime.fromisoformat(_text(item, "last_coverage_end")),
    )


def _text(item: Mapping[str, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str):
        raise ReplayStoreUnavailableError(f"stored dataset summary has no text {key!r}")
    return value


def _count(item: Mapping[str, object], key: str) -> int:
    value = item.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ReplayStoreUnavailableError(f"stored dataset summary has no integer {key!r}")
    return value


def _to_dataset(row: ReplayDatasetRow) -> StoredDataset:
    return StoredDataset(
        dataset_id=row.id,
        symbol=row.symbol,
        total_rows=row.total_rows,
        timeframes=tuple(_to_summary(item) for item in row.timeframes),
        created_at=row.created_at,
    )


def _to_session(row: ReplaySessionRow, dataset: ReplayDatasetRow) -> StoredSession:
    return StoredSession(
        session_id=row.id,
        plan=ReplayPlan(
            dataset_id=row.dataset_id,
            symbol=row.symbol,
            driver=Timeframe(row.driver_timeframe),
            replay_start=row.replay_start,
        ),
        cursor=ReplayCursor(
            as_of=row.as_of,
            revealed_driver_candles=row.revealed_driver_candles,
            status=ReplayStatus(row.state),
            version=row.version,
        ),
        dataset=_to_dataset(dataset),
        last_command_key=row.last_command_key,
        command_target_revealed=row.command_target_revealed,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_candle(row: ReplayCandleRow, timeframe: Timeframe) -> Candle:
    """A stored row as a domain candle.

    The symbol is filled in by the caller's context: replay serialises these
    back to CSV for the existing parser, which re-attaches the session's symbol,
    so nothing here invents an instrument identity.
    """
    return Candle(
        symbol="",
        timeframe=timeframe,
        open_time=row.open_time,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        is_closed=True,
    )
