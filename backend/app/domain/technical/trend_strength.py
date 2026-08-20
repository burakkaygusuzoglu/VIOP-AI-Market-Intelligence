"""Trend strength: Directional Movement and ADX (master spec section 11).

ADX has the longest warm-up and the most index arithmetic of any indicator
here, which makes it the easiest one to get quietly wrong. The two smoothing
stages are written out separately rather than fused, so each stage's first
defined index is visible in the code and pinned by a test.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.technical.smoothing import wilder
from app.domain.technical.types import (
    IndicatorValues,
    compact,
    pad,
    require_finite,
    require_period,
    require_same_length,
)
from app.domain.technical.volatility import true_range


@dataclass(frozen=True, slots=True)
class DirectionalMovement:
    """+DI, -DI, DX and ADX, each aligned to the candles."""

    plus_di: IndicatorValues
    minus_di: IndicatorValues
    dx: IndicatorValues
    adx: IndicatorValues


def adx(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> DirectionalMovement:
    """Wilder's Average Directional Index.

    Convention, following *New Concepts in Technical Trading Systems*:

    * For each candle from index 1:
      ``up = high[i] - high[i-1]``, ``down = low[i-1] - low[i]``.
      ``+DM = up`` when ``up > down and up > 0``, otherwise ``0``.
      ``-DM = down`` when ``down > up and down > 0``, otherwise ``0``.
      An inside bar produces zero on both sides; the two are never both
      positive, which is what makes the directional split meaningful.
    * ``+DM``, ``-DM`` and True Range are each **Wilder-smoothed** over
      ``period``, seeded with the arithmetic mean of their first ``period``
      defined values. All three share the same alignment because all three
      start at candle index 1.
    * ``+DI = 100 * smoothed(+DM) / smoothed(TR)``, likewise ``-DI``. First
      defined at candle index ``period``.
    * ``DX = 100 * |+DI - -DI| / (+DI + -DI)``, same index.
    * ``ADX`` is a **second** Wilder smoothing, of DX over ``period``, seeded
      with the mean of the first ``period`` DX values. It therefore first
      appears at candle index ``2 * period - 1`` - index 27 for the default
      period 14. Reporting an ADX before that, by seeding the second stage
      from a single DX, is a common shortcut that overstates early trend
      strength.

    Degenerate cases:

    * ``smoothed(TR) == 0`` - a market with no range whatsoever across the
      whole window - leaves both DI values undefined by division. They are
      reported as ``0.0``: there is no directional movement to measure.
    * ``+DI + -DI == 0`` makes DX a ``0 / 0``. It is reported as ``0.0``, the
      limit of "no directional imbalance", which is also what the ADX of a
      frozen market should be.
    """
    require_period(period)
    length = require_same_length(highs=highs, lows=lows, closes=closes)
    require_finite(highs, name="highs")
    require_finite(lows, name="lows")
    require_finite(closes, name="closes")

    empty: IndicatorValues = (None,) * length
    if length < 2:
        return DirectionalMovement(plus_di=empty, minus_di=empty, dx=empty, adx=empty)

    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for index in range(1, length):
        up_move = highs[index] - highs[index - 1]
        down_move = lows[index - 1] - lows[index]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)

    _, dense_true_range = compact(true_range(highs, lows, closes))

    smoothed_plus = wilder(plus_dm, period)
    smoothed_minus = wilder(minus_dm, period)
    smoothed_range = wilder(dense_true_range, period)

    dense_plus_di: list[float | None] = []
    dense_minus_di: list[float | None] = []
    dense_dx: list[float | None] = []
    for positive, negative, ranges in zip(
        smoothed_plus, smoothed_minus, smoothed_range, strict=True
    ):
        if positive is None or negative is None or ranges is None:
            dense_plus_di.append(None)
            dense_minus_di.append(None)
            dense_dx.append(None)
            continue

        if ranges == 0.0:
            plus_value = 0.0
            minus_value = 0.0
        else:
            plus_value = 100.0 * positive / ranges
            minus_value = 100.0 * negative / ranges

        dense_plus_di.append(plus_value)
        dense_minus_di.append(minus_value)
        total = plus_value + minus_value
        dense_dx.append(0.0 if total == 0.0 else 100.0 * abs(plus_value - minus_value) / total)

    # The directional series all start at candle index 1.
    plus_di = pad(1, dense_plus_di, length)
    minus_di = pad(1, dense_minus_di, length)
    dx = pad(1, dense_dx, length)

    adx_values: IndicatorValues = empty
    if any(value is not None for value in dx):
        dx_offset, dx_dense = compact(dx)
        adx_values = pad(dx_offset, list(wilder(dx_dense, period)), length)

    return DirectionalMovement(plus_di=plus_di, minus_di=minus_di, dx=dx, adx=adx_values)
