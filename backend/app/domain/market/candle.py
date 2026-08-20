"""The OHLCV candle value object.

Scope note (Phase 0): this is a typed, immutable container only. OHLCV
integrity validation - missing candles, duplicates, impossible OHLC
relationships, stale feeds, suspicious spikes - is the Data Quality Engine
described in master spec section 40 and belongs to Phase 1. Nothing here
silently repairs, coerces or infers data.

Prices are ``Decimal``. Binary floating point is not acceptable for money,
tick rounding or P&L arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.common.enums import Timeframe


@dataclass(frozen=True, slots=True)
class Candle:
    """A single OHLCV bar.

    ``open_time`` is the timestamp at which the bar opened and must be
    timezone-aware. ``is_closed`` distinguishes a settled bar from a bar that
    is still forming; a forming bar must never be treated as a confirmed
    signal (master spec section 52).
    """

    symbol: str
    timeframe: Timeframe
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_closed: bool
    open_interest: Decimal | None = None
