"""HTTP contract of GET /api/health."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.api.routes.health import router as health_router
from app.application.use_cases.get_liveness import GetLiveness
from app.application.use_cases.get_system_health import GetSystemHealth
from tests.conftest import FakeClock, FakeDatabaseHealth


def _app(reachable: bool) -> FastAPI:
    """Build the API with test doubles in place of infrastructure adapters."""
    application = FastAPI()
    application.add_middleware(RequestContextMiddleware)
    application.include_router(health_router, prefix="/api")
    clock = FakeClock()
    application.state.get_liveness = GetLiveness(
        clock=clock,
        app_env="test",
        version="0.1.0-test",
    )
    application.state.get_system_health = GetSystemHealth(
        clock=clock,
        database=FakeDatabaseHealth(reachable=reachable),
        app_env="test",
        version="0.1.0-test",
    )
    return application


async def _get_health(reachable: bool) -> tuple[int, dict[str, Any], str]:
    transport = ASGITransport(app=_app(reachable))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")
    return response.status_code, response.json(), response.headers.get(REQUEST_ID_HEADER, "")


@pytest.mark.unit
async def test_health_returns_200_when_healthy() -> None:
    status_code, body, _ = await _get_health(reachable=True)
    assert status_code == 200
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0-test"
    assert body["app_env"] == "test"
    assert body["components"][0]["name"] == "database"


@pytest.mark.unit
async def test_health_returns_503_when_a_dependency_is_down() -> None:
    status_code, body, _ = await _get_health(reachable=False)
    assert status_code == 503
    assert body["status"] == "degraded"
    assert body["components"][0]["healthy"] is False


@pytest.mark.unit
async def test_every_response_carries_a_request_id() -> None:
    _, _, request_id = await _get_health(reachable=True)
    assert request_id


@pytest.mark.unit
async def test_supplied_request_id_is_preserved() -> None:
    transport = ASGITransport(app=_app(reachable=True))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health", headers={REQUEST_ID_HEADER: "caller-id"})
    assert response.headers[REQUEST_ID_HEADER] == "caller-id"


@pytest.mark.unit
async def test_application_factory_exposes_the_health_route() -> None:
    from app.core.config import Settings
    from app.main import create_app

    application = create_app(Settings(app_env="test"))
    assert "/api/health" in application.openapi()["paths"]


async def _get(path: str, reachable: bool) -> tuple[int, dict[str, Any]]:
    transport = ASGITransport(app=_app(reachable))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)
    return response.status_code, response.json()


@pytest.mark.unit
async def test_liveness_is_200_even_when_the_database_is_down() -> None:
    """A dependency outage must never look like a dead process.

    If liveness reported 503 here, an orchestrator would restart a healthy
    container on every database blip and turn a recoverable failure into an
    outage.
    """
    status_code, body = await _get("/api/health/live", reachable=False)
    assert status_code == 200
    assert body["status"] == "alive"


@pytest.mark.unit
async def test_liveness_reports_no_dependency_state() -> None:
    _, body = await _get("/api/health/live", reachable=True)
    assert "components" not in body


@pytest.mark.unit
async def test_readiness_is_200_when_dependencies_are_up() -> None:
    status_code, body = await _get("/api/health/ready", reachable=True)
    assert status_code == 200
    assert body["status"] == "ok"


@pytest.mark.unit
async def test_readiness_is_503_when_a_dependency_is_down() -> None:
    status_code, body = await _get("/api/health/ready", reachable=False)
    assert status_code == 503
    assert body["status"] == "degraded"


@pytest.mark.unit
async def test_all_three_health_routes_are_published() -> None:
    from app.core.config import Settings
    from app.main import create_app

    paths = create_app(Settings(app_env="test")).openapi()["paths"]
    assert {"/api/health", "/api/health/live", "/api/health/ready"} <= set(paths)
