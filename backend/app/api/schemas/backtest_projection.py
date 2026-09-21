"""Domain and application records to backtest responses (Phase 12 Part 2A).

Projection only. Nothing here adds, subtracts, divides or compares a financial
quantity: every amount arrives already computed by Phase 9 or Phase 10 and is
turned into its exact decimal string. If a figure is missing it stays missing -
an unmodelled fee produces ``null``, never a zero that reads as "free".

The one thing this module does decide is *shape*: which bounded page a screen
receives, and how a run says whether its results are final.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Literal

from app.api.schemas.backtest import (
    BacktestConfigurationResponse,
    BacktestDatasetListResponse,
    BacktestDatasetResponse,
    BacktestDatasetTimeframeResponse,
    BacktestDecisionResponse,
    BacktestEventListResponse,
    BacktestEventResponse,
    BacktestPositionListResponse,
    BacktestPositionResponse,
    BacktestRunListResponse,
    BacktestRunResponse,
    BacktestRunSummaryResponse,
    BacktestTargetResponse,
    BacktestTotalsResponse,
    BacktestTraceResponse,
    StrategyCatalogueResponse,
    StrategyParameterResponse,
    StrategyResponse,
)
from app.application.backtest.ports import LedgerEvent, RunSummary, RunTotals, StoredRun
from app.application.replay.ports import StoredDataset
from app.domain.backtest.run import DecisionRecord, RunStatus
from app.domain.backtest.strategies.ema_crossover import (
    IDENTIFIER as EMA_IDENTIFIER,
)
from app.domain.backtest.strategies.ema_crossover import (
    EmaCrossoverSettings,
    EmaCrossoverStrategy,
)
from app.domain.common.enums import Direction, Timeframe
from app.domain.paper.model import PaperPosition

DATASET_PROVENANCE = (
    "Historical market data supplied by the user, stored immutably and read by digest."
)
RUN_PROVENANCE = (
    "Simulated trades over historical data. No order was placed and no broker was contacted."
)
POSITION_PROVENANCE = "Simulated fill computed by the paper-trading engine from historical candles."


_TIMEFRAME_LABELS: dict[Timeframe, Literal["5M", "15M", "1H", "1D"]] = {
    Timeframe.M5: "5M",
    Timeframe.M15: "15M",
    Timeframe.H1: "1H",
    Timeframe.D1: "1D",
}
"""The public label for each timeframe, narrowed for the response type.

A mapping rather than ``.value`` with a cast: adding a timeframe the API does
not publish then fails here, where it is visible, instead of at a client.
"""

_DIRECTION_LABELS: dict[Direction, Literal["LONG", "SHORT"]] = {
    Direction.LONG: "LONG",
    Direction.SHORT: "SHORT",
}
"""Narrowed for the response type, the same way the timeframe is.

