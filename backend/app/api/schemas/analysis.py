"""Public request and response shapes for on-demand analysis (§18, §19).

## The request shape *is* the trust boundary

`model_config` sets ``extra="forbid"`` on every model here. That is not
tidiness — it is the mechanism. A client that posts ``final_action``,
``allowed_actions``, ``risk_permitted``, a calculated RSI or a verified
multiplier does not get it quietly ignored: there is no such field, so pydantic
rejects the whole request with 422. The Phase 6 provenance defect happened
because a value could arrive and be *believed*; the fix there and here is to
leave nowhere for it to land.

Everything derived is constructed server-side by the engines and appears only
in the response.

## Money is text on the wire

Every monetary and price value crosses as a string and is parsed to `Decimal`.
JSON numbers are IEEE-754 doubles in every mainstream client, so a body
containing ``12345.67`` has already lost information before this process sees
it. `condecimal`-style strings keep the user's exact input.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

_PUBLIC = ConfigDict(extra="forbid", frozen=True)

TIMEFRAMES = ("1D", "1H", "15M", "5M")


def _decimal(value: str, field_name: str) -> Decimal:
    try:
        parsed = Decimal(value.strip())
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field_name} is not a number") from error
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return parsed


# ----------------------------------------------------------------------
# Request
# ----------------------------------------------------------------------


class TimeframeDatasetBody(BaseModel):
    """One timeframe's OHLCV, as CSV text."""

    model_config = _PUBLIC

    timeframe: str = Field(description="One of 1D, 1H, 15M, 5M.")
    content: str = Field(description="CSV text with the Phase 1 column schema.")
    source_name: str = Field(default="", max_length=200)

    @field_validator("timeframe")
    @classmethod
    def _known_timeframe(cls, value: str) -> str:
        if value not in TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {', '.join(TIMEFRAMES)}")
        return value


class RiskSettingsBody(BaseModel):
    """User-controlled risk configuration. Never a risk *result*."""

    model_config = _PUBLIC

    mode: str = Field(default="FIXED")
    fixed_risk: str | None = None
    risk_ratio: str | None = None
    max_contracts: Annotated[int, Field(gt=0, le=10_000)] | None = None

    @field_validator("mode")
    @classmethod
    def _known_mode(cls, value: str) -> str:
        if value not in {"FIXED", "PERCENTAGE"}:
            raise ValueError("mode must be FIXED or PERCENTAGE")
        return value


class AccountBody(BaseModel):
    """What the user says their account looks like."""

    model_config = _PUBLIC

    equity: str
    used_margin: str = "0"
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    """An ISO-style code the user supplied. Carried as USER_CONFIRMED
    provenance and never inferred from locale or instrument (§12)."""

    @field_validator("currency")
    @classmethod
    def _currency_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = value.strip().upper()
        if len(code) != 3 or not code.isalpha() or not code.isascii():
            raise ValueError("currency must be three ASCII letters")
        return code


class AnalysisRequestBody(BaseModel):
    """One analysis request. There is no field here for a derived value."""

    model_config = _PUBLIC

    symbol: str = Field(min_length=1, max_length=64)
    datasets: tuple[TimeframeDatasetBody, ...] = Field(min_length=1, max_length=4)
    account: AccountBody | None = None
    risk: RiskSettingsBody | None = None
    entry_price: str | None = None
    stop_price: str | None = None

    @field_validator("datasets")
    @classmethod
    def _unique_timeframes(
        cls, value: tuple[TimeframeDatasetBody, ...]
    ) -> tuple[TimeframeDatasetBody, ...]:
        seen = [item.timeframe for item in value]
        if len(set(seen)) != len(seen):
            raise ValueError("each timeframe may appear at most once")
        return value


# ----------------------------------------------------------------------
# Response
# ----------------------------------------------------------------------


