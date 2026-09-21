"""What a strategy is allowed to be, and what it is allowed to see.

A backtest automates *decisions*, not execution. That distinction is the whole
design of this module:

* a policy receives a :class:`StrategyContext` and returns a
  :class:`StrategyDecision`. It cannot open a position, size one, move money or
  touch a ledger - it can only state an intent;
* the context hands over **scalars at the current boundary**, never the
  indicator series. A policy that cannot reach index ``i + 1`` cannot read the
  future by mistake, so no-lookahead is a property of the type rather than a
  rule someone has to keep remembering;
* ``ENTRY_INTENT`` is not approval. The risk engine runs afterwards, server
  side, and may refuse. ``NO_SIGNAL`` and ``WAIT`` are terminal answers and
  never become trades.

Pure: stdlib and ``app.domain.common`` / ``app.domain.market`` only. No paper
types, no product policy, no storage, no clock, no HTTP, no model provider.
The runner translates an intent into the existing Phase 9 vocabulary; the
strategy never learns that vocabulary exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique
from typing import Protocol

from app.domain.common.enums import Direction, Timeframe
from app.domain.market.candle import Candle


class StrategyInputError(ValueError):
    """A strategy was handed something it cannot act on, with a stable reason."""


@unique
class DecisionKind(StrEnum):
    """The complete vocabulary. Four answers, and none of them is a trade."""

    NO_SIGNAL = "NO_SIGNAL"
    """The rule did not fire. Not a view on the market, just an absence."""

    WAIT = "WAIT"
    """The rule cannot be evaluated yet - warm-up, a missing timeframe, an
    indicator still ``None``. Distinct from NO_SIGNAL on purpose: "not enough
    information" and "no setup" are different facts, and reporting the first as
    the second would hide an unusable dataset behind a quiet run."""

    ENTRY_INTENT = "ENTRY_INTENT"
    """The rule fired. Risk approval has *not* happened."""

    EXIT_INTENT = "EXIT_INTENT"
    """The rule asks for the open position to leave. Still not execution: the
    runner asks the paper engine, which exits on a later eligible bar."""


@dataclass(frozen=True, slots=True)
class TargetLevel:
    """One take-profit level and the whole units it would close.

    Deliberately not Phase 9's ``TargetSpec``: this layer states an intention
    in plain numbers, and the runner is the only place that knows how a paper
    position is shaped.
    """

    price: Decimal
    quantity: int


@dataclass(frozen=True, slots=True)
class EntryIntent:
    """What the strategy would like to do, if risk permits and levels are valid."""

    direction: Direction
    intended_entry: Decimal
    stop: Decimal
    targets: tuple[TargetLevel, ...]
    quantity: int
    """The units the *rule* asks for. The risk engine decides what is allowed,
    and may allow fewer or none; this is a request, never an allocation."""

    def __post_init__(self) -> None:
        if self.direction not in (Direction.LONG, Direction.SHORT):
            raise StrategyInputError(f"{self.direction} is not a tradeable direction")
        if self.quantity < 1:
            raise StrategyInputError("an entry intent asks for at least one unit")
        if not self.targets:
            raise StrategyInputError("an entry intent needs at least one target")
        for name, price in (("entry", self.intended_entry), ("stop", self.stop)):
            if not price.is_finite() or price <= 0:
                raise StrategyInputError(f"{name} must be a positive finite price")


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    """One answer, with the reason it was reached.

    The reason is recorded in the run's decision trace, so a person can ask why
    a boundary produced nothing without re-running anything.
    """

    kind: DecisionKind
    reason: str
    entry: EntryIntent | None = None

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise StrategyInputError("every decision states its reason")
        if (self.kind is DecisionKind.ENTRY_INTENT) != (self.entry is not None):
            raise StrategyInputError("an entry intent carries an entry, and nothing else does")

    @classmethod
    def no_signal(cls, reason: str) -> StrategyDecision:
        return cls(DecisionKind.NO_SIGNAL, reason)

    @classmethod
    def wait(cls, reason: str) -> StrategyDecision:
        return cls(DecisionKind.WAIT, reason)

    @classmethod
    def exit_now(cls, reason: str) -> StrategyDecision:
        return cls(DecisionKind.EXIT_INTENT, reason)

    @classmethod
    def enter(cls, intent: EntryIntent, reason: str) -> StrategyDecision:
        return cls(DecisionKind.ENTRY_INTENT, reason, intent)


@dataclass(frozen=True, slots=True)
class Readings:
    """Indicator values at one boundary. Scalars, never series.

    ``None`` is warm-up - the indicator cannot be computed yet - and a strategy
    must treat it as "cannot decide", never as zero.
    """

    ema_fast: float | None = None
    ema_slow: float | None = None
    rsi: float | None = None
    atr: float | None = None
    adx: float | None = None

    @property
    def complete(self) -> bool:
        """Whether every reading this vocabulary carries has a value."""
        return all(
            value is not None
            for value in (self.ema_fast, self.ema_slow, self.rsi, self.atr, self.adx)
        )


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Everything a policy may see at one market-information boundary.

    Confirmed facts only. ``bar`` is the driver candle that *finished* at
    ``as_of``; ``previous`` is the boundary before it. Higher timeframes appear
    only once their own candle has closed, so a forming 1H bar is simply absent
    rather than present with partial values.
    """

    as_of: datetime
    symbol: str
    driver: Timeframe
    bar: Candle
    """The driver candle whose coverage ended at ``as_of``."""

    current: Readings
    previous: Readings
    higher: dict[Timeframe, Readings]
    """Confirmed readings for each higher timeframe that has closed a candle."""

    bars_available: int
    """How many driver candles are confirmed at ``as_of``. Warm-up is a count a
    strategy can check, not something it has to infer from ``None`` values."""

    has_open_position: bool
    """Whether this run already holds an open simulated position. Exposure is
    the runner's rule; the strategy is told so it need not ask twice."""

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise StrategyInputError("as_of must be timezone-aware")
        if not self.bar.is_closed:
            raise StrategyInputError("a strategy only ever sees closed candles")


class StrategyPolicy(Protocol):
    """A deterministic rule. Same inputs, same decision, every time.

    Implementations must not read a clock, use randomness, perform input or
    output, call a model, or hold mutable state between boundaries.
    """

    @property
    def identifier(self) -> str:
        """Stable name. Part of the run's semantic fingerprint."""

    @property
    def version(self) -> str:
        """Rules version. A change that alters decisions changes this."""

    @property
    def warm_up_bars(self) -> int:
        """Driver candles required before the rule can be evaluated at all."""

    def parameters(self) -> dict[str, str]:
        """The explicit parameters, as canonical text for the fingerprint."""

    def decide(self, context: StrategyContext) -> StrategyDecision: ...