``Direction`` carries more members than a position can hold - the risk engine
uses them to say "neither" - so a cast would hide a real impossibility rather
than express it. A missing key fails here, where it is visible.
"""

_RUN_STATUS_LABELS: dict[RunStatus, Literal["PENDING", "RUNNING", "COMPLETED", "FAILED"]] = {
    RunStatus.PENDING: "PENDING",
    RunStatus.RUNNING: "RUNNING",
    RunStatus.COMPLETED: "COMPLETED",
    RunStatus.FAILED: "FAILED",
}


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _amount(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


# ----------------------------------------------------------------------


def strategy_catalogue(supported: Mapping[str, frozenset[str]]) -> StrategyCatalogueResponse:
    """The registry, described. Only registered pairs appear.

    The description of the reference rule is read from the implementation - its
    own settings object and warm-up - rather than retyped here, so a parameter
    that changes in code cannot go on being advertised at its old value.
    """
    entries: list[StrategyResponse] = []
    for identifier in sorted(supported):
        for version in sorted(supported[identifier]):
            if identifier == EMA_IDENTIFIER:
                entries.append(_ema_crossover(version))
            else:
                entries.append(
                    StrategyResponse(
                        identifier=identifier,
                        version=version,
                        summary="Registered strategy rules.",
                        warm_up_bars=0,
                        parameters=[],
                        directions=["LONG", "SHORT"],
                        exposure="One position at a time.",
                        stop_model="Defined by the strategy rules.",
                        target_model="Defined by the strategy rules.",
                        entry_timing="Next bar open, at or after the decision boundary.",
                    )
                )
    return StrategyCatalogueResponse(strategies=entries)


def _directions(settings: EmaCrossoverSettings) -> list[Literal["LONG", "SHORT"]]:
    found: list[Literal["LONG", "SHORT"]] = []
    if settings.allow_long:
        found.append("LONG")
    if settings.allow_short:
        found.append("SHORT")
    return found


def _ema_crossover(version: str) -> StrategyResponse:
    settings = EmaCrossoverSettings()
    strategy = EmaCrossoverStrategy(settings)
    return StrategyResponse(
        identifier=EMA_IDENTIFIER,
        version=version,
        summary=(
            f"EMA {settings.fast_period}/{settings.slow_period} crossover, confirmed on the "
            f"candle that closed, filtered by ADX at or above {settings.adx_minimum:g}. "
            "A validation instrument, not a recommendation."
        ),
        warm_up_bars=strategy.warm_up_bars,
        parameters=[
            StrategyParameterResponse(name=name, value=value, configurable=False)
            for name, value in sorted(strategy.parameters().items())
        ],
        directions=_directions(settings),
        exposure="One position at a time; an opposite crossover closes the open one.",
        stop_model=(
            f"ATR x {settings.atr_stop_multiple}, then moved away from the entry onto the "
            "product's verified price grid."
        ),
        target_model=f"ATR x {settings.atr_target_multiple}, aligned the same way.",
        entry_timing=(
            "The first bar opening at or after the decision boundary. A signal never fills "
            "on the candle that produced it."
        ),
    )


# ----------------------------------------------------------------------


def dataset(item: StoredDataset) -> BacktestDatasetResponse:
    return BacktestDatasetResponse(
        dataset_id=item.dataset_id,
        symbol=item.symbol,
        total_rows=item.total_rows,
        timeframes=[
            BacktestDatasetTimeframeResponse(
                timeframe=_TIMEFRAME_LABELS[summary.timeframe],
                candles=summary.rows,
                first_open_time=_time(summary.first_open_time),
                last_open_time=_time(summary.last_open_time),
            )
            for summary in item.timeframes
        ],
        provenance=DATASET_PROVENANCE,
    )


def dataset_list(
    items: Sequence[StoredDataset], total: int, *, offset: int, limit: int
) -> BacktestDatasetListResponse:
    return BacktestDatasetListResponse(
        items=[dataset(item) for item in items], total=total, offset=offset, limit=limit
    )


# ----------------------------------------------------------------------


def run_summary(item: RunSummary) -> BacktestRunSummaryResponse:
    return BacktestRunSummaryResponse(
        run_id=item.run_id,
        configuration=item.configuration,
        dataset_id=item.dataset_id,
        symbol=item.symbol,
        driver_timeframe=item.driver.value,
        strategy_id=item.strategy_id,
        strategy_version=item.strategy_version,
        status=_RUN_STATUS_LABELS[item.status],
        boundaries_evaluated=item.boundaries_evaluated,
        position_count=item.position_count,
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
    )


def run_list(
    items: Sequence[RunSummary], total: int, *, offset: int, limit: int
) -> BacktestRunListResponse:
    return BacktestRunListResponse(
        items=[run_summary(item) for item in items], total=total, offset=offset, limit=limit
    )


def run(stored: StoredRun, totals: RunTotals) -> BacktestRunResponse:
    """One run's identity, configuration and totals.

    ``results_are_final`` is derived from the status and nothing else. A
    PENDING run has counts - it was created, and it may have been interrupted
    part-way - and those counts are not a result.
    """
    return BacktestRunResponse(
        run_id=stored.run_id,
        status=_RUN_STATUS_LABELS[stored.status],
        results_are_final=stored.status is RunStatus.COMPLETED,
        configuration_fingerprint=stored.configuration,
        configuration=BacktestConfigurationResponse(
            dataset_id=stored.dataset_id,
            symbol=stored.symbol,
            driver_timeframe=stored.driver.value,
            interval_start=stored.interval_start.isoformat(),
            interval_end=stored.interval_end.isoformat(),
            strategy_id=stored.strategy_id,
            strategy_version=stored.strategy_version,
            strategy_parameters=dict(stored.strategy_parameters),
            simulation=dict(stored.simulation),
            risk=dict(stored.risk),
            product_snapshot=(
                None
                if stored.product_snapshot is None
                else {key: str(value) for key, value in sorted(stored.product_snapshot.items())}
            ),
        ),
        totals=BacktestTotalsResponse(
            boundaries_evaluated=totals.boundaries_evaluated,
            first_boundary=_time(totals.first_boundary),
            last_boundary=_time(totals.last_boundary),
            decision_count=totals.decision_count,
            position_count=totals.position_count,
            result_digest=totals.result_digest,
        ),
        failure_code=stored.failure_code,
        failure_reason=stored.failure_reason,
        created_at=stored.created_at.isoformat(),
        updated_at=stored.updated_at.isoformat(),
        provenance=RUN_PROVENANCE,
    )


# ----------------------------------------------------------------------


def decision(record: DecisionRecord) -> BacktestDecisionResponse:
    return BacktestDecisionResponse(
        sequence=record.sequence,
        as_of=record.as_of.isoformat(),
        outcome=record.outcome.value,
        reason=record.reason,
        bars_available=record.bars_available,
        position_id=record.position_id,
        risk_outcome=record.risk_outcome,
        risk_reason=record.risk_reason,
        direction=record.direction,
    )


def trace(
    run_id: str, records: Sequence[DecisionRecord], total: int, *, offset: int, limit: int
) -> BacktestTraceResponse:
    return BacktestTraceResponse(
        run_id=run_id,
        items=[decision(record) for record in records],
        total=total,
        offset=offset,
        limit=limit,
    )


# ----------------------------------------------------------------------


def position(
    ordinal: int, rebuilt: PaperPosition, *, event_count: int, unrealized: Decimal | None
) -> BacktestPositionResponse:
    """A position as the Phase 9 engine reports it, after replaying its ledger.

    Every figure here was produced by that engine. The unrealized mark is
    passed in because it is a mark *of a moment*, obtained the same way Phase 9
    obtains it, and is not in the ledger.
    """
    spec = rebuilt.spec
    return BacktestPositionResponse(
        position_id=spec.position_id,
        ordinal=ordinal,
        direction=_DIRECTION_LABELS[spec.direction],
        symbol=spec.symbol,
        timeframe=spec.timeframe.value,
        quantity=spec.quantity,
        remaining=rebuilt.remaining,
        state=rebuilt.state.value,
        intended_entry=format(spec.intended_entry, "f"),
        entry_fill_price=_amount(rebuilt.entry_fill_price),
        stop=format(rebuilt.stop, "f"),
        targets=[
            BacktestTargetResponse(
                price=format(target.spec.price, "f"),
                quantity=target.spec.quantity,
                filled=target.filled,
                fill_price=_amount(target.fill_price),
            )
            for target in rebuilt.targets
        ],
        decision_time=spec.decision_time.isoformat(),
        entry_time=_time(rebuilt.entry_time),
        realized_gross=format(rebuilt.realized_gross, "f"),
        fees_total=_amount(rebuilt.fees_total),
        realized_net=(
            None
            if rebuilt.fees_total is None
            else format(rebuilt.realized_gross - rebuilt.fees_total, "f")
        ),
        unrealized_gross=_amount(unrealized),
        event_count=event_count,
        origin=spec.origin.value,
        provenance=POSITION_PROVENANCE,
    )


def position_list(
    run_id: str,
    items: Sequence[BacktestPositionResponse],
    total: int,
    *,
    offset: int,
    limit: int,
) -> BacktestPositionListResponse:
    return BacktestPositionListResponse(
        run_id=run_id, items=list(items), total=total, offset=offset, limit=limit
    )


def event_list(
    run_id: str,
    position_id: str,
    events: Sequence[LedgerEvent],
    total: int,
    *,
    after_sequence: int,
    limit: int,
) -> BacktestEventListResponse:
    return BacktestEventListResponse(
        run_id=run_id,
        position_id=position_id,
        items=[
            BacktestEventResponse(
                sequence=item.sequence,
                type=item.type,
                market_time=_time(item.market_time),
                data=dict(item.data),
            )
            for item in events
        ],
        total=total,
        after_sequence=after_sequence,
        limit=limit,
    )
