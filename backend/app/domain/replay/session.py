"""The replay session: a cursor over an immutable dataset.

A session holds almost nothing of its own. The dataset is immutable, the
financial record lives in the Phase 9 ledger and the Phase 10 engines, and what
remains here is the one thing replay adds: *where in market time the session
has got to*, and what a step does to it.

Forward only. Stepping backwards would mean reversing paper fills, ledger
events and journal writes that a person may already have acted on, so Phase 11
does not pretend to: to see an earlier moment again, start another session over
the same immutable dataset.

Pure: stdlib and ``app.domain.common`` / ``app.domain.market`` only. Nothing
here knows about products, storage, HTTP, or the engines replay drives.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique

from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from app.domain.replay.availability import coverage_end, next_boundary, revealed_count

MAX_ADVANCE_STEPS = 50
"""How many candles one ``advance`` may reveal. Bounded because each step can
feed observations to paper positions, and an unbounded request would hide an
unbounded amount of work behind one call."""


@unique
class ReplayStatus(StrEnum):
    READY = "READY"
    """Created, nothing stepped yet."""

    IN_PROGRESS = "IN_PROGRESS"
    END_OF_DATASET = "END_OF_DATASET"
    """Every driver candle has been revealed. Not an error, and not a reason to
    repeat the last bar."""


class ReplayError(ValueError):
    """A replay request that cannot be honoured, with a stable code."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")


@dataclass(frozen=True, slots=True)
class ReplayPlan:
    """What a session was created to do. Immutable for its whole life."""

    dataset_id: str
    symbol: str
    driver: Timeframe
    """What one step means: the next candle of *this* timeframe."""

    replay_start: datetime
    """The market moment the session begins at. Candles finishing at or before
    it are warm-up history - past information, freely usable - and everything
    after it is revealed only by stepping."""


@dataclass(frozen=True, slots=True)
class ReplayCursor:
    """Where the session has got to. The only thing a step changes."""

    as_of: datetime
    """The market-information boundary: nothing whose coverage ends after this
    is visible to anything the session drives."""

    revealed_driver_candles: int
    """How many driver candles are available at ``as_of``. Derived, kept for
    display and for a cheap end-of-dataset check."""

    status: ReplayStatus
    version: int
    """Incremented by every accepted step, so a second tab cannot advance the
    same session twice."""


def start_cursor(
    plan: ReplayPlan, driver_candles: list[Candle] | tuple[Candle, ...]
) -> ReplayCursor:
    """The cursor a new session begins with.

    The replay start is snapped to a real candle boundary: a person types a
    moment, and the session begins at the first driver candle that had finished
    by then, so ``as_of`` is always a market fact rather than an arbitrary time.
    """
    if not driver_candles:
        raise ReplayError("DATASET_INVALID", "the dataset has no candles for the driver timeframe")
    first_end = coverage_end(driver_candles[0])
    if plan.replay_start < first_end:
        raise ReplayError(
            "REPLAY_START_BEFORE_DATA",
            (
                f"the replay start {plan.replay_start.isoformat()} is before the first driver "
                f"candle finishes at {first_end.isoformat()}; there would be no history to "
                "begin from"
            ),
        )
    last_end = coverage_end(driver_candles[-1])
    if plan.replay_start > last_end:
        raise ReplayError(
            "REPLAY_START_AFTER_DATA",
            (
                f"the replay start {plan.replay_start.isoformat()} is after the last driver candle "
                f"finishes at {last_end.isoformat()}; there would be nothing to replay"
            ),
        )
    # Snap to the boundary at or before the requested start: everything that had
    # finished by then is warm-up, and the next step reveals what follows.
    boundary = _boundary_at_or_before(driver_candles, plan.replay_start)
    count = revealed_count(driver_candles, boundary)
    return ReplayCursor(
        as_of=boundary,
        revealed_driver_candles=count,
        status=ReplayStatus.END_OF_DATASET if count >= len(driver_candles) else ReplayStatus.READY,
        version=1,
    )


def step(cursor: ReplayCursor, driver_candles: list[Candle] | tuple[Candle, ...]) -> ReplayCursor:
    """Reveal exactly one more driver candle.

    ``as_of`` moves to that candle's coverage end - the moment it finished -
    which is why a step never exposes a bar that is still forming, on the driver
    timeframe or any other.
    """
    if cursor.status is ReplayStatus.END_OF_DATASET:
        raise ReplayError(
            "REPLAY_END",
            "every candle in this dataset has been revealed; start a new session to replay again",
        )
    boundary = next_boundary(driver_candles, cursor.as_of)
    if boundary is None:
        raise ReplayError("REPLAY_END", "the dataset has no further driver candle to reveal")
    count = revealed_count(driver_candles, boundary)
    return ReplayCursor(
        as_of=boundary,
        revealed_driver_candles=count,
        status=(
            ReplayStatus.END_OF_DATASET
            if count >= len(driver_candles)
            else ReplayStatus.IN_PROGRESS
        ),
        version=cursor.version + 1,
    )


def advance(
    cursor: ReplayCursor, driver_candles: list[Candle] | tuple[Candle, ...], steps: int
) -> tuple[ReplayCursor, tuple[datetime, ...]]:
    """Apply ``steps`` single steps, and report each boundary crossed.

    This is deliberately implemented as repeated :func:`step` rather than as a
    jump: the intermediate boundaries are what the paper engine needs in order
    to see every bar in between. Callers use the returned boundaries to feed
    observations in order.
    """
    if steps < 1:
        raise ReplayError("INVALID_ADVANCE", "an advance must be at least one step")
    if steps > MAX_ADVANCE_STEPS:
        raise ReplayError(
            "RESOURCE_LIMIT",
            f"an advance may cover at most {MAX_ADVANCE_STEPS} steps; {steps} were requested",
        )
    boundaries: list[datetime] = []
    current = cursor
    for _ in range(steps):
        if current.status is ReplayStatus.END_OF_DATASET:
            break
        current = step(current, driver_candles)
        boundaries.append(current.as_of)
    if not boundaries:
        raise ReplayError(
            "REPLAY_END",
            "every candle in this dataset has been revealed; start a new session to replay again",
        )
    return current, tuple(boundaries)


def _boundary_at_or_before(
    candles: list[Candle] | tuple[Candle, ...], moment: datetime
) -> datetime:
    latest = coverage_end(candles[0])
    for candle in candles:
        end = coverage_end(candle)
        if end > moment:
            break
        latest = end
    return latest
