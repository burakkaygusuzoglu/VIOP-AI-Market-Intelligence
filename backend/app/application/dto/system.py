"""Application-layer data transfer objects for system status."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique


@unique
class HealthStatus(StrEnum):
    """Overall service health."""

    OK = "ok"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    """Health of one dependency."""

    name: str
    healthy: bool
    detail: str
    latency_ms: float | None = None


@dataclass(frozen=True, slots=True)
class SystemHealth:
    """Aggregated service health at a point in time."""

    status: HealthStatus
    app_env: str
    version: str
    checked_at: datetime
    components: tuple[ComponentHealth, ...]

    @property
    def is_ok(self) -> bool:
        return self.status is HealthStatus.OK


@dataclass(frozen=True, slots=True)
class ServiceLiveness:
    """Evidence that the process itself is running and serving.

    Deliberately carries no dependency state: a live process with an
    unreachable database is degraded, not dead, and must not be restarted.
    """

    app_env: str
    version: str
    checked_at: datetime
