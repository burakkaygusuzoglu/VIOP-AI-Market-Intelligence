"""Open interest as a sourced, timed measurement (Phase 15 Part 2A).

Master spec section 33 treats open interest as context: rising price with
rising open interest reads differently from rising price with falling open
interest, and neither is a signal by itself. That reading is only honest if
the number is what it claims to be. This module defines what an open-interest
observation must carry and how one is looked up without looking ahead. It
computes nothing from prices or candles.

## Open interest is not volume

Volume counts contracts *traded* in an interval; open interest counts
contracts *outstanding* at a moment. Neither can be derived from the other,
and nothing here accepts a candle, reads a volume or estimates one from the
other. An instrument with no open-interest source has no open interest.

## Contract and aggregate are different quantities

The open interest of one expiry and the open interest summed over every
expiry of an underlying are different numbers. An observation states its
:class:`OpenInterestScope`, and a question for one scope is never answered
from the other.

## Four times

* ``measured_at`` - the market moment the figure describes (for example a
  session's end).
* ``published_at`` - when the source says it published the figure. A
  revision has its own, later publication time.
* ``received_at`` - when this system took it in.
* ``available_at`` - the earliest moment a decision could have used it: the
  later of publication and measurement. A decision at time T sees only
  observations with ``available_at <= T``; a figure published tomorrow for
  today does not exist today, and neither does tomorrow's revision of it.

Publication time is the research-availability clock rather than receive time
because historical research fetches the past after the fact; it is a source's
claim, so a record without it is not available to a historical decision at
all, only to a decision at or after its receipt.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.identity import same_instrument
from app.domain.common.verification import VerifiedValue

__all__ = [
    "OpenInterestAnswer",
    "OpenInterestObservation",
    "OpenInterestScope",
    "OpenInterestStatus",
    "ReportingInterval",
    "open_interest_at",
]


@unique
class OpenInterestScope(StrEnum):
    CONTRACT = "CONTRACT"
    """One listed contract - one expiry."""

    UNDERLYING_AGGREGATE = "UNDERLYING_AGGREGATE"
    """Summed over every listed contract of one underlying."""


@unique
class ReportingInterval(StrEnum):
    END_OF_DAY = "END_OF_DAY"
    INTRADAY_SNAPSHOT = "INTRADAY_SNAPSHOT"


@unique
class OpenInterestStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class OpenInterestObservation:
    instrument: str
    """The contract symbol (``CONTRACT``) or the underlying (aggregate)."""

    scope: OpenInterestScope
    contracts_open: Decimal
    """Measured in contracts outstanding - a whole, non-negative number."""

    measured_at: datetime
    received_at: datetime
    interval: ReportingInterval
    source: VerifiedValue[str]
    """The provider or publication, as a verified fact with its reference."""

    published_at: datetime | None = None
    revision: int = 0
    """0 for the first publication; each correction increments it."""

    def __post_init__(self) -> None:
        if not self.instrument.strip():
            raise ValueError("an observation names its instrument")
        if not isinstance(self.contracts_open, Decimal):
            raise ValueError("open interest is an exact Decimal")
        value = self.contracts_open
        if not value.is_finite() or value < 0 or value != value.to_integral_value():
            raise ValueError("open interest is a whole, non-negative number of contracts")
        for name in ("measured_at", "received_at", "published_at"):
            moment = getattr(self, name)
            if moment is not None and moment.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.published_at is not None and self.published_at < self.measured_at:
            raise ValueError("a figure cannot be published before the moment it measures")
        if self.revision < 0:
            raise ValueError("revision is non-negative")

    @property
    def available_at(self) -> datetime:
        """When a decision could first have used this figure."""
        if self.published_at is not None:
            return max(self.published_at, self.measured_at)
        return max(self.received_at, self.measured_at)

    @property
    def authoritative(self) -> bool:
        s = self.source
        return s.is_authoritative and bool(s.source.strip()) and s.as_of is not None


@dataclass(frozen=True, slots=True)
class OpenInterestAnswer:
    status: OpenInterestStatus
    reason: str
    observation: OpenInterestObservation | None = None

    def __post_init__(self) -> None:
        if (self.status is OpenInterestStatus.UNAVAILABLE) != (self.observation is None):
            raise ValueError("only an unavailable answer lacks an observation")


def _unavailable(reason: str) -> OpenInterestAnswer:
    return OpenInterestAnswer(status=OpenInterestStatus.UNAVAILABLE, reason=reason)


def open_interest_at(
    observations: Iterable[OpenInterestObservation],
    *,
    instrument: str,
    scope: OpenInterestScope,
    decision_time: datetime,
    max_age: timedelta,
) -> OpenInterestAnswer:
    """The latest figure a decision at ``decision_time`` could have known.

    Observations for another instrument or scope, from a non-authoritative
    source, or not yet available at ``decision_time`` are ignored. Of the
    rest, the latest ``measured_at`` wins, and for that moment the highest
    revision *available by then*. A figure measured longer than ``max_age``
    before the decision is returned as ``STALE``, never as current.
    """
    if decision_time.utcoffset() is None:
        raise ValueError("decision_time must be timezone-aware")
    known = [
        o
        for o in observations
        if o.scope is scope
        and same_instrument(o.instrument, instrument)
        and o.authoritative
        and o.available_at <= decision_time
    ]
    if not known:
        return _unavailable(
            f"no verified {scope.value.lower()} open interest for {instrument} was "
            "available at the decision time"
        )
    latest = max(o.measured_at for o in known)
    at_latest = [o for o in known if o.measured_at == latest]
    top = max(o.revision for o in at_latest)
    chosen = [o for o in at_latest if o.revision == top]
    if len({o.contracts_open for o in chosen}) > 1:
        return _unavailable("two figures for the same moment and revision disagree")
    observation = chosen[0]
    if decision_time - observation.measured_at > max_age:
        return OpenInterestAnswer(
            status=OpenInterestStatus.STALE,
            reason="the latest available figure is older than the freshness bound",
            observation=observation,
        )
    return OpenInterestAnswer(
        status=OpenInterestStatus.AVAILABLE,
        reason=f"revision {observation.revision} of the figure measured at the latest moment",
        observation=observation,
    )
