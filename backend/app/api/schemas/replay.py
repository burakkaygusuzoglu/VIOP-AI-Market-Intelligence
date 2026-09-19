"""Replay request and response schemas (Phase 11).

## The client cannot tell the server what time it is

The whole point of a replay is that the server decides what has happened yet.
So there is no field anywhere below for ``replay_as_of``, a cursor, a revealed
count, a candle to reveal, a session state or a version to write. A request that
carries one is a 422 because the field does not exist, not because it was
inspected and rejected - which is the same rule Phase 9 applied to fills.

``expected_version`` is the single exception, and it is not state: it is what
the client *last read*, used to refuse a step that would race another tab.

## Nothing here is a second engine

Analysis comes back as the Phase 8 ``AnalysisResponse`` and performance as the
Phase 10 ``PerformanceResponse``, unchanged and unwrapped except for the replay
moment they were produced at. A replay-specific analysis shape would be a second
analysis contract, and two contracts drift.

## What the response says about its own truthfulness

Every revealed window reports how many candles exist in the dataset and how many
are visible now, so a bounded chart is visibly bounded rather than silently
truncated. Provenance is stated on every session: the market data is historical
and user-supplied, the fills are simulated, and the origin is a replay.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from app.api.schemas.analysis import AccountBody, AnalysisResponse, RiskSettingsBody
from app.api.schemas.paper import DecimalText, SimulationBody, TargetBody
from app.api.schemas.performance import PerformanceResponse

_PUBLIC = ConfigDict(extra="forbid", frozen=True)

ReplayTimeframe = Literal["5M", "15M", "1H", "1D"]

MAX_DATASET_BYTES = 1024 * 1024
"""One timeframe's CSV upload. Row counts are bounded separately by the service;
this only stops a body that would be parsed before it could be counted."""


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("must include a timezone offset")
    return value


# ----------------------------------------------------------------------
# Requests
# ----------------------------------------------------------------------


class ReplayDatasetBody(BaseModel):
    """One timeframe of user-supplied historical OHLCV."""

    model_config = _PUBLIC

    timeframe: ReplayTimeframe
    content: str = Field(min_length=1, max_length=MAX_DATASET_BYTES)
    source_name: str = Field(min_length=1, max_length=128)


class CreateReplaySessionBody(BaseModel):
    """What a person supplies to start a replay: data, a driver, a start."""

    model_config = _PUBLIC

    symbol: str = Field(min_length=1, max_length=64)
    driver_timeframe: ReplayTimeframe
    """One step reveals the next candle of this timeframe. It must be among the
    supplied datasets, or a step would have nothing to reveal."""

    replay_start: datetime
    """The market moment the replay begins at. Candles that had finished by then
    are warm-up history; everything later is revealed only by stepping."""

    datasets: list[ReplayDatasetBody] = Field(min_length=1, max_length=4)

    _check_start = field_validator("replay_start")(_aware)


class AdvanceReplayBody(BaseModel):
    """How far to step. Never *where* to step to."""

    model_config = _PUBLIC

    steps: StrictInt = Field(default=1, ge=1, le=50)
    expected_version: StrictInt | None = Field(default=None, ge=1)
    """The version the client last read. When supplied, a session that has moved
    on refuses the step instead of advancing past a cursor nobody has seen."""


class ReplayAnalysisBody(BaseModel):
    """Optional sizing inputs for the analysis. No market data: the session's
    revealed candles are the data, and they are not the client's to choose."""

    model_config = _PUBLIC

    account: AccountBody | None = None
    risk: RiskSettingsBody | None = None
    entry_price: DecimalText | None = None
    stop_price: DecimalText | None = None


class OpenReplayPositionBody(BaseModel):
    """A plan for a simulated trade inside a replay.

    Deliberately not a ``CreatePaperPositionBody``: ``symbol``, ``timeframe``
    and ``decision_time`` are absent because the session owns them. A replay
    position decided at replay time is the only kind there is.
    """

    model_config = _PUBLIC

    direction: Literal["LONG", "SHORT"]
    quantity: StrictInt = Field(ge=1, le=100_000)
    intended_entry: DecimalText
    stop: DecimalText
    targets: list[TargetBody] = Field(min_length=1, max_length=5)
    account: AccountBody
    risk: RiskSettingsBody
    simulation: SimulationBody = SimulationBody()
    note: str | None = Field(default=None, max_length=280)


# ----------------------------------------------------------------------
# Responses
# ----------------------------------------------------------------------


