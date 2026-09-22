"""The single gate between what a provider said and what the system believes.

Every check here refuses; none of them repairs. A price is not clamped into
range, a timestamp is not moved onto the grid, a missing value is not filled
in, and a naive datetime is not assumed to be UTC. A refused event is counted
and recorded, and it never becomes a candle.

Rejection details describe the fault, never the payload: a hostile symbol is
reported as "not the subscribed symbol", not echoed back into a log line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.enums import Timeframe
from app.domain.live.events import Observation, RawCandleEvent, StreamKey
from app.domain.live.limits import LiveLimits
from app.domain.market.candle import Candle

__all__ = ["Rejection", "RejectionCode", "validate"]

_SYMBOL = re.compile(r"^[A-Za-z0-9._-]+$")

_ALIGNED_TIMEFRAMES = frozenset(
    {Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1}
)
"""Timeframes whose interval grid does not depend on the venue.

A 5-minute candle opens on a 5-minute boundary whatever the exchange's UTC
offset, as long as that offset is a whole number of hours. A 4-hour or daily
candle opens wherever the venue's session says it does, and this build has no
verified session calendar - so those are accepted as the provider states them
rather than checked against a grid that was guessed."""


@unique
class RejectionCode(StrEnum):
    MALFORMED = "MALFORMED"
    NAIVE_TIMESTAMP = "NAIVE_TIMESTAMP"
    WRONG_STREAM = "WRONG_STREAM"
    INVALID_SYMBOL = "INVALID_SYMBOL"
    MISALIGNED_OPEN_TIME = "MISALIGNED_OPEN_TIME"
    NON_DECIMAL = "NON_DECIMAL"
    NON_FINITE = "NON_FINITE"
    NON_POSITIVE_PRICE = "NON_POSITIVE_PRICE"
    OUT_OF_BOUNDS = "OUT_OF_BOUNDS"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
    INCONSISTENT_OHLC = "INCONSISTENT_OHLC"
    PREMATURE_CLOSE = "PREMATURE_CLOSE"
    FORMING_OUTSIDE_INTERVAL = "FORMING_OUTSIDE_INTERVAL"
    FUTURE_EVENT = "FUTURE_EVENT"
    INVALID_SEQUENCE = "INVALID_SEQUENCE"
    # Raised by the candle book rather than here, listed together so a
    # rejection count reads as one vocabulary.
    ORDER_VIOLATION = "ORDER_VIOLATION"
    LATE_UNSEQUENCED = "LATE_UNSEQUENCED"
    MIXED_SEQUENCING = "MIXED_SEQUENCING"
    STALE_FORMING = "STALE_FORMING"
    NOT_CONNECTED = "NOT_CONNECTED"


@dataclass(frozen=True, slots=True)
class Rejection:
    code: RejectionCode
    detail: str


def validate(
    raw: RawCandleEvent,
    *,
    key: StreamKey,
    received_at: datetime,
    limits: LiveLimits,
) -> Observation | Rejection:
    """A validated observation, or the first reason it is not one."""
    if not _aware(received_at):
        raise ValueError("received_at must be timezone-aware")

    if not isinstance(raw.symbol, str):
        return Rejection(RejectionCode.MALFORMED, "symbol is not text")
    if len(raw.symbol) > limits.max_symbol_length or not _SYMBOL.fullmatch(raw.symbol):
        return Rejection(RejectionCode.INVALID_SYMBOL, "symbol is not a permitted identifier")
    if raw.symbol != key.symbol:
        return Rejection(RejectionCode.WRONG_STREAM, "symbol is not the subscribed symbol")
    if raw.timeframe != key.timeframe.value:
        return Rejection(RejectionCode.WRONG_STREAM, "timeframe is not the subscribed timeframe")

    if not isinstance(raw.closed, bool):
        return Rejection(RejectionCode.MALFORMED, "closed must be a boolean")

    for name, value in (("open_time", raw.open_time), ("event_time", raw.event_time)):
        if not isinstance(value, datetime):
            return Rejection(RejectionCode.MALFORMED, f"{name} is not a timestamp")
        if not _aware(value):
            return Rejection(RejectionCode.NAIVE_TIMESTAMP, f"{name} has no timezone")
    assert isinstance(raw.open_time, datetime)  # noqa: S101 - narrowed above
    assert isinstance(raw.event_time, datetime)  # noqa: S101 - narrowed above
    open_time, event_time = raw.open_time, raw.event_time

    if key.timeframe in _ALIGNED_TIMEFRAMES and not _on_interval_grid(open_time, key.timeframe):
        return Rejection(
            RejectionCode.MISALIGNED_OPEN_TIME,
            f"open_time is not on the {key.timeframe.value} interval grid",
        )

    prices: dict[str, Decimal] = {}
    for name, value in (
        ("open", raw.open),
        ("high", raw.high),
        ("low", raw.low),
        ("close", raw.close),
    ):
        checked = _price(name, value, limits)
        if isinstance(checked, Rejection):
            return checked
        prices[name] = checked

    volume = _volume(raw.volume, limits)
    if isinstance(volume, Rejection):
        return volume

    o, h, lo, c = prices["open"], prices["high"], prices["low"], prices["close"]
    if h < max(o, c, lo) or lo > min(o, c):
        return Rejection(
            RejectionCode.INCONSISTENT_OHLC,
            "high/low do not contain open and close; the candle is refused, not clamped",
        )

    end = open_time + timedelta(minutes=key.timeframe.minutes)
    if raw.closed and event_time < end:
        return Rejection(
            RejectionCode.PREMATURE_CLOSE,
            "a candle was reported closed before its interval ended",
        )
    if not raw.closed and not (open_time <= event_time < end):
        return Rejection(
            RejectionCode.FORMING_OUTSIDE_INTERVAL,
            "a forming candle's event time lies outside its own interval",
        )
    if event_time > received_at + limits.max_clock_skew:
        return Rejection(
            RejectionCode.FUTURE_EVENT,
            "event time is ahead of receive time by more than the permitted clock skew",
        )

    sequence: int | None = None
    if raw.sequence is not None:
        if isinstance(raw.sequence, bool) or not isinstance(raw.sequence, int):
            return Rejection(RejectionCode.INVALID_SEQUENCE, "sequence is not an integer")
        if raw.sequence < 0:
            return Rejection(RejectionCode.INVALID_SEQUENCE, "sequence is negative")
        # Sequences number *closed* candles. On a forming update the field
        # carries no meaning, so it is not kept.
        sequence = raw.sequence if raw.closed else None

    return Observation(
        candle=Candle(
            symbol=key.symbol,
            timeframe=key.timeframe,
            open_time=open_time,
            open=o,
            high=h,
            low=lo,
            close=c,
            volume=volume,
            is_closed=raw.closed,
        ),
        event_time=event_time,
        received_at=received_at,
        sequence=sequence,
    )


# ----------------------------------------------------------------------


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _on_interval_grid(moment: datetime, timeframe: Timeframe) -> bool:
    if moment.second or moment.microsecond:
        return False
    minutes = int(moment.timestamp() // 60)
    return minutes % timeframe.minutes == 0


def _bounded(value: Decimal, limits: LiveLimits) -> bool:
    exponent = value.as_tuple().exponent
    return isinstance(exponent, int) and -exponent <= limits.max_scale


def _price(name: str, value: object, limits: LiveLimits) -> Decimal | Rejection:
    # A float is refused rather than converted: 0.1 is not a price anybody
    # quoted, it is the nearest binary fraction to one.
    if not isinstance(value, Decimal):
        return Rejection(RejectionCode.NON_DECIMAL, f"{name} is not an exact decimal")
    if not value.is_finite():
        return Rejection(RejectionCode.NON_FINITE, f"{name} is not finite")
    if value <= 0:
        return Rejection(RejectionCode.NON_POSITIVE_PRICE, f"{name} is not positive")
    if value > limits.max_price or not _bounded(value, limits):
        return Rejection(RejectionCode.OUT_OF_BOUNDS, f"{name} is outside the accepted range")
    return value


def _volume(value: object, limits: LiveLimits) -> Decimal | Rejection:
    if not isinstance(value, Decimal):
        return Rejection(RejectionCode.NON_DECIMAL, "volume is not an exact decimal")
    if not value.is_finite():
        return Rejection(RejectionCode.NON_FINITE, "volume is not finite")
    if value < 0:
        return Rejection(RejectionCode.NEGATIVE_VOLUME, "volume is negative")
    if value > limits.max_volume or not _bounded(value, limits):
        return Rejection(RejectionCode.OUT_OF_BOUNDS, "volume is outside the accepted range")
    return value
