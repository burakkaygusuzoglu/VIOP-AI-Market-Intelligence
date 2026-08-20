"""Database integration against a real PostgreSQL instance.

Connection settings come from the environment (see .env.example), not from the
unit-test fixture, so the same tests run locally, in Docker and in CI by
pointing POSTGRES_* at a live instance.

Skipped automatically when no PostgreSQL is reachable, so the suite stays
runnable without Docker. A skip is reported as a skip and is never counted as
a pass.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

from app.adapters.persistence.database import Database
from app.adapters.persistence.health import SqlAlchemyDatabaseHealth
from app.core.config import Settings


@pytest.fixture
def integration_settings() -> Settings:
    """Settings built from the ambient environment."""
    return Settings()


@pytest.fixture
async def database(integration_settings: Settings) -> AsyncIterator[Database]:
    db = Database(integration_settings.sqlalchemy_url)
    try:
        yield db
    finally:
        await db.dispose()


async def _require_reachable(database: Database, settings: Settings) -> None:
    probe = SqlAlchemyDatabaseHealth(database, settings.safe_database_target)
    result = await probe.check()
    if not result.reachable:
        pytest.skip(f"no PostgreSQL reachable at {settings.safe_database_target}")


@pytest.mark.integration
async def test_health_probe_reports_a_reachable_database(
    database: Database, integration_settings: Settings
) -> None:
    await _require_reachable(database, integration_settings)
    result = await SqlAlchemyDatabaseHealth(
        database, integration_settings.safe_database_target
    ).check()
    assert result.reachable is True
    assert result.latency_ms is not None
    assert "reachable" in result.detail


@pytest.mark.integration
async def test_engine_executes_a_real_query(
    database: Database, integration_settings: Settings
) -> None:
    await _require_reachable(database, integration_settings)
    async with database.engine.connect() as connection:
        value = await connection.scalar(text("SELECT 1"))
    assert value == 1


@pytest.mark.integration
async def test_server_reports_postgresql(
    database: Database, integration_settings: Settings
) -> None:
    """Confirms the target really is PostgreSQL, not another engine."""
    await _require_reachable(database, integration_settings)
    async with database.engine.connect() as connection:
        version = await connection.scalar(text("SELECT version()"))
    assert isinstance(version, str)
    assert "PostgreSQL" in version


@pytest.mark.integration
async def test_session_rolls_back_on_failure(
    database: Database, integration_settings: Settings
) -> None:
    """A failing unit of work must not leave a session in a dirty state."""
    await _require_reachable(database, integration_settings)
    with pytest.raises(RuntimeError):
        async with database.session() as session:
            await session.execute(text("SELECT 1"))
            raise RuntimeError("caller failed")

    async with database.session() as session:
        assert await session.scalar(text("SELECT 1")) == 1


@pytest.mark.integration
async def test_unreachable_database_is_reported_not_raised_and_fails_fast() -> None:
    """A dead database degrades the service; it must not crash or hang.

    Without a connect timeout this took over two minutes on Windows, which
    would stall the health endpoint and any readiness probe behind it.
    """
    settings = Settings(
        postgres_host="127.0.0.1",
        postgres_port=1,
        postgres_db="nope",
        db_connect_timeout_seconds=5,
    )
    database = Database(
        settings.sqlalchemy_url, connect_timeout=settings.db_connect_timeout_seconds
    )
    started = time.perf_counter()
    try:
        result = await SqlAlchemyDatabaseHealth(database, settings.safe_database_target).check()
    finally:
        await database.dispose()
    elapsed = time.perf_counter() - started

    assert result.reachable is False
    assert "unreachable" in result.detail
    assert elapsed < 30, f"probe took {elapsed:.1f}s; connect timeout is not applied"
