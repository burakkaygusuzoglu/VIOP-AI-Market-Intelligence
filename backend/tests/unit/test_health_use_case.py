"""GetSystemHealth aggregation logic."""

from __future__ import annotations

import pytest

from app.application.dto.system import HealthStatus
from app.application.use_cases.get_system_health import GetSystemHealth
from tests.conftest import FIXED_NOW, FakeClock, FakeDatabaseHealth


def _use_case(reachable: bool) -> GetSystemHealth:
    return GetSystemHealth(
        clock=FakeClock(),
        database=FakeDatabaseHealth(reachable=reachable),
        app_env="test",
        version="0.1.0-test",
    )


@pytest.mark.unit
async def test_health_is_ok_when_every_dependency_is_reachable() -> None:
    health = await _use_case(reachable=True).execute()
    assert health.status is HealthStatus.OK
    assert health.is_ok
    assert health.checked_at == FIXED_NOW
    assert [component.name for component in health.components] == ["database"]


@pytest.mark.unit
async def test_health_is_degraded_when_the_database_is_unreachable() -> None:
    health = await _use_case(reachable=False).execute()
    assert health.status is HealthStatus.DEGRADED
    assert not health.is_ok
    assert health.components[0].healthy is False


@pytest.mark.unit
async def test_time_comes_from_the_injected_clock() -> None:
    """Engines never read the wall clock directly, so replay can control time."""
    health = await _use_case(reachable=True).execute()
    assert health.checked_at == FIXED_NOW
