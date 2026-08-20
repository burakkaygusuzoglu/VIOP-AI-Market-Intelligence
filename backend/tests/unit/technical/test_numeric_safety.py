"""Numeric safety across the whole indicator set.

Written as part of the Phase 1 financial-calculation review. Three failure
modes are checked here rather than indicator by indicator, because each of them
is a property of the engine as a whole:

*No hidden NaN or infinity.* A NaN entering a series propagates silently
through every later comparison - ``nan > 0`` is ``False``, so a signal simply
never fires and nothing looks broken. Every division in the engine is guarded;
this proves the guards hold over a wide range of inputs, including degenerate
and extreme ones.

*No accidental zero.* A warm-up must read as absent, not as a measurement of
zero. Zero is a real, meaningful reading for MACD, DX and volume acceleration,
so the two states have to stay distinguishable.

*No silent overflow.* Prices far outside a plausible range must still produce
finite numbers or an explicit refusal - never ``inf`` presented as a level.
"""

from __future__ import annotations

import math

import pytest

from app.domain.common.enums import Timeframe
from app.domain.market.series import ValidatedCandleSeries
from app.domain.technical.engine import TechnicalConfig, compute_technicals
from app.domain.technical.types import IndicatorValues
from tests.factories import FIXTURE_SYMBOL, ohlcv_series

CONFIG = TechnicalConfig(ema_periods=(9, 20), sma_periods=(20,))


def _all_series(series: ValidatedCandleSeries) -> dict[str, IndicatorValues]:
    snapshot = compute_technicals(series, CONFIG)
    values: dict[str, IndicatorValues] = {
        "rsi": snapshot.rsi,
        "macd": snapshot.macd.macd,
        "macd_signal": snapshot.macd.signal,
        "macd_histogram": snapshot.macd.histogram,
        "true_range": snapshot.true_range,
        "atr": snapshot.atr,
        "bb_upper": snapshot.bollinger.upper,
        "bb_middle": snapshot.bollinger.middle,
        "bb_lower": snapshot.bollinger.lower,
        "plus_di": snapshot.directional.plus_di,
        "minus_di": snapshot.directional.minus_di,
        "dx": snapshot.directional.dx,
        "adx": snapshot.directional.adx,
        "vwap": snapshot.vwap,
        "volume_ma": snapshot.volume_ma,
        "relative_volume": snapshot.relative_volume,
        "volume_acceleration": snapshot.volume_acceleration,
    }
    for period, computed in snapshot.ema.items():
        values[f"ema_{period}"] = computed
    for period, computed in snapshot.sma.items():
        values[f"sma_{period}"] = computed
    return values


def _flat(size: int = 60, price: float = 100.0, volume: float = 1000.0) -> ValidatedCandleSeries:
    return ohlcv_series([price] * size, [price] * size, [price] * size, [volume] * size)


def _market(size: int = 60, scale: float = 1.0) -> ValidatedCandleSeries:
    closes = [(100.0 + (index % 11) * 2.0 - (index % 5)) * scale for index in range(size)]
    highs = [value + 1.5 * scale for value in closes]
    lows = [value - 1.5 * scale for value in closes]
    volumes = [float(500 + (index * 37) % 800) for index in range(size)]
    return ohlcv_series(highs, lows, closes, volumes)


MARKETS = {
    "trending up": ohlcv_series(
        [101.0 + i for i in range(60)],
        [99.0 + i for i in range(60)],
        [100.0 + i for i in range(60)],
        [1000.0] * 60,
    ),
    "trending down": ohlcv_series(
        [201.0 - i for i in range(60)],
        [199.0 - i for i in range(60)],
        [200.0 - i for i in range(60)],
        [1000.0] * 60,
    ),
    "flat": _flat(),
    "choppy": _market(),
    "zero volume": _flat(volume=0.0),
    "tiny prices": _market(scale=0.0001),
    "huge prices": _market(scale=1_000_000.0),
    "one candle": ohlcv_series([101.0], [99.0], [100.0], [1000.0]),
    "two candles": ohlcv_series([101.0, 102.0], [99.0, 100.0], [100.0, 101.0], [1000.0] * 2),
    "empty": ValidatedCandleSeries(candles=(), symbol=FIXTURE_SYMBOL, timeframe=Timeframe.M15),
}


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(MARKETS))
def test_no_indicator_ever_produces_nan_or_infinity(name: str) -> None:
    """The guard that keeps a silent NaN out of every downstream comparison."""
    for indicator, values in _all_series(MARKETS[name]).items():
        for index, value in enumerate(values):
            if value is None:
                continue
            assert math.isfinite(value), f"{name}: {indicator}[{index}] is {value!r}"


@pytest.mark.unit
@pytest.mark.parametrize("name", sorted(MARKETS))
def test_output_length_always_matches_the_input(name: str) -> None:
    series = MARKETS[name]
    for indicator, values in _all_series(series).items():
        assert len(values) == len(series), f"{name}: {indicator} is misaligned"


@pytest.mark.unit
def test_warm_up_is_a_leading_run_of_none_with_no_holes() -> None:
    """Once an indicator starts reporting, it does not stop.

    A hole after the first value would shift every later index for any caller
    that compacts the series, and it is how an unnoticed zero-fill or a dropped
    candle would show up.
    """
    for indicator, computed in _all_series(_market(60)).items():
        seen_value = False
        for index, value in enumerate(computed):
            if value is not None:
                seen_value = True
            elif seen_value:
                pytest.fail(f"{indicator}[{index}] is None after values had begun")


@pytest.mark.unit
def test_zero_is_still_available_as_a_real_reading() -> None:
    """Distinguishing warm-up from zero only matters if zero can occur."""
    flat = _all_series(_flat())
    assert flat["macd"][-1] == pytest.approx(0.0)
    assert flat["dx"][-1] == pytest.approx(0.0)
    assert flat["true_range"][-1] == pytest.approx(0.0)
    assert flat["volume_acceleration"][-1] == pytest.approx(0.0)


@pytest.mark.unit
def test_a_zero_volume_market_reports_absence_rather_than_a_price() -> None:
    values = _all_series(_flat(volume=0.0))
    assert all(value is None for value in values["vwap"])
    assert all(value is None for value in values["relative_volume"])
    # Price-based indicators are unaffected by the missing volume.
    assert values["rsi"][-1] is not None
    assert values["atr"][-1] is not None


@pytest.mark.unit
@pytest.mark.parametrize("scale", (0.0001, 1.0, 1_000_000.0))
def test_bounded_indicators_stay_bounded_at_any_price_scale(scale: float) -> None:
    values = _all_series(_market(60, scale=scale))
    for name in ("rsi", "plus_di", "minus_di", "dx", "adx"):
        for value in values[name]:
            if value is not None:
                assert 0.0 <= value <= 100.0, f"{name} left its range at scale {scale}"


@pytest.mark.unit
def test_price_scale_does_not_change_a_scale_invariant_reading() -> None:
    """RSI and ADX measure shape, so quoting in different units must not matter."""
    small = _all_series(_market(60, scale=1.0))
    large = _all_series(_market(60, scale=1000.0))
    for name in ("rsi", "adx"):
        for index, (one, other) in enumerate(zip(small[name], large[name], strict=True)):
            if one is None or other is None:
                assert one is None and other is None
                continue
            assert one == pytest.approx(other, rel=1e-9), f"{name}[{index}]"
