"""What a replay may see, and when.

Replay exists to ask "what did this look like at the time?" - so the only rule
that really matters is the one that decides which candles had *finished* by a
given market moment. Everything else in this phase is bookkeeping around that
rule.

A candle is available at ``as_of`` when its coverage has ended:

    coverage_end = open_time + timeframe duration
    available    = coverage_end <= as_of

Open time alone is not enough. A 1H bar that opened at 10:00 has not happened
by 10:05; it is *forming*, and treating it as data would hand the replay the
future. The same rule runs independently per timeframe, which is what stops a
higher timeframe leaking into a multi-timeframe analysis.

The duration comes from the timeframe itself, exactly as Phase 9 derives it.
That is a conservative convention, not an exchange session calendar: a 1D bar
is considered finished 24 hours after it opened, because this project has no
verified session metadata and will not invent one.

Pure: stdlib and ``app.domain.common`` / ``app.domain.market`` only.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle


def duration_of(timeframe: Timeframe) -> timedelta:
    """How long one candle of this timeframe covers."""
    return timedelta(minutes=timeframe.minutes)


def coverage_end(candle: Candle) -> datetime:
    """The moment this candle finished covering the market."""
    return candle.open_time + duration_of(candle.timeframe)


def is_available(candle: Candle, as_of: datetime) -> bool:
    """True when the candle had finished by ``as_of``.

    The comparison is inclusive: a bar that ends exactly at ``as_of`` is
    complete information at that moment, which is what makes a step land on a
    boundary rather than one microsecond past it.
    """
    return coverage_end(candle) <= as_of


def revealed(candles: Sequence[Candle], as_of: datetime) -> tuple[Candle, ...]:
    """The prefix of ``candles`` that had finished by ``as_of``.

    The input is expected in chronological order, as it is stored; the result
    is a prefix rather than a filter, so a later-but-available candle cannot
    appear without the ones before it.
    """
    result: list[Candle] = []
    for candle in candles:
        if not is_available(candle, as_of):
            break
        result.append(candle)
    return tuple(result)


def revealed_count(candles: Sequence[Candle], as_of: datetime) -> int:
    """How many candles are available, without materialising them."""
    count = 0
    for candle in candles:
        if not is_available(candle, as_of):
            break
        count += 1
    return count


def next_boundary(candles: Sequence[Candle], after: datetime) -> datetime | None:
    """The market moment at which the next candle becomes available.

    This is what a step advances to: the coverage end of the first candle that
    is not available yet. ``None`` means the dataset has nothing further to
    reveal - the honest end of a replay, rather than a repeated last bar.
    """
    for candle in candles:
        end = coverage_end(candle)
        if end > after:
            return end
    return None
