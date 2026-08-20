"""Moving averages and the two smoothing conventions everything else uses.

Three distinct recursions appear in technical analysis and are routinely
confused with one another. They are separated here by name so that no
indicator can pick up the wrong one by accident:

``sma``
    Arithmetic mean of a trailing window. No memory beyond the window.

``ema``
    Exponential moving average with ``alpha = 2 / (period + 1)``. The
    convention used for MACD and for the EMA ribbon of master spec section 11.

``wilder``
    Wilder's smoothing with ``alpha = 1 / period``, sometimes published as
    RMA or SMMA. The convention Welles Wilder defined for RSI, ATR and ADX in
    *New Concepts in Technical Trading Systems*. Using ``ema`` for those would
    make every value wrong in a way that still looks like a plausible chart -
    a Wilder 14 decays like an EMA 27, not an EMA 14.

Shared seeding rule: both recursive averages are seeded with the arithmetic
mean of the first ``period`` inputs, placed at index ``period - 1``, with
``None`` before it. Seeding from the first value alone is the other common
choice; it converges to the same series but differs measurably for the first
few hundred bars, which is exactly the range a short backtest lives in.

Sums use ``math.fsum`` rather than ``sum``. Window sums are then exact
regardless of window length, so an SMA never drifts with accumulated rounding
and two runs over the same data always agree bit for bit.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from app.domain.technical.types import (
    IndicatorValues,
    require_finite,
    require_period,
)


def sma(values: Sequence[float], period: int) -> IndicatorValues:
    """Simple moving average over a trailing, inclusive window.

    ``result[i]`` is the mean of ``values[i - period + 1 .. i]``, so it uses
    candle ``i`` and the ``period - 1`` candles before it - never a candle
    after ``i``. Defined from index ``period - 1``; ``None`` before that.
    """
    require_period(period)
    require_finite(values)
    result: list[float | None] = [None] * len(values)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1 : index + 1]
        result[index] = math.fsum(window) / period
    return tuple(result)


def _recursive_average(
    values: Sequence[float],
    period: int,
    alpha: float,
) -> IndicatorValues:
    """Shared body of ``ema`` and ``wilder``; only ``alpha`` differs."""
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return tuple(result)

    current = math.fsum(values[:period]) / period
    result[period - 1] = current
    for index in range(period, len(values)):
        current = current + alpha * (values[index] - current)
        result[index] = current
    return tuple(result)


def ema(values: Sequence[float], period: int) -> IndicatorValues:
    """Exponential moving average, ``alpha = 2 / (period + 1)``.

    Seeded with the SMA of the first ``period`` values at index
    ``period - 1``. Each later value depends only on the current input and the
    previous EMA, so no future information can enter.
    """
    require_period(period)
    require_finite(values)
    return _recursive_average(values, period, alpha=2.0 / (period + 1))


def wilder(values: Sequence[float], period: int) -> IndicatorValues:
    """Wilder's smoothing, ``alpha = 1 / period``.

    The convention required by RSI, ATR and ADX. Seeded with the arithmetic
    mean of the first ``period`` values at index ``period - 1``, matching
    Wilder's published worked examples.
    """
    require_period(period)
    require_finite(values)
    return _recursive_average(values, period, alpha=1.0 / period)


def rolling_population_stdev(values: Sequence[float], period: int) -> IndicatorValues:
    """Population standard deviation over a trailing, inclusive window.

    Population (divide by ``N``), not sample (``N - 1``). This is the
    convention Bollinger specified and the one charting platforms implement;
    the sample form would widen every band by a factor of
    ``sqrt(N / (N - 1))`` - about 2.6% at the standard 20 period, enough to
    move a band touch across a decision boundary.

    Computed from the window's own mean each step, never from a mean of the
    whole dataset, which would leak future information into early values.
    """
    require_period(period)
    require_finite(values)
    result: list[float | None] = [None] * len(values)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1 : index + 1]
        mean = math.fsum(window) / period
        variance = math.fsum((value - mean) ** 2 for value in window) / period
        result[index] = math.sqrt(variance)
    return tuple(result)
