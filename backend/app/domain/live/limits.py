"""What one live stream may hold and accept (Phase 13).

A stream runs for as long as it is left running, so anything that grows per
event grows forever unless it is bounded here. Every bound is refused or
disclosed when reached - never silently absorbed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

__all__ = ["LiveLimits"]


@dataclass(frozen=True, slots=True)
class LiveLimits:
    max_closed_candles: int = 2_500
    """Per timeframe. Equal to the analysis pipeline's own
    ``max_rows_per_timeframe``: a longer history could not be analysed anyway,
    and that cap was measured (structure analysis grows with the square of the
    row count). The oldest candle is trimmed first and the trim is counted."""

    max_symbol_length: int = 32
    max_price: Decimal = Decimal("1000000000")
    max_volume: Decimal = Decimal("1000000000000000")
    max_scale: int = 10
    """Decimal places. ``1E-9999`` is a finite Decimal and a denial of service
    for anything that formats it."""

    max_missing_sequences: int = 500
    """Recorded lost sequence numbers per timeframe. Beyond this the gap is
    still reported - as an overflowed gap - rather than enumerated."""

    max_recorded_issues: int = 200
    """Discontinuities, conflicts and rejections each keep this many recent
    records. Counts are exact; only the detail is bounded."""

    max_reconnects: int = 20
    """Per session. A source that reconnects without end is not a stream."""

    max_clock_skew: timedelta = timedelta(seconds=5)
    """How far a market event time may lie ahead of this system's receive time
    before it is treated as forged rather than as clock drift."""

    def __post_init__(self) -> None:
        for name in (
            "max_closed_candles",
            "max_symbol_length",
            "max_scale",
            "max_missing_sequences",
            "max_recorded_issues",
            "max_reconnects",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")
