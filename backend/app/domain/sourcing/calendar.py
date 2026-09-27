"""What is known about trading sessions, and what is not (Phase 15 Part 1).

Phase 13 refused to invent an exchange calendar, and this keeps that refusal.
Trading hours, evening-session eligibility, holidays and special sessions are
section 118 facts: they change by exchange announcement, they differ between
products, and they are not the same for every VIOP contract. None is written
here, and none is derived from observed candles - a quiet hour in the data is
not evidence of a session break.

A calendar answer is therefore one of two things: a verified statement, with
its source and date, about whether a moment falls in a session; or
``UNAVAILABLE``, which every caller must treat as "not known". There is no
third answer that assumes a 24/7 market, a weekday market or a default window.

## Verified calendar days (Part 2A)

A verified calendar is a list of :class:`CalendarDay` records, one per venue,
session category and **date**, each stating the timezone and UTC offset in
force, the kind of day (regular, special session, holiday, early close), the
trading intervals, the document it came from and the date that document took
effect. :func:`answer_from` answers only from such a record:

* no record for the date → ``UNAVAILABLE``. There is no weekday rule: a
  calendar that does not list Tuesday says nothing about Tuesday;
* a record that is not a sourced, dated, verified fact → ``UNAVAILABLE``;
* a record whose document took effect after the date → ``UNAVAILABLE``;
* two records for one date that disagree → ``UNAVAILABLE``;
* otherwise ``IN_SESSION`` inside an interval and ``OUT_OF_SESSION`` outside.

An out-of-session answer between two intervals exists only because a record
lists both intervals. The Phase 13 candle book is untouched by any of this: a
missing candle is still a gap, never explained away by a calendar, and the
live domain does not read a calendar at all.

The UTC offset is part of the record rather than looked up, because a venue's
offset is itself a mutable fact and because the build must not depend on a
host's timezone database.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum, unique

from app.domain.common.verification import VerifiedValue

__all__ = [
    "CalendarAnswer",
    "CalendarDay",
    "DayKind",
    "SessionInterval",
    "SessionStatus",
    "answer_from",
    "unavailable",
]


@unique
class SessionStatus(StrEnum):
    IN_SESSION = "IN_SESSION"
    OUT_OF_SESSION = "OUT_OF_SESSION"
    UNAVAILABLE = "UNAVAILABLE"
    """Nothing authoritative says. Not "open", not "closed"."""


@dataclass(frozen=True, slots=True)
class CalendarAnswer:
    """Whether ``at`` falls in a session for ``symbol``, and on whose word."""

    symbol: str
    at: datetime
    status: SessionStatus
    basis: VerifiedValue[str] | None = None
    """The calendar source for an in/out answer. Required for those, and it
    must be a sourced, dated, verified current fact."""

    reason: str = ""

    def __post_init__(self) -> None:
        if self.status is SessionStatus.UNAVAILABLE:
            if self.basis is not None:
                raise ValueError("an unavailable answer cites no calendar")
            return
        basis = self.basis
        if basis is None or not basis.is_authoritative or basis.as_of is None:
            raise ValueError(
                "a session answer needs a sourced, dated, verified calendar - otherwise it "
                "is UNAVAILABLE"
            )


def unavailable(symbol: str, at: datetime, reason: str) -> CalendarAnswer:
    return CalendarAnswer(symbol=symbol, at=at, status=SessionStatus.UNAVAILABLE, reason=reason)


@unique
class DayKind(StrEnum):
    REGULAR = "REGULAR"
    SPECIAL_SESSION = "SPECIAL_SESSION"
    HOLIDAY = "HOLIDAY"
    """No trading. Carries no intervals."""
    EARLY_CLOSE = "EARLY_CLOSE"


@dataclass(frozen=True, slots=True)
class SessionInterval:
    """``[start, end)`` in the venue's local time, within one date."""

    start: time
    end: time

    def __post_init__(self) -> None:
        if self.start.tzinfo is not None or self.end.tzinfo is not None:
            raise ValueError("interval times are local to the day's stated offset")
        if self.end <= self.start:
            raise ValueError(
                "an interval ends after it starts; one crossing midnight is two records"
            )


@dataclass(frozen=True, slots=True)
class CalendarDay:
    """What one document says about one venue's session category on one date."""

    venue: str
    session_category: str
    """Which products the day applies to - sessions differ between them."""

    day: date
    timezone_name: str
    utc_offset: timedelta
    kind: DayKind
    intervals: tuple[SessionInterval, ...]
    source: VerifiedValue[str]
    """The document reference, as a verified fact with its verification date."""

    effective_from: date
    """When the document took effect. A record is not used before it."""

    def __post_init__(self) -> None:
        if not (self.venue.strip() and self.session_category.strip()):
            raise ValueError("a calendar day names its venue and session category")
        if not self.timezone_name.strip():
            raise ValueError("a calendar day names its timezone")
        if abs(self.utc_offset) >= timedelta(hours=24):
            raise ValueError("a UTC offset is less than a day")
        if self.kind is DayKind.HOLIDAY and self.intervals:
            raise ValueError("a holiday has no trading intervals")
        if self.kind is not DayKind.HOLIDAY and not self.intervals:
            raise ValueError("a trading day lists its intervals")
        ordered = sorted(self.intervals, key=lambda i: i.start)
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            if later.start < earlier.end:
                raise ValueError("a day's intervals do not overlap")

    @property
    def usable(self) -> bool:
        s = self.source
        return (
            s.is_authoritative
            and bool(s.source.strip())
            and s.as_of is not None
            and self.effective_from <= self.day
        )

    def local(self, at: datetime) -> datetime:
        return at.astimezone(timezone(self.utc_offset))

    def in_session(self, at: datetime) -> bool:
        moment = self.local(at).time()
        return any(i.start <= moment < i.end for i in self.intervals)

    def _shape(self) -> tuple[object, ...]:
        return (self.utc_offset, self.kind, tuple(sorted(self.intervals, key=lambda i: i.start)))


def answer_from(
    days: Iterable[CalendarDay],
    *,
    symbol: str,
    venue: str,
    session_category: str,
    at: datetime,
) -> CalendarAnswer:
    """Answer only from a verified record for this venue, category and date."""
    if at.utcoffset() is None:
        raise ValueError("a calendar question is asked at an aware moment")
    matching = [
        d
        for d in days
        if d.venue == venue
        and d.session_category == session_category
        and d.local(at).date() == d.day
    ]
    if not matching:
        return unavailable(symbol, at, "no calendar record covers this date")
    usable = [d for d in matching if d.usable]
    if not usable:
        return unavailable(
            symbol, at, "the calendar record for this date is not a verified, effective source"
        )
    if len({d._shape() for d in usable}) > 1:
        return unavailable(symbol, at, "calendar records for this date disagree")
    day = usable[0]
    inside = day.in_session(at)
    return CalendarAnswer(
        symbol=symbol,
        at=at,
        status=SessionStatus.IN_SESSION if inside else SessionStatus.OUT_OF_SESSION,
        basis=day.source,
        reason=f"{day.kind.value} day per the verified calendar"
        + ("" if inside else "; outside every listed interval"),
    )
