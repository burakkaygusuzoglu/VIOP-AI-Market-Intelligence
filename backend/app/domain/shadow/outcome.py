"""What happened after a shadow decision (Phase 14 Part 2A).

Master spec section 78 asks a shadow run to record the "later outcome" of a
signal, "to evaluate system behavior under live conditions". It does not ask
for a profit, and this module does not produce one.

## The one claim this module makes

**Price development.** Given a decision that proposed an entry, a stop and
targets, it reports where the market subsequently traded relative to those
levels. That is an observation about price, and it is named as one.

Three things are deliberately not the same, and only the first is computed
here:

1. *subsequent observed market development* - price reached a level;
2. *hypothetical execution result* - an order would have been filled there;
3. *financial profit or loss* - that fill made or lost money.

Going from 1 to 2 needs an execution model and a spread; going from 2 to 3
needs a verified contract multiplier and tick value, which this deployment does
not have. A level the market touched is therefore ``STOP_LEVEL_TOUCHED``, never
``STOP_FILLED``, and never a loss. Nothing here says win, loss or profit,
because nothing here knows whether anybody could have traded it.

## Forward-only, with no exceptions

A decision made at market boundary ``T`` may only be judged by candles that
**opened at or after** ``T``. Closing after ``T`` is not enough: a candle that
opened before ``T`` and closed after it has a high and a low that may have been
made *before* the decision, and its range cannot be split. Such a straddling
candle is not later evidence, in whole or in part. The candle that produced the
decision is never part of its own outcome, and the caller cannot pass one: the
eligible interval is computed here from the boundary, not supplied.

Continuity is checked from ``T`` itself, not from the first candle that
happened to arrive: if the interval starting at ``T`` is missing, nothing after
it is observed continuously from the decision, and the development says so.

## What is refused rather than guessed

* **The same bar reaching both levels.** OHLC does not say which came first.
  That is recorded as an ambiguity, not resolved by assuming an order.
* **A gap.** If the confirmed series skips an interval inside the window, the
  development is unavailable from that point: the market may have traded
  through a level unobserved. A level already reached *before* the gap stands.
* **Data running out.** A window that never closed is unavailable, not a
  ``NONE_REACHED`` result that pretends the window completed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.enums import Direction, Timeframe
from app.domain.market.candle import Candle

__all__ = [
    "OUTCOME_RULES",
    "LevelEvent",
    "OutcomeState",
    "PriceDevelopment",
    "ShadowOutcomeRecord",
    "coverage_end_of",
    "eligible_forward_candles",
    "observe_development",
]

OUTCOME_RULES = "shadow-outcome/v1"
"""The vocabulary and the rules below, versioned. A stored outcome names the
rules that produced it, so a later change cannot silently reinterpret it."""


@unique
class OutcomeState(StrEnum):
    NOT_EVALUATED = "NOT_EVALUATED"
    """The decision proposed no levels, so there is nothing to follow. A
    NO_SIGNAL or a WAIT has no outcome, and inventing one would turn an empty
    journal into a population of imaginary trades."""

    PENDING = "PENDING"
    """Levels were proposed and the observation window is still open. Not a
    result: a question still being asked."""

    OBSERVED = "OBSERVED"
    """The window closed, or a level was reached. The detail says which."""

    UNAVAILABLE = "UNAVAILABLE"
    """The market development cannot be stated: a gap, a stale stream or the
    data ending before the window closed. Never reported as no result."""

    INVALIDATED = "INVALIDATED"
    """A correction contested evidence this development depends on: a candle the
    decision read, or a candle inside the development's own observed window.
    The decision, any earlier development and this verdict all stand; nothing
    is erased, and a correction reaching neither leaves it alone."""


@unique
class LevelEvent(StrEnum):
    NONE_REACHED = "NONE_REACHED"
    """Price stayed between the stop and every target for the whole window."""

    STOP_LEVEL_TOUCHED = "STOP_LEVEL_TOUCHED"
    """Price traded at or through the stop level. Not a filled stop order, and
    not a loss - nobody was in this market."""

    TARGET_LEVEL_TOUCHED = "TARGET_LEVEL_TOUCHED"
    """Price traded at or through a target level. Not a realised gain."""

    BOTH_LEVELS_TOUCHED_SAME_BAR = "BOTH_LEVELS_TOUCHED_SAME_BAR"
    """One candle's range covered both the stop and a target. OHLC cannot say
    which came first, so neither is claimed."""

    NOT_OBSERVED = "NOT_OBSERVED"
    """No eligible candle was seen, so nothing about price can be said."""


@dataclass(frozen=True, slots=True)
class PriceDevelopment:
    """Where price went after a decision, relative to its proposed levels."""

    state: OutcomeState
    event: LevelEvent
    rules: str = OUTCOME_RULES
    observed_from: datetime | None = None
    """Market time of the first eligible candle's coverage end."""

    observed_to: datetime | None = None
    """Market time of the last candle actually taken into account."""

    candles_observed: int = 0
    event_at: datetime | None = None
    """Market time of the candle in which the level was reached."""

    target_ordinal: int | None = None
    """Which target was touched, when one was."""

    best_price: Decimal | None = None
    """The furthest price reached in the decision's favour. A price, not money,
    and not a profit anybody could have taken."""

    worst_price: Decimal | None = None
    """The furthest price reached against the decision. Likewise a price."""

    last_close: Decimal | None = None
    ambiguous: bool = False
    """The same candle reached both levels; the order is unknowable here."""

    unresolved_reason: str | None = None
    """Why an UNAVAILABLE development could not be stated."""

    def __post_init__(self) -> None:
        if self.state is OutcomeState.OBSERVED and self.event is LevelEvent.NOT_OBSERVED:
            raise ValueError("an observed development names what price did")
        if self.state is OutcomeState.UNAVAILABLE and not self.unresolved_reason:
            raise ValueError("an unavailable development says why")
        if self.ambiguous and self.event is not LevelEvent.BOTH_LEVELS_TOUCHED_SAME_BAR:
            raise ValueError("ambiguity belongs to a candle that reached both levels")


