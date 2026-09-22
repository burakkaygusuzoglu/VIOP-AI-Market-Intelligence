"""Live Intelligence request and response schemas (Phase 13 Part 2A).

## A client supplies intent, never facts

Creating a session names a stored dataset, which timeframes to stream, how
large a window and how fast to play it. There is no field for a candle, a
price, a timestamp, a sequence number, a provenance, a market currency, a
stream status, contract metadata, an analysis, a risk approval, a position or
an order. A body that carries one is a 422 because the field does not exist -
``extra="forbid"`` on every request model.

## Every response says what it is

Provenance and market currency are ``Literal`` fields with exactly one value
each. A response model *cannot* be built claiming exchange data or a current
market, whatever the code upstream does - the validation error would be a 500
before it could be a false label.

## Three clocks, three names

``market_*`` fields are market time (from the stored candles). ``received_at``
and ``*_seconds`` freshness fields are the receive clock. ``created_at``,
``ended_at``, ``snapshot_at``, ``recorded_at`` and ``server_time`` are the
server's audit clock. None is derived from another, and none from a browser.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.api.schemas.analysis import AccountBody, AnalysisResponse, RiskSettingsBody

_PUBLIC = ConfigDict(extra="forbid", frozen=True)

LiveTimeframe = Literal["5M", "15M", "1H", "1D"]
Provenance = Literal["SIMULATED_HISTORICAL_STREAM"]
Currency = Literal["HISTORICAL"]

DATASET_ID_PATTERN = r"^RD-[0-9a-f]{32}$"
SESSION_ID_PATTERN = r"^LS-[0-9a-f]{24}$"


# ----------------------------------------------------------------------
# Requests
# ----------------------------------------------------------------------


class CreateLiveSessionBody(BaseModel):
    """Which stored history to play, and how. Nothing about the market."""

    model_config = _PUBLIC

    source_id: str = Field(pattern=DATASET_ID_PATTERN)
    timeframes: list[LiveTimeframe] = Field(min_length=1, max_length=4)
    window_candles: StrictInt = Field(default=300, ge=50, le=1000)
    """Candles of the finest chosen timeframe; coarser ones cover the same
    market stretch."""

    pace: Literal["SLOW", "NORMAL", "FAST"] = "NORMAL"


class LiveAnalysisBody(BaseModel):
    """Optional sizing inputs. The candles are the session's, not the client's."""

    model_config = _PUBLIC

    account: AccountBody | None = None
    risk: RiskSettingsBody | None = None


# ----------------------------------------------------------------------
# Capability and sources
# ----------------------------------------------------------------------


class LiveLimitsResponse(BaseModel):
    model_config = _PUBLIC

    max_sessions: int
    max_subscribers_per_session: int
    max_subscribers_total: int
    subscriber_queue: int
    timeline_retention: int
    timeline_page_max: int
    max_session_seconds: float
    creations_per_minute: int
    max_concurrent_analyses: int
    min_window_candles: int
    max_window_candles: int
    max_closed_candles: int
    max_missing_sequences: int
    max_recorded_issues: int
    max_reconnects: int
    heartbeat_seconds: float


class LiveCapabilityResponse(BaseModel):
    model_config = _PUBLIC

    state: Literal["AVAILABLE", "DISABLED"]
    detail: str
    provenance: Provenance = "SIMULATED_HISTORICAL_STREAM"
    market_currency: Currency = "HISTORICAL"
    real_exchange_connected: Literal[False] = False
    execution: Literal["DISABLED"] = "DISABLED"
    transport: Literal["SSE"] = "SSE"
    scope: Literal["LOCAL_DEVELOPMENT"] = "LOCAL_DEVELOPMENT"
    paces: list[str]
    limits: LiveLimitsResponse | None


class SourceTimeframeResponse(BaseModel):
    model_config = _PUBLIC

    timeframe: str
    rows: int
    first_open_time: str
    last_open_time: str


class LiveSourceResponse(BaseModel):
    model_config = _PUBLIC

    source_id: str
    instrument_label: str
    """The dataset's symbol text - a label, never a verified contract."""

    origin: Literal["USER_SUPPLIED_HISTORICAL"]
    contract_identity: Literal["NOT_ESTABLISHED"] = "NOT_ESTABLISHED"
    streamable: bool
    refusal: str | None
    timeframes: list[SourceTimeframeResponse]


class LiveSourceListResponse(BaseModel):
    model_config = _PUBLIC

    items: list[LiveSourceResponse]
    total: int
    offset: int
    limit: int


# ----------------------------------------------------------------------
# Sessions
# ----------------------------------------------------------------------


class LiveCandleResponse(BaseModel):
    """One candle as the stream delivered it. Money-shaped values are text."""

    model_config = _PUBLIC

    state: Literal["CLOSED", "FORMING"]
    market_open_time: str
    market_coverage_end: str
    market_event_time: str
    received_at: str
    open: str
    high: str
    low: str
    close: str
    volume: str
    sequence: int | None


class LiveTimeframeResponse(BaseModel):
    model_config = _PUBLIC

    timeframe: str
    freshness: Literal["NO_DATA", "FRESH", "STALE"]
    """Transport freshness on the receive clock. Never market currency."""

    freshness_threshold_seconds: float
    integrity: Literal["COMPLETE", "GAPPED", "DISCONTINUOUS", "CONFLICTED", "UNVERIFIED"]
    availability: Literal["AVAILABLE", "UNAVAILABLE"]
    reasons: list[str]
    closed_count: int
    trimmed: int
    first_closed_open_time: str | None
    last_closed_open_time: str | None
    last_sequence: int | None
    last_market_event_time: str | None
    last_received_at: str | None
    missing_sequences: int
    missing_overflowed: bool
    temporal_gaps: int
    sequence_mismatches: int
    conflicts: int
    duplicates: int
    late_fills: int
    awaiting_continuity: bool
    forming_ahead: bool
    unresolved_trimmed: int
    version: int
    latest_confirmed: LiveCandleResponse | None
    forming: LiveCandleResponse | None


