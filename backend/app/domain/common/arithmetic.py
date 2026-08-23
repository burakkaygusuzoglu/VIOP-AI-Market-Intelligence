"""Decimal arithmetic helpers for financial calculation.

Money is not analytics. Phase 1 indicators may use ``float`` because a
smoothed average is an estimate anyway; a P&L figure is not an estimate, and a
contract count is not a rounding preference. Everything in the futures and risk
engines is ``Decimal``, and these helpers exist so the three operations that
can silently go wrong - division, flooring and zero handling - go through one
audited place.

**Division runs in a pinned context.** ``decimal.getcontext()`` is thread-local
and any caller can change its precision or rounding. A risk engine whose answers
depend on ambient global state is not deterministic, so every ratio here is
computed inside ``localcontext`` with an explicit precision and rounding mode.

**Division by zero returns ``None``, never infinity and never zero.** A
percentage of a zero account is not "0%"; it is undefined, and saying so is the
only honest answer.
"""

from __future__ import annotations

from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext

FINANCIAL_PRECISION = 28
"""Significant digits for intermediate financial division.

Well beyond any instrument's quoted precision, so a ratio never loses
information that later quantization would have wanted.
"""

FINANCIAL_ROUNDING = ROUND_HALF_EVEN
"""Banker's rounding for intermediate division - the IEEE 754 default, and the
convention that does not bias a long series of roundings upward."""


def safe_ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """``numerator / denominator``, or ``None`` when it is undefined.

    Computed in a pinned context so the result cannot depend on whatever the
    ambient ``decimal`` context happened to be.
    """
    if denominator == 0:
        return None
    with localcontext() as context:
        context.prec = FINANCIAL_PRECISION
        context.rounding = FINANCIAL_ROUNDING
        try:
            return +(numerator / denominator)
        except (InvalidOperation, ArithmeticError):
            return None


def floor_divide(numerator: Decimal, denominator: Decimal) -> int | None:
    """How many whole ``denominator`` fit in ``numerator``, rounded **down**.

    This is the contract-count operation, and the rounding direction is the
    whole point. ``2.9`` contracts is two contracts: taking three would exceed
    the limit the division was measuring. Master spec section 42 makes the
    extreme case mandatory - when a single contract already exceeds the
    configured risk the answer is zero, not one.

    ``ROUND_FLOOR`` rather than truncation, so a negative numerator (a
    nonsensical input that should never reach here) floors toward negative
    rather than quietly toward zero.

    ``None`` when the denominator is zero.
    """
    if denominator == 0:
        return None
    with localcontext() as context:
        context.prec = FINANCIAL_PRECISION
        context.rounding = ROUND_FLOOR
        try:
            return int((numerator / denominator).to_integral_value(rounding=ROUND_FLOOR))
        except (InvalidOperation, ArithmeticError):
            return None


def normalise_zero(value: Decimal) -> Decimal:
    """Turn ``-0`` into ``0``.

    ``Decimal("-0") == Decimal("0")`` is true, so arithmetic is unaffected, but
    the two render differently. A break-even trade reported as ``-0.00`` reads
    as a loss to anyone scanning a column of numbers.
    """
    return value + Decimal(0) if value == 0 else value


def as_percent(ratio: Decimal | None) -> Decimal | None:
    """Convert a fraction to percentage points: ``0.025`` becomes ``2.5``.

    The two conventions are the most reliable source of factor-of-100 errors in
    financial code, so every value in this codebase states which it is in its
    own name: ``*_ratio`` is a fraction, ``*_percent`` is percentage points.
    """
    return None if ratio is None else ratio * 100
