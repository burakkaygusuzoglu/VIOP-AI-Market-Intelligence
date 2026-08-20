"""Volume metrics: hand-computed values and the divide-by-nothing cases."""

from __future__ import annotations

import pytest

from app.domain.technical.types import IndicatorInputError
from app.domain.technical.volume import (
    relative_volume,
    volume_acceleration,
    volume_moving_average,
)

VOLUMES = [10.0, 20.0, 30.0, 40.0]


@pytest.mark.unit
def test_volume_moving_average_hand_computed() -> None:
    # period 2: [10,20]=15, [20,30]=25, [30,40]=35
    assert volume_moving_average(VOLUMES, 2) == (None, 15.0, 25.0, 35.0)


@pytest.mark.unit
def test_relative_volume_hand_computed() -> None:
    # 20/15, 30/25, 40/35
    result = relative_volume(VOLUMES, 2)
    assert result[0] is None
    assert result[1] == pytest.approx(20.0 / 15.0)
    assert result[2] == pytest.approx(30.0 / 25.0)
    assert result[3] == pytest.approx(40.0 / 35.0)


@pytest.mark.unit
def test_relative_volume_of_a_steady_tape_is_one() -> None:
    result = relative_volume([500.0] * 30, 20)
    assert result[-1] == pytest.approx(1.0)


@pytest.mark.unit
def test_relative_volume_includes_the_current_candle_in_its_own_average() -> None:
    """Stated behaviour, so nobody "fixes" it into a forward-looking window.

    A spike raises its own denominator, so the reading is slightly damped
    relative to comparing against the *previous* N candles.
    """
    volumes = [100.0] * 19 + [1100.0]
    result = relative_volume(volumes, 20)
    average = (100.0 * 19 + 1100.0) / 20  # = 150
    assert result[-1] == pytest.approx(1100.0 / average)


@pytest.mark.unit
def test_volume_acceleration_hand_computed() -> None:
    # volume MA period 2 = [None, 15, 25, 35]
    # acceleration = 25/15 - 1, then 35/25 - 1
    result = volume_acceleration(VOLUMES, 2)
    assert result[0] is None
    assert result[1] is None
    assert result[2] == pytest.approx(25.0 / 15.0 - 1.0)
    assert result[3] == pytest.approx(35.0 / 25.0 - 1.0)


@pytest.mark.unit
def test_volume_acceleration_is_zero_on_a_steady_tape() -> None:
    result = volume_acceleration([250.0] * 30, 20)
    assert result[-1] == pytest.approx(0.0)


@pytest.mark.unit
def test_volume_acceleration_sign_follows_the_direction_of_change() -> None:
    rising = [float(100 + index * 10) for index in range(30)]
    falling = list(reversed(rising))
    assert (value := volume_acceleration(rising, 5)[-1]) is not None and value > 0
    assert (value := volume_acceleration(falling, 5)[-1]) is not None and value < 0


@pytest.mark.unit
def test_zero_average_volume_gives_none_not_infinity() -> None:
    """A multiple of nothing is undefined; infinity would read as a measurement."""
    volumes = [0.0, 0.0, 0.0, 5.0]
    assert relative_volume(volumes, 3)[2] is None
    assert volume_acceleration(volumes, 3)[3] is None


@pytest.mark.unit
def test_zero_volume_candles_do_not_break_the_average() -> None:
    volumes = [0.0, 100.0, 200.0, 300.0]
    result = volume_moving_average(volumes, 2)
    assert result[1] == pytest.approx(50.0)


@pytest.mark.unit
@pytest.mark.parametrize("function", (volume_moving_average, relative_volume, volume_acceleration))
def test_negative_volume_is_rejected(function) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(IndicatorInputError, match="negative"):
        function([10.0, -1.0, 30.0], 2)


@pytest.mark.unit
@pytest.mark.parametrize("function", (volume_moving_average, relative_volume, volume_acceleration))
def test_empty_input_is_empty_output(function) -> None:  # type: ignore[no-untyped-def]
    assert function([], 20) == ()


@pytest.mark.unit
@pytest.mark.parametrize("function", (volume_moving_average, relative_volume, volume_acceleration))
def test_short_history_is_all_none(function) -> None:  # type: ignore[no-untyped-def]
    assert function([10.0, 20.0], 20) == (None, None)
