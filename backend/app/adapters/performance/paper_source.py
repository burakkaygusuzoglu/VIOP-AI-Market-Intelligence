"""Authoritative performance records, folded from the paper ledger.

## Why the ledger and not the row

``paper_positions`` carries a projection: convenient, mutable, and - as Phase 9
established - not financial authority. Every fact this adapter reports comes
instead from ``paper_position_events``, which is append-only and trigger-
protected:

* ``POSITION_CREATED`` froze the symbol, asset class, direction, quantity and
  timeframe at the moment the position was opened;
* ``TARGET_FILLED`` / ``STOP_FILLED`` / ``MANUAL_EXIT_FILLED`` each recorded the
  gross amount and the fee of one fill;
* ``POSITION_CLOSED`` recorded the final realized gross, fees and net;
* ``ENTRY_REJECTED`` and ``POSITION_CANCELLED`` say the position never entered.

Nothing is recomputed here - no P&L formula, no fee arithmetic beyond adding up
amounts the engine already wrote. A test proves the folded outcome equals the
one a full verified rebuild produces.

## The one thing the ledger does not hold

Unrealized P&L is a mark-to-market of *now*, not a past event, so it is not in
the ledger. For positions still exposed it is read through Phase 9's verified
path (``PaperTradingService.get``), which replays the ledger and refuses if the
stored projection disagrees. That path is bounded: it runs only for positions
that are still open, and only up to ``MAX_VERIFIED_OPEN``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError

from app.adapters.persistence.database import Database
from app.application.paper.codec import StoredValueError, decode_approval, decode_spec
from app.application.performance.ports import (
    JournalRow,
    OutcomeFilters,
    OutcomePage,
    PerformanceSourceUnavailableError,
)
from app.application.ports.paper import ProductSnapshotCodec, ProductSnapshotError
from app.domain.common.enums import Direction, Timeframe
from app.domain.journal import JournalAnnotation
from app.domain.paper import (
    PaperEvent,
    PaperEventType,
    PaperRefusalError,
    rebuild,
    unrealized_gross,
)
from app.domain.performance import (
    InstrumentIdentity,
    Population,
    PositionOutcome,
    RealizedFill,
)

MAX_VERIFIED_OPEN = 50
"""How many still-open positions will be verified for their current mark in one
request. Beyond this the open total is reported unavailable rather than making
an analytics request replay an unbounded number of ledgers."""

_EXIT_EVENTS = ("TARGET_FILLED", "STOP_FILLED", "MANUAL_EXIT_FILLED")
_FINANCIAL_EVENTS = (
    "POSITION_CREATED",
    "ENTRY_FILLED",
    "ENTRY_REJECTED",
    "SAME_BAR_AMBIGUITY",
    "POSITION_CANCELLED",
    "POSITION_CLOSED",
    *_EXIT_EVENTS,
)
"""Everything that carries a financial fact. Bar observations are excluded, so
the cost of analytics does not grow with how many bars a position was fed."""

# Position-level selection. Every filter compares a frozen creation fact or a
# terminal market time; nothing reads the mutable projection row.
_SELECT_IDS = """
WITH created AS (
    SELECT position_id, data, market_time
    FROM paper_position_events
    WHERE sequence = 1
),
terminal AS (
    SELECT position_id,
           max(market_time) FILTER (WHERE event_type = 'POSITION_CLOSED') AS closed_at
    FROM paper_position_events
    WHERE event_type = 'POSITION_CLOSED'
    GROUP BY position_id
),
-- When any exit filled. A position whose *close* falls outside the range may
-- still have realized money inside it, and dropping the position here would
-- take that money with it.
fills AS (
    SELECT position_id,
           min(market_time) AS first_fill_at,
           max(market_time) AS last_fill_at
    FROM paper_position_events
    WHERE event_type IN ('TARGET_FILLED', 'STOP_FILLED', 'MANUAL_EXIT_FILLED')
    GROUP BY position_id
)
SELECT c.position_id, count(*) OVER () AS total_matching
FROM created c
LEFT JOIN terminal t ON t.position_id = c.position_id
LEFT JOIN fills f ON f.position_id = c.position_id
LEFT JOIN paper_journal_annotations j ON j.position_id = c.position_id
WHERE (CAST(:direction AS text) IS NULL OR c.data->>'direction' = CAST(:direction AS text))
  AND (CAST(:symbol AS text) IS NULL OR c.data->>'symbol' = CAST(:symbol AS text))
  AND (CAST(:timeframe AS text) IS NULL OR c.data->>'timeframe' = CAST(:timeframe AS text))
  AND (CAST(:tag AS text) IS NULL OR j.tags @> to_jsonb(ARRAY[CAST(:tag AS text)]))
  -- A date range selects completed trades by their closing market time. A
  -- position that has not finished has no closing time, so it cannot be chosen
  -- that way - and dropping it would make current exposure vanish from a
  -- filtered view while still being labelled current. Non-terminal positions
  -- therefore stay in the selection; which metrics use them, and under which
  -- time rule, is decided per metric.
  AND (
        t.closed_at IS NULL
        OR (
              -- a fill of this position lands inside the range
              CAST(:closed_to AS timestamptz) IS NOT NULL
              AND CAST(:closed_from AS timestamptz) IS NOT NULL
              AND f.last_fill_at >= CAST(:closed_from AS timestamptz)
              AND f.first_fill_at <= CAST(:closed_to AS timestamptz)
        )
        OR (
              (
                CAST(:closed_from AS timestamptz) IS NULL
                OR t.closed_at >= CAST(:closed_from AS timestamptz)
              )
          AND (
                CAST(:closed_to AS timestamptz) IS NULL
                OR t.closed_at <= CAST(:closed_to AS timestamptz)
              )
        )
  )
  AND (NOT CAST(:completed_only AS boolean) OR t.closed_at IS NOT NULL)
