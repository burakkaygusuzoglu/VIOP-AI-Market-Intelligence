"""What performance is computed *from*, and how an answer reports itself.

The vocabulary here exists to stop two specific lies.

The first is counting the wrong things: a position that never entered is not a
losing trade, an open position is not a completed one, and a position that
exited in three pieces is still one trade. So a record carries its
:class:`Population`, and every metric states the population it used.

The second is presenting an unknown as a zero. Fees may be deliberately not
modelled (Phase 9 ``FeePolicy.NOT_MODELLED``), in which case net profit is not
zero-fee profit - it is unknown. A metric is therefore a
:class:`Metric`, not a number: it reports AVAILABLE with a value, or
UNAVAILABLE/PARTIAL_COVERAGE with a reason a person can read.

This module is pure: stdlib and ``app.domain.common`` only. It knows nothing
about paper trading, products, storage or HTTP - a future replay or backtest
could produce the same :class:`PositionOutcome` records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.enums import Direction, Timeframe

SOURCE_PAPER_SIMULATION = "PAPER_SIMULATION"
"""The only outcome source that exists. Recorded so a later replay or backtest
can be told apart rather than silently mixed in."""


@unique
class Population(StrEnum):
    """Where a position stands. Every metric names the populations it counts."""

    PENDING_ENTRY = "PENDING_ENTRY"
    """Created; the entry has not filled. Never entered, never a trade."""

    OPEN = "OPEN"
    ENTERED_PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    AMBIGUOUS_HALTED = "AMBIGUOUS_HALTED"
    """Entered and frozen by same-bar ambiguity. Exposure remains; not complete."""

    CLOSED = "CLOSED"
    """The only population that contributes a completed trade sample."""

    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

    @property
    def entered(self) -> bool:
        """True once an entry actually filled."""
        return self in _ENTERED

    @property
    def completed(self) -> bool:
        """True when the position is finished and its realized result is final."""
        return self is Population.CLOSED

    @property
    def open_exposure(self) -> bool:
        return self in _EXPOSED


_ENTERED = frozenset(
    {
        Population.OPEN,
        Population.ENTERED_PARTIALLY_CLOSED,
        Population.AMBIGUOUS_HALTED,
        Population.CLOSED,
    }
)
_EXPOSED = frozenset(
    {Population.OPEN, Population.ENTERED_PARTIALLY_CLOSED, Population.AMBIGUOUS_HALTED}
)


@unique
class Outcome(StrEnum):
    """How a completed trade ended, on a stated basis."""

    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"
    """Exactly zero on the basis used. Never counted as a win."""


@unique
class PnlBasis(StrEnum):
    """Which money a metric is about. Always reported beside the number."""

    REALIZED_GROSS = "REALIZED_GROSS"
    """Before costs. Always knowable, because fills are simulated exactly."""

    REALIZED_NET = "REALIZED_NET"
    """After the fees the person asked to be modelled. Only usable when every
    position in the population modelled them."""


@unique
class MetricStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    """The inputs do not exist, or the mathematics is undefined here."""

    PARTIAL_COVERAGE = "PARTIAL_COVERAGE"
    """Some of the population supports the metric and some does not. The value
    is withheld: a sum over part of a population is not that population's sum."""

    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    """Deliberately not built, because its semantics are not unambiguous in
    this data model. Stated so the gap is visible rather than guessed at."""


@dataclass(frozen=True, slots=True)
class Coverage:
    """How much of a population supported a metric."""

    covered: int
    total: int

    @property
    def complete(self) -> bool:
        return self.covered == self.total

    @property
    def empty(self) -> bool:
        return self.total == 0


@dataclass(frozen=True, slots=True)
class Metric:
    """One answer, or an honest account of why there isn't one."""

    status: MetricStatus
    value: Decimal | None = None
    basis: PnlBasis | None = None
    sample_size: int = 0
    coverage: Coverage | None = None
    reason: str | None = None
    numerator: int | None = None
    denominator: int | None = None

    def __post_init__(self) -> None:
        if self.status is MetricStatus.AVAILABLE and self.value is None:
            raise ValueError("an AVAILABLE metric must carry a value")
        if self.status is not MetricStatus.AVAILABLE and self.value is not None:
            raise ValueError("only an AVAILABLE metric may carry a value")
        if self.status is not MetricStatus.AVAILABLE and not self.reason:
            raise ValueError("an unavailable metric must say why")


