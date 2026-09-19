"""Replay application shapes to API responses (Phase 11).

Reads only. Every value here is copied from a stored session, an immutable
dataset row or a candle the store returned, and formatted as exact text. The one
piece of arithmetic is ``truncated``, which compares two counts the service
already produced - so a response cannot claim a market fact that no engine
computed.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.api.schemas.replay import (
    ReplayCandleResponse,
    ReplayCursorResponse,
    ReplayDatasetSummaryResponse,
    ReplayPlanResponse,
    ReplaySessionListResponse,
    ReplaySessionResponse,
    ReplaySessionSummaryResponse,
    ReplayStepResponse,
    ReplayTimeframeSummaryResponse,
    ReplayWindowResponse,
)
from app.application.replay.ports import SessionSummary, StoredDataset, StoredSession
from app.application.replay.service import (
    ReplayLimits,
    RevealedWindow,
    SessionView,
    StepResult,
)
from app.domain.market.candle import Candle


def session(
    view: SessionView, limits: ReplayLimits, *, replayed: bool = False
) -> ReplaySessionResponse:
    stored = view.session
    return ReplaySessionResponse(
        id=stored.session_id,
        plan=ReplayPlanResponse(
            symbol=stored.plan.symbol,
            driver_timeframe=stored.plan.driver.value,
            replay_start=_time(stored.plan.replay_start),
        ),
        cursor=_cursor(stored, view.availability),
        dataset=_dataset(stored.dataset),
        availability=[_window(item, limits) for item in view.availability],
        linked_position_ids=list(view.linked_positions),
        max_advance_steps=limits.max_advance_steps,
        created_at=_time(stored.created_at),
        updated_at=_time(stored.updated_at),
        idempotent_replay=replayed,
    )


def step(result: StepResult, limits: ReplayLimits) -> ReplayStepResponse:
    return ReplayStepResponse(
        session=session(result.view, limits),
        revealed_boundaries=[_time(item) for item in result.revealed],
        observed_positions=result.observed_positions,
        idempotent_replay=result.replayed,
    )


def session_list(
    items: tuple[SessionSummary, ...], total: int, *, offset: int, limit: int
) -> ReplaySessionListResponse:
    return ReplaySessionListResponse(
        items=[_summary(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


# ----------------------------------------------------------------------


def _cursor(stored: StoredSession, windows: tuple[RevealedWindow, ...]) -> ReplayCursorResponse:
    driver_total = next(
        (item.dataset_total for item in windows if item.timeframe is stored.plan.driver), 0
    )
    return ReplayCursorResponse(
        replay_as_of=_time(stored.cursor.as_of),
        revealed_driver_candles=stored.cursor.revealed_driver_candles,
        driver_total_candles=driver_total,
        state=stored.cursor.status.value,
        version=stored.cursor.version,
    )


def _dataset(item: StoredDataset) -> ReplayDatasetSummaryResponse:
    return ReplayDatasetSummaryResponse(
        dataset_id=item.dataset_id,
        symbol=item.symbol,
        total_rows=item.total_rows,
        timeframes=[
            ReplayTimeframeSummaryResponse(
                timeframe=summary.timeframe.value,
                rows=summary.rows,
                first_open_time=_time(summary.first_open_time),
                last_open_time=_time(summary.last_open_time),
                last_coverage_end=_time(summary.last_coverage_end),
            )
            for summary in item.timeframes
        ],
    )


def _window(item: RevealedWindow, limits: ReplayLimits) -> ReplayWindowResponse:
    return ReplayWindowResponse(
        timeframe=item.timeframe.value,
        candles=[_candle(candle) for candle in item.candles],
        revealed=item.revealed_total,
        dataset_total=item.dataset_total,
        window_limit=limits.max_chart_candles,
        truncated=item.truncated,
    )


def _candle(candle: Candle) -> ReplayCandleResponse:
    return ReplayCandleResponse(
        open_time=_time(candle.open_time),
        open=_text(candle.open),
        high=_text(candle.high),
        low=_text(candle.low),
        close=_text(candle.close),
        volume=_text(candle.volume),
    )


def _summary(item: SessionSummary) -> ReplaySessionSummaryResponse:
    return ReplaySessionSummaryResponse(
        id=item.session_id,
        dataset_id=item.dataset_id,
        symbol=item.symbol,
        driver_timeframe=item.driver.value,
        replay_start=_time(item.replay_start),
        replay_as_of=_time(item.as_of),
        revealed_driver_candles=item.revealed_driver_candles,
        driver_total_candles=item.driver_total_candles,
        state=item.state,
        version=item.version,
        created_at=_time(item.created_at),
        updated_at=_time(item.updated_at),
    )


def _time(value: datetime) -> str:
    return value.isoformat()


def _text(value: Decimal) -> str:
    return format(value, "f")