class ReplayCandleResponse(BaseModel):
    model_config = _PUBLIC

    open_time: str
    open: str
    high: str
    low: str
    close: str
    volume: str


class ReplayWindowResponse(BaseModel):
    """One timeframe as the session currently sees it."""

    model_config = _PUBLIC

    timeframe: str
    candles: list[ReplayCandleResponse]
    """Only candles that had finished at ``replay_as_of``, oldest first, and at
    most ``window_limit`` of them. Empty for every timeframe except the one the
    chart asked for."""

    revealed: int
    """How many candles of this timeframe are available now."""

    dataset_total: int
    """How many exist in the dataset. ``revealed`` below this is the replay
    working, not data missing."""

    window_limit: int
    truncated: bool
    """True when ``revealed`` exceeds the window and older candles were left
    out. A bounded chart says so rather than looking like the whole history."""


class ReplayCursorResponse(BaseModel):
    model_config = _PUBLIC

    replay_as_of: str
    """The market-information boundary. Every number in this session was
    computed as if this were now."""

    revealed_driver_candles: int
    driver_total_candles: int
    state: Literal["READY", "IN_PROGRESS", "END_OF_DATASET"]
    version: int


class ReplayPlanResponse(BaseModel):
    model_config = _PUBLIC

    symbol: str
    driver_timeframe: str
    replay_start: str
    """The requested start, snapped to the driver candle boundary at or before
    it - so it is a market fact, not a typed timestamp."""


class ReplayDatasetSummaryResponse(BaseModel):
    model_config = _PUBLIC

    dataset_id: str
    """Derived from the candles themselves: the same market uploaded twice is
    one dataset, and one changed price is a different one."""

    symbol: str
    total_rows: int
    timeframes: list[ReplayTimeframeSummaryResponse]


class ReplayTimeframeSummaryResponse(BaseModel):
    model_config = _PUBLIC

    timeframe: str
    rows: int
    first_open_time: str
    last_open_time: str
    last_coverage_end: str


class ReplayProvenanceResponse(BaseModel):
    model_config = _PUBLIC

    market_data: Literal["USER_SUPPLIED_HISTORICAL"] = "USER_SUPPLIED_HISTORICAL"
    fills: Literal["SIMULATED"] = "SIMULATED"
    origin: Literal["REPLAY"] = "REPLAY"
    execution: Literal["DISABLED"] = "DISABLED"


class ReplaySessionResponse(BaseModel):
    model_config = _PUBLIC

    id: str
    simulated: Literal[True] = True
    plan: ReplayPlanResponse
    cursor: ReplayCursorResponse
    dataset: ReplayDatasetSummaryResponse
    availability: list[ReplayWindowResponse]
    linked_position_ids: list[str]
    """The paper positions this session opened, in creation order. A position
    belongs to at most one session."""

    provenance: ReplayProvenanceResponse = ReplayProvenanceResponse()
    max_advance_steps: int
    created_at: str
    updated_at: str
    idempotent_replay: bool = False


class ReplaySessionSummaryResponse(BaseModel):
    model_config = _PUBLIC

    id: str
    dataset_id: str
    symbol: str
    driver_timeframe: str
    replay_start: str
    replay_as_of: str
    revealed_driver_candles: int
    driver_total_candles: int
    state: str
    version: int
    created_at: str
    updated_at: str


class ReplaySessionListResponse(BaseModel):
    model_config = _PUBLIC

    items: list[ReplaySessionSummaryResponse]
    total: int
    offset: int
    limit: int


class ReplayStepResponse(BaseModel):
    model_config = _PUBLIC

    session: ReplaySessionResponse
    revealed_boundaries: list[str]
    """Every market moment this command crossed, in order. A bounded advance is
    its single steps, so this has one entry per candle revealed."""

    observed_positions: int
    """How many of this session's paper positions were given the new bars."""

    idempotent_replay: bool = False


class ReplayAnalysisResponse(BaseModel):
    model_config = _PUBLIC

    session_id: str
    replay_as_of: str
    analysis: AnalysisResponse
    """The Phase 8 analysis, unchanged. Run over exactly the candles that had
    finished at ``replay_as_of`` - the same code path as ``POST /analysis``."""


class ReplayPerformanceResponse(BaseModel):
    model_config = _PUBLIC

    session_id: str
    replay_as_of: str
    position_ids: list[str]
    """The exact population the metrics were computed over. A session's
    performance is its own positions and nothing else."""

    performance: PerformanceResponse


ReplayDatasetSummaryResponse.model_rebuild()

SessionId = Annotated[str, Field(pattern=r"^RS-[0-9a-f]{24}$")]