def coverage_end_of(candle: Candle) -> datetime:
    """When the candle's interval finished - the market time it is evidence of."""
    return candle.open_time + timedelta(minutes=candle.timeframe.minutes)


def eligible_forward_candles(
    candles: tuple[Candle, ...], boundary: datetime, *, limit: int
) -> tuple[Candle, ...]:
    """The candles a decision at ``boundary`` is allowed to be judged by.

    Those that *opened* at or after the boundary. A candle whose coverage ended
    at or before it was evidence *for* the decision; one that opened before it
    and closed after it straddles the decision, and its range mixes trading
    from before the decision with trading after. Neither is evidence *about*
    the decision.
    """
    forward = tuple(candle for candle in candles if candle.open_time >= boundary)
    return forward[:limit] if limit > 0 else ()


def _has_gap(candles: tuple[Candle, ...], timeframe: Timeframe, boundary: datetime) -> int | None:
    """The index of the first candle that does not follow its predecessor.

    The decision boundary is the first predecessor: a missing interval right
    after the decision is a gap like any other, at index 0.
    """
    step = timedelta(minutes=timeframe.minutes)
    expected = boundary
    for index, candle in enumerate(candles):
        if candle.open_time != expected:
            return index
        expected = candle.open_time + step
    return None


def observe_development(
    *,
    direction: Direction,
    stop: Decimal,
    targets: tuple[tuple[Decimal, int], ...],
    boundary: datetime,
    timeframe: Timeframe,
    candles: tuple[Candle, ...],
    window: int,
    exhausted: bool,
) -> PriceDevelopment:
    """Follow price after ``boundary`` and say what it did to the levels.

    ``candles`` is this timeframe's confirmed series; only the part after the
    boundary is used. ``window`` bounds how far the question is asked, and
    ``exhausted`` says whether the stream has ended - an open stream with a
    window still to run is *pending*, which is a different answer from a window
    that completed with nothing reached.
    """
    forward = eligible_forward_candles(candles, boundary, limit=window)
    if not forward:
        if exhausted:
            return PriceDevelopment(
                state=OutcomeState.UNAVAILABLE,
                event=LevelEvent.NOT_OBSERVED,
                unresolved_reason="the stream ended before any later candle was confirmed",
            )
        return PriceDevelopment(state=OutcomeState.PENDING, event=LevelEvent.NOT_OBSERVED)

    gap_at = _has_gap(forward, timeframe, boundary)
    usable = forward[:gap_at] if gap_at is not None else forward
    long = direction is Direction.LONG

    best = worst = None
    event = LevelEvent.NONE_REACHED
    event_at: datetime | None = None
    ordinal: int | None = None
    ambiguous = False

    for candle in usable:
        # "Favourable" and "adverse" are directions on a price axis, nothing
        # more: long wants the high, short wants the low.
        favourable, adverse = (candle.high, candle.low) if long else (candle.low, candle.high)
        if best is None or worst is None:
            best, worst = favourable, adverse
        elif long:
            best, worst = max(best, favourable), min(worst, adverse)
        else:
            best, worst = min(best, favourable), max(worst, adverse)

        stop_touched = candle.low <= stop if long else candle.high >= stop
        hit = _target_touched(candle, targets, long=long)
        if stop_touched and hit is not None:
            event, ambiguous, ordinal = LevelEvent.BOTH_LEVELS_TOUCHED_SAME_BAR, True, hit[1]
        elif stop_touched:
            event = LevelEvent.STOP_LEVEL_TOUCHED
        elif hit is not None:
            event, ordinal = LevelEvent.TARGET_LEVEL_TOUCHED, hit[1]
        if event is not LevelEvent.NONE_REACHED:
            event_at = coverage_end_of(candle)
            break

    observed = usable[: len(usable) if event_at is None else _index_of(usable, event_at) + 1]

    def stated(state: OutcomeState, reason: str | None = None) -> PriceDevelopment:
        return PriceDevelopment(
            state=state,
            event=event,
            observed_from=coverage_end_of(usable[0]) if usable else None,
            observed_to=coverage_end_of(observed[-1]) if observed else None,
            candles_observed=len(observed),
            event_at=event_at,
            target_ordinal=ordinal,
            best_price=best,
            worst_price=worst,
            last_close=observed[-1].close if observed else None,
            ambiguous=ambiguous,
            unresolved_reason=reason,
        )

    if event_at is not None:
        # A level was reached before anything could interrupt the observation.
        return stated(OutcomeState.OBSERVED)
    if gap_at is not None:
        return stated(
            OutcomeState.UNAVAILABLE,
            "the confirmed series skips an interval inside the observation "
            "window; price may have reached a level unobserved",
        )
    if len(usable) >= window:
        return stated(OutcomeState.OBSERVED)
    if exhausted:
        return stated(
            OutcomeState.UNAVAILABLE,
            f"the stream ended after {len(usable)} of {window} candles; the "
            "observation window never closed",
        )
    return stated(OutcomeState.PENDING)