ORDER BY t.closed_at NULLS LAST, c.market_time, c.position_id
"""


def _decimal(value: str | None) -> Decimal | None:
    """A recorded amount, or None when the ledger recorded no value.

    Phase 9 writes an empty string where a value was not modelled - an absent
    fee is not a zero fee - so that distinction survives the read.
    """
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation as error:  # pragma: no cover - ledger values are engine-written
        raise PerformanceSourceUnavailableError(
            f"unreadable amount in the ledger: {value!r}"
        ) from error


class SqlPaperPerformanceSource:
    """``PerformanceSource`` over the Phase 9 paper ledger."""

    def __init__(self, database: Database, codec: ProductSnapshotCodec) -> None:
        self._database = database
        self._codec = codec

    async def count_matching(self, filters: OutcomeFilters) -> int:
        """How many positions match, counted in the database.

        The service asks this before loading anything, so an oversized range is
        refused without reading a single ledger.
        """
        _ids, total = await self._matching(filters)
        return total

    async def outcomes(self, filters: OutcomeFilters, *, limit: int) -> OutcomePage:
        ids, total = await self._matching(filters)
        selected = ids[:limit]
        folded, fills = await self._fold_positions(selected)
        records = await self._with_open_marks(folded)
        return OutcomePage(records=records, fill_count=fills, total_matching=total)

    async def journal_rows(
        self, filters: OutcomeFilters, *, offset: int, limit: int
    ) -> tuple[tuple[JournalRow, ...], int]:
        ids, total = await self._matching(filters)
        page = ids[offset : offset + limit]
        folded, _ = await self._fold_positions(page)
        annotations = await self._annotations(page)
        rows = tuple(
            JournalRow(
                outcome=record,
                annotation=annotations.get(
                    record.position_id, JournalAnnotation(position_id=record.position_id)
                ),
            )
            for record in folded
        )
        return rows, total

    # -- reading --------------------------------------------------------

    def _parameters(self, filters: OutcomeFilters) -> dict[str, object]:
        return {
            "direction": filters.direction.value if filters.direction else None,
            "symbol": filters.symbol,
            "timeframe": filters.timeframe.value if filters.timeframe else None,
            "tag": filters.tag,
            "closed_from": filters.closed_from,
            "closed_to": filters.closed_to,
            "completed_only": bool(filters.outcomes_only),
        }

    async def _matching(self, filters: OutcomeFilters) -> tuple[list[str], int]:
        """Matching position ids and how many there are, in one statement.

        The total comes back as a window count on the same rows, so the number
        used to bound a request and the ids that are then read can never
        describe different selections.
        """
        async with self._connection() as connection:
            result = await connection.execute(text(_SELECT_IDS), self._parameters(filters))
            rows = result.all()
        return [row[0] for row in rows], (int(rows[0][1]) if rows else 0)

    async def _fold_positions(self, ids: Sequence[str]) -> tuple[tuple[PositionOutcome, ...], int]:
        if not ids:
            return (), 0
        query = text(
            """
            SELECT position_id, sequence, event_type, market_time, data
            FROM paper_position_events
            WHERE position_id = ANY(CAST(:ids AS text[]))
              AND event_type = ANY(CAST(:types AS text[]))
            ORDER BY position_id, sequence
            """
        )
        async with self._connection() as connection:
            result = await connection.execute(
                query, {"ids": list(ids), "types": list(_FINANCIAL_EVENTS)}
            )
            rows = result.all()

        grouped: dict[str, list[tuple[str, datetime | None, Mapping[str, Any]]]] = {}
        for position_id, _sequence, event_type, market_time, data in rows:
            grouped.setdefault(position_id, []).append((event_type, market_time, data))

        order = {position_id: index for index, position_id in enumerate(ids)}
        records = [_fold(position_id, events) for position_id, events in grouped.items()]
        records.sort(key=lambda record: order[record.position_id])
        fills = sum(
            1
            for events in grouped.values()
            for event_type, _time, _data in events
            if event_type in _EXIT_EVENTS
        )
        return tuple(records), fills

    async def _annotations(self, ids: Sequence[str]) -> dict[str, JournalAnnotation]:
        if not ids:
            return {}
        query = text(
            """
            SELECT position_id, note, tags, version, created_at, updated_at
            FROM paper_journal_annotations
            WHERE position_id = ANY(CAST(:ids AS text[]))
            """
        )
        async with self._connection() as connection:
            result = await connection.execute(query, {"ids": list(ids)})
            return {
                row.position_id: JournalAnnotation(
                    position_id=row.position_id,
                    note=row.note,
                    tags=tuple(row.tags or ()),
                    version=row.version,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in result
            }

    async def _with_open_marks(
        self, records: Sequence[PositionOutcome]
    ) -> tuple[PositionOutcome, ...]:
        """Fill in the current mark for still-open positions, in two statements.

        Unrealized P&L is a mark of *now*, so it is not in the ledger. It is
        obtained the way Phase 9 obtains it - replay the stored inputs and
        require the stored projection to agree - but for every open position at
        once rather than one request each: one read of their rows, one read of
        their events, then the replay in memory. A position whose row disagrees
        with its ledger is refused, exactly as Phase 9's own read refuses it.
        """
        exposed = [record for record in records if record.population.open_exposure]
        if not exposed:
            return tuple(records)
        if len(exposed) > MAX_VERIFIED_OPEN:
            # Bounded on purpose: the total is reported unavailable rather than
            # replaying an unbounded number of ledgers inside one request.
            return tuple(records)

        ids = [record.position_id for record in exposed]
        rows = await self._position_rows(ids)
        events = await self._all_events(ids)
        marks: dict[str, Decimal | None] = {}
        for position_id in ids:
            row = rows.get(position_id)
            if row is None:  # pragma: no cover - selected from the same table
                raise PerformanceSourceUnavailableError(f"position {position_id} vanished")
            marks[position_id] = _verified_mark(
                position_id, row, events.get(position_id, ()), self._codec
            )
        return tuple(
            record
            if record.position_id not in marks
            else _replace_unrealized(record, marks[record.position_id])
            for record in records
        )

    async def _position_rows(self, ids: Sequence[str]) -> dict[str, Any]:
        query = text(
            """
            SELECT id, spec, approval, product_snapshot, state, remaining,
                   entry_fill_price, stop, last_mark, realized_gross, fees_total,
                   realized_net, unrealized_gross, bars_applied, event_count
            FROM paper_positions
            WHERE id = ANY(CAST(:ids AS text[]))
            """
        )
        async with self._connection() as connection:
            result = await connection.execute(query, {"ids": list(ids)})
            return {row.id: row for row in result}

    async def _all_events(
        self, ids: Sequence[str]
    ) -> dict[str, list[tuple[int, str, datetime | None, Mapping[str, Any]]]]:
        """Every event of these positions - observations included, for replay."""
        query = text(
            """
            SELECT position_id, sequence, event_type, market_time, data
            FROM paper_position_events
            WHERE position_id = ANY(CAST(:ids AS text[]))
            ORDER BY position_id, sequence
            """
        )
        async with self._connection() as connection:
            result = await connection.execute(query, {"ids": list(ids)})
            grouped: dict[str, list[tuple[int, str, datetime | None, Mapping[str, Any]]]] = {}
            for position_id, sequence, event_type, market_time, data in result:
                grouped.setdefault(position_id, []).append(
                    (sequence, event_type, market_time, data)
                )
            return grouped

    def _connection(self) -> _Guarded:
        return _Guarded(self._database)


def _verified_mark(
    position_id: str,
    row: Any,
    events: Sequence[tuple[int, str, datetime | None, Mapping[str, Any]]],
    codec: ProductSnapshotCodec,
) -> Decimal | None:
    """Replay this position and return its mark, or refuse if the row disagrees.

    The comparison is the point: the number reported is the replayed one, and a
    projection that does not match its own ledger is never quietly adopted.
    """
    try:
        product = codec.restore(row.product_snapshot)
        spec = decode_spec(row.spec)
        approval = decode_approval(row.approval)
        position = rebuild(
            spec,
            approval,
            product,
            tuple(
                PaperEvent(
                    sequence=sequence,
                    type=PaperEventType(event_type),
                    market_time=market_time,
                    data=data,
                )
                for sequence, event_type, market_time, data in events
            ),
        )
        replayed = unrealized_gross(position, product)
    except (PaperRefusalError, ProductSnapshotError, StoredValueError, ValueError) as error:
        raise PerformanceSourceUnavailableError(
            f"position {position_id} could not be verified: {error}"
        ) from error
    mismatch = _row_disagreement(row, position, replayed)
    if mismatch:
        raise PerformanceSourceUnavailableError(
            f"position {position_id} has a stored row that disagrees with its ledger: {mismatch}"
        )
    return replayed


def _row_disagreement(row: Any, position: Any, replayed_mark: Decimal | None) -> str | None:
    """Which stored column, if any, contradicts the replayed position.

    The same check Phase 9's own read performs, applied to the columns that
    carry financial meaning - so an edited row is refused here too rather than
    being read past in silence.
    """
    expected: dict[str, Decimal | int | str | None] = {
        "state": position.state.value,
        "remaining": position.remaining,
        "entry_fill_price": position.entry_fill_price,
        "stop": position.stop,
        "last_mark": position.last_mark,
        "realized_gross": position.realized_gross,
        "fees_total": position.fees_total,
        "realized_net": position.realized_net,
        "unrealized_gross": replayed_mark,
        "bars_applied": position.bars_applied,
        "event_count": len(position.events),
    }
    for column, want in expected.items():
        stored = getattr(row, column)
        if isinstance(want, Decimal) or isinstance(stored, Decimal):
            # NUMERIC round-trips as Decimal; compare by value, not by scale.
            if (stored is None) != (want is None):
                return column
            if (
                stored is not None
                and want is not None
                and Decimal(str(stored)) != Decimal(str(want))
            ):
                return column
        elif stored != want:
            return column
    return None


def _replace_unrealized(record: PositionOutcome, value: Decimal | None) -> PositionOutcome:
    from dataclasses import replace

    return replace(record, unrealized_gross=value)


def _fold(
    position_id: str, events: Sequence[tuple[str, datetime | None, Mapping[str, Any]]]
) -> PositionOutcome:
    """One position's ledger, read into one authoritative outcome record."""
    creation = next((data for kind, _t, data in events if kind == "POSITION_CREATED"), None)
    if creation is None:  # pragma: no cover - sequence 1 is always the creation event
        raise PerformanceSourceUnavailableError(f"position {position_id} has no creation event")

    kinds = {kind for kind, _t, _d in events}
    entry_time = next(
        (time for kind, time, _d in events if kind == "ENTRY_FILLED"),
        None,
    )
    closed = next((data for kind, _t, data in events if kind == "POSITION_CLOSED"), None)
    terminal_time = next(
        (time for kind, time, _d in events if kind == "POSITION_CLOSED"),
        None,
    )

    if "ENTRY_REJECTED" in kinds:
        population = Population.REJECTED
    elif "POSITION_CANCELLED" in kinds:
        population = Population.CANCELLED
    elif closed is not None:
        population = Population.CLOSED
    elif "ENTRY_FILLED" not in kinds:
        population = Population.PENDING_ENTRY
    elif _halted(events):
        population = Population.AMBIGUOUS_HALTED
    elif any(kind in _EXIT_EVENTS for kind, _t, _d in events):
        population = Population.ENTERED_PARTIALLY_CLOSED
    else:
        population = Population.OPEN

    fee_modelled = creation.get("fee_mode") != "NOT_MODELLED"
    if closed is not None:
        realized_gross = _decimal(closed.get("realized_gross")) or Decimal(0)
        fees_total = _decimal(closed.get("fees_total")) if fee_modelled else None
        realized_net = _decimal(closed.get("realized_net")) if fee_modelled else None
    else:
        exits = [data for kind, _t, data in events if kind in _EXIT_EVENTS]
        realized_gross = sum(
            ((_decimal(data.get("gross_pnl")) or Decimal(0)) for data in exits), Decimal(0)
        )
        fees_total = (
            sum(((_decimal(data.get("fee")) or Decimal(0)) for data in exits), Decimal(0))
            if fee_modelled
            else None
        )
        realized_net = None if fees_total is None else realized_gross - fees_total

    realized_fills = tuple(
        RealizedFill(
            amount=_decimal(data.get("gross_pnl")) or Decimal(0),
            fee=_decimal(data.get("fee")) if fee_modelled else None,
            market_time=market_time,
        )
        for kind, market_time, data in events
        if kind in _EXIT_EVENTS and market_time is not None
    )

    return PositionOutcome(
        position_id=position_id,
        instrument=InstrumentIdentity(
            symbol=str(creation["symbol"]), asset_class=str(creation["asset_class"])
        ),
        direction=Direction(str(creation["direction"])),
        timeframe=Timeframe(str(creation["timeframe"])),
        quantity=int(creation["quantity"]),
        population=population,
        realized_gross=realized_gross,
        fees_total=fees_total,
        realized_net=realized_net,
        unrealized_gross=None,
        fee_mode=str(creation.get("fee_mode", "")),
        decision_time=_creation_time(events),
        entry_time=entry_time,
        terminal_time=terminal_time if population.completed else None,
        fills=realized_fills,
    )


