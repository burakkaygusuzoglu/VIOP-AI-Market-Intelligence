"""Momentum indicators: RSI and MACD (master spec section 11).

Both are defined here in full rather than deferred to a library, because both
have popular variants that disagree numerically while sharing a name.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.technical.smoothing import ema, wilder
from app.domain.technical.types import (
    IndicatorValues,
    compact,
    pad,
    require_finite,
    require_period,
)

RSI_NEUTRAL = 50.0
"""RSI for a perfectly flat window - see ``rsi`` for why this value."""


def rsi(closes: Sequence[float], period: int = 14) -> IndicatorValues:
    """Wilder's Relative Strength Index.

    Convention, stated in full because "RSI 14" alone does not determine a
    number:

    * Changes are close-to-close, so the first change exists at index 1.
    * Gains are ``max(change, 0)``, losses are ``max(-change, 0)``. Both are
      non-negative, and a flat step contributes zero to each.
    * Average gain and average loss are **Wilder-smoothed** (``alpha = 1 /
      period``), seeded with the arithmetic mean of the first ``period``
      changes. Using an ordinary EMA here is the single most common RSI bug.
    * ``RS = avgGain / avgLoss`` and ``RSI = 100 - 100 / (1 + RS)``.
    * The first value therefore lands at candle index ``period`` - one later
      than an SMA of the same period, because index 0 yields no change.

    Degenerate windows, each chosen deliberately:

    * No losses, some gains -> ``100.0``. The limit of the formula as
      ``avgLoss`` approaches zero; also Wilder's own instruction.
    * No gains, some losses -> ``0.0``, by the same limit.
    * Neither gains nor losses, i.e. a perfectly constant price -> ``50.0``.
      Here ``RS`` is genuinely ``0 / 0`` and the formula says nothing. Of the
      three candidate answers, ``100`` (what a naive "no losses" branch
      returns) asserts maximum bullish momentum for a market that has not
      moved at all, which is actively misleading; ``None`` would claim the
      indicator is still warming up when it is not. ``50.0`` - no directional
      pressure in either direction - is the honest reading, and
      ``test_rsi_constant_price_is_neutral`` pins it.
    """
    require_period(period)
    require_finite(closes, name="closes")
    total = len(closes)
    if total < 2:
        return (None,) * total

    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, total):
        change = closes[index] - closes[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    average_gain = wilder(gains, period)
    average_loss = wilder(losses, period)

    values: list[float | None] = []
    for gain, loss in zip(average_gain, average_loss, strict=True):
        if gain is None or loss is None:
            values.append(None)
        elif loss == 0.0 and gain == 0.0:
            values.append(RSI_NEUTRAL)
        elif loss == 0.0:
            values.append(100.0)
        elif gain == 0.0:
            values.append(0.0)
        else:
            values.append(100.0 - 100.0 / (1.0 + gain / loss))

    # The change series starts at candle 1, so shift back onto candle indices.
    return pad(1, values, total)


@dataclass(frozen=True, slots=True)
class MacdResult:
    """MACD line, signal line and histogram, each aligned to the candles."""

    macd: IndicatorValues
    signal: IndicatorValues
    histogram: IndicatorValues


def macd(
    closes: Sequence[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> MacdResult:
    """Moving Average Convergence Divergence, 12 / 26 / 9.

    Convention:

    * ``macd = EMA(fast) - EMA(slow)``, both ordinary EMAs
      (``alpha = 2 / (period + 1)``) seeded with an SMA of their first
      ``period`` closes. First defined at index ``slow_period - 1``, where
      both inputs first exist - never earlier from a partially warmed slow EMA.
    * ``signal = EMA(signal_period)`` of the MACD line, computed over the MACD
      line's **defined** values only and seeded with their SMA. Feeding the
      warm-up ``None`` positions in as zeros - a common shortcut - would drag
      the signal line toward zero for its first several dozen bars.
    * ``histogram = macd - signal``, defined only where both exist.

    With the defaults the MACD line begins at index 25 and the signal line at
    index 33.
    """
    require_period(fast_period, name="fast_period")
    require_period(slow_period, name="slow_period")
    require_period(signal_period, name="signal_period")
    if fast_period >= slow_period:
        raise ValueError(
            f"fast_period ({fast_period}) must be shorter than slow_period ({slow_period})"
        )
    require_finite(closes, name="closes")

    total = len(closes)
    fast = ema(closes, fast_period)
    slow = ema(closes, slow_period)

    macd_line: list[float | None] = [
        None if fast_value is None or slow_value is None else fast_value - slow_value
        for fast_value, slow_value in zip(fast, slow, strict=True)
    ]

    offset, dense = compact(tuple(macd_line))
    signal_dense = ema(dense, signal_period) if dense else ()
    signal_line = pad(offset, list(signal_dense), total)

    histogram: list[float | None] = [
        None if macd_value is None or signal_value is None else macd_value - signal_value
        for macd_value, signal_value in zip(macd_line, signal_line, strict=True)
    ]

    return MacdResult(
        macd=tuple(macd_line),
        signal=tuple(signal_line),
        histogram=tuple(histogram),
    )
