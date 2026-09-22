"""One timeframe's candles, and what is known about their completeness.

The book keeps two things apart that a naive implementation merges:

* the **confirmed sequence** - closed candles, in market-time order, which is
  the only thing an analysis may ever read; and
* the **forming candle** - at most one, the interval still in progress, which
  is observation rather than evidence and is never in the confirmed sequence.

## Completeness is proven, never assumed

A candle arriving later than the one before it does not prove that nothing
fell between them. Three different facts are kept apart:

* **provider sequence continuity** - the provider numbered its closed candles
  without skipping one;
* **interval coverage** - every interval of the timeframe's grid between two
  confirmed candles holds a confirmed candle;
* **exchange session boundaries** - which of those intervals the market was
  actually closed for. This build has no verified calendar, so it never knows
  this, and nothing here ever concludes that a jump was a session break.

The sequenced-provider contract is: *one consecutive number per closed
interval of the stream's grid*. Sequence numbers are checked against time on
every new candle:

* the number jumps by exactly as many intervals as the time does - the lost
  candles are identified, recorded as a *gap*, and a late candle carrying
  the right number at the right time fills them;
* the time jumps further than the number (contiguous numbers across an
  overnight jump, say) - the uncovered intervals may be a session break, an
  interval without trades, or lost candles. That is an unexplained *temporal
  gap*, a discontinuity, and it blocks analysis;
* the number jumps further than the time - the provider broke its own
  contract. Also a discontinuity; the "missing" numbers are not recorded as
  candles to wait for, because no interval exists for them.

An unsequenced stream has only the time check: any jump is a discontinuity,
and a late candle is refused because nothing can prove where it belongs.

A forming candle more than one interval ahead of the last confirmed candle is
proof that closed intervals went by unreceived. Until confirmed history
catches up, the book is unverified rather than complete.

## Duplicates and corrections

An identical repeat of a closed candle is not a new observation: it is counted
and does not refresh freshness, so a flood of repeats cannot keep a dead
stream looking alive. A *conflicting* repeat - same interval, different values
- is quarantined: the stored candle is not rewritten, because an analysis may
already have read it, and the timeframe is marked conflicted until the
conflicting candle leaves the retained window. Provider corrections are not
supported in this build, and this is how they are refused.

## Bounded

The confirmed sequence is trimmed from the oldest end at ``max_closed_candles``
and the trim is counted. Completeness is a claim about the *retained window*,
the only thing an analysis reads, and it holds only if every adjacent pair in
that window was checked on the way in and no unresolved issue lies inside it.
An issue leaves the record only once it lies wholly before the oldest retained
candle - never earlier, and never because a record had to be dropped for room:

* recorded missing numbers at or below the oldest retained sequence;
* an overflowed gap only when *every* number it could not enumerate is at or
  below that floor;
* a discontinuity only when the candle after the jump is the oldest retained.

Each issue that scrolls out unresolved is counted in ``unresolved_trimmed``,
so the history of a window is never presented as cleaner than it was, even
when the window itself is now complete.
"""

from __future__ import annotations

import bisect
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum, unique

from app.domain.common.enums import Timeframe
from app.domain.live.events import Observation
from app.domain.live.limits import LiveLimits
from app.domain.live.validation import Rejection, RejectionCode

__all__ = [
    "ApplyOutcome",
    "BookStatus",
    "CandleBook",
    "Conflict",
    "Discontinuity",
    "DiscontinuityKind",
    "Integrity",
]


@unique
class ApplyOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    LATE_FILL = "LATE_FILL"
    """A closed candle that arrived after its successor and exactly filled a
    recorded gap. Not a rewrite: the interval had no candle before."""

    FORMING_UPDATED = "FORMING_UPDATED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"


@unique
class Integrity(StrEnum):
    COMPLETE = "COMPLETE"
    GAPPED = "GAPPED"
    """Closed candles are known to be missing, from sequence numbers."""

    DISCONTINUOUS = "DISCONTINUOUS"
    """Interval coverage is unexplained: a jump in time that sequence numbers
    do not account for, or sequence numbers that disagree with time."""

    CONFLICTED = "CONFLICTED"
    UNVERIFIED = "UNVERIFIED"
    """Continuity up to the present has not been demonstrated: since a
    disconnect, or since a forming candle showed intervals closing unreceived."""


