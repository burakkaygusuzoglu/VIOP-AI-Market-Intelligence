"""Shared test fixtures.

Any market value appearing in tests is TEST_FIXTURE data. Fixture values must
never be read as current exchange specifications (master spec section 118).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from app.application.ports.system import DatabaseHealth
from app.core.config import Settings, get_settings
from app.core.runtime import configure_event_loop_policy

# Must run at import time, before pytest-asyncio creates an event loop.
configure_event_loop_policy()

FIXED_NOW = datetime(2026, 1, 2, 10, 30, tzinfo=UTC)


class FakeClock:
    """Deterministic ClockPort implementation."""

    def __init__(self, now: datetime = FIXED_NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeDatabaseHealth:
    """DatabaseHealthPort double with a configurable outcome."""

    def __init__(self, reachable: bool = True, detail: str = "test database") -> None:
        self._reachable = reachable
        self._detail = detail

    async def check(self) -> DatabaseHealth:
        return DatabaseHealth(
            reachable=self._reachable,
            detail=self._detail,
            latency_ms=1.5 if self._reachable else None,
        )


@pytest.fixture
def test_settings() -> Settings:
    """Settings for tests, independent of any .env file on the machine."""
    return Settings(
        app_env="test",
        app_version="0.1.0-test",
        log_level="INFO",
        log_format="json",
        postgres_host="localhost",
        postgres_port=5432,
        postgres_user="viop",
        postgres_password=SecretStr("fixture-password"),  # TEST_FIXTURE value
        postgres_db="viop_test",
        cors_origins=("http://localhost:5173",),
    )


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Keep the cached process settings from leaking between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
