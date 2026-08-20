"""Report process liveness.

Separate from GetSystemHealth on purpose. Liveness answers "is this process
alive"; readiness answers "can it serve requests correctly right now". Serving
one from the other lets a transient database outage look like a dead process,
which an orchestrator would answer by restarting a perfectly healthy
container - turning a recoverable dependency failure into an outage.
"""

from __future__ import annotations

from app.application.dto.system import ServiceLiveness
from app.application.ports.system import ClockPort


class GetLiveness:
    """Confirms the process is running. Touches no dependency."""

    def __init__(self, clock: ClockPort, app_env: str, version: str) -> None:
        self._clock = clock
        self._app_env = app_env
        self._version = version

    def execute(self) -> ServiceLiveness:
        return ServiceLiveness(
            app_env=self._app_env,
            version=self._version,
            checked_at=self._clock.now(),
        )