class NumericFactResponse(BaseModel):
    """One number with its origin. ``raw`` is exact; the client formats it."""

    model_config = _PUBLIC

    id: str
    label: str
    raw: str
    unit: str
    source: str
    currency: str | None = None
    """Present only when the user supplied an account currency and the value is
    money. Never inferred (§12)."""


class DataQualityIssueResponse(BaseModel):
    model_config = _PUBLIC

    code: str
    severity: str
    message: str


class TimeframeResultResponse(BaseModel):
    """One timeframe's outcome, including a blocked one."""

    model_config = _PUBLIC

    timeframe: str
    role: str
    usable: bool
    verdict: str
    candle_count: int
    direction: str
    confirmation: str
    coverage_end: str | None = None
    """The instant this timeframe is known through: its last open time plus one
    interval. `None` when it produced no usable series."""

    bars_behind: int = 0
    """Whole bars of this timeframe that should have closed by `analysis_as_of`
    and are absent (§3).

    Zero is the ordinary case. A non-zero value says this part of the picture is
    genuinely older than the instant the analysis is stamped with - which is
    legal and coherent, but must not be silent.
    """

    issues: tuple[DataQualityIssueResponse, ...] = ()
    omitted_issue_count: int = 0
    """Findings not listed, after the blocking ones (§13).

    A file of 2 400 malformed rows produced 2 400 near-identical findings. The
    list is bounded so a response and a rendered page stay finite; blocking
    findings are kept first and are never among the omitted, and the count is
    stated so nothing looks complete when it is not."""


class CandleResponse(BaseModel):
    """One OHLCV bar, exactly as validated. The only price source the chart
    is allowed to draw (§24)."""

    model_config = _PUBLIC

    open_time: str
    open: str
    high: str
    low: str
    close: str
    volume: str
    is_closed: bool


class ChartSeriesResponse(BaseModel):
    """The bars the chart may draw, and an honest account of the rest.

    The analytical dataset and the display dataset are deliberately different
    sizes. Every candle supplied is analysed; only a bounded window is sent for
    drawing. That is only honest if the payload says so, which is what
    ``analysed_count`` and ``omitted_count`` are for - a chart captioned "400
    mum" while 2 500 were analysed would be describing itself, not the data.
    """

    model_config = _PUBLIC

    timeframe: str
    candles: tuple[CandleResponse, ...]
    zones: tuple[ZoneResponse, ...] = ()
    omitted_zone_count: int = 0
    """Bands not drawn (§4).

    The strongest are kept. A zone is context for reading the chart and never a
    blocker, so a non-zero count here costs the reader detail, not a warning.
    """

    overlays: tuple[ChartOverlayResponse, ...] = ()
    """Indicator series aligned one-to-one with ``candles`` (§11)."""

    analysed_count: int = 0
    """Every candle the engines read for this timeframe."""

    omitted_count: int = 0
    """Analysed but not drawn. Older than the display window, never
    downsampled or summarised - an omitted bar is absent, not approximated."""

    window_policy: str = ""
    """The named policy that produced this window, so a screenshot of a chart
    can be traced to the rule that shaped it."""


class ZoneResponse(BaseModel):
    """A support or resistance band the Phase 2 engine identified."""

    model_config = _PUBLIC

    id: str
    kind: str
    lower: str
    upper: str
    score: int | None = None


class TechnicalReadingResponse(BaseModel):
    """One Phase 1 indicator reading, or an honest absence (§10).

    ``available=False`` means the indicator's warm-up has not elapsed for this
    dataset. It is never reported as ``0``: a zero ATR reads as "no volatility
    measured" rather than "not enough candles yet".
    """

    model_config = _PUBLIC

    key: str
    label: str
    raw: str = ""
    unit: str = "indicator"
    available: bool = True
    unavailable_reason: str = ""
    beginner: bool = False
    """Whether this reading is part of the Beginner subset. Pro sees more of
    the same list, not a different list."""


class TechnicalPanelResponse(BaseModel):
    model_config = _PUBLIC

    timeframe: str
    role: str
    readings: tuple[TechnicalReadingResponse, ...] = ()


