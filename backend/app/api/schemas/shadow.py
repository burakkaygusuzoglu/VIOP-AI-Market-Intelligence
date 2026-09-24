"""Shadow Mode request and response schemas (Phase 14 Part 2A).

## A client asks a question; it never supplies an answer

Creating a run names an existing live session, a registered strategy id and
version, a driver timeframe and the timeframes to watch. There is no field for
a decision, an outcome, a price development, a fill, a profit, contract
metadata, a risk approval, an approved quantity, a journal entry, a sequence
number, a provenance, a market currency or a run status. A body carrying one is
a 422 because the field does not exist - ``extra="forbid"`` on every request
model.

A strategy is named, not supplied. The name is matched against the shipped
registry and turned into an object this build already contains; nothing is
imported from a request, and nothing is evaluated.

## A response cannot claim what the system cannot know

``provenance`` and ``market_currency`` are single-valued ``Literal`` fields, so
a response model cannot be constructed claiming exchange data or a current
market. There is no ``pnl``, ``profit``, ``win`` or ``filled`` field anywhere in
this module: a level the market touched is ``STOP_LEVEL_TOUCHED``, and the
type system is where that distinction is kept honest.

## Three clocks, three names

``market_*``, ``boundary`` and the development's observation times are market
time. ``recorded_at``, ``started_at`` and ``ended_at`` are the server's audit
clock. Nothing is derived from a browser.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from app.api.schemas.analysis import AccountBody, RiskSettingsBody

_PUBLIC = ConfigDict(extra="forbid", frozen=True)

ShadowTimeframe = Literal["5M", "15M", "1H", "1D"]
Provenance = Literal["SIMULATED_HISTORICAL_STREAM"]
Currency = Literal["HISTORICAL"]

RUN_ID_PATTERN = r"^SR-[0-9a-f]{24}$"
SESSION_ID_PATTERN = r"^LS-[0-9a-f]{24}$"
DECISION_KEY_PATTERN = r"^[0-9a-f]{64}$"

OutcomeWord = Literal["NO_SIGNAL", "WAIT", "ENTRY_INTENT", "EXIT_INTENT", "UNAVAILABLE", "REFUSED"]
FinancialWord = Literal[
    "NOT_APPLICABLE",
    "NOT_CONFIGURED",
    "METADATA_UNAVAILABLE",
    "APPROVED",
    "REFUSED",
    "UNDETERMINED",
]
DevelopmentState = Literal["NOT_EVALUATED", "PENDING", "OBSERVED", "UNAVAILABLE", "INVALIDATED"]
LevelWord = Literal[
    "NONE_REACHED",
    "STOP_LEVEL_TOUCHED",
    "TARGET_LEVEL_TOUCHED",
    "BOTH_LEVELS_TOUCHED_SAME_BAR",
    "NOT_OBSERVED",
]


# ----------------------------------------------------------------------
# Requests
# ----------------------------------------------------------------------


class CreateShadowRunBody(BaseModel):
    """Which session to watch, under which registered rules."""

    model_config = _PUBLIC

    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    strategy_id: str = Field(min_length=1, max_length=64)
    strategy_version: str = Field(min_length=1, max_length=32)
    driver: ShadowTimeframe
    timeframes: list[ShadowTimeframe] = Field(min_length=1, max_length=4)
    required: list[ShadowTimeframe] = Field(default_factory=list, max_length=4)
    """Timeframes the rules genuinely need. Empty means the driver alone; this
    build does not invent a dependency a policy does not have."""

    account: AccountBody | None = None
    risk: RiskSettingsBody | None = None
    """Both or neither. Without them no sizing is attempted and every intent
    records NOT_CONFIGURED - which is a legitimate observation-only run."""

    analysis_evidence: StrictBool = True
    attempt_key: str | None = Field(default=None, min_length=8, max_length=64)
    """Makes creation idempotent. The same key with the same configuration
    returns the run already created; with a different one it is a conflict."""


# ----------------------------------------------------------------------
# Responses
# ----------------------------------------------------------------------


class ShadowLimitsResponse(BaseModel):
    model_config = _PUBLIC

    max_runs: StrictInt
    max_observations: StrictInt
    max_journal_page: StrictInt
    max_outcome_page: StrictInt
    max_outcome_window: StrictInt
    max_open_watches: StrictInt


class ShadowStrategyResponse(BaseModel):
    model_config = _PUBLIC

    strategy_id: str
    versions: list[str]


class ShadowCapabilityResponse(BaseModel):
    """What this deployment can do, said plainly rather than discovered."""

    model_config = _PUBLIC

    available: StrictBool
    reason: str
    provenance: Provenance
    market_currency: Currency
    financial_metadata_available: StrictBool
    """False means no run can produce an approved quantity, a risk amount or a
    margin figure, and every proposed entry will say METADATA_UNAVAILABLE."""

    execution_enabled: Literal[False] = False
    """There is no order path. The field is a constant so a client cannot read
    its absence as "not yet known"."""

    strategies: list[ShadowStrategyResponse]
    limits: ShadowLimitsResponse
    server_time: str


class ShadowRunResponse(BaseModel):
    model_config = _PUBLIC

    run_id: str
    configuration: str
    source_id: str
    instrument_label: str
    provenance: Provenance
    market_currency: Currency
    strategy_id: str
    strategy_version: str
    strategy_parameters: dict[str, str]
    driver: ShadowTimeframe
    timeframes: list[ShadowTimeframe]
    required_timeframes: list[ShadowTimeframe]
    status: Literal["OBSERVING", "ENDED"]
    end_reason: str | None
    failure_code: str | None
    observations: StrictInt
    decisions: StrictInt
    entries: StrictInt
    first_boundary: str | None
    last_boundary: str | None
    started_at: str
    ended_at: str | None
    completeness: Literal["OBSERVING", "COMPLETE", "PARTIAL", "INTERRUPTED"]
    """Never inferred by a client. A run that ended because its data ran out is
    COMPLETE; one cancelled or stopped at a bound is PARTIAL; one that failed
    is INTERRUPTED."""


class ShadowRunListResponse(BaseModel):
    model_config = _PUBLIC

    items: list[ShadowRunResponse]
    total: StrictInt


class TimeframeEvidenceResponse(BaseModel):
    model_config = _PUBLIC

    timeframe: ShadowTimeframe
    available: StrictBool
    freshness: str
    integrity: str
    confirmed_count: StrictInt
    reasons: list[str]
    last_coverage_end: str | None


class TimeframeReadingsResponse(BaseModel):
    model_config = _PUBLIC

    timeframe: ShadowTimeframe
    ema_fast: str | None = None
    ema_slow: str | None = None
    rsi: str | None = None
    atr: str | None = None
    adx: str | None = None


class ShadowEvidenceResponse(BaseModel):
    model_config = _PUBLIC

    provenance: Provenance
    market_currency: Currency
    connection: str
    bars_available: StrictInt
    included: list[ShadowTimeframe]
    excluded: list[TimeframeEvidenceResponse]
    timeframes: list[TimeframeEvidenceResponse]
    readings: list[TimeframeReadingsResponse]
    regime: str | None
    suitability: str | None
    setup_quality: str | None
    analysis_market_as_of: str | None


class EntrySketchResponse(BaseModel):
    """What the rules asked for. Not a position, not an order, never filled."""

    model_config = _PUBLIC

    direction: Literal["LONG", "SHORT"]
    intended_entry: str
    stop: str
    targets: list[tuple[str, int]]
    requested_quantity: StrictInt
    approved_quantity: StrictInt | None
    """Only from the risk engine with verified product facts. ``null`` means
    nothing was approved, and is never rendered as zero."""


class PriceDevelopmentResponse(BaseModel):
    """Where price went afterwards. Not a trade result.

    Deliberately absent: entry fill, exit fill, quantity, gross, fees, net,
    return, win or loss. The system does not know whether anybody could have
    traded this, so it says only what price did.
    """

    model_config = _PUBLIC

    state: DevelopmentState
    event: LevelWord
    rules: str
    observed_from: str | None
    observed_to: str | None
    candles_observed: StrictInt
    event_at: str | None
    target_ordinal: StrictInt | None
    best_price: str | None
    worst_price: str | None
    last_close: str | None
    ambiguous: StrictBool
    """One candle reached both levels. OHLC cannot say which came first, and
    this build does not guess."""

    unresolved_reason: str | None
    recorded_at: str | None = None
    decision_boundary: str | None = None


class ShadowEntryResponse(BaseModel):
    model_config = _PUBLIC

    kind: Literal["DECISION", "OPERATIONAL"]
    sequence: StrictInt
    decision_key: str
    market_boundary: str | None
    recorded_at: str
    outcome: OutcomeWord | None
    operational: str | None
    strategy_kind: str | None
    reason: str
    direction: Literal["LONG", "SHORT"] | None
    entry: EntrySketchResponse | None
    financial_state: FinancialWord
    risk_outcome: str | None
    risk_reason: str | None
    evidence: ShadowEvidenceResponse | None
    input_fingerprint: str | None
    development: PriceDevelopmentResponse | None
    """The current published development for this decision, when there is one.
    ``null`` means none was published, not that nothing happened."""


class ShadowJournalPageResponse(BaseModel):
    model_config = _PUBLIC

    run_id: str
    items: list[ShadowEntryResponse]
    total: StrictInt
    next_after: StrictInt | None
    server_time: str


class ShadowOutcomeResponse(BaseModel):
    model_config = _PUBLIC

    decision_key: str
    sequence: StrictInt
    outcome_key: str
    recorded_at: str
    decision_boundary: str
    direction: Literal["LONG", "SHORT"]
    development: PriceDevelopmentResponse


class ShadowOutcomePageResponse(BaseModel):
    model_config = _PUBLIC

    run_id: str
    items: list[ShadowOutcomeResponse]
    total: StrictInt
    next_after: StrictInt | None
    server_time: str
