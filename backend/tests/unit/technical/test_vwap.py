"""VWAP: hand-computed values, anchoring, and zero-volume behaviour."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.domain.technical.types import IndicatorInputError
from app.domain.technical.vwap import daily_anchors, typical_prices, vwap


@pytest.mark.unit
def test_typical_price_is_the_three_way_average() -> None:
    assert typical_prices([12.0], [9.0], [10.5])[0] == pytest.approx(10.5)


@pytest.mark.unit
def test_vwap_hand_computed() -> None:
    """Two candles, weights chosen so the answer is checkable on paper.

    bar 0: typical 10, volume 100
    bar 1: typical 12, volume 300
    cumulative = (10*100 + 12*300) / 400 = 4600 / 400 = 11.5
    """
    result = vwap([11.0, 13.0], [9.0, 11.0], [10.0, 12.0], [100.0, 300.0])
    assert result[0] == pytest.approx(10.0)
    assert result[1] == pytest.approx(11.5)


@pytest.mark.unit
def test_vwap_has_no_warm_up() -> None:
    """It is cumulative, not rolling: the first candle is its own VWAP."""
    result = vwap([11.0], [9.0], [10.0], [50.0])
    assert result[0] == pytest.approx(10.0)


@pytest.mark.unit
def test_vwap_weights_by_volume_not_by_count() -> None:
    """A large bar pulls the average toward its own price."""
    heavy = vwap([11.0, 21.0], [9.0, 19.0], [10.0, 20.0], [1.0, 999.0])
    light = vwap([11.0, 21.0], [9.0, 19.0], [10.0, 20.0], [999.0, 1.0])
    assert heavy[1] is not None and light[1] is not None
    assert heavy[1] > 19.0
    assert light[1] < 11.0


@pytest.mark.unit
def test_zero_volume_gives_none_not_a_price() -> None:
    """There is nothing to weight by, so any number would be invented."""
    result = vwap([11.0, 12.0], [9.0, 10.0], [10.0, 11.0], [0.0, 0.0])
    assert result == (None, None)


@pytest.mark.unit
def test_vwap_resumes_once_volume_arrives() -> None:
    result = vwap(
        [11.0, 13.0, 15.0],
        [9.0, 11.0, 13.0],
        [10.0, 12.0, 14.0],
        [0.0, 0.0, 200.0],
    )
    assert result[0] is None
    assert result[1] is None
    assert result[2] == pytest.approx(14.0)


@pytest.mark.unit
def test_anchor_restarts_the_accumulation() -> None:
    highs = [11.0, 13.0, 21.0, 23.0]
    lows = [9.0, 11.0, 19.0, 21.0]
    closes = [10.0, 12.0, 20.0, 22.0]
    volumes = [100.0, 100.0, 100.0, 100.0]

    whole = vwap(highs, lows, closes, volumes, anchors=(0,))
    anchored = vwap(highs, lows, closes, volumes, anchors=(0, 2))

    # Whole series: mean of 10, 12, 20, 22 = 16.
    assert whole[3] == pytest.approx(16.0)
    # Anchored at index 2: mean of 20 and 22 = 21.
    assert anchored[3] == pytest.approx(21.0)
    # Everything before the anchor is untouched.
    assert anchored[:2] == whole[:2]


@pytest.mark.unit
def test_daily_anchors_break_on_the_utc_date() -> None:
    origin = datetime(2026, 1, 2, 22, 0, tzinfo=UTC)
    times = [origin + index * timedelta(hours=1) for index in range(5)]
    # 22:00, 23:00 on the 2nd; 00:00, 01:00, 02:00 on the 3rd.
    assert daily_anchors(times) == (0, 2)


@pytest.mark.unit
def test_daily_anchors_of_a_single_session() -> None:
    origin = datetime(2026, 1, 2, 9, 0, tzinfo=UTC)
    times = [origin + index * timedelta(minutes=15) for index in range(10)]
    assert daily_anchors(times) == (0,)


@pytest.mark.unit
def test_daily_anchors_reject_naive_timestamps() -> None:
    with pytest.raises(IndicatorInputError, match="naive"):
        daily_anchors([datetime(2026, 1, 2, 9, 0)])  # noqa: DTZ001


@pytest.mark.unit
def test_daily_anchors_compare_the_same_instant_across_zones() -> None:
    """A timestamp expressed in another offset must not invent a new session."""
    utc_times = [
        datetime(2026, 1, 2, 22, 0, tzinfo=UTC),
        datetime(2026, 1, 2, 23, 0, tzinfo=UTC),
    ]
    istanbul = timezone(timedelta(hours=3))
    shifted = [moment.astimezone(istanbul) for moment in utc_times]
    assert daily_anchors(shifted) == daily_anchors(utc_times) == (0,)


@pytest.mark.unit
def test_empty_series_is_empty() -> None:
    assert vwap([], [], [], []) == ()


@pytest.mark.unit
@pytest.mark.parametrize("anchors", ((1,), (0, 0), (0, 2, 1), (0, 99)))
def test_invalid_anchors_are_rejected(anchors: tuple[int, ...]) -> None:
    with pytest.raises(IndicatorInputError):
        vwap([11.0] * 3, [9.0] * 3, [10.0] * 3, [100.0] * 3, anchors=anchors)


@pytest.mark.unit
def test_negative_volume_is_rejected() -> None:
    with pytest.raises(IndicatorInputError, match="non-negative"):
        vwap([11.0], [9.0], [10.0], [-5.0])


@pytest.mark.unit
def test_vwap_lies_within_the_range_of_typical_prices() -> None:
    size = 50
    highs = [100.0 + (index % 7) for index in range(size)]
    lows = [95.0 + (index % 5) for index in range(size)]
    closes = [98.0 + (index % 6) for index in range(size)]
    volumes = [100.0 + (index % 9) * 10 for index in range(size)]

    prices = typical_prices(highs, lows, closes)
    result = vwap(highs, lows, closes, volumes)
    for index, value in enumerate(result):
        assert value is not None
        window = prices[: index + 1]
        assert min(window) - 1e-9 <= value <= max(window) + 1e-9