def _halted(events: Sequence[tuple[str, datetime | None, Mapping[str, Any]]]) -> bool:
    """A HALT ambiguity froze the position and nothing has closed it since."""
    for kind, _time, data in reversed(events):
        if kind == "SAME_BAR_AMBIGUITY":
            return str(data.get("policy")) == "HALT"
        if kind in {"POSITION_CLOSED", *_EXIT_EVENTS}:
            return False
    return False


def _creation_time(
    events: Sequence[tuple[str, datetime | None, Mapping[str, Any]]],
) -> datetime:
    for kind, time, _data in events:
        if kind == "POSITION_CREATED" and time is not None:
            return time
    raise PerformanceSourceUnavailableError("the creation event carries no decision time")


class _Guarded:
    """Turns driver-level failures into a typed unavailability."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._context: Any = None

    async def __aenter__(self) -> Any:
        self._context = self._database.engine.connect()
        try:
            return await self._context.__aenter__()
        except (OperationalError, InterfaceError) as error:
            raise PerformanceSourceUnavailableError(str(error)) from error

    async def __aexit__(self, *exc: Any) -> None:
        try:
            await self._context.__aexit__(*exc)
        except (OperationalError, InterfaceError) as error:
            raise PerformanceSourceUnavailableError(str(error)) from error
