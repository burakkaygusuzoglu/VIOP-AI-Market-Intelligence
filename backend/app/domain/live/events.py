"""What a market-data stream may say, before anything believes it (Phase 13).

A provider speaks in *raw* events. Nothing here is trusted: every field of a
:class:`RawCandleEvent` is typed ``object`` on purpose, because a real feed can
send a string where a price should be, a naive timestamp, a float, or nothing.
Validation turns a raw event into an :class:`Observation` or refuses it; there
is no third path in which a malformed event becomes a candle.

## Three clocks, never collapsed

* **event time** - when the market says this happened. For a closed candle it
  is the moment its interval ended. Market ordering uses this and only this.
* **received time** - when this system took the event in, from an injected
  clock. Freshness compares this with "now"; a simulated *historical* stream
  has event times years in the past and is still fresh if it is still
  arriving.
* **audit time** - when a server wrote something down. It belongs to the
  application layer and never reaches market logic.

## Provenance is the provider's, not the event's

There is no provenance field on a raw event, so an event cannot promote itself
to exchange data. Provenance is a property of the provider object that produced
the stream, and the only value this build has is the truthful one for the
mock: simulated historical data played through a streaming interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique

from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle

__all__ = [
    "CandleState",
    "MarketCurrency",
    "Observation",
    "ProviderSignal",
    "RawCandleEvent",
    "SignalKind",
    "StreamItem",
    "StreamKey",
    "StreamProvenance",
    "market_currency_of",
]


@unique
class StreamProvenance(StrEnum):
    """Where a stream's market data came from.

    One member, deliberately. A licensed provider adds its own value in Phase
    15, once there is one; until then any other label would be a claim this
    build cannot back. In particular there is no ``EXCHANGE_VERIFIED`` and no
    ``LIVE_EXCHANGE_FEED``.
    """

    SIMULATED_HISTORICAL_STREAM = "SIMULATED_HISTORICAL_STREAM"
    """Historical candles replayed through a streaming interface. Not a quote
    arriving from any exchange now, whatever the receive times say."""


@unique
class MarketCurrency(StrEnum):
    """Whether observations describe the market *now* - a separate question
    from whether they are still arriving.

    Freshness (see :mod:`app.domain.live.state`) is transport liveness,
    measured on the receive clock. A historical candle received a second ago
    is fresh and is still history. Currency is decided by provenance alone -
    never by receive times, never by the connection state, and never by
    comparing a candle's market time with a wall clock.
    """

    HISTORICAL = "HISTORICAL"
    """Past market data. However recently it arrived, not a current quote."""


_CURRENCY: dict[StreamProvenance, MarketCurrency] = {
    StreamProvenance.SIMULATED_HISTORICAL_STREAM: MarketCurrency.HISTORICAL,
}


def market_currency_of(provenance: StreamProvenance) -> MarketCurrency:
    """The only way to obtain a currency. A provenance added without a
    stated currency fails here rather than defaulting to anything."""
    return _CURRENCY[provenance]


@unique
class CandleState(StrEnum):
    """A candle is either still forming or it is finished.

    Invalid events are not a third state: they are refused and never stored.
    A forming candle is not confirmed market evidence - its high, low and
    close can still move - so it never enters an analysis prefix.
    """

    FORMING = "FORMING"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class StreamKey:
    """One market-data stream: an instrument label and a timeframe.

    The symbol is a *data-stream* identity, not a verified tradeable product.
    Nothing is inferred from it: not an asset class, a venue, a multiplier, a
    tick size or an expiry.
    """

    symbol: str
    timeframe: Timeframe


@dataclass(frozen=True, slots=True)
class RawCandleEvent:
    """A candle as a provider reported it. Untrusted in every field."""

    symbol: object
    timeframe: object
    open_time: object
    open: object
    high: object
    low: object
    close: object
    volume: object
    closed: object
    event_time: object
    sequence: object = None
    """The provider's number for this *closed* candle within its stream, where
    it supports one. The contract is one consecutive number per closed
    interval of the stream's grid, and it is checked against time on every
    candle: numbers are evidence of lost candles only where they agree with
    the candle times. Contiguous numbers never prove a session break."""


@unique
class SignalKind(StrEnum):
    """What a provider may say about the stream itself."""

    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    END_OF_STREAM = "END_OF_STREAM"
    OVERFLOW = "OVERFLOW"
    """A push source outran its bounded buffer. Events were lost, so the
    stream is declared broken rather than continued with a hole in it."""


@dataclass(frozen=True, slots=True)
class ProviderSignal:
    kind: SignalKind


StreamItem = RawCandleEvent | ProviderSignal


@dataclass(frozen=True, slots=True)
class Observation:
    """A validated candle observation, with both of its clocks."""

    candle: Candle
    event_time: datetime
    received_at: datetime
    sequence: int | None

    @property
    def state(self) -> CandleState:
        return CandleState.CLOSED if self.candle.is_closed else CandleState.FORMING

    def same_values(self, other: Observation) -> bool:
        """Whether two observations of one interval say the same thing.

        Receive time is excluded: the same candle heard twice is still one
        candle.
        """
        a, b = self.candle, other.candle
        return (
            a.open == b.open
            and a.high == b.high
            and a.low == b.low
            and a.close == b.close
            and a.volume == b.volume
            and a.is_closed == b.is_closed
            and self.sequence == other.sequence
        )
