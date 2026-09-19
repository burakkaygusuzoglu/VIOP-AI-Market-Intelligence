"""Builders for Phase 11 replay tests.

**Every price and volume here is TEST_FIXTURE data** (master spec section 118).
The walk is deterministic arithmetic on ``Decimal`` - no randomness, no float -
so a golden expectation written once stays true, and two runs of the same test
see byte-identical CSV.

Higher timeframes are *aggregated* from the five-minute series rather than
generated separately. That is what makes the multi-timeframe no-lookahead tests
meaningful: a 1H bar covers exactly the twelve 5M bars inside it, so "the 1H bar
has not finished yet" is a statement about the same market, not about two
unrelated fixtures that happen to disagree.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.common.enums import Timeframe

BASE = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
"""The first 5M open time. Aligned to the hour so aggregation is exact."""

FIXTURE_SYMBOL = "TEST_FIXTURE_FUT"

HEADER = "open_time,open,high,low,close,volume"

FACTOR = {Timeframe.M5: 1, Timeframe.M15: 3, Timeframe.H1: 12, Timeframe.D1: 288}
"""How many 5M candles one candle of each timeframe covers."""


@dataclass(frozen=True, slots=True)
class Row:
    """One fixture candle, before it becomes text."""

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def line(self) -> str:
        return ",".join(
            (
                self.open_time.isoformat(),
                format(self.open, "f"),
                format(self.high, "f"),
                format(self.low, "f"),
                format(self.close, "f"),
                format(self.volume, "f"),
            )
        )


def five_minute(count: int, *, start: datetime = BASE, base: str = "100") -> list[Row]:
    """A deterministic 5M walk of ``count`` candles.

    The step cycles through a fixed pattern rather than drifting one way, so a
    long series stays in a plausible range and indicators have something to say.
    """
    rows: list[Row] = []
    close = Decimal(base)
    for index in range(count):
        opened = close
        move = (Decimal((index * 7) % 13) - Decimal(6)) / Decimal(10)
        close = opened + move
        rows.append(
            Row(
                open_time=start + timedelta(minutes=5 * index),
                open=opened,
                high=max(opened, close) + Decimal("0.20"),
                low=min(opened, close) - Decimal("0.20"),
                close=close,
                volume=Decimal(1000 + index),
            )
        )
    return rows


def aggregate(rows: Sequence[Row], timeframe: Timeframe) -> list[Row]:
    """Fold 5M candles into whole candles of a higher timeframe.

    A partial group at the end is dropped: a bar that the source data does not
    cover completely has not happened, and inventing it would be exactly the
    lookahead this phase exists to prevent.
    """
    factor = FACTOR[timeframe]
    out: list[Row] = []
    for start in range(0, len(rows) - factor + 1, factor):
        group = rows[start : start + factor]
        out.append(
            Row(
                open_time=group[0].open_time,
                open=group[0].open,
                high=max(item.high for item in group),
                low=min(item.low for item in group),
                close=group[-1].close,
                volume=sum((item.volume for item in group), Decimal(0)),
            )
        )
    return out


def csv_of(rows: Sequence[Row]) -> str:
    return "\n".join([HEADER, *(row.line() for row in rows)]) + "\n"


def dataset(
    count: int = 288,
    *,
    timeframes: Sequence[Timeframe] = (Timeframe.M5, Timeframe.M15, Timeframe.H1),
    start: datetime = BASE,
    base: str = "100",
) -> dict[Timeframe, str]:
    """One aligned multi-timeframe dataset as uploadable CSV text."""
    rows = five_minute(count, start=start, base=base)
    return {
        timeframe: csv_of(rows if timeframe is Timeframe.M5 else aggregate(rows, timeframe))
        for timeframe in timeframes
    }


def with_absurd_future_candle(rows: list[Row], index: int, *, high: str = "999999999") -> list[Row]:
    """Replace one candle with an unmistakable one.

    Used to prove that an unrevealed candle cannot reach a chart, an indicator
    or a fill: a value this size would move any of them visibly.
    """
    victim = rows[index]
    replaced = Row(
        open_time=victim.open_time,
        open=victim.open,
        high=Decimal(high),
        low=victim.low,
        close=victim.close,
        volume=victim.volume,
    )
    return [*rows[:index], replaced, *rows[index + 1 :]]


def coverage_end_of(row: Row, timeframe: Timeframe) -> datetime:
    return row.open_time + timedelta(minutes=timeframe.minutes)
