"""Rendering performance answers as transport types.

Read-only. Every number is already a ``Decimal`` the engine produced; this
module formats it as an exact string and copies the status, basis, sample size
and reason beside it. It performs no arithmetic - not a sum, not a ratio, not a
rounding of an amount - so a metric cannot acquire a different meaning on its
way out.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.api.schemas.performance import (
    BreakdownsResponse,
    CoverageResponse,
    FiltersResponse,
    GroupResponse,
    GroupSetResponse,
    JournalAnnotationResponse,
    JournalPageResponse,
    JournalRowResponse,
    MetricResponse,
    PerformanceResponse,
    PopulationCountsResponse,
    RealizedAccountingResponse,
    StreaksResponse,
    TagCountResponse,
    TagListResponse,
    TimelinePointResponse,
)
from app.application.performance.ports import JournalRow, OutcomeFilters
from app.application.performance.service import (
    BreakdownView,
    GroupSet,
    JournalPage,
    PerformanceView,
)
from app.domain.journal import JournalAnnotation
from app.domain.performance import (
    Coverage,
    GroupPerformance,
    Metric,
    PerformanceSummary,
    PnlBasis,
    PopulationCounts,
    PositionOutcome,
    RealizedAccounting,
    Streaks,
    TimelinePoint,
)

RANGE_RULE = (
    "Completed trades belong to a range by the MARKET time of their closing fill. "
    "Realized accounting selects individual FILLS by their own market time, so a "
    "position still open contributes the money it has already realized. Current "
    "exposure and unrealized P&L are as of each position's last observed bar and "
    "are never filtered out by a date range - hiding them would misdescribe what "
    "is open right now."
)

ACCOUNTING_TIME_RULE = (
    "Each fill is attributed to its own market time, not to the position's eventual "
    "closing time and never to a database timestamp."
)

ANALYSIS_LINKAGE = (
    "Positions are USER_CREATED and no analysis snapshot is persisted, so setup, "
    "regime, signal and AI-verdict performance cannot be derived. This is the "
    "current data model, not a missing screen."
)

UNAVAILABLE_BREAKDOWNS = (
    "setup: no persisted analysis linkage",
    "regime: no persisted analysis linkage",
    "ai_verdict: no persisted analysis linkage",
    "venue: no venue is recorded on an instrument identity",
)


def _text(value: Decimal) -> str:
    return format(value, "f")


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def metric(item: Metric) -> MetricResponse:
    return MetricResponse(
        status=item.status.value,
        value=None if item.value is None else _text(item.value),
        basis=None if item.basis is None else item.basis.value,
        sample_size=item.sample_size,
        coverage=None if item.coverage is None else coverage(item.coverage),
        reason=item.reason,
        numerator=item.numerator,
        denominator=item.denominator,
    )


def coverage(item: Coverage) -> CoverageResponse:
    return CoverageResponse(covered=item.covered, total=item.total)


def counts(item: PopulationCounts) -> PopulationCountsResponse:
    return PopulationCountsResponse(
        total=item.total,
        pending_entry=item.pending_entry,
        open_positions=item.open_positions,
        partially_closed=item.partially_closed,
        ambiguous_halted=item.ambiguous_halted,
        closed=item.closed,
        cancelled=item.cancelled,
        rejected=item.rejected,
        entered=item.entered,
        open_exposure=item.open_exposure,
        never_entered=item.never_entered,
    )


def streaks(item: Streaks) -> StreaksResponse:
    return StreaksResponse(
        current_kind=None if item.current_kind is None else item.current_kind.value,
        current_length=item.current_length,
        max_win_streak=item.max_win_streak,
        max_loss_streak=item.max_loss_streak,
        policy=item.policy,
    )


def point(item: TimelinePoint) -> TimelinePointResponse:
    return TimelinePointResponse(
        position_id=item.position_id,
        terminal_time=item.terminal_time.isoformat(),
        amount=_text(item.amount),
        cumulative=_text(item.cumulative),
    )


def filters(item: OutcomeFilters) -> FiltersResponse:
    return FiltersResponse(
        closed_from=_time(item.closed_from),
        closed_to=_time(item.closed_to),
        direction=None if item.direction is None else item.direction.value,
        symbol=item.symbol,
        timeframe=None if item.timeframe is None else item.timeframe.value,
        tag=item.tag,
        range_rule=RANGE_RULE,
    )


def accounting(item: RealizedAccounting) -> RealizedAccountingResponse:
    return RealizedAccountingResponse(
        fill_count=item.fill_count,
        position_count=item.position_count,
        from_completed_positions=item.from_completed,
        from_open_positions=item.from_open,
        coverage=coverage(item.coverage),
        gross=metric(item.gross),
        fees_known=metric(item.fees_known),
        net=metric(item.net),
        time_rule=ACCOUNTING_TIME_RULE,
    )


def group_set(item: GroupSet) -> GroupSetResponse:
    return GroupSetResponse(
        rows=tuple(group(row) for row in item.rows),
        total=item.total,
        returned=len(item.rows),
        omitted=item.omitted,
        is_complete=item.is_complete,
    )


def performance(view: PerformanceView) -> PerformanceResponse:
    summary: PerformanceSummary = view.summary
    return PerformanceResponse(
        source="PAPER_SIMULATION",
        basis=summary.basis.value,
        basis_reason=summary.basis_reason,
        filters=filters(view.filters),
        counts=counts(summary.counts),
        sample_size=summary.sample_size,
        fill_count=summary.fill_count,
        wins=summary.wins,
        losses=summary.losses,
        breakevens=summary.breakevens,
        fee_coverage=coverage(summary.fee_coverage),
        realized_accounting=accounting(summary.accounting),
        realized_gross=metric(summary.realized_gross),
        fees_known=metric(summary.fees_known),
        realized_net=metric(summary.realized_net),
        unrealized_gross_open=metric(summary.unrealized_gross_open),
        win_rate=metric(summary.win_rate),
        average_win=metric(summary.average_win),
        average_loss=metric(summary.average_loss),
        profit_factor=metric(summary.profit_factor),
        expectancy=metric(summary.expectancy),
        max_drawdown_absolute=metric(summary.max_drawdown_absolute),
        drawdown_percentage=metric(summary.drawdown_percentage),
        realized_r_expectancy=metric(summary.realized_r_expectancy),
        mae=metric(summary.mae),
        mfe=metric(summary.mfe),
        sharpe_ratio=metric(summary.sharpe_ratio),
        sortino_ratio=metric(summary.sortino_ratio),
        annualised_return=metric(summary.annualised_return),
        streaks=streaks(summary.streaks),
        timeline=tuple(point(item) for item in summary.timeline),
        analysis_linkage=ANALYSIS_LINKAGE,
    )


def group(item: GroupPerformance) -> GroupResponse:
    return GroupResponse(
        key=item.key,
        label=item.label,
        counts=counts(item.counts),
        sample_size=item.sample_size,
        wins=item.wins,
        losses=item.losses,
        breakevens=item.breakevens,
        realized_gross=metric(item.realized_gross),
        realized_net=metric(item.realized_net),
        win_rate=metric(item.win_rate),
        expectancy=metric(item.expectancy),
    )


def breakdowns(view: BreakdownView) -> BreakdownsResponse:
    return BreakdownsResponse(
        filters=filters(view.filters),
        by_direction=group_set(view.by_direction),
        by_instrument=group_set(view.by_instrument),
        by_timeframe=group_set(view.by_timeframe),
        unavailable_breakdowns=UNAVAILABLE_BREAKDOWNS,
    )


def annotation(item: JournalAnnotation) -> JournalAnnotationResponse:
    return JournalAnnotationResponse(
        position_id=item.position_id,
        note=item.note,
        tags=item.tags,
        version=item.version,
        created_at=_time(item.created_at),
        updated_at=_time(item.updated_at),
    )


def journal_row(item: JournalRow) -> JournalRowResponse:
    outcome: PositionOutcome = item.outcome
    # This position's own facts, computed from this position alone: what other
    # positions are in the selection cannot change them.
    completed_position = outcome.population.completed
    gross_outcome = outcome.outcome(PnlBasis.REALIZED_GROSS) if completed_position else None
    net_outcome = (
        outcome.outcome(PnlBasis.REALIZED_NET)
        if completed_position and outcome.fees_modelled
        else None
    )
    basis = PnlBasis.REALIZED_NET if outcome.fees_modelled else PnlBasis.REALIZED_GROSS
    classified = net_outcome if net_outcome is not None else gross_outcome
    return JournalRowResponse(
        position_id=outcome.position_id,
        symbol=outcome.instrument.symbol,
        asset_class=outcome.instrument.asset_class,
        direction=outcome.direction.value,
        timeframe=outcome.timeframe.value,
        quantity=outcome.quantity,
        population=outcome.population.value,
        outcome=None if classified is None else classified.value,
        outcome_basis=None if classified is None else basis.value,
        outcome_gross=None if gross_outcome is None else gross_outcome.value,
        outcome_net=None if net_outcome is None else net_outcome.value,
        realized_gross=_text(outcome.realized_gross),
        fees_total=None if outcome.fees_total is None else _text(outcome.fees_total),
        realized_net=None if outcome.realized_net is None else _text(outcome.realized_net),
        terminal_time=_time(outcome.terminal_time),
        decision_time=outcome.decision_time.isoformat(),
        annotation=annotation(item.annotation),
    )


def journal_page(page: JournalPage, applied: OutcomeFilters) -> JournalPageResponse:
    return JournalPageResponse(
        items=tuple(journal_row(row) for row in page.rows),
        total=page.total,
        offset=page.offset,
        limit=page.limit,
        filters=filters(applied),
    )


def tag_list(
    items: tuple[tuple[str, int], ...], limit: int, *, is_complete: bool = True
) -> TagListResponse:
    return TagListResponse(
        items=tuple(TagCountResponse(tag=tag, positions=count) for tag, count in items),
        limit=limit,
        is_complete=is_complete,
    )
