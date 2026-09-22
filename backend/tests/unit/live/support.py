"""Shared fixtures for the Phase 13 live tests.

Candles come from the Phase 11 replay factories, so every price here is a
fixture price the rest of the suite already uses - nothing is invented for the
live tests. The receive clock starts well after the fixture's market dates:
this is simulated *historical* streaming, and the gap between the two clocks
is the point, not an accident.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from app.adapters.live.mock_stream import ManualClock, MockStreamProvider, Step
from app.adapters.market_data.csv_provider import CsvCandleTextParser
from app.application.live.session import LiveSession
from app.domain.common.enums import Timeframe
from app.domain.live.limits import LiveLimits
from app.domain.live.state import FreshnessPolicy
from app.domain.market.candle import Candle
from tests.factories_replay import BASE, FIXTURE_SYMBOL, Row, aggregate, five_minute

SYMBOL = FIXTURE_SYMBOL
RECEIVE_START = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
"""The receive clock. Months after the fixture's market dates, on purpose."""

M5, M15, H1, D1 = Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.D1

FRESHNESS = FreshnessPolicy(
    {
        M5: timedelta(minutes=10),
        M15: timedelta(minutes=30),
        H1: timedelta(hours=2),
        D1: timedelta(days=2),
    }
)
"""Explicit per timeframe. Not derived from any market's hours."""


def candles(rows: Sequence[Row], timeframe: Timeframe) -> list[Candle]:
    return [
        Candle(
            symbol=SYMBOL,
            timeframe=timeframe,
            open_time=row.open_time,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            is_closed=True,
        )
        for row in rows
    ]


def fixture_market(count: int = 288) -> dict[Timeframe, list[Candle]]:
    """A day of 5M candles with honestly aggregated 15M and 1H - the replay
    factories' own aggregation, used elsewhere in the suite."""
    rows = five_minute(count)
    return {
        M5: candles(rows, M5),
        M15: candles(aggregate(rows, M15), M15),
        H1: candles(aggregate(rows, H1), H1),
    }


def session_for(
    script: Sequence[Step],
    *,
    timeframes: tuple[Timeframe, ...] = (M5, M15, H1),
    clock: ManualClock | None = None,
    limits: LiveLimits | None = None,
    provider: object | None = None,
) -> tuple[LiveSession, ManualClock]:
    moment = clock or ManualClock(RECEIVE_START)
    source = provider or MockStreamProvider(script=script, clock=moment)
    return (
        LiveSession(
            provider=source,  # type: ignore[arg-type]
            symbol=SYMBOL,
            timeframes=timeframes,
            clock=moment,
            parser=CsvCandleTextParser(),
            freshness=FRESHNESS,
            limits=limits,
        ),
        moment,
    )


def at(minutes: int) -> datetime:
    """A 5M-grid market time, ``minutes`` after the fixture base."""
    return BASE + timedelta(minutes=minutes)