@unique
class DiscontinuityKind(StrEnum):
    TEMPORAL_GAP = "TEMPORAL_GAP"
    """Time jumped further than the sequence numbers did (or, unsequenced, at
    all). A session break, an interval without trades or lost candles - not
    distinguishable without verified session evidence."""

    SEQUENCE_MISMATCH = "SEQUENCE_MISMATCH"
    """Sequence numbers jumped further than time did: the provider's
    one-number-per-interval contract is broken."""


@dataclass(frozen=True, slots=True)
class Discontinuity:
    after: datetime
    before: datetime
    kind: DiscontinuityKind = DiscontinuityKind.TEMPORAL_GAP


@dataclass(frozen=True, slots=True)
class Conflict:
    open_time: datetime
    first_seen_at: datetime


@dataclass(frozen=True, slots=True)
class BookStatus:
    timeframe: Timeframe
    integrity: Integrity
    closed_count: int
    trimmed: int
    first_closed_open_time: datetime | None
    last_closed_open_time: datetime | None
    last_sequence: int | None
    forming_open_time: datetime | None
    last_event_time: datetime | None
    last_received_at: datetime | None
    missing_sequences: int
    missing_overflowed: bool
    discontinuities: int
    temporal_gaps: int
    sequence_mismatches: int
    conflicts: int
    awaiting_continuity: bool
    forming_ahead: bool
    """A forming candle lies more than one interval past the last confirmed
    candle: intervals have closed that were never received."""

    unresolved_trimmed: int
    """Unresolved gaps, discontinuities and conflicts that left the retained
    window by trimming rather than by being resolved. Disclosure only: the
    window may still be complete, but its history was not."""

    duplicates: int
    late_fills: int
    version: int
    """Changes whenever the confirmed sequence changes. Two equal versions
    mean the same prefix, which is how a repeated analysis is deduplicated."""


