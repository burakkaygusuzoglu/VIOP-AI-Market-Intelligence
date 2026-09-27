"""Session calendar port (Phase 15 Part 1).

The only way a statement about trading sessions may enter the system. An
implementation answers from an authoritative, dated calendar source or answers
``UNAVAILABLE``; it never falls back to a default window, a weekday rule or a
24/7 assumption. This build composes only the implementation that always
answers ``UNAVAILABLE``, because no verified calendar source is available.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.domain.sourcing.calendar import CalendarAnswer

__all__ = ["SessionCalendarProvider"]


@runtime_checkable
class SessionCalendarProvider(Protocol):
    async def session_at(self, symbol: str, at: datetime) -> CalendarAnswer:
        """Whether ``at`` falls in a trading session for ``symbol``."""
        ...
