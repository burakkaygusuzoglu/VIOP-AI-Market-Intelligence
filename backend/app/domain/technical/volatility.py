"""Volatility: True Range, ATR, Bollinger Bands and historical volatility.

Master spec section 11. ``historical_volatility`` arrived in Phase 2 - Phase 1
deferred it because section 103 did not name it, and Phase 2 regime
classification needs a volatility context that ATR alone does not give.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.technical.smoothing import rolling_population_stdev, sma, wilder
from app.domain.technical.types import (
    IndicatorInputError,
    IndicatorValues,
    compact,
    pad,
    require_finite,
    require_period,
    require_same_length,
)


def true_range(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
) -> IndicatorValues:
    """Wilder's True Range.

    ``TR = max(high - low, |high - prevClose|, |low - prevClose|)``

    The two terms involving the previous close are what make this a *true*
    range: they absorb an overnight gap that a bare high-minus-low would miss
    entirely, which matters for a futures market that stops and restarts.

    **Index 0 is ``None``.** There is no previous close, so the true range is
    genuinely unknown. Substituting ``high - low`` there is a widespread
    shortcut, and it fabricates a value: on a gap-open first bar it understates
    the range, and it then propagates into the ATR seed. Leaving it undefined
    costs one bar of warm-up and keeps the first ATR honest.
    """
    length = require_same_length(highs=highs, lows=lows, closes=closes)
    require_finite(highs, name="highs")
    require_finite(lows, name="lows")
    require_finite(closes, name="closes")

    result: list[float | None] = [None] * length
    for index in range(1, length):
        previous_close = closes[index - 1]
        result[index] = max(
            highs[index] - lows[index],
            abs(highs[index] - previous_close),
            abs(lows[index] - previous_close),
        )
    return tuple(result)


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> IndicatorValues:
    """Average True Range, Wilder-smoothed.

    Convention:

    * True Range as above, undefined at index 0.
    * Wilder smoothing (``alpha = 1 / period``), seeded with the arithmetic
      mean of the first ``period`` defined true ranges - that is, of
      ``TR[1 .. period]``.
    * The first ATR therefore lands at candle index ``period``.

    Gap handling needs no special case: it is already inside the True Range
    definition, so an ATR computed across a session break widens as it should
    instead of being smoothed away.
    """
    require_period(period)
    length = require_same_length(highs=highs, lows=lows, closes=closes)
    ranges = true_range(highs, lows, closes)
    if length < 2:
        return (None,) * length

    offset, dense = compact(ranges)
    smoothed = wilder(dense, period)
    return pad(offset, list(smoothed), length)


@dataclass(frozen=True, slots=True)
class BollingerBands:
    """Upper, middle and lower band, each aligned to the candles."""

    upper: IndicatorValues
    middle: IndicatorValues
    lower: IndicatorValues


def bollinger_bands(
    closes: Sequence[float],
    period: int = 20,
    multiplier: float = 2.0,
) -> BollingerBands:
    """Bollinger Bands, 20 period and 2 standard deviations.

    Convention:

    * Middle band is a **simple** moving average of the close. Bollinger
      specified an SMA; substituting an EMA changes both the centre line and,
      because the deviation is measured against it, the band width.
    * Deviation is the **population** standard deviation over the same window
      (see ``rolling_population_stdev`` for why not the sample form).
    * ``upper = middle + multiplier * sigma``, ``lower = middle - multiplier *
      sigma``.
    * All three are defined from index ``period - 1``; ``None`` before that.
      A flat window gives ``sigma = 0`` and three coincident bands, which is
      the correct answer rather than a degenerate case.
    """
    require_period(period)
    require_finite(closes, name="closes")
    if multiplier <= 0:
        raise ValueError(f"multiplier must be positive, got {multiplier}")

    middle = sma(closes, period)
    deviation = rolling_population_stdev(closes, period)

    upper: list[float | None] = []
    lower: list[float | None] = []
    for centre, sigma in zip(middle, deviation, strict=True):
        if centre is None or sigma is None:
            upper.append(None)
            lower.append(None)
        else:
            upper.append(centre + multiplier * sigma)
            lower.append(centre - multiplier * sigma)

    return BollingerBands(upper=tuple(upper), middle=middle, lower=tuple(lower))


def log_returns(closes: Sequence[float]) -> IndicatorValues:
    """Continuously compounded returns, ``ln(close[i] / close[i-1])``.

    Log rather than simple returns: they are additive over time and symmetric
    in direction, so a +10% move followed by a -10% move sums to a small
    negative number instead of appearing to net to zero. Undefined at index 0,
    where there is no previous close.
    """
    require_finite(closes, name="closes")
    for index, close in enumerate(closes):
        if close <= 0:
            raise IndicatorInputError(f"closes[{index}] must be positive, got {close!r}")

    result: list[float | None] = [None] * len(closes)
    for index in range(1, len(closes)):
        result[index] = math.log(closes[index] / closes[index - 1])
    return tuple(result)


def historical_volatility(closes: Sequence[float], period: int = 20) -> IndicatorValues:
    """Standard deviation of log returns over a trailing window.

    **Per candle, and deliberately not annualised.**

    An annualised figure needs the number of trading periods in a year, and
    that is a property of the VIOP trading calendar - session hours, holidays,
    whether an evening session counts. Master spec section 118 forbids assuming
    such a fact from memory, and this one is unusually dangerous because the
    mistake is invisible: multiply by ``sqrt(252)`` for an instrument that
    trades a different number of sessions and the answer is still a
    plausible-looking percentage, merely wrong. The widely copied 252 describes
    US equities and has never been verified for this exchange.

    So this returns the standard deviation of a single candle's log return, as
    a fraction: ``0.012`` means 1.2% per candle of the series' own timeframe.
    That is comparable across instruments and across time, which is everything
    the regime engine needs. If a verified trading calendar ever arrives,
    annualising is one multiplication away - and that constant should arrive as
    a ``VerifiedValue`` recording its source.

    Convention:

    * Returns are ``ln(close[i] / close[i-1])``, first defined at index 1.
    * The deviation is the **population** form over ``period`` returns, the same
      convention as the Bollinger bands above, so the two volatility measures in
      this module cannot silently disagree.
    * First value at index ``period`` - one later than an SMA of the same
      period, because index 0 yields no return.
    * A constant price gives exactly ``0.0``, which is correct rather than
      degenerate.
    """
    require_period(period)
    require_finite(closes, name="closes")
    total = len(closes)
    if total < 2:
        return (None,) * total

    returns = log_returns(closes)
    offset, dense = compact(returns)
    deviation = rolling_population_stdev(dense, period)
    return pad(offset, list(deviation), total)