class ChartOverlayResponse(BaseModel):
    """A per-bar series aligned to the drawn candles (§11).

    Produced by Phase 1 and sliced to the display window, so a point can never
    appear beside a bar that is not drawn. ``None`` where the indicator had no
    value; the chart breaks the line rather than interpolating one.
    """

    model_config = _PUBLIC

    key: str
    label: str
    values: tuple[str | None, ...] = ()


class EvidenceItemResponse(BaseModel):
    model_config = _PUBLIC

    id: str
    direction: str
    strength: str
    category: str
    reason: str
    timeframe: str | None
    confirmation: str
    source: str


class ContradictionResponse(BaseModel):
    model_config = _PUBLIC

    id: str
    type: str
    severity: str
    detail: str


class ComponentScoreResponse(BaseModel):
    model_config = _PUBLIC

    component: str
    awarded: int | None
    weight: int
    availability: str


class ScenarioResponse(BaseModel):
    """Bull, bear or neutral. Deliberately not normalised against each other."""

    model_config = _PUBLIC

    case: str
    state: str
    reason: str
    quality_score: int | None = None
    quality_label: str = ""
    entry_score: int | None = None
    supporting: tuple[EvidenceItemResponse, ...] = ()
    counter: tuple[EvidenceItemResponse, ...] = ()
    requirements: tuple[str, ...] = ()
    invalidations: tuple[str, ...] = ()
    components: tuple[ComponentScoreResponse, ...] = ()


class FindingResponse(BaseModel):
    model_config = _PUBLIC

    id: str
    code: str
    severity: str
    detail: str


class SuitabilityResponse(BaseModel):
    model_config = _PUBLIC

    direction: str
    no_trade: bool | None
    """Three states. ``null`` is "could not be determined", which must never
    render as permission."""

    findings: tuple[FindingResponse, ...] = ()
    missing_requirements: tuple[str, ...] = ()


class ContractResponse(BaseModel):
    """Contract metadata, always with its verification status."""

    model_config = _PUBLIC

    symbol: str
    verified: bool
    multiplier: str | None = None
    multiplier_status: str | None = None
    tick_size: str | None = None
    tick_size_status: str | None = None
    asset_class: str | None = None
    """Phase 8.5, additive. The asset class of the *contract record* the
    trusted provider returned - never derived from the symbol the user typed.
    Present only when a contract exists; absent otherwise, because an analysis
    of uploaded candles has no established asset class."""

    asset_class_status: str | None = None
    """How well that classification is known. Authoritative only when the
    record's required specification facts are."""


class RiskResponse(BaseModel):
    """The risk answer, or exactly why there is not one."""

    model_config = _PUBLIC

    available: bool
    outcome: str
    """ALLOWED, NOT_PERMITTED or UNAVAILABLE. Never a zero standing in for
    "not calculated" (§32)."""

    direction: str | None = None
    detail: str = ""
    unavailable_reasons: tuple[str, ...] = ()
    facts: tuple[NumericFactResponse, ...] = ()
    warnings: tuple[str, ...] = ()
    contract: ContractResponse | None = None


class SynthesisResponse(BaseModel):
    """What the model said, or the typed reason it said nothing."""

    model_config = _PUBLIC

    status: str
    detail: str = ""
    final_action: str | None = None
    """Only ever set from an ACCEPTED synthesis inside an ActionEnvelope. A
    provider failure leaves this ``null`` (§22)."""

    allowed_actions: tuple[str, ...] = ()
    summary: tuple[SegmentResponse, ...] = ()
    bull_case: tuple[SegmentResponse, ...] = ()
    bear_case: tuple[SegmentResponse, ...] = ()
    neutral_case: tuple[SegmentResponse, ...] = ()
    devils_advocate: tuple[SegmentResponse, ...] = ()
    context_digest: str = ""


