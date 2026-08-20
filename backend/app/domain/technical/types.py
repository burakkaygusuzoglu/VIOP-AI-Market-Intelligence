"""Shared vocabulary for the deterministic technical engine.

Every indicator returns a tuple **the same length as its input**, aligned
index-for-index with the candles it was computed from. A position that cannot
be computed yet holds ``None``.

That alignment is the property the whole engine rests on. It means index ``i``
of any indicator always refers to candle ``i``, so no caller ever has to
reason about an offset - and an off-by-one in a window shows up as a shifted
value rather than as a silently mismatched pairing.

``None`` rather than zero, and never NaN: a zero would be read as a real
reading of zero, and a NaN propagates invisibly through later arithmetic.
Warm-up is a state, not a value.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

type IndicatorValues = tuple[float | None, ...]
"""One value per input candle. ``None`` means "not computable yet"."""


class IndicatorInputError(ValueError):
    """Raised when an indicator is called with unusable inputs.

    Failing loudly is deliberate. An indicator that quietly returns ``None``
    for a bad period, or that lets a NaN through, produces a plausible-looking
    chart built on nothing.
    """


def require_period(period: int, *, name: str = "period", minimum: int = 1) -> int:
    """Validate a lookback length."""
    if period < minimum:
        raise IndicatorInputError(f"{name} must be >= {minimum}, got {period}")
    return period


def require_finite(values: Sequence[float], *, name: str = "values") -> None:
    """Reject NaN and infinity at the door.

    Validated candle series cannot contain these - the Data Quality Engine
    blocks them - but the indicator functions are public and independently
    testable, so they defend their own contract instead of assuming a caller.
    """
    for index, value in enumerate(values):
        if not math.isfinite(value):
            raise IndicatorInputError(f"{name}[{index}] is not finite: {value!r}")


def require_same_length(**series: Sequence[float]) -> int:
    """Ensure parallel inputs line up, and return the common length."""
    lengths = {name: len(values) for name, values in series.items()}
    if len(set(lengths.values())) > 1:
        detail = ", ".join(f"{name}={length}" for name, length in lengths.items())
        raise IndicatorInputError(f"parallel series must be the same length: {detail}")
    return next(iter(lengths.values()), 0)


def compact(values: IndicatorValues) -> tuple[int, list[float]]:
    """Strip the leading ``None`` warm-up and return ``(offset, dense values)``.

    Used when an indicator is computed *from another indicator* - MACD's signal
    line, ADX's smoothing of DX. Holes after the first real value are rejected
    rather than skipped: they would silently shift every subsequent index.
    """
    offset = 0
    for value in values:
        if value is not None:
            break
        offset += 1

    dense: list[float] = []
    for index in range(offset, len(values)):
        value = values[index]
        if value is None:
            raise IndicatorInputError(
                f"gap at index {index}: a warm-up may only appear as a leading run of None"
            )
        dense.append(value)
    return offset, dense


def pad(offset: int, values: Sequence[float | None], total: int) -> IndicatorValues:
    """Re-align a densely computed result back onto the full candle index."""
    if offset + len(values) != total:
        raise IndicatorInputError(
            f"cannot align {len(values)} values at offset {offset} into length {total}"
        )
    return (None,) * offset + tuple(values)
