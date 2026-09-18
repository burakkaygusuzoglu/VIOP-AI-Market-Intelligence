"""Performance and journal transport types (Phase 10).

Two rules shape every model here.

**A client states intentions, never results.** The journal request carries a
note, some tags and the version it was read at. There is no field for a P&L, an
outcome, a win rate, a streak, a provenance or a classification, and
``extra="forbid"`` refuses one that is invented.

**An unknown is never a zero.** Every metric travels as a status with an
optional value, so ``profit_factor`` with no losing trades arrives as
UNAVAILABLE with the reason, not as ``0``. Amounts are exact decimal strings.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.journal import MAX_NOTE_LENGTH, MAX_TAG_LENGTH, MAX_TAGS


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Response(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CoverageResponse(_Response):
    covered: int
    total: int


class MetricResponse(_Response):
    """One number, or the reason there isn't one."""

    status: Literal["AVAILABLE", "UNAVAILABLE", "PARTIAL_COVERAGE", "NOT_IMPLEMENTED"]
    value: str | None = None
    basis: str | None = None
    sample_size: int = 0
    coverage: CoverageResponse | None = None
    reason: str | None = None
    numerator: int | None = None
    denominator: int | None = None


class PopulationCountsResponse(_Response):
    total: int
    pending_entry: int
    open_positions: int
    partially_closed: int
    ambiguous_halted: int
    closed: int
    cancelled: int
    rejected: int
    entered: int
    open_exposure: int
    never_entered: int


class StreaksResponse(_Response):
    current_kind: str | None
    current_length: int
    max_win_streak: int
    max_loss_streak: int
    policy: str


class TimelinePointResponse(_Response):
    position_id: str
    terminal_time: str
    amount: str
    cumulative: str


class FiltersResponse(_Response):
    """What the answer is about, echoed so a screen cannot mislabel it."""

    closed_from: str | None
    closed_to: str | None
    direction: str | None
    symbol: str | None
    timeframe: str | None
    tag: str | None
    range_rule: str


class RealizedAccountingResponse(_Response):
    """Money already realized, whatever state its position is in.

    Separate from the trade statistics on purpose: a position that took one
    target and still holds the rest has realized that money, and has not
    finished a trade. Both facts are true and neither is allowed to hide the
    other.
    """

    fill_count: int
    position_count: int
    from_completed_positions: int
    from_open_positions: int
    coverage: CoverageResponse
    gross: MetricResponse
    fees_known: MetricResponse
    net: MetricResponse
    time_rule: str


class PerformanceResponse(_Response):
    simulated: Literal[True] = True
    source: str
    basis: str
    basis_reason: str
    filters: FiltersResponse
    counts: PopulationCountsResponse
    sample_size: int
    fill_count: int
    wins: int
    losses: int
    breakevens: int
    fee_coverage: CoverageResponse
    realized_accounting: RealizedAccountingResponse
    realized_gross: MetricResponse
    fees_known: MetricResponse
    realized_net: MetricResponse
    unrealized_gross_open: MetricResponse
    win_rate: MetricResponse
    average_win: MetricResponse
    average_loss: MetricResponse
    profit_factor: MetricResponse
    expectancy: MetricResponse
    max_drawdown_absolute: MetricResponse
    drawdown_percentage: MetricResponse
    realized_r_expectancy: MetricResponse
    mae: MetricResponse
    mfe: MetricResponse
    sharpe_ratio: MetricResponse
    sortino_ratio: MetricResponse
    annualised_return: MetricResponse
    streaks: StreaksResponse
    timeline: tuple[TimelinePointResponse, ...]
    analysis_linkage: str
    """Why no setup, regime or AI breakdown exists in this data model."""


class GroupResponse(_Response):
    key: str
    label: str
    counts: PopulationCountsResponse
    sample_size: int
    wins: int
    losses: int
    breakevens: int
    realized_gross: MetricResponse
    realized_net: MetricResponse
    win_rate: MetricResponse
    expectancy: MetricResponse


class GroupSetResponse(_Response):
    """A breakdown with its own completeness, so a partial list cannot pass as whole."""

    rows: tuple[GroupResponse, ...]
    total: int
    returned: int
    omitted: int
    is_complete: bool


class BreakdownsResponse(_Response):
    simulated: Literal[True] = True
    filters: FiltersResponse
    by_direction: GroupSetResponse
    by_instrument: GroupSetResponse
    by_timeframe: GroupSetResponse
    unavailable_breakdowns: tuple[str, ...]
    """Breakdowns that cannot exist here - setup, regime, AI verdict - named so
    their absence reads as a decision rather than an oversight."""


class JournalAnnotationResponse(_Response):
    position_id: str
    note: str | None
    tags: tuple[str, ...]
    version: int
    created_at: str | None
    updated_at: str | None
    provenance: Literal["USER_AUTHORED"] = "USER_AUTHORED"


class JournalRowResponse(_Response):
    """Immutable simulated facts beside the person's own writing."""

    position_id: str
    symbol: str
    asset_class: str
    direction: str
    timeframe: str
    quantity: int
    population: str
    outcome: str | None
    outcome_basis: str | None
    outcome_gross: str | None
    """This position's own outcome before costs. Depends on nothing but itself."""

    outcome_net: str | None
    """Its outcome after the fees it modelled, or null when they are unknown."""
    realized_gross: str
    fees_total: str | None
    realized_net: str | None
    terminal_time: str | None
    decision_time: str
    annotation: JournalAnnotationResponse


class JournalPageResponse(_Response):
    simulated: Literal[True] = True
    items: tuple[JournalRowResponse, ...]
    total: int
    offset: int
    limit: int
    filters: FiltersResponse


class TagCountResponse(_Response):
    tag: str
    positions: int


class TagListResponse(_Response):
    items: tuple[TagCountResponse, ...]
    limit: int
    is_complete: bool
    """False when more tags exist than are listed - a suggestion list may be
    shortened, but never while pretending to be all of them."""


class JournalUpdateBody(_Strict):
    """Everything a person may change about a position: their own words.

    ``expected_version`` is the version they read. A mismatch is a conflict, so
    a second editor is told instead of overwriting the first.
    """

    note: str | None = Field(default=None, max_length=MAX_NOTE_LENGTH)
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    expected_version: int = Field(ge=0, le=1_000_000)

    model_config = ConfigDict(extra="forbid")


MAX_TAG_TEXT = MAX_TAG_LENGTH
