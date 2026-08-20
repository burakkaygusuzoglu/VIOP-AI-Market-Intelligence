"""System-level ports used by the health slice."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class ClockPort(Protocol):
    """Time source.

    Injected rather than read directly, so that replay and backtest execution
    can supply historical time without any engine being able to observe a
    timestamp from the future.
    """

    def now(self) -> datetime:
        """Return the current timezone-aware time."""
        ...


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    """Outcome of a database reachability probe."""

    reachable: bool
    detail: str
    latency_ms: float | None = None


@runtime_checkable
class DatabaseHealthPort(Protocol):
    """Probes whether the configured database is reachable."""

    async def check(self) -> DatabaseHealth:
        """Return the current database reachability, never raising."""
        ...
