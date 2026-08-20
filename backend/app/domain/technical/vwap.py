"""Volume Weighted Average Price, with explicit anchoring.

Master spec section 11 asks for VWAP now and for "architecture for Anchored
VWAP later". Those are the same calculation differing only in where the
accumulation restarts, so this module takes the anchor points as data. Session
VWAP today and a VWAP anchored to a swing high, a gap or an event in a later
phase are then the same function called with different anchors - no second
implementation, and no rewrite of the mathematics (master spec section 74).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime

from app.domain.technical.types import (
    IndicatorInputError,
    IndicatorValues,
    require_finite,
    require_same_length,
)


def daily_anchors(open_times: Sequence[datetime]) -> tuple[int, ...]:
    """Anchor indices at each change of UTC calendar date.

    **This is a development default, not a verified market fact.** A true
    session VWAP restarts at the exchange's session open, and VIOP session
    hours - including whether an evening session applies - are mutable
    exchange facts that master spec section 118 forbids assuming from memory.
    Until they are obtained from Borsa Istanbul, the UTC date boundary is used
    because it is unambiguous, deterministic and honest about what it is.

    When verified session hours arrive, only this function changes; ``vwap``
    itself is already independent of the choice.
    """
    anchors: list[int] = []
    previous_date = None
    for index, moment in enumerate(open_times):
        if moment.tzinfo is None:
            raise IndicatorInputError(f"open_times[{index}] is timezone-naive")
        current_date = moment.astimezone(UTC).date()
        if current_date != previous_date:
            anchors.append(index)
            previous_date = current_date
    return tuple(anchors)


def typical_prices(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
) -> tuple[float, ...]:
    """``(high + low + close) / 3`` - the standard VWAP price basis.

    The alternatives in circulation are the close alone and the HLC/HLCC
    weightings. The three-way typical price is the classic definition and is
    what an exchange-published VWAP is usually compared against.
    """
    require_same_length(highs=highs, lows=lows, closes=closes)
    require_finite(highs, name="highs")
    require_finite(lows, name="lows")
    require_finite(closes, name="closes")
    return tuple(
        (high + low + close) / 3.0 for high, low, close in zip(highs, lows, closes, strict=True)
    )


def vwap(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    anchors: Sequence[int] | None = None,
) -> IndicatorValues:
    """Volume weighted average price, accumulated from each anchor.

    Convention:

    * Price basis is the typical price ``(H + L + C) / 3``.
    * ``VWAP[i] = sum(TP * V) / sum(V)`` over the candles from the most recent
      anchor at or before ``i`` through ``i`` inclusive. It is cumulative
      within a window, not a rolling average, so it has **no warm-up**: the
      first candle of each window is its own VWAP.
    * ``anchors`` are start indices, ascending, and must include ``0``.
      Passing ``(0,)`` gives a whole-series VWAP; passing ``daily_anchors(...)``
      gives a session VWAP.

    Zero volume: while the cumulative volume since the anchor is zero, the
    result is ``None``. There is no volume to weight by, so any number would be
    invented - carrying the previous VWAP forward or emitting the typical price
    would both present a fabricated level as a traded average.

    Accumulation is a single forward pass using Neumaier compensated
    summation, so a long session does not accumulate rounding drift and the
    cost stays linear in the number of candles rather than quadratic.
    """
    length = require_same_length(highs=highs, lows=lows, closes=closes, volumes=volumes)
    require_finite(volumes, name="volumes")
    if any(volume < 0 for volume in volumes):
        raise IndicatorInputError("volumes must be non-negative")

    if anchors is None:
        anchors = (0,)
    if length == 0:
        return ()
    if not anchors or anchors[0] != 0:
        raise IndicatorInputError("anchors must be non-empty and start at index 0")
    if any(later <= earlier for earlier, later in zip(anchors, anchors[1:], strict=False)):
        raise IndicatorInputError("anchors must be strictly ascending")
    if anchors[-1] >= length:
        raise IndicatorInputError(f"anchor {anchors[-1]} is outside a series of {length}")

    prices = typical_prices(highs, lows, closes)
    anchor_set = set(anchors)

    result: list[float | None] = []
    volume_total = _Accumulator()
    weighted_total = _Accumulator()
    for index in range(length):
        if index in anchor_set:
            volume_total = _Accumulator()
            weighted_total = _Accumulator()
        volume_total.add(volumes[index])
        weighted_total.add(prices[index] * volumes[index])

        volume_sum = volume_total.total
        result.append(None if volume_sum == 0.0 else weighted_total.total / volume_sum)
    return tuple(result)


class _Accumulator:
    """Neumaier compensated running sum.

    A plain ``+=`` would still be deterministic, but over a long anchor window
    it accumulates rounding error in the low bits of a price average. This
    keeps the discarded low-order part and folds it back in, at constant cost
    per candle - unlike ``math.fsum`` over the whole window, which would make
    the pass quadratic.
    """

    __slots__ = ("_sum", "_compensation")

    def __init__(self) -> None:
        self._sum = 0.0
        self._compensation = 0.0

    def add(self, value: float) -> None:
        total = self._sum + value
        if math.fabs(self._sum) >= math.fabs(value):
            self._compensation += (self._sum - total) + value
        else:
            self._compensation += (value - total) + self._sum
        self._sum = total

    @property
    def total(self) -> float:
        return self._sum + self._compensation
