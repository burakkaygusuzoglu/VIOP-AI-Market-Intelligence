"""What happened on a stream, one item at a time (Phase 13 Part 2A).

A :class:`StreamRecord` is written *after* an item has been applied to the
pure market state, and says what the state did with it: confirmed a candle,
filled a gap, counted a duplicate, quarantined a conflict, refused an event,
or took a provider signal. It is an account of something that happened, never
a reconstruction from the current state - which is what lets a timeline be
honest about order.

## Only validated times are carried

A refused event's timestamps are the provider's claims and failed validation,
so a record of a refusal carries the rejection code and nothing else. A record
of an accepted observation carries the market interval start and the market
event time it was validated with, and the receive time it arrived at. The
three clocks stay three fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique

from app.domain.common.enums import Timeframe

__all__ = ["RecordKind", "StreamRecord"]


@unique
class RecordKind(StrEnum):
    OBSERVATION = "OBSERVATION"
    """A candle event that validation accepted; ``outcome`` is the book's
    :class:`~app.domain.live.book.ApplyOutcome`."""

    REJECTED = "REJECTED"
    """A candle event that was refused; ``outcome`` is the rejection code."""

    SIGNAL = "SIGNAL"
    """A provider signal about the stream itself; ``outcome`` is its kind."""


@dataclass(frozen=True, slots=True)
class StreamRecord:
    kind: RecordKind
    outcome: str
    received_at: datetime
    """Receive clock. When this system took the item in."""

    timeframe: Timeframe | None = None
    open_time: datetime | None = None
    """Market time: the start of the candle's interval. Validated only."""

    event_time: datetime | None = None
    """Market time: when the provider says the event happened. Validated only."""

    closed: bool | None = None
    sequence: int | None = None
    backfill: bool = False
    """Arrived through a backfill request after a reconnect, not the stream."""
