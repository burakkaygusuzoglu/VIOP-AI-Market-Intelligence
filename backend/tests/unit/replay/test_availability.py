"""The one rule replay is built on: what had finished by a given moment.

Every other guarantee in Phase 11 - no future candle on a chart, no higher
timeframe leaking into a multi-timeframe analysis, no fill from a bar that had
not happened - reduces to ``coverage_end <= as_of``. So these tests are about
the boundary itself, one microsecond either side of it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from app.domain.replay import (
    coverage_end,
    duration_of,
    is_available,
    next_boundary,
    revealed,
    revealed_count,
)

BASE = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
TICK = timedelta(microseconds=1)


def candle(timeframe: Timeframe, minutes: int) -> Candle:
    return Candle(
        symbol="TEST_FIXTURE_FUT",
        timeframe=timeframe,
        open_time=BASE + timedelta(minutes=minutes),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("1000"),
        is_closed=True,
    )


def series(timeframe: Timeframe, count: int) -> list[Candle]:
    step = timeframe.minutes
    return [candle(timeframe, index * step) for index in range(count)]


@pytest.mark.unit
class TestCoverage:
    @pytest.mark.parametrize(
        ("timeframe", "minutes"),
        [(Timeframe.M5, 5), (Timeframe.M15, 15), (Timeframe.H1, 60), (Timeframe.D1, 1440)],
    )
    def test_duration_comes_from_the_timeframe(self, timeframe: Timeframe, minutes: int) -> None:
        assert duration_of(timeframe) == timedelta(minutes=minutes)

    def test_coverage_ends_one_duration_after_it_opens(self) -> None:
        assert coverage_end(candle(Timeframe.H1, 0)) == BASE + timedelta(hours=1)

    def test_a_bar_is_available_exactly_at_its_coverage_end(self) -> None:
        """Inclusive on purpose: a bar that ends at ``as_of`` is complete."""
        bar = candle(Timeframe.H1, 0)
        end = coverage_end(bar)
        assert is_available(bar, end)

    def test_a_bar_is_not_available_a_microsecond_earlier(self) -> None:
        bar = candle(Timeframe.H1, 0)
        assert not is_available(bar, coverage_end(bar) - TICK)

    def test_a_forming_bar_is_not_data(self) -> None:
        """The 1H bar that opened at 10:00 has not happened at 10:05."""
        assert not is_available(candle(Timeframe.H1, 0), BASE + timedelta(minutes=5))


@pytest.mark.unit
class TestRevealedPrefix:
    def test_only_finished_candles_are_revealed(self) -> None:
        candles = series(Timeframe.M5, 10)
        visible = revealed(candles, BASE + timedelta(minutes=25))
        assert [item.open_time for item in visible] == [
            BASE + timedelta(minutes=5 * index) for index in range(5)
        ]

    def test_the_count_agrees_with_the_prefix(self) -> None:
        candles = series(Timeframe.M5, 10)
        for minutes in range(0, 60, 3):
            moment = BASE + timedelta(minutes=minutes)
            assert revealed_count(candles, moment) == len(revealed(candles, moment))

    def test_nothing_is_revealed_before_the_first_bar_closes(self) -> None:
        candles = series(Timeframe.M5, 10)
        assert revealed(candles, BASE + timedelta(minutes=5) - TICK) == ()

    def test_a_later_available_candle_cannot_jump_a_gap(self) -> None:
        """A prefix, not a filter.

        If the series were out of order, filtering would hand the caller a bar
        without the ones before it - a hole in the middle of a chart that the
        engines would read as contiguous history.
        """
        candles = series(Timeframe.M5, 5)
        disordered = [candles[0], candles[3], candles[1], candles[2], candles[4]]
        visible = revealed(disordered, BASE + timedelta(minutes=10))
        assert [item.open_time for item in visible] == [BASE]


@pytest.mark.unit
class TestNextBoundary:
    def test_the_next_boundary_is_the_next_bar_to_finish(self) -> None:
        candles = series(Timeframe.M5, 5)
        assert next_boundary(candles, BASE + timedelta(minutes=10)) == BASE + timedelta(minutes=15)

    def test_a_boundary_exactly_at_now_is_already_past(self) -> None:
        candles = series(Timeframe.M5, 5)
        moment = BASE + timedelta(minutes=10)
        boundary = next_boundary(candles, moment)
        assert boundary is not None
        assert boundary > moment

    def test_the_end_of_the_dataset_is_none_not_a_repeat(self) -> None:
        candles = series(Timeframe.M5, 3)
        assert next_boundary(candles, BASE + timedelta(minutes=15)) is None


@pytest.mark.unit
class TestTimeframesAreIndependent:
    def test_a_finer_timeframe_does_not_complete_a_coarser_one(self) -> None:
        """The case the whole multi-timeframe guarantee rests on.

        At 10:15 three 5M bars have finished and one 15M bar has; the 1H bar
        that opened at 10:00 is still forming and must stay invisible.
        """
        as_of = BASE + timedelta(minutes=15)
        assert revealed_count(series(Timeframe.M5, 12), as_of) == 3
        assert revealed_count(series(Timeframe.M15, 4), as_of) == 1
        assert revealed_count(series(Timeframe.H1, 2), as_of) == 0

    def test_the_hour_appears_exactly_at_its_close(self) -> None:
        hourly = series(Timeframe.H1, 2)
        close = BASE + timedelta(hours=1)
        assert revealed_count(hourly, close - TICK) == 0
        assert revealed_count(hourly, close) == 1
