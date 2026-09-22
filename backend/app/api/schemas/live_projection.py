"""Application views to Live Intelligence responses (Phase 13 Part 2A).

Pure mapping. Every value here was decided by Part 1's market state, the
workspace or the Phase 8 analysis; this module only spells it. Times are
ISO-8601 with their offset, and Decimal values are carried as text so no
price passes through a float on its way to a screen.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Literal

from app.api.schemas.analysis import SynthesisResponse
from app.api.schemas.analysis_projection import project
from app.api.schemas.live import (
    LiveAlertResponse,
    LiveAnalysisResponse,
    LiveAnalysisStatusResponse,
    LiveCandleResponse,
    LiveEventEnvelope,
    LiveExcludedTimeframeResponse,
    LiveFingerprintResponse,
    LiveIdentityResponse,
    LivePlaybackResponse,
    LiveRejectionResponse,
    LiveSessionListResponse,
    LiveSessionResponse,
    LiveSessionSummaryResponse,
    LiveSourceListResponse,
    LiveSourceResponse,
    LiveTimeframeResponse,
    LiveTimelineEntryResponse,
    LiveTimelinePageResponse,
    SourceTimeframeResponse,
)
from app.application.live.catalog import SourceSummary
from app.application.live.workspace import (
    AnalysisResult,
    Notification,
    SessionView,
    TimelineEntry,
    TimelinePage,
)
from app.domain.live.events import Observation

__all__ = [
    "LIVE_SYNTHESIS",
    "analysis_response",
    "envelope",
    "heartbeat",
    "session",
    "session_list",
    "source_list",
    "timeline",
]

LIVE_SYNTHESIS = SynthesisResponse(
    status="NOT_APPLICABLE",
    detail=(
        "Canlı akışta Claude sentezi çalıştırılmaz; yalnızca deterministik analiz "
        "gösterilir. Her mum veya olay için bir dil modeli çağrılmaz."
    ),
)
"""Live never narrates. A per-candle model call is exactly what §64 forbids,
and an on-demand one is out of this phase - so the status says so."""


def _origin(value: str) -> Literal["USER_SUPPLIED_HISTORICAL"]:
    """The only origin a live source has. Anything else fails loudly rather
    than being relabelled on its way out."""
    if value != "USER_SUPPLIED_HISTORICAL":
        raise ValueError("unknown live source origin")
    return "USER_SUPPLIED_HISTORICAL"


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _required(value: datetime) -> str:
    return value.isoformat()


def _candle(observation: Observation | None) -> LiveCandleResponse | None:
    if observation is None:
        return None
    candle = observation.candle
    return LiveCandleResponse(
        state="CLOSED" if candle.is_closed else "FORMING",
        market_open_time=_required(candle.open_time),
        market_coverage_end=_required(
            candle.open_time + timedelta(minutes=candle.timeframe.minutes)
        ),
        market_event_time=_required(observation.event_time),
        received_at=_required(observation.received_at),
        open=str(candle.open),
        high=str(candle.high),
        low=str(candle.low),
        close=str(candle.close),
        volume=str(candle.volume),
        sequence=observation.sequence,
    )


def session(view: SessionView) -> LiveSessionResponse:
    snapshot = view.snapshot
    source = view.source
    timeframes = []
    for item in snapshot.timeframes:
        book = item.book
        timeframe = book.timeframe
        timeframes.append(
            LiveTimeframeResponse(
                timeframe=timeframe.value,
                freshness=item.freshness.value,
                freshness_threshold_seconds=view.freshness.threshold(timeframe).total_seconds(),
                integrity=book.integrity.value,
                availability=item.availability.value,
                reasons=list(item.reasons),
                closed_count=book.closed_count,
                trimmed=book.trimmed,
                first_closed_open_time=_time(book.first_closed_open_time),
                last_closed_open_time=_time(book.last_closed_open_time),
                last_sequence=book.last_sequence,
                last_market_event_time=_time(book.last_event_time),
                last_received_at=_time(book.last_received_at),
                missing_sequences=book.missing_sequences,
                missing_overflowed=book.missing_overflowed,
                temporal_gaps=book.temporal_gaps,
                sequence_mismatches=book.sequence_mismatches,
                conflicts=book.conflicts,
                duplicates=book.duplicates,
                late_fills=book.late_fills,
                awaiting_continuity=book.awaiting_continuity,
                forming_ahead=book.forming_ahead,
                unresolved_trimmed=book.unresolved_trimmed,
                version=book.version,
                latest_confirmed=_candle(view.latest_confirmed.get(timeframe)),
                forming=_candle(view.forming.get(timeframe)),
            )
        )
    return LiveSessionResponse(
        id=view.session_id,
        identity=LiveIdentityResponse(
            stream_id=view.session_id,
            source_id=source.summary.source_id,
            source_origin=_origin(source.summary.origin),
            provider_id=source.provider_id,
            instrument_label=source.summary.instrument_label,
            provenance=snapshot.provenance.value,
            market_currency=snapshot.market_currency.value,
        ),
        lifecycle=view.lifecycle.value,
        end_origin=None if view.end_origin is None else view.end_origin.value,
        connection=snapshot.connection.value,
        termination_reason=(
            None if snapshot.termination_reason is None else snapshot.termination_reason.value
        ),
        reconnects=snapshot.reconnects,
        created_at=_required(view.created_at),
        ended_at=_time(view.ended_at),
        snapshot_at=_required(snapshot.as_of),
        playback=LivePlaybackResponse(
            pace=view.pace.value,
            event_spacing_seconds=source.event_spacing_seconds,
            total_events=source.total_events,
            market_window_start=_required(source.market_window_start),
            market_window_end=_required(source.market_window_end),
            candles_per_timeframe={
                tf.value: count for tf, count in source.candles_per_timeframe.items()
            },
        ),
        timeframes=timeframes,
        alerts=[
            LiveAlertResponse(
                kind=alert.kind.value,
                timeframe=None if alert.timeframe is None else alert.timeframe.value,
                detail=alert.detail,
            )
            for alert in view.alerts
        ],
        rejection_counts=dict(snapshot.rejection_counts),
        recent_rejections=[
            LiveRejectionResponse(
                timeframe=None if record.timeframe is None else record.timeframe.value,
                code=record.code.value,
                detail=record.detail,
                received_at=_required(record.received_at),
            )
            for record in snapshot.recent_rejections
        ],
        analysis=LiveAnalysisStatusResponse(
            analyses_run=view.analysis.analyses_run,
            available_timeframes=[tf.value for tf in snapshot.available],
            last_market_as_of=_time(view.analysis.last_market_as_of),
            last_requested_at=_time(view.analysis.last_requested_at),
            last_current=view.analysis.last_current,
        ),
        cursor=view.cursor,
        oldest_retained=view.oldest_retained,
        subscribers=view.subscribers,
    )


def session_list(views: tuple[SessionView, ...], capacity: int) -> LiveSessionListResponse:
    return LiveSessionListResponse(
        items=[
            LiveSessionSummaryResponse(
                id=view.session_id,
                instrument_label=view.source.summary.instrument_label,
                source_id=view.source.summary.source_id,
                lifecycle=view.lifecycle.value,
                connection=view.snapshot.connection.value,
                created_at=_required(view.created_at),
                cursor=view.cursor,
            )
            for view in views
        ],
        capacity=capacity,
    )


def source_list(
    items: tuple[SourceSummary, ...], total: int, *, offset: int, limit: int
) -> LiveSourceListResponse:
    return LiveSourceListResponse(
        items=[
            LiveSourceResponse(
                source_id=item.source_id,
                instrument_label=item.instrument_label,
                origin=_origin(item.origin),
                streamable=item.streamable,
                refusal=item.refusal,
                timeframes=[
                    SourceTimeframeResponse(
                        timeframe=tf.timeframe.value,
                        rows=tf.rows,
                        first_open_time=_required(tf.first_open_time),
                        last_open_time=_required(tf.last_open_time),
                    )
                    for tf in item.timeframes
                ],
            )
            for item in items
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


def _entry(item: TimelineEntry) -> LiveTimelineEntryResponse:
    return LiveTimelineEntryResponse(
        seq=item.seq,
        kind=item.kind.value,
        recorded_at=_required(item.recorded_at),
        code=item.code,
        timeframe=None if item.timeframe is None else item.timeframe.value,
        market_open_time=_time(item.market_open_time),
        market_event_time=_time(item.market_event_time),
        sequence=item.sequence,
        before=item.before,
        after=item.after,
        backfill=item.backfill,
    )


def timeline(session_id: str, page: TimelinePage, limit: int) -> LiveTimelinePageResponse:
    return LiveTimelinePageResponse(
        session_id=session_id,
        entries=[_entry(item) for item in page.entries],
        cursor=page.cursor,
        oldest_retained=page.oldest_retained,
        gap=page.gap,
        limit=limit,
    )


def analysis_response(result: AnalysisResult) -> LiveAnalysisResponse:
    analysis = result.analysis
    versions = analysis.fingerprint[0]
    assert isinstance(versions, tuple)  # noqa: S101 - built by LiveSession
    return LiveAnalysisResponse(
        session_id=result.view.session_id,
        provenance=analysis.provenance.value,
        market_currency=analysis.market_currency.value,
        market_as_of=_required(analysis.market_as_of),
        requested_at=_required(analysis.requested_at),
        included=[tf.value for tf in analysis.included],
        excluded=[
            LiveExcludedTimeframeResponse(timeframe=tf.value, reasons=list(reasons))
            for tf, reasons in analysis.excluded.items()
        ],
        fingerprint=[
            LiveFingerprintResponse(timeframe=str(name), version=int(version))
            for name, version in versions
        ],
        reused=result.reused,
        current=result.view.analysis.last_current,
        analysis=project(analysis.outcome, LIVE_SYNTHESIS),
        session=session(result.view),
    )


def envelope(notice: Notification, server_time: datetime) -> LiveEventEnvelope:
    has_id = notice.entry is not None or notice.kind.value in ("STATE", "END")
    return LiveEventEnvelope(
        session_id=notice.session_id,
        event_id=notice.cursor if has_id else None,
        kind=notice.kind.value,
        server_time=_required(server_time),
        cursor=notice.cursor,
        entry=None if notice.entry is None else _entry(notice.entry),
        session=None if notice.view is None else session(notice.view),
        reason=None if notice.reason is None else notice.reason.value,
    )


def heartbeat(session_id: str, cursor: int, server_time: datetime) -> LiveEventEnvelope:
    return LiveEventEnvelope(
        session_id=session_id,
        event_id=None,
        kind="HEARTBEAT",
        server_time=_required(server_time),
        cursor=cursor,
    )


def sse_frame(item: LiveEventEnvelope) -> str:
    """One Server-Sent Event. ``id`` only on events that advance the cursor,
    so a heartbeat can never move a reconnecting client's Last-Event-ID."""
    data = json.dumps(item.model_dump(mode="json"), separators=(",", ":"))
    head = f"id: {item.event_id}\n" if item.event_id is not None else ""
    return f"{head}data: {data}\n\n"
