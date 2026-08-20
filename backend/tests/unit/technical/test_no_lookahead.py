"""Proof that no indicator can see the future.

The property, stated precisely: the value an indicator reports at index ``i``
must depend only on candles ``0..i``. Equivalently, extending a dataset with
later candles must not change any value already computed.

This is the invariant that separates a usable backtest from a fantasy. An
indicator that centres its window, backfills, or normalizes against the whole
dataset's statistics will still produce a beautiful equity curve - one that
cannot be reproduced in live trading, because live trading has no future
candles to consult.

The test is structural rather than case-by-case: it runs the entire indicator
set over a prefix, then over the full series, and compares. A leak anywhere in
any indicator changes a historical value and fails here.
"""

from __future__ import annotations

import math

import pytest

from app.domain.market.series import ValidatedCandleSeries
from app.domain.technical.engine import TechnicalConfig, VwapAnchoring, compute_technicals
from app.domain.technical.types import IndicatorValues
from tests.factories import ohlcv_series

PREFIX = 90
TOTAL = 150


REVERSAL_AT = 75
"""Fixed, and deliberately not derived from ``size``.

Candle ``i`` must be the same candle whatever length is requested, or the test
compares two different markets and proves nothing about look-ahead.
"""


def _market(size: int) -> ValidatedCandleSeries:
    """A varied but deterministic series: trend, chop and a reversal."""
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []
    price = 100.0
    for index in range(size):
        # Deterministic, non-monotonic, and it changes direction partway
        # through, so a leak has something to leak.
        drift = 0.4 if index < REVERSAL_AT else -0.6
        wiggle = ((index * 37) % 11 - 5) * 0.3
        price = max(1.0, price + drift + wiggle)
        closes.append(round(price, 2))
        highs.append(round(price + 0.8 + (index % 5) * 0.1, 2))
        lows.append(round(price - 0.8 - (index % 7) * 0.1, 2))
        volumes.append(float(500 + (index * 91) % 700))
    return ohlcv_series(highs, lows, closes, volumes)


def _named_series(config: TechnicalConfig, size: int) -> dict[str, IndicatorValues]:
    snapshot = compute_technicals(_market(size), config)
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
    for period, series in snapshot.ema.items():
        values[f"ema_{period}"] = series
    for period, series in snapshot.sma.items():
        values[f"sma_{period}"] = series
    return values


@pytest.fixture(scope="module")
def prefix_and_full() -> tuple[dict[str, IndicatorValues], dict[str, IndicatorValues]]:
    """Whole-series VWAP anchoring, so even VWAP has no daily reset to hide behind."""
    config = TechnicalConfig(
        ema_periods=(9, 20, 50),
        sma_periods=(20, 50),
        vwap_anchoring=VwapAnchoring.WHOLE_SERIES,
    )
    return _named_series(config, PREFIX), _named_series(config, TOTAL)


@pytest.mark.unit
def test_the_two_datasets_really_do_differ(
    prefix_and_full: tuple[dict[str, IndicatorValues], dict[str, IndicatorValues]],
) -> None:
    """Guard against the test passing because nothing was appended."""
    prefix, full = prefix_and_full
    assert len(prefix["rsi"]) == PREFIX
    assert len(full["rsi"]) == TOTAL
    assert full["rsi"][PREFIX] is not None


@pytest.mark.unit
def test_appending_future_candles_changes_no_historical_value(
    prefix_and_full: tuple[dict[str, IndicatorValues], dict[str, IndicatorValues]],
) -> None:
    """The core no-look-ahead assertion, across every indicator at once."""
    prefix, full = prefix_and_full
    assert set(prefix) == set(full)

    for name in sorted(prefix):
        short, long = prefix[name], full[name]
        for index in range(PREFIX):
            before, after = short[index], long[index]
            if before is None:
                assert after is None, (
                    f"{name}[{index}]: was undefined on {PREFIX} candles but became "
                    f"{after!r} once later candles were appended"
                )
                continue
            assert after is not None, f"{name}[{index}]: lost its value when data was appended"
            assert before == after or math.isclose(before, after, rel_tol=0.0, abs_tol=0.0), (
                f"{name}[{index}]: {before!r} -> {after!r} after appending future candles"
            )


@pytest.mark.unit
def test_every_indicator_was_actually_exercised(
    prefix_and_full: tuple[dict[str, IndicatorValues], dict[str, IndicatorValues]],
) -> None:
    """A vacuous comparison of all-``None`` series would prove nothing."""
    prefix, _ = prefix_and_full
    for name, values in prefix.items():
        defined = sum(1 for value in values if value is not None)
        assert defined > 5, f"{name} produced only {defined} defined values in the prefix"


@pytest.mark.unit
def test_results_are_bit_for_bit_reproducible() -> None:
    """Determinism: no clock, no randomness, no global state."""
    config = TechnicalConfig(ema_periods=(9,), sma_periods=(20,))
    first = _named_series(config, 80)
    second = _named_series(config, 80)
    assert first == second


@pytest.mark.unit
def test_a_growing_window_reproduces_the_final_value_at_every_step() -> None:
    """The replay simulation: feed one candle at a time and check the last value.

    This is how live mode will consume the engine, so it is the strongest
    statement of parity available without a live feed (master spec section 74).
    """
    config = TechnicalConfig(ema_periods=(9,), sma_periods=(20,))
    full = compute_technicals(_market(TOTAL), config)

    for size in (40, 60, 95, 120, TOTAL):
        stepwise = compute_technicals(_market(size), config)
        assert stepwise.rsi[size - 1] == full.rsi[size - 1]
        assert stepwise.atr[size - 1] == full.atr[size - 1]
        assert stepwise.directional.adx[size - 1] == full.directional.adx[size - 1]
        assert stepwise.macd.macd[size - 1] == full.macd.macd[size - 1]
