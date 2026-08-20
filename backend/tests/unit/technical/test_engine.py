"""The batch indicator engine: alignment, configuration, and what it is not."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.common.enums import Timeframe
from app.domain.market.series import ValidatedCandleSeries
from app.domain.technical.engine import (
    TechnicalConfig,
    VwapAnchoring,
    compute_technicals,
)
from tests.factories import FIXTURE_SYMBOL, candles_from_closes, ohlcv_series


def _series(size: int = 80) -> ValidatedCandleSeries:
    closes = [100.0 + (index % 13) * 1.5 - (index % 7) * 0.8 for index in range(size)]
    highs = [value + 1.2 for value in closes]
    lows = [value - 1.1 for value in closes]
    volumes = [float(800 + (index * 53) % 900) for index in range(size)]
    return ohlcv_series(highs, lows, closes, volumes)


@pytest.mark.unit
def test_every_indicator_is_aligned_with_the_candles() -> None:
    """Index i of any series refers to candle i. Nothing is offset."""
    series = _series(80)
    snapshot = compute_technicals(series)

    lengths = [
        len(snapshot.rsi),
        len(snapshot.macd.macd),
        len(snapshot.macd.signal),
        len(snapshot.macd.histogram),
        len(snapshot.true_range),
        len(snapshot.atr),
        len(snapshot.bollinger.upper),
        len(snapshot.bollinger.middle),
        len(snapshot.bollinger.lower),
        len(snapshot.directional.plus_di),
        len(snapshot.directional.minus_di),
        len(snapshot.directional.dx),
        len(snapshot.adx),
        len(snapshot.vwap),
        len(snapshot.volume_ma),
        len(snapshot.relative_volume),
        len(snapshot.volume_acceleration),
        *[len(values) for values in snapshot.ema.values()],
        *[len(values) for values in snapshot.sma.values()],
    ]
    assert set(lengths) == {len(series)}


@pytest.mark.unit
def test_default_periods_are_the_ones_the_specification_names() -> None:
    """Master spec section 11: EMA 9/20/50/200, SMA 20/50/200, RSI 14, and so on."""
    config = TechnicalConfig()
    assert config.ema_periods == (9, 20, 50, 200)
    assert config.sma_periods == (20, 50, 200)
    assert config.rsi_period == 14
    assert (config.macd_fast, config.macd_slow, config.macd_signal) == (12, 26, 9)
    assert config.atr_period == 14
    assert config.adx_period == 14
    assert (config.bollinger_period, config.bollinger_multiplier) == (20, 2.0)


@pytest.mark.unit
def test_the_configuration_used_travels_with_the_result() -> None:
    """A backtest and a live run can be shown to have used the same settings."""
    config = TechnicalConfig(rsi_period=7)
    snapshot = compute_technicals(_series(40), config)
    assert snapshot.config is config
    assert snapshot.config.rsi_period == 7


@pytest.mark.unit
def test_requested_periods_are_the_ones_computed() -> None:
    config = TechnicalConfig(ema_periods=(5, 10), sma_periods=(30,))
    snapshot = compute_technicals(_series(60), config)
    assert set(snapshot.ema) == {5, 10}
    assert set(snapshot.sma) == {30}
    assert snapshot.ema[5][4] is not None
    assert snapshot.ema[5][3] is None


@pytest.mark.unit
def test_a_longer_period_warms_up_later() -> None:
    snapshot = compute_technicals(_series(80), TechnicalConfig(sma_periods=(20, 50)))
    assert snapshot.sma[20][19] is not None
    assert snapshot.sma[50][19] is None
    assert snapshot.sma[50][49] is not None


@pytest.mark.unit
def test_periods_longer_than_the_history_are_all_none_not_an_error() -> None:
    """EMA 200 on 80 candles is simply not available yet."""
    snapshot = compute_technicals(_series(80))
    assert all(value is None for value in snapshot.ema[200])
    assert all(value is None for value in snapshot.sma[200])


@pytest.mark.unit
def test_vwap_anchoring_is_configurable() -> None:
    """A multi-day series: daily anchoring resets, whole-series does not."""
    origin = datetime(2026, 1, 2, 22, 0, tzinfo=UTC)
    closes = [100.0 + index for index in range(12)]
    highs = [value + 1 for value in closes]
    lows = [value - 1 for value in closes]
    volumes = [100.0] * 12
    series = ValidatedCandleSeries(
        candles=tuple(
            ohlcv_series(highs, lows, closes, volumes, timeframe=Timeframe.H1, origin=origin)
        ),
        symbol=FIXTURE_SYMBOL,
        timeframe=Timeframe.H1,
    )

    daily = compute_technicals(series, TechnicalConfig(vwap_anchoring=VwapAnchoring.DAILY))
    whole = compute_technicals(series, TechnicalConfig(vwap_anchoring=VwapAnchoring.WHOLE_SERIES))

    assert daily.vwap[-1] is not None and whole.vwap[-1] is not None
    # The day rolls over at index 2, so the daily VWAP forgets the first bars
    # and sits closer to the recent, higher prices.
    assert daily.vwap[-1] > whole.vwap[-1]


@pytest.mark.unit
def test_an_empty_series_produces_empty_indicators_rather_than_failing() -> None:
    empty = ValidatedCandleSeries(candles=(), symbol=FIXTURE_SYMBOL, timeframe=Timeframe.M15)
    snapshot = compute_technicals(empty)
    assert snapshot.candle_count == 0
    assert snapshot.rsi == ()
    assert snapshot.vwap == ()
    assert snapshot.adx == ()


@pytest.mark.unit
def test_a_single_candle_produces_a_vwap_and_nothing_else() -> None:
    """VWAP is cumulative so it needs no history; everything else does."""
    series = ValidatedCandleSeries(
        candles=candles_from_closes(["100"]),
        symbol=FIXTURE_SYMBOL,
        timeframe=Timeframe.M15,
    )
    snapshot = compute_technicals(series)
    assert snapshot.vwap[0] is not None
    assert snapshot.rsi == (None,)
    assert snapshot.atr == (None,)
    assert snapshot.adx == (None,)


@pytest.mark.unit
def test_the_snapshot_carries_no_interpretation() -> None:
    """Phase 1 computes numbers. Labels, scores and signals are Phase 2 onward.

    A field like ``trend`` or ``regime`` appearing here would mean phase
    boundaries had leaked.
    """
    snapshot = compute_technicals(_series(60))
    fields = set(snapshot.__slots__)
    forbidden = {
        "trend",
        "regime",
        "bias",
        "signal",
        "score",
        "quality",
        "setup",
        "decision",
        "recommendation",
    }
    assert not (fields & forbidden)


@pytest.mark.unit
def test_engine_output_is_reproducible() -> None:
    series = _series(70)
    assert compute_technicals(series) == compute_technicals(series)


@pytest.mark.unit
def test_the_engine_only_accepts_a_validated_series() -> None:
    """The type signature is the guarantee - checked here so it stays one."""
    import inspect

    signature = inspect.signature(compute_technicals)
    assert signature.parameters["series"].annotation == "ValidatedCandleSeries"


@pytest.mark.unit
def test_daily_anchor_boundary_is_visible_in_the_result() -> None:
    origin = datetime(2026, 1, 2, 23, 0, tzinfo=UTC)
    closes = [100.0, 100.0, 200.0, 200.0]
    highs = list(closes)
    lows = list(closes)
    series = ValidatedCandleSeries(
        candles=tuple(
            ohlcv_series(highs, lows, closes, [100.0] * 4, timeframe=Timeframe.H1, origin=origin)
        ),
        symbol=FIXTURE_SYMBOL,
        timeframe=Timeframe.H1,
    )
    snapshot = compute_technicals(series)
    # 23:00 and 00:00 are different UTC dates, so index 1 restarts the anchor.
    assert snapshot.vwap[0] == pytest.approx(100.0)
    assert snapshot.vwap[1] == pytest.approx(100.0)
    assert snapshot.vwap[2] == pytest.approx(150.0)
    assert series.open_times[1] - series.open_times[0] == timedelta(hours=1)
