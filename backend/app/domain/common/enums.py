"""Core vocabulary shared by every analytical engine.

This module is intentionally free of business logic. Classification,
scoring and calculation belong to the deterministic engines introduced in
later phases (master spec sections 11-19).
"""

from __future__ import annotations

from enum import IntEnum, StrEnum, unique


@unique
class Timeframe(StrEnum):
    """Supported analysis timeframes (master spec section 10)."""

    M1 = "1M"
    M5 = "5M"
    M15 = "15M"
    M30 = "30M"
    H1 = "1H"
    H4 = "4H"
    D1 = "1D"

    @property
    def minutes(self) -> int:
        """Duration of one candle of this timeframe, in minutes."""
        return _TIMEFRAME_MINUTES[self]


_TIMEFRAME_MINUTES: dict[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.M30: 30,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
    Timeframe.D1: 1440,
}


@unique
class Direction(StrEnum):
    """Direction of a position or a piece of evidence."""

    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


@unique
class TradeDecision(StrEnum):
    """Final decision states.

    WAIT and NO_TRADE are first-class, valid outcomes and are never treated
    as failures of the system (master spec sections 25 and 113).
    """

    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"
    NO_TRADE = "NO_TRADE"


@unique
class DataSourcePriority(IntEnum):
    """Trust ordering for conflicting values (master spec section 1).

    A lower value wins. Structured market data must never be overwritten by a
    visual estimate.
    """

    STRUCTURED_MARKET_DATA = 1
    USER_CONFIRMED = 2
    VALIDATED_CONTRACT_METADATA = 3
    SCREENSHOT_EXTRACTED = 4
    AI_VISUAL_INFERENCE = 5

    def wins_over(self, other: DataSourcePriority) -> bool:
        """Return True when a value from this source overrides ``other``."""
        return self.value < other.value
