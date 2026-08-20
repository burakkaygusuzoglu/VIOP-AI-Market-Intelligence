"""Wall-clock implementation of ClockPort."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Returns the real current time in UTC."""

    def now(self) -> datetime:
        return datetime.now(tz=UTC)
