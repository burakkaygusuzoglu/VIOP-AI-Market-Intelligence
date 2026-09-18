"""Paper-trading request and response schemas (Phase 9).

## Requests carry intentions, never results

Every request model forbids unknown fields. There is no field anywhere below for
a state, a fill price, a P&L, an event type, a risk approval, a product, an
asset class or a provenance: those are derived by the server, and a client that
sends one gets a 422 because the field does not exist - not because it was
inspected and rejected.

Prices and money travel as decimal *strings*, validated here as finite, bounded
in length and magnitude, and converted to ``Decimal`` exactly. A JSON number
would pass through a float in most clients.

## Responses say what is simulated

``simulated`` is always ``true`` and cannot be anything else. Fills are labelled
``SIMULATED`` and the bars they came from ``USER_SUPPLIED_HISTORICAL_BARS``: the
paper engine has no market-data feed, and its output is a model applied to data
a person uploaded - not an execution report.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, field_validator

from app.api.schemas.analysis import AccountBody, RiskSettingsBody

_PUBLIC = ConfigDict(extra="forbid", frozen=True)

MAX_DECIMAL_TEXT = 40
MAX_DECIMAL_MAGNITUDE = 12
"""Digits before the point. A trillion is beyond any price, position or account
this system will ever be asked to simulate, and bounding it keeps an input like
``1E+100000`` from becoming a number the engine has to carry."""


def _decimal_text(value: str) -> str:
    text = value.strip()
    if not text or len(text) > MAX_DECIMAL_TEXT:
        raise ValueError(f"must be a decimal of 1-{MAX_DECIMAL_TEXT} characters")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("must be a decimal number") from error
    if not number.is_finite():
        raise ValueError("must be finite")
    if number != 0 and number.adjusted() >= MAX_DECIMAL_MAGNITUDE:
        raise ValueError(f"must be below 10^{MAX_DECIMAL_MAGNITUDE}")
    return text


DecimalText = Annotated[str, AfterValidator(_decimal_text)]


class TargetBody(BaseModel):
    model_config = _PUBLIC

    price: DecimalText
    quantity: StrictInt = Field(ge=1, le=100_000)


class SimulationBody(BaseModel):
    """The configurable simulation choices. Defaults are the conservative ones."""

    model_config = _PUBLIC

    rules_version: Literal["paper-sim/v1"] = "paper-sim/v1"
    same_bar: Literal["STOP_FIRST", "HALT"] = "STOP_FIRST"
    slippage_mode: Literal["ZERO", "FIXED_POINTS"] = "ZERO"
    slippage_points: DecimalText | None = None
    fee_mode: Literal["NOT_MODELLED", "USER_DEFINED_PER_UNIT"] = "NOT_MODELLED"
    fee_per_unit: DecimalText | None = None


class CreatePaperPositionBody(BaseModel):
    """A person's plan for a simulated trade. Nothing here is a result."""

    model_config = _PUBLIC

    symbol: str = Field(min_length=1, max_length=64)
    direction: Literal["LONG", "SHORT"]
    quantity: StrictInt = Field(ge=1, le=100_000)
    """A JSON integer. ``"4"`` is refused rather than coerced: a unit count that
    arrived as text is a client bug worth surfacing, not guessing past."""
    intended_entry: DecimalText
    stop: DecimalText
    targets: list[TargetBody] = Field(min_length=1, max_length=5)
    timeframe: Literal["5M", "15M", "1H", "1D"]
    decision_time: datetime
    account: AccountBody
    risk: RiskSettingsBody
    simulation: SimulationBody = SimulationBody()
    note: str | None = Field(default=None, max_length=280)

    @field_validator("decision_time")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision_time must include a timezone offset")
        return value


class ObservationsBody(BaseModel):
    """Closed historical bars as CSV text, in the Phase 8 analysis format."""

    model_config = _PUBLIC

    content: str = Field(min_length=1, max_length=512 * 1024)
    source_name: str = Field(min_length=1, max_length=128)


# ----------------------------------------------------------------------
# Responses
# ----------------------------------------------------------------------


class PaperEventResponse(BaseModel):
    model_config = _PUBLIC

    sequence: int
    type: str
    market_time: str | None
    data: dict[str, str]
    recorded_at: str | None = None


class PaperTargetResponse(BaseModel):
    model_config = _PUBLIC

    index: int
    price: str
    quantity: int
    filled: bool
    fill_price: str | None


class PaperSimulationResponse(BaseModel):
    model_config = _PUBLIC

    rules_version: str
    same_bar: str
    slippage_mode: str
    slippage_points: str | None
    fee_mode: str
    fee_per_unit: str | None
    entry_model: str
    stop_fill_model: str
    target_fill_model: str
    manual_exit_model: str


class PaperRiskResponse(BaseModel):
    model_config = _PUBLIC

    outcome: str
    allowed_units: int | None
    reason: str


class PaperProvenanceResponse(BaseModel):
    model_config = _PUBLIC

    fills: Literal["SIMULATED"] = "SIMULATED"
    market_data: Literal["USER_SUPPLIED_HISTORICAL_BARS"] = "USER_SUPPLIED_HISTORICAL_BARS"
    origin: str
    asset_class: str
    asset_class_status: str
    point_value: str
    point_value_status: str
    point_value_source: str
    unit: str


class PaperPositionSummaryResponse(BaseModel):
    model_config = _PUBLIC

    id: str
    simulated: Literal[True] = True
    symbol: str
    asset_class: str
    direction: str
    state: str
    quantity: int
    remaining: int
    entry_fill_price: str | None
    stop: str
    last_mark: str | None
    realized_gross: str
    fees_total: str | None
    realized_net: str | None
    unrealized_gross: str | None
    close_pending: bool
    created_at: str
    updated_at: str


class PaperPositionResponse(PaperPositionSummaryResponse):
    origin: str
    timeframe: str
    decision_time: str
    intended_entry: str
    initial_stop: str
    closed_quantity: int
    targets: list[PaperTargetResponse]
    last_bar_time: str | None
    bars_applied: int
    event_count: int
    simulation: PaperSimulationResponse
    risk: PaperRiskResponse
    provenance: PaperProvenanceResponse
    note: str | None
    key_events: list[PaperEventResponse]
    """Every event except bar observations - fills, rejections, ambiguity,
    requests, closing. Bounded by the lifecycle itself (one entry, at most five
    targets, one stop or manual exit, one close), so nothing safety-relevant is
    ever truncated from it. Bar observations are paged separately."""

    idempotent_replay: bool = False


class PaperPositionListResponse(BaseModel):
    model_config = _PUBLIC

    items: list[PaperPositionSummaryResponse]
    total: int
    offset: int
    limit: int


class PaperEventPageResponse(BaseModel):
    model_config = _PUBLIC

    position_id: str
    items: list[PaperEventResponse]
    total: int
    after_sequence: int
    limit: int
    next_after_sequence: int | None
