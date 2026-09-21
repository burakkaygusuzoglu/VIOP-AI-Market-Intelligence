"""Backtest request and response schemas (Phase 12 Part 2A).

## The client says what to evaluate, never what happened

There is no field below for a fill price, a realized or unrealized amount, a
performance metric, a decision outcome, a risk approval, a result digest, a
status, a trace entry, a product multiplier, a tick size, a margin or a candle.
A request carrying one is a 422 because **the field does not exist**, not
because it was inspected and rejected - the same rule Phase 9 applied to fills
and Phase 11 applied to replay time.

What a client may send is the question: which immutable dataset, which driver
timeframe, which window, which registered strategy, and the policies it is
entitled to choose - account, risk, simulation. Everything else is computed by
engines that already existed.

## Strategies come from the registry, not from here

``StrategyCatalogueResponse`` is projected from
``app.domain.backtest.registry``. There is deliberately no second allow-list in
this module: two lists drift, and the one that drifts is the one nobody runs.
The strategy's parameters are **pinned in code** for the reference rule, so the
catalogue reports them as fixed rather than offering controls that would not be
honoured.

## Money is text

Every amount crosses as an exact decimal string. A JSON float would round a
price on the way out of a system that spent eleven phases keeping it exact.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.schemas.analysis import AccountBody, RiskSettingsBody
from app.api.schemas.paper import SimulationBody
from app.api.schemas.performance import PerformanceResponse

_PUBLIC = ConfigDict(extra="forbid", frozen=True)

BacktestTimeframe = Literal["5M", "15M", "1H", "1D"]

MAX_RUN_PAGE = 25
MAX_TRACE_PAGE = 200
MAX_POSITION_PAGE = 50
MAX_EVENT_PAGE = 200
MAX_DATASET_PAGE = 25
"""Every list is bounded. A page says its own total, so a bounded view is
visibly bounded rather than quietly truncated."""


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("must include a timezone offset")
    return value


# ----------------------------------------------------------------------
# Requests
# ----------------------------------------------------------------------


class CreateBacktestRunBody(BaseModel):
    """One question to ask of one immutable dataset.

    ``strategy_id`` and ``strategy_version`` are checked against the registry
    server-side. An unknown pair is refused with a typed code; it is never
    mapped onto whatever implementation happens to exist now.
    """

    model_config = _PUBLIC

    dataset_id: str = Field(pattern=r"^RD-[0-9a-f]{32}$")
    driver_timeframe: BacktestTimeframe
    start: datetime
    end: datetime
    strategy_id: str = Field(min_length=1, max_length=64)
    strategy_version: str = Field(min_length=1, max_length=32)
    account: AccountBody
    risk: RiskSettingsBody
    simulation: SimulationBody = SimulationBody()

    @field_validator("start", "end")
    @classmethod
    def _tz(cls, value: datetime) -> datetime:
        return _aware(value)


# ----------------------------------------------------------------------
# Strategy catalogue
# ----------------------------------------------------------------------


class StrategyParameterResponse(BaseModel):
    """One parameter, and whether a client may actually change it."""

    model_config = _PUBLIC

    name: str
    value: str
    configurable: bool
    """False for the reference strategy: its parameters are pinned in code, and
    a form that offered to change them would be offering something the backend
    would ignore."""


class StrategyResponse(BaseModel):
    model_config = _PUBLIC

    identifier: str
    version: str
    summary: str
    warm_up_bars: int
    parameters: list[StrategyParameterResponse]
    directions: list[Literal["LONG", "SHORT"]]
    exposure: str
    stop_model: str
    target_model: str
    entry_timing: str


class StrategyCatalogueResponse(BaseModel):
    """Every strategy this build will accept, straight from the registry."""

    model_config = _PUBLIC

    strategies: list[StrategyResponse]


# ----------------------------------------------------------------------
# Capability
# ----------------------------------------------------------------------


class BacktestCapabilityResponse(BaseModel):
    """Whether this deployment can price a simulated trade at all.

    Historical OHLCV does not establish a multiplier, a tick size, a margin or a
    contract identity. Without a verified product metadata provider a run is
    refused outright rather than reported as a tidy zero-trade success, and the
    UI says so before a person fills in a form.
    """

    model_config = _PUBLIC

    financial_execution_available: bool
    reason: str
    refusal_code: str | None
    max_boundaries: int
    max_positions: int
    max_warm_up_bars: int


# ----------------------------------------------------------------------
# Datasets
# ----------------------------------------------------------------------


class BacktestDatasetTimeframeResponse(BaseModel):
    model_config = _PUBLIC

    timeframe: BacktestTimeframe
    candles: int
    first_open_time: str | None
    last_open_time: str | None


class BacktestDatasetResponse(BaseModel):
    model_config = _PUBLIC

    dataset_id: str
    symbol: str
    total_rows: int
    timeframes: list[BacktestDatasetTimeframeResponse]
    provenance: str


class BacktestDatasetListResponse(BaseModel):
    model_config = _PUBLIC

    items: list[BacktestDatasetResponse]
    total: int
    offset: int
    limit: int


# ----------------------------------------------------------------------
# Runs
# ----------------------------------------------------------------------


class BacktestRunSummaryResponse(BaseModel):
    model_config = _PUBLIC

    run_id: str
    configuration: str
    dataset_id: str
    symbol: str
    driver_timeframe: str
    strategy_id: str
    strategy_version: str
    status: Literal["PENDING", "RUNNING", "COMPLETED", "FAILED"]
    boundaries_evaluated: int
    position_count: int
    created_at: str
    updated_at: str


class BacktestRunListResponse(BaseModel):
    model_config = _PUBLIC

    items: list[BacktestRunSummaryResponse]
    total: int
    offset: int
    limit: int


class BacktestConfigurationResponse(BaseModel):
    """Exactly what was asked, frozen at the moment it was asked."""

    model_config = _PUBLIC

    dataset_id: str
    symbol: str
    driver_timeframe: str
    interval_start: str
    interval_end: str
    strategy_id: str
    strategy_version: str
    strategy_parameters: dict[str, str]
    simulation: dict[str, str]
    risk: dict[str, str]
    product_snapshot: dict[str, str] | None
    """The contract facts as they stood when the run began. Nothing re-reads the
    current provider, so changing it cannot alter a result already computed."""


class BacktestTotalsResponse(BaseModel):
    """Counts and digests. Present regardless of status; complete only when
    the run is COMPLETED, which ``status`` is the only thing that says."""

    model_config = _PUBLIC

    boundaries_evaluated: int
    first_boundary: str | None
    last_boundary: str | None
    decision_count: int
    position_count: int
    result_digest: str | None


class BacktestRunResponse(BaseModel):
    model_config = _PUBLIC

    run_id: str
    status: Literal["PENDING", "RUNNING", "COMPLETED", "FAILED"]
    results_are_final: bool
    """True only for COMPLETED. A PENDING run has counts and a configuration; it
    does not have a result, and this field is what a client keys the difference
    off rather than guessing from a number being present."""

    configuration_fingerprint: str
    configuration: BacktestConfigurationResponse
    totals: BacktestTotalsResponse
    failure_code: str | None
    failure_reason: str | None
    created_at: str
    updated_at: str
    provenance: str


# ----------------------------------------------------------------------
# Decision trace
# ----------------------------------------------------------------------


class BacktestDecisionResponse(BaseModel):
    model_config = _PUBLIC

    sequence: int
    as_of: str
    outcome: str
    reason: str
    bars_available: int
    position_id: str | None
    risk_outcome: str | None
    risk_reason: str | None
    direction: str | None


class BacktestTraceResponse(BaseModel):
    """A bounded window on the trace. ``total`` is the whole run's count.

    A refusal is not a trade and a NO_SIGNAL is not a fill; the outcome is
    carried verbatim so nothing downstream has to infer one from the other.
    """

    model_config = _PUBLIC

    run_id: str
    items: list[BacktestDecisionResponse]
    total: int
    offset: int
    limit: int


# ----------------------------------------------------------------------
# Positions and events
# ----------------------------------------------------------------------


class BacktestTargetResponse(BaseModel):
    model_config = _PUBLIC

    price: str
    quantity: int
    filled: bool
    fill_price: str | None


class BacktestPositionResponse(BaseModel):
    model_config = _PUBLIC

    position_id: str
    ordinal: int
    direction: Literal["LONG", "SHORT"]
    symbol: str
    timeframe: str
    quantity: int
    remaining: int
    state: str
    intended_entry: str
    entry_fill_price: str | None
    stop: str
    targets: list[BacktestTargetResponse]
    decision_time: str
    entry_time: str | None
    realized_gross: str
    fees_total: str | None
    realized_net: str | None
    """``null`` where fees were not modelled. Unknown cost is not zero cost, so
    there is no net figure at all rather than one equal to the gross."""

    unrealized_gross: str | None
    event_count: int
    origin: str
    provenance: str


class BacktestPositionListResponse(BaseModel):
    model_config = _PUBLIC

    run_id: str
    items: list[BacktestPositionResponse]
    total: int
    offset: int
    limit: int


class BacktestEventResponse(BaseModel):
    model_config = _PUBLIC

    sequence: int
    type: str
    market_time: str | None
    data: dict[str, str]


class BacktestEventListResponse(BaseModel):
    model_config = _PUBLIC

    run_id: str
    position_id: str
    items: list[BacktestEventResponse]
    total: int
    after_sequence: int
    limit: int


# ----------------------------------------------------------------------
# Performance
# ----------------------------------------------------------------------


class BacktestPerformanceResponse(BaseModel):
    """Phase 10's answer, unwrapped, for one run's own population.

    The Phase 10 payload is embedded unchanged rather than reshaped: a
    backtest-specific metrics contract would be a second contract, and two
    contracts drift.
    """

    model_config = _PUBLIC

    run_id: str
    status: str
    performance: PerformanceResponse


# ----------------------------------------------------------------------
# Pagination aliases
# ----------------------------------------------------------------------

# Query-string bounds. Plain ``int`` rather than ``StrictInt``: a query value
# arrives as text, and strict mode would refuse ``?limit=25`` outright. Body
# fields keep ``StrictInt``, where a quoted number really is a client bug.
RunOffset = Annotated[int, Field(ge=0, le=1_000_000)]
RunLimit = Annotated[int, Field(ge=1, le=MAX_RUN_PAGE)]
TraceLimit = Annotated[int, Field(ge=1, le=MAX_TRACE_PAGE)]
PositionLimit = Annotated[int, Field(ge=1, le=MAX_POSITION_PAGE)]
EventLimit = Annotated[int, Field(ge=1, le=MAX_EVENT_PAGE)]
