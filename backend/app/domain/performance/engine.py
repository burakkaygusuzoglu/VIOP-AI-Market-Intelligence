"""Turning authoritative position outcomes into statements a person can trust.

Everything here is a pure function of the records it is given. No clock, no
randomness, no locale, no database order: the caller supplies records and gets
the same answer every time, in ``Decimal`` throughout.

Two rules shape most of the code:

* **One position is one trade.** A position that exited through two targets and
  a stop contributed three fills and exactly one sample. Fill counts are
  reported separately and never inflate a trade-level statistic.
* **A metric that cannot be computed says so.** Division by an empty
  population, a profit factor with no losses, an average win with no wins - each
  returns an unavailable :class:`Metric` carrying its reason, never ``0``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.domain.common.enums import Direction, Timeframe
from app.domain.performance.model import (
    Coverage,
    InstrumentIdentity,
    Metric,
    MetricStatus,
    Outcome,
    PnlBasis,
    Population,
    PositionOutcome,
    RealizedFill,
    available,
    unavailable,
)

ZERO = Decimal(0)
_RATIO_PLACES = Decimal("0.0001")


def _ratio(value: Decimal) -> Decimal:
    """Round a *derived statistic* - never a stored amount - to four places.

    A mean or a percentage is a division that rarely terminates, and carrying
    28 significant digits into a response would imply a precision the sample
    does not have. Sums of money are never rounded here, and every rate also
    reports its exact numerator and denominator, so nothing is lost.
    """
    return value.quantize(_RATIO_PLACES, rounding=ROUND_HALF_UP)


BREAKEVEN_POLICY = "BREAKEVEN_BREAKS_BOTH_STREAKS"
"""A flat trade is neither a win nor a loss, so it ends a run of either. Stated
here because the alternative - ignoring breakevens - is equally defensible and
the choice must not be invisible."""


@dataclass(frozen=True, slots=True)
class PopulationCounts:
    """How many positions sit in each population. The denominators, in the open."""

    total: int
    pending_entry: int
    open_positions: int
    partially_closed: int
    ambiguous_halted: int
    closed: int
    cancelled: int
    rejected: int

    @property
    def entered(self) -> int:
        return self.open_positions + self.partially_closed + self.ambiguous_halted + self.closed

    @property
    def open_exposure(self) -> int:
        return self.open_positions + self.partially_closed + self.ambiguous_halted

    @property
    def never_entered(self) -> int:
        return self.pending_entry + self.cancelled + self.rejected


@dataclass(frozen=True, slots=True)
class TimelinePoint:
    """One completed trade's contribution to the cumulative realized curve."""

    position_id: str
    terminal_time: datetime
    amount: Decimal
    cumulative: Decimal


@dataclass(frozen=True, slots=True)
class Streaks:
    current_kind: Outcome | None
    current_length: int
    max_win_streak: int
    max_loss_streak: int
    policy: str = BREAKEVEN_POLICY


@dataclass(frozen=True, slots=True)
class GroupPerformance:
    """One row of a breakdown, computed by the same code as the headline."""

    key: str
    label: str
    counts: PopulationCounts
    sample_size: int
    wins: int
    losses: int
    breakevens: int
    realized_gross: Metric
    realized_net: Metric
    win_rate: Metric
    expectancy: Metric


@dataclass(frozen=True, slots=True)
class RealizedAccounting:
    """Money that has actually been realized, whatever state its position is in.

    This is not a trade statistic. A position that took one target and still
    holds the rest has realized that money - it simply has not finished, so it
    contributes here and *not* to the win rate, the expectancy sample or a
    streak. Keeping the two apart is the only way both can be true at once.
    """

    fill_count: int
    position_count: int
    from_completed: int
    from_open: int
    gross: Metric
    fees_known: Metric
    net: Metric
    coverage: Coverage


