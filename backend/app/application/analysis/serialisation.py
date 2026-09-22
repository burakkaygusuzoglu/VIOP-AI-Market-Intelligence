"""Candles back to the canonical CSV the analysis pipeline reads.

Moved here from the Phase 11 replay service in Phase 13, unchanged, because live
observation needs exactly the same step: a set of confirmed candles handed to
``run_analysis`` through the Phase 1 parser. Two copies of a serialiser are two
ways for a price to be spelled differently on its way into the same engine.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.market.candle import Candle

__all__ = ["candles_to_csv"]


def candles_to_csv(candles: Sequence[Candle]) -> str:
    """Canonical CSV for a prefix of confirmed candles.

    Amounts are written with ``format(value, "f")`` and read back as ``Decimal``,
    so the round trip through the Phase 1 parser is exact. Serialising rather
    than bypassing the parser is deliberate: it keeps every caller on the same
    validated path as every other analysis in the system.
    """
    lines = ["open_time,open,high,low,close,volume"]
    for candle in candles:
        volume = "" if candle.volume is None else format(candle.volume, "f")
        lines.append(
            ",".join(
                (
                    candle.open_time.isoformat(),
                    format(candle.open, "f"),
                    format(candle.high, "f"),
                    format(candle.low, "f"),
                    format(candle.close, "f"),
                    volume,
                )
            )
        )
    return "\n".join(lines) + "\n"
