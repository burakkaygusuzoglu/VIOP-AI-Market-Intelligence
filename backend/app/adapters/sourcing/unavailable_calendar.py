"""The calendar this build has: none (Phase 15 Part 1).

No verified trading-session calendar source is available to this project.
Borsa İstanbul publishes session hours and changes them by announcement, and
holidays and special sessions are announced separately; none of that is
encoded here, and none is inferred from data. Every question is answered
``UNAVAILABLE``.
"""

from __future__ import annotations

from datetime import datetime

from app.domain.sourcing.calendar import CalendarAnswer, unavailable

__all__ = ["UnavailableSessionCalendar"]


class UnavailableSessionCalendar:
    """Implements ``SessionCalendarProvider`` by never claiming to know."""

    async def session_at(self, symbol: str, at: datetime) -> CalendarAnswer:
        return unavailable(
            symbol,
            at,
            "no verified trading-session calendar is configured; sessions, holidays "
            "and special sessions are not known",
        )