@dataclass
class CandleBook:
    timeframe: Timeframe
    limits: LiveLimits
    _closed: list[Observation] = field(default_factory=list)
    _times: list[datetime] = field(default_factory=list)
    _forming: Observation | None = None
    _sequenced: bool | None = None
    _last_sequence: int | None = None
    _missing: set[int] = field(default_factory=set)
    _missing_overflowed: bool = False
    _unenumerated_through: int | None = None
    """The highest lost number an overflowed gap could not enumerate."""

    _discontinuities: deque[Discontinuity] = field(default_factory=deque)
    _conflicts: dict[datetime, Conflict] = field(default_factory=dict)
    _awaiting_continuity: bool = False
    _trimmed: int = 0
    _unresolved_trimmed: int = 0
    _duplicates: int = 0
    _late_fills: int = 0
    _version: int = 0
    _last_event_time: datetime | None = None
    _last_received_at: datetime | None = None

    # -- reading -----------------------------------------------------------

    @property
    def interval(self) -> timedelta:
        return timedelta(minutes=self.timeframe.minutes)

    def confirmed(self) -> tuple[Observation, ...]:
        """The closed candles, oldest first. The only thing analysis reads."""
        return tuple(self._closed)

    @property
    def forming(self) -> Observation | None:
        return self._forming

    @property
    def last_received_at(self) -> datetime | None:
        return self._last_received_at

    def integrity(self) -> Integrity:
        if self._conflicts:
            return Integrity.CONFLICTED
        if self._missing or self._missing_overflowed:
            return Integrity.GAPPED
        if self._discontinuities:
            return Integrity.DISCONTINUOUS
        if self._awaiting_continuity or self.forming_ahead:
            return Integrity.UNVERIFIED
        return Integrity.COMPLETE

    @property
    def awaiting_continuity(self) -> bool:
        return self._awaiting_continuity

    @property
    def forming_ahead(self) -> bool:
        return (
            self._forming is not None
            and bool(self._times)
            and self._forming.candle.open_time > self._times[-1] + self.interval
        )

    def status(self) -> BookStatus:
        kinds = [item.kind for item in self._discontinuities]
        return BookStatus(
            timeframe=self.timeframe,
            integrity=self.integrity(),
            closed_count=len(self._closed),
            trimmed=self._trimmed,
            first_closed_open_time=self._times[0] if self._times else None,
            last_closed_open_time=self._times[-1] if self._times else None,
            last_sequence=self._last_sequence,
            forming_open_time=None if self._forming is None else self._forming.candle.open_time,
            last_event_time=self._last_event_time,
            last_received_at=self._last_received_at,
            missing_sequences=len(self._missing),
            missing_overflowed=self._missing_overflowed,
            discontinuities=len(self._discontinuities),
            temporal_gaps=kinds.count(DiscontinuityKind.TEMPORAL_GAP),
            sequence_mismatches=kinds.count(DiscontinuityKind.SEQUENCE_MISMATCH),
            conflicts=len(self._conflicts),
            awaiting_continuity=self._awaiting_continuity,
            forming_ahead=self.forming_ahead,
            unresolved_trimmed=self._unresolved_trimmed,
            duplicates=self._duplicates,
            late_fills=self._late_fills,
            version=self._version,
        )

    # -- connection events -------------------------------------------------

    def mark_disconnected(self) -> None:
        """The source went away. What was confirmed stays confirmed.

        The forming candle is discarded: its remaining high, low and close are
        now unknowable, and keeping it would present a half-finished interval
        as if it were still being observed. Continuity becomes unverified
        until the next closed candle proves or disproves it.
        """
        self._forming = None
        self._awaiting_continuity = True

    # -- applying observations ---------------------------------------------

    def apply(self, observation: Observation) -> ApplyOutcome | Rejection:
        if observation.candle.timeframe is not self.timeframe:
            raise ValueError("observation belongs to a different timeframe")
        if observation.candle.is_closed:
            return self._apply_closed(observation)
        return self._apply_forming(observation)

    def _apply_forming(self, observation: Observation) -> ApplyOutcome | Rejection:
        open_time = observation.candle.open_time
        if self._times and open_time <= self._times[-1]:
            return Rejection(
                RejectionCode.STALE_FORMING,
                "a forming update for an interval that has already closed",
            )
        self._forming = observation
        self._touch(observation)
        return ApplyOutcome.FORMING_UPDATED

    def _apply_closed(self, observation: Observation) -> ApplyOutcome | Rejection:
        open_time = observation.candle.open_time
        sequence = observation.sequence

        existing_index = self._index_of(open_time)
        if existing_index is not None:
            stored = self._closed[existing_index]
            if stored.same_values(observation):
                self._duplicates += 1
                return ApplyOutcome.DUPLICATE
            if open_time not in self._conflicts:
                self._conflicts[open_time] = Conflict(
                    open_time=open_time, first_seen_at=observation.received_at
                )
            return ApplyOutcome.CONFLICT

        sequenced = sequence is not None
        if self._sequenced is None:
            self._sequenced = sequenced
        elif self._sequenced != sequenced:
            return Rejection(
                RejectionCode.MIXED_SEQUENCING,
                "this stream mixes sequenced and unsequenced closed candles",
            )

        if sequenced:
            assert sequence is not None  # noqa: S101 - narrowed by `sequenced`
            outcome = self._place_sequenced(observation, sequence)
        else:
            outcome = self._place_unsequenced(observation)
        if isinstance(outcome, Rejection):
            return outcome

        if self._forming is not None and self._forming.candle.open_time <= open_time:
            self._forming = None
        if outcome is ApplyOutcome.ACCEPTED:
            # Only a new newest candle speaks about the present; its placement
            # has just recorded any gap or jump since the previous one. A late
            # fill of an old hole proves nothing about the time since a
            # disconnect.
            self._awaiting_continuity = False
        self._touch(observation)
        self._version += 1
        self._trim()
        return outcome

    def _place_sequenced(self, observation: Observation, sequence: int) -> ApplyOutcome | Rejection:
        open_time = observation.candle.open_time
        last = self._last_sequence
        if last is None or sequence > last:
            if self._times and open_time <= self._times[-1]:
                return Rejection(
                    RejectionCode.ORDER_VIOLATION,
                    "a higher sequence number carries an earlier interval",
                )
            if last is not None and self._times:
                self._check_coverage(open_time, sequence - last)
            self._append(observation)
            self._last_sequence = sequence
            return ApplyOutcome.ACCEPTED
        if sequence in self._missing:
            position = bisect.bisect_left(self._times, open_time)
            before_ok = position == 0 or self._sequence_at(position - 1) < sequence
            after_ok = position == len(self._closed) or self._sequence_at(position) > sequence
            # A recorded gap was consistent with time on both sides, so the
            # number fixes the interval exactly; nothing else may fill it.
            exact = position == 0 or open_time == self._times[position - 1] + self.interval * (
                sequence - self._sequence_at(position - 1)
            )
            if not (before_ok and after_ok and exact):
                return Rejection(
                    RejectionCode.ORDER_VIOLATION,
                    "a late candle's time and sequence disagree about where it belongs",
                )
            self._closed.insert(position, observation)
            self._times.insert(position, open_time)
            self._missing.discard(sequence)
            self._late_fills += 1
            return ApplyOutcome.LATE_FILL
        return Rejection(
            RejectionCode.ORDER_VIOLATION,
            "a sequence number that is neither new nor a recorded gap",
        )

    def _place_unsequenced(self, observation: Observation) -> ApplyOutcome | Rejection:
        open_time = observation.candle.open_time
        if self._times and open_time < self._times[-1]:
            # No sequence means no way to prove this belongs in the hole it
            # appears to fit. Refused rather than inserted on trust.
            return Rejection(
                RejectionCode.LATE_UNSEQUENCED,
                "a late candle on an unsequenced stream cannot be placed verifiably",
            )
        if self._times and open_time != self._times[-1] + self.interval:
            self._record_discontinuity(open_time, DiscontinuityKind.TEMPORAL_GAP)
        self._append(observation)
        return ApplyOutcome.ACCEPTED

    def _check_coverage(self, open_time: datetime, step: int) -> None:
        """Hold a new sequenced candle's number against its time.

        ``step`` is how far the number moved; ``slots`` how many intervals the
        time moved. Only agreement lets a sequence jump name lost candles.
        """
        slots, remainder = divmod(open_time - self._times[-1], self.interval)
        if not remainder and slots == step:
            if step > 1:
                last = self._last_sequence
                assert last is not None  # noqa: S101 - a sequenced book with candles
                self._record_missing(range(last + 1, last + step))
            return
        mismatch = not remainder and slots < step
        self._record_discontinuity(
            open_time,
            DiscontinuityKind.SEQUENCE_MISMATCH if mismatch else DiscontinuityKind.TEMPORAL_GAP,
        )

    # -- helpers -----------------------------------------------------------

    def _record_discontinuity(self, open_time: datetime, kind: DiscontinuityKind) -> None:
        # Records arrive in market-time order, so dropping the oldest record
        # for room never lets a jump inside the window go unmarked: the newer
        # records that remain hold the book discontinuous until they, too,
        # lie before the window - and the dropped one lies earlier still.
        self._discontinuities.append(
            Discontinuity(after=self._times[-1], before=open_time, kind=kind)
        )
        while len(self._discontinuities) > self.limits.max_recorded_issues:
            self._discontinuities.popleft()

    def _append(self, observation: Observation) -> None:
        self._closed.append(observation)
        self._times.append(observation.candle.open_time)

    def _index_of(self, open_time: datetime) -> int | None:
        position = bisect.bisect_left(self._times, open_time)
        if position < len(self._times) and self._times[position] == open_time:
            return position
        return None

    def _sequence_at(self, index: int) -> int:
        value = self._closed[index].sequence
        assert value is not None  # noqa: S101 - only called on sequenced books
        return value

    def _record_missing(self, numbers: range) -> None:
        room = max(self.limits.max_missing_sequences - len(self._missing), 0)
        if len(numbers) > room:
            # The numbers not enumerated are the top of the range; remember how
            # far they reach, so the gap can be retired only once all of them
            # lie before the window. Slicing the range, not listing it, keeps a
            # jump of a million numbers from allocating a million integers.
            self._missing_overflowed = True
            ceiling = numbers[-1]
            if self._unenumerated_through is None or ceiling > self._unenumerated_through:
                self._unenumerated_through = ceiling
        self._missing.update(numbers[:room])

    def _touch(self, observation: Observation) -> None:
        self._last_received_at = observation.received_at
        if self._last_event_time is None or observation.event_time > self._last_event_time:
            self._last_event_time = observation.event_time

    def _trim(self) -> None:
        while len(self._closed) > self.limits.max_closed_candles:
            dropped = self._closed.pop(0)
            self._times.pop(0)
            self._trimmed += 1
            if self._conflicts.pop(dropped.candle.open_time, None) is not None:
                self._unresolved_trimmed += 1
        if not self._closed:
            return
        oldest = self._times[0]
        while self._discontinuities and self._discontinuities[0].before <= oldest:
            self._discontinuities.popleft()
            self._unresolved_trimmed += 1
        if self._sequenced and self._closed[0].sequence is not None:
            floor = self._closed[0].sequence
            kept = {number for number in self._missing if number > floor}
            self._unresolved_trimmed += len(self._missing) - len(kept)
            self._missing = kept
            ceiling = self._unenumerated_through
            if self._missing_overflowed and ceiling is not None and ceiling <= floor:
                self._missing_overflowed = False
                self._unenumerated_through = None
                self._unresolved_trimmed += 1