class LiveAlertResponse(BaseModel):
    """A data-quality candidate from Part 1's ``alert_candidates``. Never a
    trade instruction."""

    model_config = _PUBLIC

    kind: str
    timeframe: str | None
    detail: str


class LiveRejectionResponse(BaseModel):
    model_config = _PUBLIC

    timeframe: str | None
    code: str
    detail: str
    received_at: str


class LiveIdentityResponse(BaseModel):
    """Five identities, never merged."""

    model_config = _PUBLIC

    stream_id: str
    source_id: str
    source_origin: Literal["USER_SUPPLIED_HISTORICAL"]
    provider_id: str
    instrument_label: str
    contract_identity: Literal["NOT_ESTABLISHED"] = "NOT_ESTABLISHED"
    provenance: Provenance
    market_currency: Currency


class LivePlaybackResponse(BaseModel):
    model_config = _PUBLIC

    pace: str
    event_spacing_seconds: float
    total_events: int
    market_window_start: str
    market_window_end: str
    candles_per_timeframe: dict[str, int]
    forming_candles_published: Literal[False] = False
    """Stored candles are finished ones; no intermediate state is invented."""


class LiveAnalysisStatusResponse(BaseModel):
    model_config = _PUBLIC

    analyses_run: int
    available_timeframes: list[str]
    last_market_as_of: str | None
    last_requested_at: str | None
    last_current: bool
    """Whether the last analysis still describes this stream's confirmed
    state. Never a claim that the market is current."""


class LiveSessionResponse(BaseModel):
    model_config = _PUBLIC

    id: str
    simulated: Literal[True] = True
    execution: Literal["DISABLED"] = "DISABLED"
    identity: LiveIdentityResponse
    lifecycle: Literal["RUNNING", "ENDED"]
    end_origin: Literal["STREAM", "USER_CANCELLED", "DEADLINE", "SHUTDOWN"] | None
    connection: Literal["INITIALIZING", "CONNECTED", "DISCONNECTED", "RECOVERING", "TERMINATED"]
    termination_reason: str | None
    reconnects: int
    created_at: str
    ended_at: str | None
    snapshot_at: str
    playback: LivePlaybackResponse
    timeframes: list[LiveTimeframeResponse]
    alerts: list[LiveAlertResponse]
    rejection_counts: dict[str, int]
    recent_rejections: list[LiveRejectionResponse]
    analysis: LiveAnalysisStatusResponse
    cursor: int
    oldest_retained: int
    subscribers: int


class LiveSessionSummaryResponse(BaseModel):
    model_config = _PUBLIC

    id: str
    instrument_label: str
    source_id: str
    lifecycle: str
    connection: str
    created_at: str
    cursor: int


class LiveSessionListResponse(BaseModel):
    model_config = _PUBLIC

    items: list[LiveSessionSummaryResponse]
    capacity: int


# ----------------------------------------------------------------------
# Timeline
# ----------------------------------------------------------------------


class LiveTimelineEntryResponse(BaseModel):
    model_config = _PUBLIC

    seq: int
    kind: str
    recorded_at: str
    code: str
    timeframe: str | None
    market_open_time: str | None
    market_event_time: str | None
    sequence: int | None
    before: str | None
    after: str | None
    backfill: bool


class LiveTimelinePageResponse(BaseModel):
    model_config = _PUBLIC

    session_id: str
    entries: list[LiveTimelineEntryResponse]
    cursor: int
    oldest_retained: int
    gap: bool
    limit: int


# ----------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------


class LiveExcludedTimeframeResponse(BaseModel):
    model_config = _PUBLIC

    timeframe: str
    reasons: list[str]


class LiveFingerprintResponse(BaseModel):
    model_config = _PUBLIC

    timeframe: str
    version: int


class LiveAnalysisResponse(BaseModel):
    """The existing Phase 8 analysis over confirmed, available candles."""

    model_config = _PUBLIC

    session_id: str
    provenance: Provenance
    market_currency: Currency
    market_as_of: str
    """Market time: the end of the latest confirmed candle analysed."""

    requested_at: str
    included: list[str]
    excluded: list[LiveExcludedTimeframeResponse]
    fingerprint: list[LiveFingerprintResponse]
    reused: bool
    """Served from the cache because its inputs are identical."""

    current: bool
    analysis: AnalysisResponse
    session: LiveSessionResponse


# ----------------------------------------------------------------------
# SSE
# ----------------------------------------------------------------------


class LiveEventEnvelope(BaseModel):
    """One Server-Sent Event's ``data``. Also the documented wire contract.

    ``HEARTBEAT`` means the transport is alive and carries the current cursor
    so a client can tell it missed something. It is not a candle and it
    touches no market state.
    """

    model_config = _PUBLIC

    protocol: Literal["viop.live.v1"] = "viop.live.v1"
    session_id: str
    event_id: int | None
    kind: Literal["TIMELINE", "STATE", "RESYNC_REQUIRED", "END", "HEARTBEAT"]
    server_time: str
    cursor: int
    entry: LiveTimelineEntryResponse | None = None
    session: LiveSessionResponse | None = None
    reason: str | None = None
