"""Stored paper positions to API responses (Phase 9).

Reads only. Every number is copied from the stored projection or from the
ledger - a target's fill price from its ``TARGET_FILLED`` event, the point value
from ``POSITION_CREATED`` - and formatted as exact decimal text. No arithmetic
happens here, so a response cannot disagree with the ledger it was read from.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from app.api.schemas.paper import (
    PaperEventResponse,
    PaperPositionResponse,
    PaperPositionSummaryResponse,
    PaperProvenanceResponse,
    PaperRiskResponse,
    PaperSimulationResponse,
    PaperTargetResponse,
)
from app.application.ports.paper import PositionSummary, StoredEvent, StoredPosition
from app.domain.paper import (
    MANUAL_EXIT_MODEL,
    STOP_FILL_MODEL,
    TARGET_FILL_MODEL,
    PaperEvent,
    PaperEventType,
)

KEY_EVENT_TYPES = frozenset(set(PaperEventType) - {PaperEventType.OBSERVATION_APPLIED})


def summary(item: PositionSummary) -> PaperPositionSummaryResponse:
    p = item.projection
    return PaperPositionSummaryResponse(
        id=item.position_id,
        symbol=p.symbol,
        asset_class=p.asset_class,
        direction=p.direction,
        state=p.state,
        quantity=p.quantity,
        remaining=p.remaining,
        entry_fill_price=_opt(p.entry_fill_price),
        stop=_text(p.stop),
        last_mark=_opt(p.last_mark),
        realized_gross=_text(p.realized_gross),
        fees_total=_opt(p.fees_total),
        realized_net=_opt(p.realized_net),
        unrealized_gross=_opt(p.unrealized_gross),
        close_pending=p.close_pending,
        created_at=_time(item.created_at),
        updated_at=_time(item.updated_at),
    )


def detail(stored: StoredPosition, *, replayed: bool = False) -> PaperPositionResponse:
    p = stored.projection
    spec = stored.spec
    created = _created_event(stored.events)
    fills = {
        event.data["target"]: event.data["fill_price"]
        for event in stored.events
        if event.type is PaperEventType.TARGET_FILLED
    }
    return PaperPositionResponse(
        id=stored.position_id,
        symbol=p.symbol,
        asset_class=p.asset_class,
        direction=p.direction,
        state=p.state,
        quantity=p.quantity,
        remaining=p.remaining,
        entry_fill_price=_opt(p.entry_fill_price),
        stop=_text(p.stop),
        last_mark=_opt(p.last_mark),
        realized_gross=_text(p.realized_gross),
        fees_total=_opt(p.fees_total),
        realized_net=_opt(p.realized_net),
        unrealized_gross=_opt(p.unrealized_gross),
        close_pending=p.close_pending,
        created_at=_time(stored.created_at),
        updated_at=_time(stored.updated_at),
        origin=spec.origin.value,
        timeframe=spec.timeframe.value,
        decision_time=_time(spec.decision_time),
        intended_entry=_text(spec.intended_entry),
        initial_stop=_text(spec.stop),
        closed_quantity=_closed(stored),
        targets=[
            PaperTargetResponse(
                index=index,
                price=_text(target.price),
                quantity=target.quantity,
                filled=str(index) in fills,
                fill_price=fills.get(str(index)),
            )
            for index, target in enumerate(spec.targets, start=1)
        ],
        last_bar_time=None if p.last_bar_time is None else _time(p.last_bar_time),
        bars_applied=p.bars_applied,
        event_count=p.event_count,
        simulation=PaperSimulationResponse(
            rules_version=created.get("rules_version", ""),
            same_bar=created.get("same_bar", ""),
            slippage_mode=created.get("slippage_mode", ""),
            slippage_points=created.get("slippage_points") or None,
            fee_mode=created.get("fee_mode", ""),
            fee_per_unit=created.get("fee_per_unit") or None,
            entry_model=created.get("entry_model", ""),
            stop_fill_model=STOP_FILL_MODEL,
            target_fill_model=TARGET_FILL_MODEL,
            manual_exit_model=MANUAL_EXIT_MODEL,
        ),
        risk=PaperRiskResponse(
            outcome=stored.approval.outcome.value,
            allowed_units=stored.approval.allowed_units,
            reason=stored.approval.reason,
        ),
        provenance=PaperProvenanceResponse(
            origin=created.get("origin", ""),
            asset_class=created.get("asset_class", ""),
            asset_class_status=created.get("asset_class_status", ""),
            point_value=created.get("point_value", ""),
            point_value_status=created.get("point_value_status", ""),
            point_value_source=created.get("point_value_source", ""),
            unit=created.get("unit", ""),
        ),
        note=spec.note,
        key_events=[
            event_response(event) for event in stored.events if event.type in KEY_EVENT_TYPES
        ],
        idempotent_replay=replayed,
    )


def event_response(event: PaperEvent, recorded_at: datetime | None = None) -> PaperEventResponse:
    return PaperEventResponse(
        sequence=event.sequence,
        type=event.type.value,
        market_time=None if event.market_time is None else _time(event.market_time),
        data=dict(event.data),
        recorded_at=None if recorded_at is None else _time(recorded_at),
    )


def stored_event_response(item: StoredEvent) -> PaperEventResponse:
    return event_response(item.event, item.recorded_at)


def _created_event(events: Sequence[PaperEvent]) -> dict[str, str]:
    for event in events:
        if event.type is PaperEventType.POSITION_CREATED:
            return dict(event.data)
    return {}


def _closed(stored: StoredPosition) -> int:
    p = stored.projection
    return 0 if p.entry_fill_price is None else p.quantity - p.remaining


def _text(value: Decimal) -> str:
    return format(value, "f")


def _opt(value: Decimal | None) -> str | None:
    return None if value is None else _text(value)


def _time(value: datetime) -> str:
    return value.isoformat()