def available(
    value: Decimal,
    *,
    basis: PnlBasis | None = None,
    sample_size: int = 0,
    coverage: Coverage | None = None,
    numerator: int | None = None,
    denominator: int | None = None,
) -> Metric:
    return Metric(
        status=MetricStatus.AVAILABLE,
        value=value,
        basis=basis,
        sample_size=sample_size,
        coverage=coverage,
        numerator=numerator,
        denominator=denominator,
    )


def unavailable(
    reason: str,
    *,
    basis: PnlBasis | None = None,
    sample_size: int = 0,
    coverage: Coverage | None = None,
    status: MetricStatus = MetricStatus.UNAVAILABLE,
) -> Metric:
    return Metric(
        status=status,
        basis=basis,
        sample_size=sample_size,
        coverage=coverage,
        reason=reason,
    )


@dataclass(frozen=True, slots=True)
class InstrumentIdentity:
    """What makes two positions the same instrument.

    Symbol text alone is not identity - two markets can print the same ticker -
    so the asset class travels with it. Nothing is inferred from the string.
    """

    symbol: str
    asset_class: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.asset_class, self.symbol)


@dataclass(frozen=True, slots=True)
class RealizedFill:
    """One exit that actually happened, with the market time it happened at.

    A fill is realized when it fills. It does not stop being realized because
    the rest of the position is still open, and it does not move to the
    position's eventual closing time - so a fill belongs to the date range
    containing *its own* market time.
    """

    amount: Decimal
    fee: Decimal | None
    """None when fees were not modelled: unknown cost, not zero cost."""

    market_time: datetime

    def within(self, start: datetime | None, end: datetime | None) -> bool:
        if start is not None and self.market_time < start:
            return False
        return not (end is not None and self.market_time > end)


@dataclass(frozen=True, slots=True)
class PositionOutcome:
    """One position as performance sees it: authoritative facts only.

    Every field comes from an append-only ledger fact frozen when it happened.
    Nothing here is read from a mutable projection, and nothing is recomputed
    by this layer - the amounts are the ones the simulation engine recorded.
    """

    position_id: str
    instrument: InstrumentIdentity
    direction: Direction
    timeframe: Timeframe
    quantity: int
    population: Population
    realized_gross: Decimal
    """Sum of the fills that happened. Zero for a position that never entered."""

    fees_total: Decimal | None
    """None means fees were not modelled - unknown cost, not zero cost."""

    realized_net: Decimal | None
    unrealized_gross: Decimal | None
    """Mark-to-market on what is still open. Never mixed into realized totals."""

    fee_mode: str
    decision_time: datetime
    entry_time: datetime | None
    terminal_time: datetime | None
    """Market time of the closing fill. The only time performance groups by."""

    fills: tuple[RealizedFill, ...] = ()
    """Every exit this position has already made, in ledger order. Present for
    open and partially closed positions too - that is the point."""

    source: str = SOURCE_PAPER_SIMULATION

    def __post_init__(self) -> None:
        if self.population.completed and self.terminal_time is None:
            raise ValueError("a completed position must carry its terminal market time")
        if self.fees_total is None and self.realized_net is not None:
            raise ValueError("net cannot be known while fees are unknown")

    @property
    def fees_modelled(self) -> bool:
        return self.fees_total is not None

    def amount(self, basis: PnlBasis) -> Decimal | None:
        """The realized result on one basis, or None when that basis is unknown."""
        if basis is PnlBasis.REALIZED_GROSS:
            return self.realized_gross
        return self.realized_net

    def outcome(self, basis: PnlBasis) -> Outcome | None:
        amount = self.amount(basis)
        if amount is None:
            return None
        if amount > 0:
            return Outcome.WIN
        if amount < 0:
            return Outcome.LOSS
        return Outcome.BREAKEVEN

    @property
    def has_realized_fills(self) -> bool:
        return bool(self.fills)

    @property
    def order_key(self) -> tuple[datetime, str]:
        """Deterministic ordering: terminal market time, then position id.

        Two positions can close on the same bar; the id breaks the tie so a
        cumulative curve and a streak never depend on database row order.
        """
        if self.terminal_time is None:  # pragma: no cover - guarded in __post_init__
            raise ValueError("only a completed position has an order key")
        return (self.terminal_time, self.position_id)
