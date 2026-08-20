"""Volume-derived metrics (master spec section 11, VOLUME).

Section 11 lists seven volume capabilities. **Four are available**:

``Raw Volume``
    Already available as ``Candle.volume`` - exact as ``Decimal`` and carried
    unchanged through the whole pipeline. It needs no derived function here.
``Volume Moving Average``
    Implemented below.
``Relative Volume``
    Implemented below.
``Volume Acceleration``
    Implemented below.

**Exactly three are deferred.** None is stubbed, because each needs machinery
that belongs to a later phase:

``Breakout Volume Confirmation``
    Needs a breakout, which needs support/resistance zones and market
    structure - master spec sections 12 and 13, Phase 2.
``Volume Divergence``
    Needs swing highs and lows to compare price and volume against - section
    12, Phase 2.
``Time-of-Day Normalized Volume``
    Needs the position of a candle within the trading session, which needs
    verified VIOP session hours. Those are mutable exchange facts under
    section 118 and are not available yet; inventing a session grid to make
    the metric compile would be exactly the failure that rule exists to
    prevent. Section 37, and the phase that obtains the session data.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.technical.smoothing import sma
from app.domain.technical.types import (
    IndicatorInputError,
    IndicatorValues,
    require_finite,
    require_period,
)


def volume_moving_average(volumes: Sequence[float], period: int = 20) -> IndicatorValues:
    """Simple moving average of volume over a trailing window.

    The baseline every other volume measure is expressed against. Simple
    rather than exponential, so "twice the average" means twice a plainly
    stated average of the last ``period`` candles.
    """
    require_period(period)
    _require_non_negative(volumes)
    return sma(volumes, period)


def relative_volume(volumes: Sequence[float], period: int = 20) -> IndicatorValues:
    """Volume as a multiple of its own trailing average.

    ``relative_volume[i] = volume[i] / volume_moving_average[i]``

    The average includes candle ``i`` itself, matching how the measure is
    normally read on a chart ("this bar is 2.1x average"). It is therefore
    slightly self-damping - a huge bar raises its own denominator - which is
    stated here so nobody later "fixes" it into a look-ahead by using a
    forward window.

    ``None`` during the moving average's warm-up, and ``None`` when the
    average is zero: a multiple of nothing is not defined, and emitting
    infinity or zero would both read as a real measurement.
    """
    require_period(period)
    _require_non_negative(volumes)
    averages = volume_moving_average(volumes, period)
    return tuple(
        None if average is None or average == 0.0 else volume / average
        for volume, average in zip(volumes, averages, strict=True)
    )


def volume_acceleration(volumes: Sequence[float], period: int = 20) -> IndicatorValues:
    """Rate of change of the volume moving average, candle over candle.

    ``acceleration[i] = volume_ma[i] / volume_ma[i-1] - 1``

    Expressed as a fraction, so ``0.05`` is "smoothed volume is 5% higher than
    the previous candle". The moving average is differenced rather than raw
    volume, because raw volume is far too noisy bar to bar for a second
    derivative to mean anything.

    ``None`` during warm-up, for the first candle after it (no predecessor to
    difference against), and when the previous average is zero.
    """
    require_period(period)
    _require_non_negative(volumes)
    averages = volume_moving_average(volumes, period)

    result: list[float | None] = [None] * len(averages)
    for index in range(1, len(averages)):
        current = averages[index]
        previous = averages[index - 1]
        if current is None or previous is None or previous == 0.0:
            continue
        result[index] = current / previous - 1.0
    return tuple(result)


def _require_non_negative(volumes: Sequence[float]) -> None:
    require_finite(volumes, name="volumes")
    for index, volume in enumerate(volumes):
        if volume < 0:
            raise IndicatorInputError(f"volumes[{index}] is negative: {volume!r}")