@dataclass(frozen=True, slots=True)
class PerformanceSummary:
    """The whole answer for one filtered set of positions."""

    basis: PnlBasis
    basis_reason: str
    counts: PopulationCounts
    sample_size: int
    fill_count: int
    wins: int
    losses: int
    breakevens: int
    fee_coverage: Coverage
    accounting: RealizedAccounting
    realized_gross: Metric
    fees_known: Metric
    realized_net: Metric
    unrealized_gross_open: Metric
    win_rate: Metric
    average_win: Metric
    average_loss: Metric
    profit_factor: Metric
    expectancy: Metric
    max_drawdown_absolute: Metric
    drawdown_percentage: Metric
    realized_r_expectancy: Metric
    mae: Metric
    mfe: Metric
    sharpe_ratio: Metric
    sortino_ratio: Metric
    annualised_return: Metric
    streaks: Streaks
    timeline: tuple[TimelinePoint, ...]


# ---------------------------------------------------------------------------
# Populations and ordering
# ---------------------------------------------------------------------------


def count_populations(records: Iterable[PositionOutcome]) -> PopulationCounts:
    tally = dict.fromkeys(Population, 0)
    total = 0
    for record in records:
        tally[record.population] += 1
        total += 1
    return PopulationCounts(
        total=total,
        pending_entry=tally[Population.PENDING_ENTRY],
        open_positions=tally[Population.OPEN],
        partially_closed=tally[Population.ENTERED_PARTIALLY_CLOSED],
        ambiguous_halted=tally[Population.AMBIGUOUS_HALTED],
        closed=tally[Population.CLOSED],
        cancelled=tally[Population.CANCELLED],
        rejected=tally[Population.REJECTED],
    )


