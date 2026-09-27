"""The vendor-neutral candle a provider adapter produces (Phase 15 Part 1).

A licensed provider speaks its own protocol. Its adapter's one job is to turn
each message into a :class:`VendorCandle` and hand that here, where it becomes
a Phase 13 :class:`RawCandleEvent` - untrusted, and validated by the same
``validate`` and ``CandleBook`` every other stream goes through. Nothing about
being external lets a candle skip validation, and nothing here decides that a
candle is confirmed, current or correct.

## Four times, kept apart

* ``event_time`` - the market's time for the candle (for a closed candle, the
  end of its interval). The only time market logic orders by.
* ``provider_time`` - when the provider says it published the message. It
  travels as the raw event's optional ``published_at``, is validated, and is
  kept on the stream record for audit and delay measurement; it is never used
  as market time and never as receive time.
* receive time - when this system took the event in, stamped by the session's
  injected clock. The provider cannot set it.
* audit time - when a server wrote something down.

## Corrections are not an adapter's to apply

A provider that re-sends a candle for an interval already held - a correction
- produces an ordinary event for that interval. The book decides: an identical
one is a duplicate, a different one is a quarantined conflict. The adapter
never overwrites a stored candle and never flags a correction as authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.domain.live.events import RawCandleEvent

__all__ = ["VendorCandle", "to_raw_event"]


@dataclass(frozen=True, slots=True)
class VendorCandle:
    """A provider message reduced to what a candle is. Fields stay untyped
    where the provider controls them, exactly like ``RawCandleEvent``."""

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
    provider_time: datetime | None = None


def _exact(value: object) -> object:
    """An exact decimal string becomes a ``Decimal``; anything else is passed
    on untouched for validation to judge. A float is *not* converted: it has
    already lost the price's exact value, and validation refuses it."""
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            return value
    return value


def to_raw_event(candle: VendorCandle) -> RawCandleEvent:
    """The Phase 13 raw event for this candle. ``provider_time`` becomes the
    optional ``published_at``, a field no ordering, freshness or confirmation
    rule reads - so it is preserved for audit without becoming market time."""
    return RawCandleEvent(
        symbol=candle.symbol,
        timeframe=candle.timeframe,
        open_time=candle.open_time,
        open=_exact(candle.open),
        high=_exact(candle.high),
        low=_exact(candle.low),
        close=_exact(candle.close),
        volume=_exact(candle.volume),
        closed=candle.closed,
        event_time=candle.event_time,
        sequence=candle.sequence,
        published_at=candle.provider_time,
    )