class SegmentResponse(BaseModel):
    """One piece of model narrative, already resolved against the facts.

    Text segments are prose; fact segments carry the deterministic value the
    model cited, so the client renders the number from the analysis rather than
    from anything the model typed (§22 of Phase 7).
    """

    model_config = _PUBLIC

    kind: str
    text: str = ""
    fact_id: str | None = None
    fact_label: str | None = None
    fact_value: str | None = None
    fact_source: str | None = None


class WhyReasonResponse(BaseModel):
    """One traceable cause, phrased for both audiences.

    ``beginner`` and ``pro`` are two renderings of one fact, produced together
    by the Why Engine from the same breakdown. Neither is derived from the
    other, so they cannot drift apart.
    """

    model_config = _PUBLIC

    code: str
    """Stable identifier - a component name, a reason code, an evidence source.
    The join back to the engine that produced it."""

    source: str
    severity: str
    beginner: str
    pro: str
    timeframe: str | None = None
    role: str | None = None


class WhyExplanationResponse(BaseModel):
    """Why one conclusion holds, or an honest statement that it cannot be said.

    ``available=False`` is a real answer, not an empty one: a position-size
    explanation for an analysis that never sized a position would explain
    nothing, so the topic says why instead of inventing a breakdown (§6).
    """

    model_config = _PUBLIC

    topic: str
    subject: str
    available: bool = True
    unavailable_reason: str = ""
    reasons: tuple[WhyReasonResponse, ...] = ()
    omitted_reason_count: int = 0
    """Reasons not listed under this topic (§4).

    One of every distinct reason code is always kept, most severe first, so a
    non-zero count means repeated instances of codes already shown were left
    out - never a kind of reason, and never a blocking one.
    """


class AnalysisIdentityResponse(BaseModel):
    model_config = _PUBLIC

    analysis_id: str
    symbol: str
    analysis_as_of: str | None
    generated_at: str
    contract_metadata_verified: bool
    datasets: tuple[DatasetIdentityResponse, ...] = ()
    ephemeral: bool = True
    """Always true. Stated in the payload so a client cannot mistake
    ``analysis_id`` for something it can fetch back (§7)."""


class DatasetIdentityResponse(BaseModel):
    model_config = _PUBLIC

    timeframe: str
    digest: str
    source_name: str
    row_count: int
    usable: bool


class AnalysisResponse(BaseModel):
    """One finished, ephemeral analysis."""

    model_config = _PUBLIC

    identity: AnalysisIdentityResponse
    technical_available: bool
    """Whether any timeframe produced a usable series. Separate from risk and
    synthesis availability, which are their own facts (§9)."""

    timeframes: tuple[TimeframeResultResponse, ...] = ()
    missing_timeframes: tuple[str, ...] = ()
    input_errors: tuple[str, ...] = ()
    chart: tuple[ChartSeriesResponse, ...] = ()
    evidence: tuple[EvidenceItemResponse, ...] = ()
    omitted_evidence_count: int = 0
    """Readings not listed (§4).

    The evidence list is capped per direction at one of every kind the engine can
    produce. A non-zero count means repeated instances of kinds already shown
    were left out - never a kind, and never a whole direction.
    """

    contradictions: tuple[ContradictionResponse, ...] = ()
    scenarios: tuple[ScenarioResponse, ...] = ()
    suitability: tuple[SuitabilityResponse, ...] = ()
    risk: RiskResponse
    synthesis: SynthesisResponse
    facts: tuple[NumericFactResponse, ...] = ()
    missing: tuple[str, ...] = ()
    technical: tuple[TechnicalPanelResponse, ...] = ()
    why: tuple[WhyExplanationResponse, ...] = ()
    """Explanations for the conclusions this analysis reached (§6).

    Assembled *after* everything else and never fed back in. A Why explanation
    describes an existing conclusion; it never creates one."""


class AnalysisErrorResponse(BaseModel):
    model_config = _PUBLIC

    code: str
    detail: str
