"""API schemas for the health endpoint (master spec section 96)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.application.dto.system import ServiceLiveness, SystemHealth


class ComponentHealthSchema(BaseModel):
    """Health of a single dependency."""

    name: str
    healthy: bool
    detail: str
    latency_ms: float | None = None


class HealthResponse(BaseModel):
    """Service health as returned by GET /api/health."""

    status: str = Field(description="ok or degraded")
    app_env: str
    version: str
    checked_at: datetime
    components: list[ComponentHealthSchema]

    @classmethod
    def from_dto(cls, health: SystemHealth) -> HealthResponse:
        return cls(
            status=health.status.value,
            app_env=health.app_env,
            version=health.version,
            checked_at=health.checked_at,
            components=[
                ComponentHealthSchema(
                    name=component.name,
                    healthy=component.healthy,
                    detail=component.detail,
                    latency_ms=component.latency_ms,
                )
                for component in health.components
            ],
        )


class LivenessResponse(BaseModel):
    """Process liveness as returned by GET /api/health/live."""

    status: str = Field(description="always 'alive' while the process serves")
    app_env: str
    version: str
    checked_at: datetime

    @classmethod
    def from_dto(cls, liveness: ServiceLiveness) -> LivenessResponse:
        return cls(
            status="alive",
            app_env=liveness.app_env,
            version=liveness.version,
            checked_at=liveness.checked_at,
        )
