"""Report service health.

This is the Phase 0 vertical slice through the architecture: an API route
calls a use case, the use case talks only to ports, and adapters supply the
implementations. It carries no financial logic - it exists to prove the
dependency direction works end to end before any analytical engine is built.
"""

from __future__ import annotations

from app.application.dto.system import ComponentHealth, HealthStatus, SystemHealth
from app.application.ports.system import ClockPort, DatabaseHealthPort


class GetSystemHealth:
    """Aggregates dependency probes into a single status."""

    def __init__(
        self,
        clock: ClockPort,
        database: DatabaseHealthPort,
        app_env: str,
        version: str,
    ) -> None:
        self._clock = clock
        self._database = database
        self._app_env = app_env
        self._version = version

    async def execute(self) -> SystemHealth:
        db = await self._database.check()
        components = (
            ComponentHealth(
                name="database",
                healthy=db.reachable,
                detail=db.detail,
                latency_ms=db.latency_ms,
            ),
        )
        status = (
            HealthStatus.OK
            if all(component.healthy for component in components)
            else HealthStatus.DEGRADED
        )
        return SystemHealth(
            status=status,
            app_env=self._app_env,
            version=self._version,
            checked_at=self._clock.now(),
            components=components,
        )