def completed(
    records: Iterable[PositionOutcome],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[PositionOutcome, ...]:
    """Completed entered positions, in deterministic terminal-market-time order.

    This is the only sample any trade-level statistic is computed from. Open,
    halted, pending, cancelled and rejected positions are excluded here once,
    rather than being filtered out again in every metric.

    A window selects by the *closing* market time, which is the documented rule
    for completed trades. It is applied here rather than left to whatever
    supplied the records, so the rule holds for every source.
    """
    chosen = []
    for record in records:
        if not record.population.completed:
            continue
        closed_at = record.terminal_time
        if start is not None and closed_at is not None and closed_at < start:
            continue
        if end is not None and closed_at is not None and closed_at > end:
            continue
        chosen.append(record)
    return tuple(sorted(chosen, key=lambda r: r.order_key))


def choose_basis(sample: Sequence[PositionOutcome]) -> tuple[PnlBasis, str, Coverage]:
    """Net when every completed position modelled its fees; gross otherwise.

    Mixing a fee-modelled position with one whose fees are unknown cannot
    produce a net total, and quietly treating the unknown as zero would be a
    fabricated number. So the whole answer drops to gross and says so.
    """
    covered = sum(1 for record in sample if record.fees_modelled)
    coverage = Coverage(covered=covered, total=len(sample))
    if coverage.empty:
        return (
            PnlBasis.REALIZED_GROSS,
            "no completed positions; gross is the default basis",
            coverage,
        )
    if coverage.complete:
        return (
            PnlBasis.REALIZED_NET,
            "every completed position in this selection modelled its fees",
            coverage,
        )
    return (
        PnlBasis.REALIZED_GROSS,
        (
            f"{coverage.covered} of {coverage.total} completed positions modelled fees, "
            "so a net result is not knowable for this selection"
        ),
        coverage,
    )


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------


def realized_gross_total(sample: Sequence[PositionOutcome]) -> Metric:
    if not sample:
        return unavailable("no completed positions in this selection")
    total = sum((record.realized_gross for record in sample), ZERO)
    return available(total, basis=PnlBasis.REALIZED_GROSS, sample_size=len(sample))


def fees_known_total(sample: Sequence[PositionOutcome]) -> Metric:
    """Fees actually modelled. Reported with its coverage, never extrapolated."""
    coverage = Coverage(sum(1 for r in sample if r.fees_modelled), len(sample))
    if coverage.empty:
        return unavailable("no completed positions in this selection", coverage=coverage)
    if coverage.covered == 0:
        return unavailable(
            "no completed position modelled fees", coverage=coverage, sample_size=len(sample)
        )
    total = sum((r.fees_total for r in sample if r.fees_total is not None), ZERO)
    return available(total, sample_size=coverage.covered, coverage=coverage)


def realized_net_total(sample: Sequence[PositionOutcome]) -> Metric:
    coverage = Coverage(sum(1 for r in sample if r.fees_modelled), len(sample))
    if coverage.empty:
        return unavailable("no completed positions in this selection", coverage=coverage)
    if coverage.covered == 0:
        return unavailable(
            "no completed position modelled fees, so net results are unknown for this selection",
            basis=PnlBasis.REALIZED_NET,
            coverage=coverage,
            sample_size=len(sample),
        )
    if not coverage.complete:
        return unavailable(
            (
                f"only {coverage.covered} of {coverage.total} completed positions modelled fees; "
                "a net total over a partly costed population would be a fabricated number"
            ),
            basis=PnlBasis.REALIZED_NET,
            coverage=coverage,
            sample_size=len(sample),
            status=MetricStatus.PARTIAL_COVERAGE,
        )
    total = sum((r.realized_net for r in sample if r.realized_net is not None), ZERO)
    return available(total, basis=PnlBasis.REALIZED_NET, sample_size=len(sample), coverage=coverage)


def realized_accounting(
    records: Sequence[PositionOutcome],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> RealizedAccounting:
    """Every fill that has already happened, attributed to its own market time.

    Completed and still-open positions both contribute; the date window - when
    one is given - selects *fills*, not positions, because that is when the
    money was realized. Fees follow the same rule: a fill whose fee was not
    modelled leaves the net unknown for the whole selection rather than being
    counted as free.
    """
    fills: list[RealizedFill] = []
    positions = completed_count = open_count = 0
    for record in records:
        selected = [fill for fill in record.fills if fill.within(start, end)]
        if not selected:
            continue
        positions += 1
        if record.population.completed:
            completed_count += 1
        else:
            open_count += 1
        fills.extend(selected)

    costed = sum(1 for fill in fills if fill.fee is not None)
    coverage = Coverage(covered=costed, total=len(fills))
    if not fills:
        reason = "no exit has been filled in this selection"
        return RealizedAccounting(
            fill_count=0,
            position_count=0,
            from_completed=0,
            from_open=0,
            gross=unavailable(reason, basis=PnlBasis.REALIZED_GROSS),
            fees_known=unavailable(reason),
            net=unavailable(reason, basis=PnlBasis.REALIZED_NET),
            coverage=coverage,
        )

    gross = sum((fill.amount for fill in fills), ZERO)
    fees = sum((fill.fee for fill in fills if fill.fee is not None), ZERO)
    if coverage.complete:
        net = available(
            gross - fees, basis=PnlBasis.REALIZED_NET, sample_size=len(fills), coverage=coverage
        )
    elif costed == 0:
        net = unavailable(
            "no fill in this selection modelled a fee, so a net result is unknown",
            basis=PnlBasis.REALIZED_NET,
            coverage=coverage,
        )
    else:
        net = unavailable(
            (
                f"only {costed} of {len(fills)} fills modelled a fee; a net total over partly "
                "costed fills would be a fabricated number"
            ),
            basis=PnlBasis.REALIZED_NET,
            coverage=coverage,
            status=MetricStatus.PARTIAL_COVERAGE,
        )
    return RealizedAccounting(
        fill_count=len(fills),
        position_count=positions,
        from_completed=completed_count,
        from_open=open_count,
        gross=available(gross, basis=PnlBasis.REALIZED_GROSS, sample_size=len(fills)),
        fees_known=(
            available(fees, sample_size=costed, coverage=coverage)
            if costed
            else unavailable("no fill in this selection modelled a fee", coverage=coverage)
        ),
        net=net,
        coverage=coverage,
    )


def unrealized_gross_open(records: Sequence[PositionOutcome]) -> Metric:
    """Mark-to-market on positions still exposed. Never added to realized money."""
    exposed = [r for r in records if r.population.open_exposure]
    if not exposed:
        return unavailable("no position is currently open")
    known = [r.unrealized_gross for r in exposed if r.unrealized_gross is not None]
    coverage = Coverage(len(known), len(exposed))
    if not coverage.complete:
        return unavailable(
            "some open positions have no mark yet, so the open total is not knowable",
            coverage=coverage,
            status=MetricStatus.PARTIAL_COVERAGE,
        )
    return available(
        sum(known, ZERO),
        basis=PnlBasis.REALIZED_GROSS,
        sample_size=len(exposed),
        coverage=coverage,
    )


# ---------------------------------------------------------------------------
# Trade-level statistics
# ---------------------------------------------------------------------------


def classify(sample: Sequence[PositionOutcome], basis: PnlBasis) -> tuple[int, int, int]:
    """Wins, losses and breakevens on one stated basis."""
    wins = losses = breakevens = 0
    for record in sample:
        match record.outcome(basis):
            case Outcome.WIN:
                wins += 1
            case Outcome.LOSS:
                losses += 1
            case Outcome.BREAKEVEN:
                breakevens += 1
            case None:  # pragma: no cover - basis is chosen to be computable
                continue
    return wins, losses, breakevens


def win_rate(sample: Sequence[PositionOutcome], basis: PnlBasis) -> Metric:
    """Wins over completed entered positions. Breakeven is in the denominator."""
    if not sample:
        return unavailable(
            "no completed positions, so there is no win rate to compute", basis=basis
        )
    wins, _, _ = classify(sample, basis)
    rate = _ratio((Decimal(wins) / Decimal(len(sample))) * Decimal(100))
    return available(
        rate, basis=basis, sample_size=len(sample), numerator=wins, denominator=len(sample)
    )


def average_win(sample: Sequence[PositionOutcome], basis: PnlBasis) -> Metric:
    wins = [a for r in sample if (a := r.amount(basis)) is not None and a > 0]
    if not wins:
        return unavailable("no winning completed position in this selection", basis=basis)
    return available(
        _ratio(sum(wins, ZERO) / Decimal(len(wins))), basis=basis, sample_size=len(wins)
    )


def average_loss(sample: Sequence[PositionOutcome], basis: PnlBasis) -> Metric:
    """The average losing amount, as a magnitude. Compared against average win."""
    losses = [-a for r in sample if (a := r.amount(basis)) is not None and a < 0]
    if not losses:
        return unavailable("no losing completed position in this selection", basis=basis)
    return available(
        _ratio(sum(losses, ZERO) / Decimal(len(losses))), basis=basis, sample_size=len(losses)
    )


def profit_factor(sample: Sequence[PositionOutcome], basis: PnlBasis) -> Metric:
    """Winnings divided by losings - undefined without losings.

    With no losses the ratio has no finite value, and ``Infinity`` is not a
    number this API will send. It is reported unavailable with that reason.
    """
    if not sample:
        return unavailable("no completed positions in this selection", basis=basis)
    gains = sum((a for r in sample if (a := r.amount(basis)) is not None and a > 0), ZERO)
    losses = sum((-a for r in sample if (a := r.amount(basis)) is not None and a < 0), ZERO)
    if losses == ZERO:
        return unavailable(
            "no losing completed position, so the ratio has no finite value",
            basis=basis,
            sample_size=len(sample),
        )
    return available(_ratio(gains / losses), basis=basis, sample_size=len(sample))


def expectancy(sample: Sequence[PositionOutcome], basis: PnlBasis) -> Metric:
    """Mean realized result per completed position. A description, not a forecast.

    The sample size travels with it precisely so a mean of three trades cannot
    be read as a property of a strategy.
    """
    if not sample:
        return unavailable("no completed positions in this selection", basis=basis)
    amounts = [a for r in sample if (a := r.amount(basis)) is not None]
    return available(
        _ratio(sum(amounts, ZERO) / Decimal(len(amounts))), basis=basis, sample_size=len(amounts)
    )


# ---------------------------------------------------------------------------
# Curve, drawdown, streaks
# ---------------------------------------------------------------------------


def timeline(sample: Sequence[PositionOutcome], basis: PnlBasis) -> tuple[TimelinePoint, ...]:
    """Cumulative *realized* result, one point per completed position.

    This is not an account equity curve: the application holds no capital
    history, and nothing about open positions is inserted into it.
    """
    points: list[TimelinePoint] = []
    running = ZERO
    for record in sample:
        amount = record.amount(basis)
        if amount is None:  # pragma: no cover - basis is chosen to be computable
            continue
        running += amount
        points.append(
            TimelinePoint(
                position_id=record.position_id,
                terminal_time=record.terminal_time,  # type: ignore[arg-type]
                amount=amount,
                cumulative=running,
            )
        )
    return tuple(points)


def max_drawdown_absolute(points: Sequence[TimelinePoint], basis: PnlBasis) -> Metric:
    """Largest peak-to-trough fall of the cumulative realized curve.

    An amount of simulated money, not a percentage: a percentage needs a capital
    timeline this application does not have.
    """
    if not points:
        return unavailable("no completed positions, so there is no curve", basis=basis)
    peak = points[0].cumulative
    worst = ZERO
    for point in points:
        peak = max(peak, point.cumulative)
        worst = max(worst, peak - point.cumulative)
    return available(worst, basis=basis, sample_size=len(points))


def streaks(sample: Sequence[PositionOutcome], basis: PnlBasis) -> Streaks:
    """Runs of wins and losses in terminal-market-time order.

    A breakeven ends both runs - see :data:`BREAKEVEN_POLICY`.
    """
    current_kind: Outcome | None = None
    current = longest_win = longest_loss = 0
    for record in sample:
        outcome = record.outcome(basis)
        if outcome is None:  # pragma: no cover - basis is chosen to be computable
            continue
        if outcome is Outcome.BREAKEVEN:
            current_kind, current = None, 0
            continue
        current = current + 1 if outcome is current_kind else 1
        current_kind = outcome
        if outcome is Outcome.WIN:
            longest_win = max(longest_win, current)
        else:
            longest_loss = max(longest_loss, current)
    return Streaks(
        current_kind=current_kind,
        current_length=current,
        max_win_streak=longest_win,
        max_loss_streak=longest_loss,
    )


# ---------------------------------------------------------------------------
# Deliberately unavailable metrics
# ---------------------------------------------------------------------------

_NO_CAPITAL_HISTORY = (
    "the application holds no account-capital timeline, so a percentage of capital "
    "cannot be computed from simulated position results"
)
_NO_R_DENOMINATOR = (
    "an R multiple needs one unambiguous initial-risk amount; the stored risk approval, "
    "the planned stop distance and the realized entry-fill-to-stop distance are three "
    "different quantities, and choosing between them here would be a guess"
)
_NO_EXCURSION_SEMANTICS = (
    "bar OHLC records no order inside a bar, so the worst and best prices a position "
    "actually saw - before or after each partial exit - are not determinable without "
    "assuming an intrabar path"
)
_NO_RETURN_SERIES = (
    "a risk-adjusted ratio needs a capital base, a periodic return series and a "
    "sampling convention; none of them exist in this data model"
)


def unavailable_by_design() -> dict[str, Metric]:
    """Metrics deliberately not implemented, each with the reason it cannot be."""
    return {
        "drawdown_percentage": unavailable(_NO_CAPITAL_HISTORY),
        "realized_r_expectancy": unavailable(
            _NO_R_DENOMINATOR, status=MetricStatus.NOT_IMPLEMENTED
        ),
        "mae": unavailable(_NO_EXCURSION_SEMANTICS, status=MetricStatus.NOT_IMPLEMENTED),
        "mfe": unavailable(_NO_EXCURSION_SEMANTICS, status=MetricStatus.NOT_IMPLEMENTED),
        "sharpe_ratio": unavailable(_NO_RETURN_SERIES, status=MetricStatus.NOT_IMPLEMENTED),
        "sortino_ratio": unavailable(_NO_RETURN_SERIES, status=MetricStatus.NOT_IMPLEMENTED),
        "annualised_return": unavailable(_NO_RETURN_SERIES, status=MetricStatus.NOT_IMPLEMENTED),
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def summarise(
    records: Sequence[PositionOutcome],
    *,
    fill_count: int = 0,
    start: datetime | None = None,
    end: datetime | None = None,
) -> PerformanceSummary:
    """The headline answer for one selection of positions.

    Two populations are reported side by side and never mixed: completed trades
    (the sample every statistic is computed from) and realized accounting (every
    fill that has already happened, including those of positions still open).
    """
    sample = completed(records, start=start, end=end)
    basis, basis_reason, coverage = choose_basis(sample)
    wins, losses, breakevens = classify(sample, basis)
    points = timeline(sample, basis)
    absent = unavailable_by_design()
    return PerformanceSummary(
        basis=basis,
        basis_reason=basis_reason,
        counts=count_populations(records),
        sample_size=len(sample),
        fill_count=fill_count,
        wins=wins,
        losses=losses,
        breakevens=breakevens,
        fee_coverage=coverage,
        accounting=realized_accounting(records, start=start, end=end),
        realized_gross=realized_gross_total(sample),
        fees_known=fees_known_total(sample),
        realized_net=realized_net_total(sample),
        unrealized_gross_open=unrealized_gross_open(records),
        win_rate=win_rate(sample, basis),
        average_win=average_win(sample, basis),
        average_loss=average_loss(sample, basis),
        profit_factor=profit_factor(sample, basis),
        expectancy=expectancy(sample, basis),
        max_drawdown_absolute=max_drawdown_absolute(points, basis),
        drawdown_percentage=absent["drawdown_percentage"],
        realized_r_expectancy=absent["realized_r_expectancy"],
        mae=absent["mae"],
        mfe=absent["mfe"],
        sharpe_ratio=absent["sharpe_ratio"],
        sortino_ratio=absent["sortino_ratio"],
        annualised_return=absent["annualised_return"],
        streaks=streaks(sample, basis),
        timeline=points,
    )


def _group(
    records: Sequence[PositionOutcome],
    *,
    key_of: Callable[[PositionOutcome], tuple[str, str]],
) -> tuple[GroupPerformance, ...]:
    """Group records and compute each group with the same functions as the headline.

    The basis is chosen per group, so a group whose fees are fully modelled can
    report net even when the whole selection cannot.
    """
    buckets: dict[tuple[str, str], list[PositionOutcome]] = {}
    for record in records:
        buckets.setdefault(key_of(record), []).append(record)

    rows: list[GroupPerformance] = []
    for (key, label), members in buckets.items():
        sample = completed(members)
        basis, _, _ = choose_basis(sample)
        wins, losses, breakevens = classify(sample, basis)
        rows.append(
            GroupPerformance(
                key=key,
                label=label,
                counts=count_populations(members),
                sample_size=len(sample),
                wins=wins,
                losses=losses,
                breakevens=breakevens,
                realized_gross=realized_gross_total(sample),
                realized_net=realized_net_total(sample),
                win_rate=win_rate(sample, basis),
                expectancy=expectancy(sample, basis),
            )
        )
    # Deterministic: most completed trades first, then by key text.
    return tuple(sorted(rows, key=lambda row: (-row.sample_size, row.key)))


def by_direction(records: Sequence[PositionOutcome]) -> tuple[GroupPerformance, ...]:
    def key_of(record: PositionOutcome) -> tuple[str, str]:
        direction: Direction = record.direction
        return (direction.value, direction.value)

    return _group(records, key_of=key_of)


def by_instrument(records: Sequence[PositionOutcome]) -> tuple[GroupPerformance, ...]:
    def key_of(record: PositionOutcome) -> tuple[str, str]:
        identity: InstrumentIdentity = record.instrument
        return (f"{identity.asset_class}:{identity.symbol}", identity.symbol)

    return _group(records, key_of=key_of)


def by_timeframe(records: Sequence[PositionOutcome]) -> tuple[GroupPerformance, ...]:
    def key_of(record: PositionOutcome) -> tuple[str, str]:
        timeframe: Timeframe = record.timeframe
        return (timeframe.value, timeframe.value)

    return _group(records, key_of=key_of)
