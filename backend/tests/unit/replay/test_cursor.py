"""Stepping: one candle, once, forward, and an honest end.

The cursor is the only state replay owns, so these are the tests that decide
whether a replay can be trusted to be deterministic. They are written against
the pure functions, with no store and no clock, because a rule that needs a
database to hold is not a rule.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from app.domain.replay import (
    MAX_ADVANCE_STEPS,
    ReplayCursor,
    ReplayError,
    ReplayPlan,
    ReplayStatus,
    advance,
    start_cursor,
    step,
)

BASE = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
TICK = timedelta(microseconds=1)


def series(count: int, timeframe: Timeframe = Timeframe.M5) -> list[Candle]:
    return [
        Candle(
            symbol="TEST_FIXTURE_FUT",
            timeframe=timeframe,
            open_time=BASE + timedelta(minutes=timeframe.minutes * index),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=Decimal("1000"),
            is_closed=True,
        )
        for index in range(count)
    ]


def plan(start: datetime, timeframe: Timeframe = Timeframe.M5) -> ReplayPlan:
    return ReplayPlan(
        dataset_id="RD-0000000000000000000000000000000f",
        symbol="TEST_FIXTURE_FUT",
        driver=timeframe,
        replay_start=start,
    )


@pytest.mark.unit
class TestStart:
    def test_the_start_snaps_to_a_real_candle_boundary(self) -> None:
        """A person types a moment; the session begins at a market fact."""
        cursor = start_cursor(plan(BASE + timedelta(minutes=27)), series(12))
        assert cursor.as_of == BASE + timedelta(minutes=25)

    def test_warm_up_history_is_everything_that_had_finished(self) -> None:
        cursor = start_cursor(plan(BASE + timedelta(minutes=25)), series(12))
        assert cursor.revealed_driver_candles == 5

    def test_a_new_session_starts_at_version_one_and_is_ready(self) -> None:
        cursor = start_cursor(plan(BASE + timedelta(minutes=25)), series(12))
        assert (cursor.version, cursor.status) == (1, ReplayStatus.READY)

    def test_a_start_before_any_candle_closes_is_refused(self) -> None:
        with pytest.raises(ReplayError) as error:
            start_cursor(plan(BASE + timedelta(minutes=5) - TICK), series(12))
        assert error.value.code == "REPLAY_START_BEFORE_DATA"

    def test_a_start_past_the_data_is_refused(self) -> None:
        with pytest.raises(ReplayError) as error:
            start_cursor(plan(BASE + timedelta(hours=9)), series(12))
        assert error.value.code == "REPLAY_START_AFTER_DATA"

    def test_a_start_on_the_last_boundary_is_already_the_end(self) -> None:
        candles = series(12)
        cursor = start_cursor(plan(BASE + timedelta(minutes=60)), candles)
        assert cursor.status is ReplayStatus.END_OF_DATASET
        assert cursor.revealed_driver_candles == 12

    def test_an_empty_driver_series_is_refused(self) -> None:
        with pytest.raises(ReplayError) as error:
            start_cursor(plan(BASE), [])
        assert error.value.code == "DATASET_INVALID"


@pytest.mark.unit
class TestStep:
    def test_one_step_reveals_exactly_one_candle(self) -> None:
        candles = series(12)
        first = start_cursor(plan(BASE + timedelta(minutes=25)), candles)
        second = step(first, candles)
        assert second.revealed_driver_candles == first.revealed_driver_candles + 1

    def test_the_clock_moves_to_that_candle_s_close(self) -> None:
        candles = series(12)
        cursor = step(start_cursor(plan(BASE + timedelta(minutes=25)), candles), candles)
        assert cursor.as_of == BASE + timedelta(minutes=30)

    def test_each_step_increments_the_version_once(self) -> None:
        candles = series(12)
        cursor = start_cursor(plan(BASE + timedelta(minutes=25)), candles)
        versions = []
        for _ in range(4):
            cursor = step(cursor, candles)
            versions.append(cursor.version)
        assert versions == [2, 3, 4, 5]

    def test_stepping_is_forward_only(self) -> None:
        candles = series(12)
        cursor = start_cursor(plan(BASE + timedelta(minutes=25)), candles)
        moments = []
        while cursor.status is not ReplayStatus.END_OF_DATASET:
            cursor = step(cursor, candles)
            moments.append(cursor.as_of)
        assert moments == sorted(moments)
        assert len(set(moments)) == len(moments)

    def test_the_end_is_stated_not_repeated(self) -> None:
        candles = series(3)
        cursor = start_cursor(plan(BASE + timedelta(minutes=5)), candles)
        cursor = step(cursor, candles)
        cursor = step(cursor, candles)
        assert cursor.status is ReplayStatus.END_OF_DATASET
        with pytest.raises(ReplayError) as error:
            step(cursor, candles)
        assert error.value.code == "REPLAY_END"

    def test_nothing_is_fabricated_past_the_last_candle(self) -> None:
        candles = series(3)
        cursor = start_cursor(plan(BASE + timedelta(minutes=5)), candles)
        while cursor.status is not ReplayStatus.END_OF_DATASET:
            cursor = step(cursor, candles)
        assert cursor.as_of == candles[-1].open_time + timedelta(minutes=5)
        assert cursor.revealed_driver_candles == len(candles)


@pytest.mark.unit
class TestAdvance:
    def test_advance_of_n_equals_n_single_steps(self) -> None:
        """The equivalence the API depends on, proven on the cursor itself."""
        candles = series(24)
        start = start_cursor(plan(BASE + timedelta(minutes=25)), candles)

        one_at_a_time = start
        for _ in range(7):
            one_at_a_time = step(one_at_a_time, candles)

        in_one_go, boundaries = advance(start, candles, 7)
        assert in_one_go == one_at_a_time
        assert len(boundaries) == 7

    def test_every_intermediate_boundary_is_reported(self) -> None:
        candles = series(24)
        start = start_cursor(plan(BASE + timedelta(minutes=25)), candles)
        _cursor, boundaries = advance(start, candles, 4)
        assert list(boundaries) == [
            BASE + timedelta(minutes=30),
            BASE + timedelta(minutes=35),
            BASE + timedelta(minutes=40),
            BASE + timedelta(minutes=45),
        ]

    def test_an_advance_stops_at_the_end_rather_than_overrunning(self) -> None:
        candles = series(8)
        start = start_cursor(plan(BASE + timedelta(minutes=25)), candles)
        cursor, boundaries = advance(start, candles, 10)
        assert cursor.status is ReplayStatus.END_OF_DATASET
        assert len(boundaries) == 3
        assert cursor.revealed_driver_candles == 8

    def test_an_advance_at_the_end_is_refused(self) -> None:
        candles = series(3)
        cursor = ReplayCursor(
            as_of=BASE + timedelta(minutes=15),
            revealed_driver_candles=3,
            status=ReplayStatus.END_OF_DATASET,
            version=4,
        )
        with pytest.raises(ReplayError) as error:
            advance(cursor, candles, 1)
        assert error.value.code == "REPLAY_END"

    @pytest.mark.parametrize("steps", [0, -1, -1000])
    def test_a_non_positive_advance_is_refused(self, steps: int) -> None:
        candles = series(12)
        start = start_cursor(plan(BASE + timedelta(minutes=25)), candles)
        with pytest.raises(ReplayError) as error:
            advance(start, candles, steps)
        assert error.value.code == "INVALID_ADVANCE"

    def test_an_unbounded_advance_is_refused(self) -> None:
        candles = series(12)
        start = start_cursor(plan(BASE + timedelta(minutes=25)), candles)
        with pytest.raises(ReplayError) as error:
            advance(start, candles, MAX_ADVANCE_STEPS + 1)
        assert error.value.code == "RESOURCE_LIMIT"

    def test_the_version_advances_once_per_candle_not_once_per_command(self) -> None:
        candles = series(24)
        start = start_cursor(plan(BASE + timedelta(minutes=25)), candles)
        cursor, _ = advance(start, candles, 5)
        assert cursor.version == start.version + 5
