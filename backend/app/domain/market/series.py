"""Ordered collections of candles, and the type that proves validation ran.

Two types, deliberately distinct:

``CandleSeries``
    Whatever a provider actually returned. It may be empty, unordered,
    duplicated, corrupt or contain forming bars. It exists so the Data Quality
    Engine has something to inspect and report on.

``ValidatedCandleSeries``
    A series whose structural invariants are enforced at construction and
    which the Data Quality Engine did not block. An indicator that accepts
    this type therefore cannot be handed raw provider output, which turns
    "the provider must not bypass validation" from a convention into a type
    error (master spec section 40).

Numeric representation is documented in ``docs/technical_conventions.md``.
In short: prices, volumes and open interest are ``Decimal`` here because they
are exact quantities as received. Indicator mathematics is float, and this
module holds the single conversion point.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle

if TYPE_CHECKING:  # pragma: no cover - typing only
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class CandleSeries:
    """An ordered collection of candles as received from a provider.

    No integrity invariant is enforced here on purpose. Rejecting bad data at
    construction time would leave the Data Quality Engine unable to explain
    *why* a dataset is unusable, and the master specification requires that
    explanation (sections 40 and 41).
    """

    candles: tuple[Candle, ...]

    def __len__(self) -> int:
        return len(self.candles)

    def __iter__(self) -> Iterator[Candle]:
        return iter(self.candles)

    def __getitem__(self, index: int) -> Candle:
        return self.candles[index]

    @property
    def is_empty(self) -> bool:
        return not self.candles

    @classmethod
    def of(cls, candles: Sequence[Candle]) -> CandleSeries:
        """Build a series from any sequence, preserving the given order."""
        return cls(candles=tuple(candles))


class InvalidCandleSeriesError(ValueError):
    """Raised when a ``ValidatedCandleSeries`` invariant does not hold."""


@dataclass(frozen=True, slots=True)
class ValidatedCandleSeries:
    """A candle series that satisfies the invariants calculation depends on.

    Every candle is closed, strictly ascending by ``open_time``, timezone
    aware, and shares the series' symbol and timeframe. The invariants are
    enforced in ``__post_init__``, so the type itself is the guarantee - not a
    naming convention and not a promise made in a docstring. An indicator that
    accepts this type therefore cannot be handed raw provider output.

    The Data Quality Engine is the normal way to obtain one: it applies the
    full rule set of master spec section 40, which is broader than these
    structural invariants, and reports *why* a dataset was accepted, warned or
    blocked. Constructing one directly is possible but proves only that the
    structural invariants hold.
    """

    candles: tuple[Candle, ...]
    symbol: str
    timeframe: Timeframe

    def __post_init__(self) -> None:
        previous: datetime | None = None
        for index, candle in enumerate(self.candles):
            if not candle.is_closed:
                raise InvalidCandleSeriesError(
                    f"candle {index} is still forming; a forming bar is never a historical fact"
                )
            if candle.symbol != self.symbol:
                raise InvalidCandleSeriesError(
                    f"candle {index} has symbol {candle.symbol!r}, series is {self.symbol!r}"
                )
            if candle.timeframe is not self.timeframe:
                raise InvalidCandleSeriesError(
                    f"candle {index} has timeframe {candle.timeframe}, series is {self.timeframe}"
                )
            if candle.open_time.tzinfo is None or candle.open_time.utcoffset() is None:
                raise InvalidCandleSeriesError(f"candle {index} has a naive open_time")
            if previous is not None and candle.open_time <= previous:
                raise InvalidCandleSeriesError(
                    f"candle {index} is not strictly after its predecessor"
                )
            previous = candle.open_time

    def __len__(self) -> int:
        return len(self.candles)

    def __iter__(self) -> Iterator[Candle]:
        return iter(self.candles)

    def __getitem__(self, index: int) -> Candle:
        return self.candles[index]

    @property
    def interval(self) -> timedelta:
        """Nominal duration of one candle in this series."""
        return timedelta(minutes=self.timeframe.minutes)

    @property
    def open_times(self) -> tuple[datetime, ...]:
        return tuple(candle.open_time for candle in self.candles)

    @property
    def opens(self) -> tuple[Decimal, ...]:
        return tuple(candle.open for candle in self.candles)

    @property
    def highs(self) -> tuple[Decimal, ...]:
        return tuple(candle.high for candle in self.candles)

    @property
    def lows(self) -> tuple[Decimal, ...]:
        return tuple(candle.low for candle in self.candles)

    @property
    def closes(self) -> tuple[Decimal, ...]:
        return tuple(candle.close for candle in self.candles)

    @property
    def volumes(self) -> tuple[Decimal, ...]:
        return tuple(candle.volume for candle in self.candles)

    # ------------------------------------------------------------------
    # The Decimal -> float boundary.
    #
    # Indicator mathematics is float (see docs/technical_conventions.md).
    # These four accessors are the only sanctioned crossing, so the boundary
    # is greppable rather than scattered through the engines. Nothing converts
    # back: an indicator result is an analytic quantity, not money, and is
    # never silently re-promoted to Decimal.
    # ------------------------------------------------------------------

    def float_opens(self) -> tuple[float, ...]:
        return tuple(float(candle.open) for candle in self.candles)

    def float_highs(self) -> tuple[float, ...]:
        return tuple(float(candle.high) for candle in self.candles)

    def float_lows(self) -> tuple[float, ...]:
        return tuple(float(candle.low) for candle in self.candles)

    def float_closes(self) -> tuple[float, ...]:
        return tuple(float(candle.close) for candle in self.candles)

    def float_volumes(self) -> tuple[float, ...]:
        return tuple(float(candle.volume) for candle in self.candles)
