"""PostgreSQL implementation of ``BacktestStore`` (Phase 12).

One idea runs through this module: **a run becomes COMPLETED in one
transaction, or it does not become COMPLETED at all.** ``publish`` writes every
decision, every position and every ledger event alongside the status change, so
there is no window in which a reader could see a completed run holding half a
result. The database backs that up with a check constraint - a COMPLETED row
without a result digest cannot be stored - and the event table carries the same
append-only trigger Phase 9 uses.

Historical candles are absent by design. A run names a Phase 11 dataset and the
runner reads it through the replay store, so the market has one immutable copy
and one rule for what had happened by a given moment.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError

from app.adapters.persistence.backtest_models import (
    BacktestDecisionRow,
    BacktestEventRow,
    BacktestPositionRow,
    BacktestRunRow,
)
from app.adapters.persistence.database import Database
from app.application.backtest.ports import (
    BacktestPosition,
    BacktestResult,
    BacktestStoreUnavailableError,
    CompletedRunError,
    DuplicateRunError,
    LedgerEvent,
    RunSummary,
    RunTotals,
    StoredRun,
    TerminalRunError,
)
from app.application.paper.codec import (
    decode_approval,
    decode_spec,
    encode_approval,
    encode_spec,
)
from app.domain.backtest.run import DecisionOutcome, DecisionRecord, RunStatus
from app.domain.common.enums import Timeframe
from app.domain.paper.model import PaperEvent, PaperEventType

_UNREACHABLE = (OperationalError, InterfaceError)


@asynccontextmanager
async def _reachable() -> AsyncIterator[None]:
    try:
        yield
    except _UNREACHABLE as error:
        raise BacktestStoreUnavailableError(str(error)) from error


class SqlAlchemyBacktestStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    # -- lifecycle ------------------------------------------------------

    async def create_run(self, run: StoredRun) -> StoredRun:
        async with _reachable(), self._database.session() as session:
            session.add(
                BacktestRunRow(
                    id=run.run_id,
                    configuration=run.configuration,
                    attempt_key=run.attempt_key,
                    dataset_id=run.dataset_id,
                    symbol=run.symbol,
                    driver_timeframe=run.driver.value,
                    interval_start=run.interval_start,
                    interval_end=run.interval_end,
                    strategy_id=run.strategy_id,
                    strategy_version=run.strategy_version,
                    strategy_parameters=dict(run.strategy_parameters),
                    simulation=dict(run.simulation),
                    risk=dict(run.risk),
                    product_snapshot=(
                        None if run.product_snapshot is None else dict(run.product_snapshot)
                    ),
                    status=run.status.value,
                    failure_code=None,
                    failure_reason=None,
                    boundaries_evaluated=0,
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                )
            )
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise DuplicateRunError(str(error)) from error
            stored = await session.get(BacktestRunRow, run.run_id)
            assert stored is not None  # noqa: S101 - just written in this transaction
            return _to_run(stored, result=None)

    async def find_by_attempt(self, attempt_key: str) -> StoredRun | None:
        async with _reachable(), self._database.session() as session:
            row = (
                await session.execute(
                    select(BacktestRunRow).where(BacktestRunRow.attempt_key == attempt_key)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return await self._hydrate(session, row)

    async def publish(self, run_id: str, result: BacktestResult, *, now: datetime) -> StoredRun:
        """The whole result and the status change, in one transaction."""
        async with _reachable(), self._database.session() as session:
            row = await session.get(BacktestRunRow, run_id)
            if row is None:
                raise BacktestStoreUnavailableError(f"run {run_id} disappeared before publication")
            if row.status in (RunStatus.FAILED.value, RunStatus.COMPLETED.value):
                # Checked inside the publishing transaction, so an abandon that
                # committed first wins and a stale publisher loses - rather than
                # both appearing to succeed and the last write deciding.
                raise TerminalRunError(
                    f"run {run_id} already ended as {row.status}; a result published now "
                    "would overwrite a terminal state somebody was already told about"
                )

            for position in result.positions:
                session.add(
                    BacktestPositionRow(
                        id=position.position_id,
                        run_id=run_id,
                        ordinal=position.ordinal,
                        spec=encode_spec(position.spec),
                        approval=encode_approval(position.approval),
                        product_snapshot=dict(position.product_snapshot),
                        created_at=now,
                    )
                )
            await session.flush()

            events = [
                {
                    "position_id": position.position_id,
                    "sequence": event.sequence,
                    "event_type": event.type.value,
                    "market_time": event.market_time,
                    "data": dict(event.data),
                }
                for position in result.positions
                for event in position.events
            ]
            if events:
                await session.execute(insert(BacktestEventRow), events)

            decisions = [
                {
                    "run_id": run_id,
                    "sequence": record.sequence,
                    "as_of": record.as_of,
                    "outcome": record.outcome.value,
                    "reason": record.reason[:500],
                    "bars_available": record.bars_available,
                    "position_id": record.position_id,
                    "risk_outcome": record.risk_outcome,
                    "risk_reason": None if record.risk_reason is None else record.risk_reason[:500],
                    "direction": record.direction,
                }
                for record in result.decisions
            ]
            if decisions:
                await session.execute(insert(BacktestDecisionRow), decisions)

            row.status = RunStatus.COMPLETED.value
            row.boundaries_evaluated = result.boundaries_evaluated
            row.first_boundary = result.first_boundary
            row.last_boundary = result.last_boundary
            row.result_digest = result.result_digest
            row.updated_at = now
            await session.commit()

            published = await session.get(BacktestRunRow, run_id)
            assert published is not None  # noqa: S101 - just committed
            return await self._hydrate(session, published)

    async def fail(self, run_id: str, *, code: str, reason: str, now: datetime) -> StoredRun:
        async with _reachable(), self._database.session() as session:
            row = await session.get(BacktestRunRow, run_id)
            if row is None:
                raise BacktestStoreUnavailableError(f"run {run_id} disappeared before failing")
            if row.status == RunStatus.COMPLETED.value:
                # Defensive, not decorative: the caller checks too, and this is
                # the statement that would actually destroy a real result.
                raise CompletedRunError(
                    f"run {run_id} completed and holds a result; it cannot be marked failed"
                )
            row.status = RunStatus.FAILED.value
            row.failure_code = code[:64]
            row.failure_reason = reason[:500]
            row.updated_at = now
            await session.commit()
            failed = await session.get(BacktestRunRow, run_id)
            assert failed is not None  # noqa: S101 - just committed
            return _to_run(failed, result=None)

    # -- reads ----------------------------------------------------------

    async def get(self, run_id: str) -> StoredRun | None:
        async with _reachable(), self._database.session() as session:
            row = await session.get(BacktestRunRow, run_id)
            if row is None:
                return None
            return await self._hydrate(session, row)

    async def head(self, run_id: str) -> tuple[StoredRun, RunTotals] | None:
        """Identity, configuration, status and counts. No trace, no ledgers."""
        async with _reachable(), self._database.session() as session:
            row = await session.get(BacktestRunRow, run_id)
            if row is None:
                return None
            decisions = (
                await session.execute(
                    select(func.count())
                    .select_from(BacktestDecisionRow)
                    .where(BacktestDecisionRow.run_id == run_id)
                )
            ).scalar_one()
            positions = (
                await session.execute(
                    select(func.count())
                    .select_from(BacktestPositionRow)
                    .where(BacktestPositionRow.run_id == run_id)
                )
            ).scalar_one()
            return _to_run(row, result=None), RunTotals(
                boundaries_evaluated=row.boundaries_evaluated,
                first_boundary=row.first_boundary,
                last_boundary=row.last_boundary,
                result_digest=row.result_digest,
                decision_count=decisions,
                position_count=positions,
            )

    async def trace_page(
        self, run_id: str, *, offset: int, limit: int
    ) -> tuple[tuple[DecisionRecord, ...], int]:
        async with _reachable(), self._database.session() as session:
            total = (
                await session.execute(
                    select(func.count())
                    .select_from(BacktestDecisionRow)
                    .where(BacktestDecisionRow.run_id == run_id)
                )
            ).scalar_one()
            rows = (
                (
                    await session.execute(
                        select(BacktestDecisionRow)
                        .where(BacktestDecisionRow.run_id == run_id)
                        .order_by(BacktestDecisionRow.sequence)
                        .offset(offset)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return tuple(_to_decision(row) for row in rows), total

    async def events_of(
        self, position_id: str, *, after_sequence: int, limit: int
    ) -> tuple[tuple[LedgerEvent, ...], int]:
        """A ledger page. Ordered by sequence, which is the order it happened."""
        async with _reachable(), self._database.session() as session:
            total = (
                await session.execute(
                    select(func.count())
                    .select_from(BacktestEventRow)
                    .where(BacktestEventRow.position_id == position_id)
                )
            ).scalar_one()
            rows = (
                (
                    await session.execute(
                        select(BacktestEventRow)
                        .where(BacktestEventRow.position_id == position_id)
                        .where(BacktestEventRow.sequence > after_sequence)
                        .order_by(BacktestEventRow.sequence)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return (
                tuple(
                    LedgerEvent(
                        sequence=row.sequence,
                        type=row.event_type,
                        market_time=row.market_time,
                        data=dict(row.data),
                    )
                    for row in rows
                ),
                total,
            )

    async def list_runs(self, *, offset: int, limit: int) -> tuple[tuple[RunSummary, ...], int]:
        async with _reachable(), self._database.session() as session:
            total = (
                await session.execute(select(func.count()).select_from(BacktestRunRow))
            ).scalar_one()
            rows = (
                (
                    await session.execute(
                        select(BacktestRunRow)
                        .order_by(BacktestRunRow.created_at.desc(), BacktestRunRow.id)
                        .offset(offset)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                return (), total
            counted = (
                await session.execute(
                    select(BacktestPositionRow.run_id, func.count(BacktestPositionRow.id))
                    .where(BacktestPositionRow.run_id.in_([row.id for row in rows]))
                    .group_by(BacktestPositionRow.run_id)
                )
            ).all()
            counts: dict[str, int] = {run: int(count) for run, count in counted}
            return (
                tuple(_to_summary(row, counts.get(row.id, 0)) for row in rows),
                total,
            )

    async def positions_of(self, run_id: str) -> tuple[BacktestPosition, ...]:
        async with _reachable(), self._database.session() as session:
            return await self._positions(session, run_id)

    async def ledger_of(
        self, run_id: str
    ) -> dict[str, Sequence[tuple[str, datetime | None, Mapping[str, Any]]]]:
        """Each position's events in the shape the performance fold reads.

        Deliberately the same tuple shape Phase 10 already folds, so the backtest
        source can reuse that fold rather than growing a second one.
        """
        async with _reachable(), self._database.session() as session:
            ids = [
                row.id
                for row in (
                    await session.execute(
                        select(BacktestPositionRow)
                        .where(BacktestPositionRow.run_id == run_id)
                        .order_by(BacktestPositionRow.ordinal)
                    )
                )
                .scalars()
                .all()
            ]
            if not ids:
                return {}
            rows = (
                (
                    await session.execute(
                        select(BacktestEventRow)
                        .where(BacktestEventRow.position_id.in_(ids))
                        .order_by(BacktestEventRow.position_id, BacktestEventRow.sequence)
                    )
                )
                .scalars()
                .all()
            )
            ledger: dict[str, list[tuple[str, datetime | None, Mapping[str, Any]]]] = {
                position_id: [] for position_id in ids
            }
            for row in rows:
                ledger[row.position_id].append((row.event_type, row.market_time, dict(row.data)))
            return dict(ledger)

    # -- helpers --------------------------------------------------------

    async def _hydrate(self, session: Any, row: BacktestRunRow) -> StoredRun:
        """A run with its result, and only when it really has one."""
        if row.status != RunStatus.COMPLETED.value:
            return _to_run(row, result=None)
        positions = await self._positions(session, row.id)
        decisions = tuple(
            _to_decision(record)
            for record in (
                await session.execute(
                    select(BacktestDecisionRow)
                    .where(BacktestDecisionRow.run_id == row.id)
                    .order_by(BacktestDecisionRow.sequence)
                )
            )
            .scalars()
            .all()
        )
        return _to_run(
            row,
            result=BacktestResult(
                boundaries_evaluated=row.boundaries_evaluated,
                first_boundary=row.first_boundary,
                last_boundary=row.last_boundary,
                decisions=decisions,
                positions=positions,
                result_digest=row.result_digest or "",
            ),
        )

    async def _positions(self, session: Any, run_id: str) -> tuple[BacktestPosition, ...]:
        rows = (
            (
                await session.execute(
                    select(BacktestPositionRow)
                    .where(BacktestPositionRow.run_id == run_id)
                    .order_by(BacktestPositionRow.ordinal)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return ()
        events = (
            (
                await session.execute(
                    select(BacktestEventRow)
                    .where(BacktestEventRow.position_id.in_([row.id for row in rows]))
                    .order_by(BacktestEventRow.position_id, BacktestEventRow.sequence)
                )
            )
            .scalars()
            .all()
        )
        by_position: dict[str, list[PaperEvent]] = {row.id: [] for row in rows}
        for event in events:
            by_position[event.position_id].append(
                PaperEvent(
                    sequence=event.sequence,
                    type=PaperEventType(event.event_type),
                    market_time=event.market_time,
                    data=dict(event.data),
                )
            )
        return tuple(
            BacktestPosition(
                position_id=row.id,
                ordinal=row.ordinal,
                spec=decode_spec(row.spec),
                approval=decode_approval(row.approval),
                product_snapshot=dict(row.product_snapshot),
                events=tuple(by_position[row.id]),
            )
            for row in rows
        )


def _to_run(row: BacktestRunRow, *, result: BacktestResult | None) -> StoredRun:
    return StoredRun(
        run_id=row.id,
        configuration=row.configuration,
        attempt_key=row.attempt_key,
        dataset_id=row.dataset_id,
        symbol=row.symbol,
        driver=Timeframe(row.driver_timeframe),
        interval_start=row.interval_start,
        interval_end=row.interval_end,
        strategy_id=row.strategy_id,
        strategy_version=row.strategy_version,
        strategy_parameters={k: str(v) for k, v in row.strategy_parameters.items()},
        simulation={k: str(v) for k, v in row.simulation.items()},
        risk={k: str(v) for k, v in row.risk.items()},
        product_snapshot=row.product_snapshot,
        status=RunStatus(row.status),
        failure_code=row.failure_code,
        failure_reason=row.failure_reason,
        result=result,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_summary(row: BacktestRunRow, position_count: int) -> RunSummary:
    return RunSummary(
        run_id=row.id,
        configuration=row.configuration,
        dataset_id=row.dataset_id,
        symbol=row.symbol,
        driver=Timeframe(row.driver_timeframe),
        strategy_id=row.strategy_id,
        strategy_version=row.strategy_version,
        status=RunStatus(row.status),
        boundaries_evaluated=row.boundaries_evaluated,
        position_count=position_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_decision(row: BacktestDecisionRow) -> DecisionRecord:
    return DecisionRecord(
        sequence=row.sequence,
        as_of=row.as_of,
        outcome=DecisionOutcome(row.outcome),
        reason=row.reason,
        bars_available=row.bars_available,
        position_id=row.position_id,
        risk_outcome=row.risk_outcome,
        risk_reason=row.risk_reason,
        direction=row.direction,
    )
