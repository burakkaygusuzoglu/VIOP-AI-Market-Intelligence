"""A run's outcomes, for the Phase 10 engine (Phase 12).

This is a *source*, not an engine. It reads one run's ledger and hands the same
``PositionOutcome`` records Phase 10 already consumes, through the same fold:
``fold_ledger`` is imported from the paper source rather than reimplemented,
because two functions turning events into outcomes is how two populations start
disagreeing about what a fill was worth.

The unrealized mark of a still-open position is not in the ledger - it is a
mark of a moment - so it is obtained the way Phase 9 obtains it: replay the
stored ledger against the frozen product snapshot and read what the engine
says. Nothing here multiplies a price by a quantity.
"""

from __future__ import annotations

from decimal import Decimal

from app.adapters.performance.paper_source import fold_ledger, replace_unrealized
from app.application.backtest.ports import (
    BacktestPosition,
    BacktestStore,
    BacktestStoreUnavailableError,
)
from app.application.performance.ports import (
    JournalRow,
    OutcomeFilters,
    OutcomePage,
    PerformanceSourceUnavailableError,
)
from app.application.ports.paper import ProductSnapshotCodec, ProductSnapshotError
from app.domain.paper.engine import PaperRefusalError, rebuild, unrealized_gross
from app.domain.performance.model import PositionOutcome


class BacktestRunPerformanceSource:
    """The outcomes of exactly one run. Never another run's, never a person's.

    Scoped at construction rather than by filter: a source that could be asked
    about a different run would make cross-run contamination a question of
    remembering to pass the right argument.
    """

    def __init__(self, store: BacktestStore, codec: ProductSnapshotCodec, run_id: str) -> None:
        self._store = store
        self._codec = codec
        self._run_id = run_id

    async def count_matching(self, filters: OutcomeFilters) -> int:
        return len(await self._records(filters))

    async def outcomes(self, filters: OutcomeFilters, *, limit: int) -> OutcomePage:
        records = await self._records(filters)
        fills = sum(len(record.fills) for record in records)
        return OutcomePage(
            records=tuple(records[:limit]),
            fill_count=fills,
            total_matching=len(records),
        )

    async def journal_rows(
        self, filters: OutcomeFilters, *, offset: int, limit: int
    ) -> tuple[tuple[JournalRow, ...], int]:
        """A backtest has no journal: nobody wrote a note about a simulated run.

        Reported as empty rather than unsupported, because the population is
        genuinely empty - not because the question could not be answered.
        """
        return (), 0

    # ------------------------------------------------------------------

    async def _records(self, filters: OutcomeFilters) -> list[PositionOutcome]:
        try:
            ledger = await self._store.ledger_of(self._run_id)
            positions = await self._store.positions_of(self._run_id)
        except BacktestStoreUnavailableError as error:
            raise PerformanceSourceUnavailableError(str(error)) from error

        records: list[PositionOutcome] = []
        by_id = {item.position_id: item for item in positions}
        for position_id, events in ledger.items():
            financial = [
                (kind, time, data) for kind, time, data in events if kind != "OBSERVATION_APPLIED"
            ]
            record = fold_ledger(position_id, financial)
            if record.population.open_exposure:
                record = replace_unrealized(record, self._mark(by_id[position_id]))
            records.append(record)

        records.sort(key=lambda record: by_id[record.position_id].ordinal)
        return [record for record in records if _matches(record, filters)]

    def _mark(self, position: BacktestPosition) -> Decimal | None:
        """What the Phase 9 engine says this position is worth right now."""
        try:
            product = self._codec.restore(position.product_snapshot)
            rebuilt = rebuild(position.spec, position.approval, product, position.events)
            mark = unrealized_gross(rebuilt, product)
        except (ProductSnapshotError, PaperRefusalError) as error:
            raise PerformanceSourceUnavailableError(
                f"{position.position_id} could not be replayed from its ledger: {error}"
            ) from error
        return mark


def _matches(record: PositionOutcome, filters: OutcomeFilters) -> bool:
    """The Phase 10 filter vocabulary, applied to one run's own population."""
    if filters.direction is not None and record.direction is not filters.direction:
        return False
    if filters.timeframe is not None and record.timeframe is not filters.timeframe:
        return False
    if filters.symbol is not None and record.instrument.symbol != filters.symbol:
        return False
    if filters.position_ids is not None and record.position_id not in filters.position_ids:
        return False
    moment = record.terminal_time
    if filters.closed_from is not None and (moment is None or moment < filters.closed_from):
        return False
    return not (filters.closed_to is not None and (moment is None or moment > filters.closed_to))