def _target_touched(
    candle: Candle, targets: tuple[tuple[Decimal, int], ...], *, long: bool
) -> tuple[Decimal, int] | None:
    """The nearest target this candle's range reached, if any."""
    reached = [
        item for item in targets if (candle.high >= item[0] if long else candle.low <= item[0])
    ]
    if not reached:
        return None
    return (
        min(reached, key=lambda item: item[0]) if long else max(reached, key=lambda item: item[0])
    )


def _index_of(candles: tuple[Candle, ...], coverage: datetime) -> int:
    for index, candle in enumerate(candles):
        if coverage_end_of(candle) == coverage:
            return index
    return len(candles) - 1  # pragma: no cover - the coverage came from this series


@dataclass(frozen=True, slots=True)
class ShadowOutcomeRecord:
    """One published development, tied to the decision it followed.

    Immutable once written. A later, better answer about the same decision is a
    new record with a later sequence; the earlier one stays, because "we did
    not know yet" is itself part of the history a shadow journal exists to
    keep.
    """

    run_id: str
    decision_key: str
    """The decision this followed. Outcomes never exist on their own."""

    sequence: int
    outcome_key: str
    recorded_at: datetime
    """Audit time. The market times are inside the development."""

    decision_boundary: datetime
    direction: Direction
    development: PriceDevelopment

    def __post_init__(self) -> None:
        observed_from = self.development.observed_from
        if observed_from is not None and observed_from <= self.decision_boundary:
            raise ValueError("an outcome may only observe what came after the decision")
