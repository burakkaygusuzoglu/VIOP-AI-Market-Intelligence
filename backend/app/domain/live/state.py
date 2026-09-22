"""One subscription's market state: connection, freshness, availability.

Three separate questions, answered separately, because merging them is how a
live system comes to report "connected" while showing an hour-old price:

* **connection** - what the provider says about the stream. A property of the
  subscription as a whole.
* **freshness** - whether a *valid* observation has been received recently
  enough, per timeframe. Invalid events and identical duplicates do not count:
  a feed that is up but sending garbage, or repeating itself, is not fresh.
* **integrity** - whether the confirmed candles are provably complete, per
  timeframe (see :mod:`app.domain.live.book`).

A timeframe is **available** for confirmed analysis only when all three agree.

## What this module does not know

It has no exchange calendar, so it never says a market is closed. Without
fresh data it says STALE; without a connection it says DISCONNECTED. Absence
of data is never interpreted as a candle, and a timestamp is never refreshed
to make old data look current.

## Pure

No clock is read here. Every method that needs "now" is handed it, which is
what lets a test advance ten simulated minutes in no real time at all.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum, unique

from app.domain.common.enums import Timeframe
from app.domain.live.book import ApplyOutcome, BookStatus, CandleBook, Integrity
from app.domain.live.events import (
    MarketCurrency,
    ProviderSignal,
    RawCandleEvent,
    SignalKind,
    StreamKey,
    StreamProvenance,
    market_currency_of,
)
from app.domain.live.limits import LiveLimits
from app.domain.live.validation import Rejection, RejectionCode, validate

__all__ = [
    "Availability",
    "ConnectionState",
    "Freshness",
    "FreshnessPolicy",
    "IllegalTransitionError",
    "LiveMarketState",
    "MarketStateSnapshot",
    "RejectionRecord",
    "TerminationReason",
    "TimeframeStatus",
]


@unique
class ConnectionState(StrEnum):
    INITIALIZING = "INITIALIZING"
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    RECOVERING = "RECOVERING"
    """Reconnected, and continuity since the disconnect is not yet proven for
    every timeframe. Connection restored is not coverage restored."""

    TERMINATED = "TERMINATED"


@unique
class TerminationReason(StrEnum):
    END_OF_STREAM = "END_OF_STREAM"
    RECONNECT_LIMIT = "RECONNECT_LIMIT"
    OVERLOADED = "OVERLOADED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    CANCELLED = "CANCELLED"


_TRANSITIONS: Mapping[ConnectionState, frozenset[ConnectionState]] = {
    ConnectionState.INITIALIZING: frozenset(
        {ConnectionState.CONNECTED, ConnectionState.DISCONNECTED, ConnectionState.TERMINATED}
    ),
    ConnectionState.CONNECTED: frozenset(
        {ConnectionState.DISCONNECTED, ConnectionState.TERMINATED}
    ),
    ConnectionState.DISCONNECTED: frozenset(
        {ConnectionState.RECOVERING, ConnectionState.TERMINATED}
    ),
    ConnectionState.RECOVERING: frozenset(
        {ConnectionState.CONNECTED, ConnectionState.DISCONNECTED, ConnectionState.TERMINATED}
    ),
    ConnectionState.TERMINATED: frozenset(),
}


class IllegalTransitionError(RuntimeError):
    """A provider signal that makes no sense from the current state."""


@unique
class Freshness(StrEnum):
    """*Transport* freshness: whether valid observations are still arriving,
    on the receive clock. It says nothing about how old the market data is -
    a simulated historical stream is FRESH while it plays - and it never
    establishes that data is current or exchange-verified. That is
    :class:`~app.domain.live.events.MarketCurrency`, decided by provenance."""

    NO_DATA = "NO_DATA"
    FRESH = "FRESH"
    STALE = "STALE"


@unique
class Availability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    """How long each timeframe may go without a valid observation.

    Explicit per timeframe, because one threshold for every market and
    timeframe is wrong for all of them: a daily stream that is silent for ten
    minutes is fine, a 1-minute stream that is silent for ten minutes is not.
    """

    thresholds: Mapping[Timeframe, timedelta]

    def __post_init__(self) -> None:
        for timeframe, threshold in self.thresholds.items():
            if threshold <= timedelta(0):
                raise ValueError(f"the {timeframe.value} freshness threshold must be positive")

    @classmethod
    def from_intervals(
        cls, timeframes: Sequence[Timeframe], *, multiple: int, grace: timedelta
    ) -> FreshnessPolicy:
        """``interval x multiple + grace`` for each timeframe - a stated rule.

        Not a market fact. A stream that delivers only closed candles emits
        once per interval, so twice the interval plus a grace period is the
        earliest point at which silence means something is wrong.
        """
        if multiple < 1:
            raise ValueError("multiple must be at least 1")
        return cls({tf: timedelta(minutes=tf.minutes) * multiple + grace for tf in timeframes})

    def threshold(self, timeframe: Timeframe) -> timedelta:
        try:
            return self.thresholds[timeframe]
        except KeyError:
            raise ValueError(f"no freshness threshold for {timeframe.value}") from None


@dataclass(frozen=True, slots=True)
class RejectionRecord:
    timeframe: Timeframe | None
    code: RejectionCode
    detail: str
    received_at: datetime


@dataclass(frozen=True, slots=True)
class TimeframeStatus:
    book: BookStatus
    freshness: Freshness
    availability: Availability
    reasons: tuple[str, ...]
    """Every reason it is unavailable, not only the first. Empty when available."""


@dataclass(frozen=True, slots=True)
class MarketStateSnapshot:
    symbol: str
    provenance: StreamProvenance
    market_currency: MarketCurrency
    """Derived from provenance and checked against it: a snapshot cannot be
    built that calls simulated history a current market."""

    connection: ConnectionState
    termination_reason: TerminationReason | None
    as_of: datetime
    """The receive-side "now" the snapshot was taken at. Never a market time."""

    timeframes: tuple[TimeframeStatus, ...]
    reconnects: int
    rejection_counts: Mapping[str, int]
    recent_rejections: tuple[RejectionRecord, ...]

    def __post_init__(self) -> None:
        if self.market_currency is not market_currency_of(self.provenance):
            raise ValueError("market currency must follow from provenance")

    def status(self, timeframe: Timeframe) -> TimeframeStatus:
        for item in self.timeframes:
            if item.book.timeframe is timeframe:
                return item
        raise KeyError(timeframe)

    @property
    def available(self) -> tuple[Timeframe, ...]:
        return tuple(
            item.book.timeframe
            for item in self.timeframes
            if item.availability is Availability.AVAILABLE
        )


@dataclass
class LiveMarketState:
    """Everything known about one subscription. In memory; see the report."""

    symbol: str
    timeframes: tuple[Timeframe, ...]
    provenance: StreamProvenance
    freshness: FreshnessPolicy
    limits: LiveLimits = field(default_factory=LiveLimits)
    connection: ConnectionState = ConnectionState.INITIALIZING
    termination_reason: TerminationReason | None = None
    reconnects: int = 0
    _books: dict[Timeframe, CandleBook] = field(default_factory=dict)
    _rejections: deque[RejectionRecord] = field(default_factory=deque)
    _rejection_counts: Counter[str] = field(default_factory=Counter)

    def __post_init__(self) -> None:
        if not self.timeframes:
            raise ValueError("a subscription needs at least one timeframe")
        if len(set(self.timeframes)) != len(self.timeframes):
            raise ValueError("a timeframe is subscribed twice")
        for timeframe in self.timeframes:
            self.freshness.threshold(timeframe)  # refuses a missing threshold now
            self._books[timeframe] = CandleBook(timeframe=timeframe, limits=self.limits)

    # -- reading -----------------------------------------------------------

    def book(self, timeframe: Timeframe) -> CandleBook:
        return self._books[timeframe]

    def snapshot(self, now: datetime) -> MarketStateSnapshot:
        return MarketStateSnapshot(
            symbol=self.symbol,
            provenance=self.provenance,
            market_currency=market_currency_of(self.provenance),
            connection=self.connection,
            termination_reason=self.termination_reason,
            as_of=now,
            timeframes=tuple(self._status(timeframe, now) for timeframe in self.timeframes),
            reconnects=self.reconnects,
            rejection_counts=dict(self._rejection_counts),
            recent_rejections=tuple(self._rejections),
        )

    def _status(self, timeframe: Timeframe, now: datetime) -> TimeframeStatus:
        book = self._books[timeframe]
        status = book.status()
        freshness = self._freshness(book, now)
        reasons: list[str] = []
        if self.connection is ConnectionState.INITIALIZING:
            reasons.append("the stream has not connected yet")
        elif self.connection is ConnectionState.DISCONNECTED:
            reasons.append("the provider is disconnected")
        elif self.connection is ConnectionState.TERMINATED:
            reason = self.termination_reason.value if self.termination_reason else "unknown"
            reasons.append(f"the stream has ended ({reason})")
        if freshness is Freshness.NO_DATA:
            reasons.append("no valid observation has been received")
        elif freshness is Freshness.STALE:
            reasons.append(
                f"no valid observation for longer than {self.freshness.threshold(timeframe)}"
            )
        # Every open issue is listed, not only the one integrity ranks first.
        if status.conflicts:
            reasons.append("a conflicting correction is quarantined")
        if status.missing_sequences or status.missing_overflowed:
            reasons.append("closed candles are known to be missing")
        if status.temporal_gaps:
            reasons.append(
                "an unexplained jump in time: a session break, an interval without "
                "trades or lost candles, which cannot be told apart without a "
                "verified exchange calendar"
            )
        if status.sequence_mismatches:
            reasons.append("provider sequence numbers disagree with the candle times")
        if status.awaiting_continuity:
            reasons.append("continuity since the last disconnect is not yet proven")
        if status.forming_ahead:
            reasons.append("a forming candle shows closed intervals that were not received")
        if status.integrity is not Integrity.COMPLETE and not reasons:
            # Fail closed: an incomplete book is never available, even if an
            # issue were ever added to the book without a reason here.
            reasons.append(f"integrity is {status.integrity.value}")
        if status.closed_count == 0:
            reasons.append("no closed candle has been confirmed")
        return TimeframeStatus(
            book=status,
            freshness=freshness,
            availability=Availability.UNAVAILABLE if reasons else Availability.AVAILABLE,
            reasons=tuple(reasons),
        )

    def _freshness(self, book: CandleBook, now: datetime) -> Freshness:
        received = book.last_received_at
        if received is None:
            return Freshness.NO_DATA
        if now - received > self.freshness.threshold(book.timeframe):
            return Freshness.STALE
        return Freshness.FRESH

    # -- applying provider output ------------------------------------------

    def apply_signal(self, signal: ProviderSignal) -> None:
        kind = signal.kind
        if self.connection is ConnectionState.TERMINATED:
            return  # nothing a provider says can revive an ended stream
        if kind is SignalKind.CONNECTED:
            if self.connection in (ConnectionState.CONNECTED, ConnectionState.RECOVERING):
                # A repeated notice is not news - and during recovery it must
                # not be allowed to end recovery. Only proven continuity does.
                return
            if self.connection is ConnectionState.DISCONNECTED:
                self.reconnects += 1
                if self.reconnects > self.limits.max_reconnects:
                    self.terminate(TerminationReason.RECONNECT_LIMIT)
                    return
                self._move(ConnectionState.RECOVERING)
            else:
                self._move(ConnectionState.CONNECTED)
        elif kind is SignalKind.DISCONNECTED:
            if self.connection is ConnectionState.DISCONNECTED:
                return
            self._move(ConnectionState.DISCONNECTED)
            for book in self._books.values():
                book.mark_disconnected()
        elif kind is SignalKind.END_OF_STREAM:
            self.terminate(TerminationReason.END_OF_STREAM)
        elif kind is SignalKind.OVERFLOW:
            self.terminate(TerminationReason.OVERLOADED)

    def apply_event(
        self, raw: RawCandleEvent, *, received_at: datetime
    ) -> ApplyOutcome | Rejection:
        timeframe = _timeframe_of(raw, self.timeframes)
        if self.connection not in (ConnectionState.CONNECTED, ConnectionState.RECOVERING):
            return self._reject(
                timeframe,
                Rejection(
                    RejectionCode.NOT_CONNECTED,
                    f"a candle arrived while the stream is {self.connection.value}",
                ),
                received_at,
            )
        if timeframe is None:
            return self._reject(
                None,
                Rejection(RejectionCode.WRONG_STREAM, "timeframe is not subscribed"),
                received_at,
            )
        checked = validate(
            raw,
            key=StreamKey(self.symbol, timeframe),
            received_at=received_at,
            limits=self.limits,
        )
        if isinstance(checked, Rejection):
            return self._reject(timeframe, checked, received_at)
        outcome = self._books[timeframe].apply(checked)
        if isinstance(outcome, Rejection):
            return self._reject(timeframe, outcome, received_at)
        if self.connection is ConnectionState.RECOVERING and not any(
            book.awaiting_continuity for book in self._books.values()
        ):
            self._move(ConnectionState.CONNECTED)
        return outcome

    def terminate(self, reason: TerminationReason) -> None:
        if self.connection is ConnectionState.TERMINATED:
            return
        self._move(ConnectionState.TERMINATED)
        self.termination_reason = reason

    # -- helpers -----------------------------------------------------------

    def _move(self, target: ConnectionState) -> None:
        if target not in _TRANSITIONS[self.connection]:
            raise IllegalTransitionError(f"{self.connection.value} cannot move to {target.value}")
        self.connection = target

    def _reject(
        self, timeframe: Timeframe | None, rejection: Rejection, received_at: datetime
    ) -> Rejection:
        self._rejection_counts[rejection.code.value] += 1
        self._rejections.append(
            RejectionRecord(
                timeframe=timeframe,
                code=rejection.code,
                detail=rejection.detail,
                received_at=received_at,
            )
        )
        while len(self._rejections) > self.limits.max_recorded_issues:
            self._rejections.popleft()
        return rejection


def _timeframe_of(raw: RawCandleEvent, subscribed: tuple[Timeframe, ...]) -> Timeframe | None:
    for timeframe in subscribed:
        if raw.timeframe == timeframe.value:
            return timeframe
    return None
