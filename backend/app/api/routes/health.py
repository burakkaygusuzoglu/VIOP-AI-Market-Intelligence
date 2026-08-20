"""Health endpoints (master spec sections 96 and 97).

Three endpoints, three distinct questions:

``/api/health/live``   Is the process alive? Never touches a dependency, so a
                       database outage can never be mistaken for a dead
                       process and answered with a restart.
``/api/health/ready``  Can the service handle work right now? 503 when a
                       dependency is unreachable.
``/api/health``        The human and specification-facing aggregate. Same
                       payload and status as readiness.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.dependencies import LivenessUseCase, SystemHealthUseCase
from app.api.schemas.health import HealthResponse, LivenessResponse

router = APIRouter(tags=["system"])


@router.get(
    "/health/live",
    response_model=LivenessResponse,
    summary="Process liveness",
)
async def get_liveness(use_case: LivenessUseCase) -> LivenessResponse:
    """Always 200 while the process serves. Checks no dependency."""
    return LivenessResponse.from_dto(use_case.execute())


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    summary="Dependency readiness",
    responses={503: {"description": "A dependency is unreachable"}},
)
async def get_readiness(use_case: SystemHealthUseCase, response: Response) -> HealthResponse:
    """Report whether every dependency is usable. 503 when one is not."""
    health = await use_case.execute()
    if not health.is_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse.from_dto(health)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service and dependency health",
    responses={503: {"description": "A dependency is unreachable"}},
)
async def get_health(use_case: SystemHealthUseCase, response: Response) -> HealthResponse:
    """Aggregate health, equivalent to readiness."""
    health = await use_case.execute()
    if not health.is_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse.from_dto(health)
