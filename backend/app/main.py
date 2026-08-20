"""FastAPI application factory and composition root.

Concrete adapters are constructed here and injected into use cases. No other
module outside app/api/dependencies.py may wire infrastructure.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.persistence.database import Database
from app.adapters.persistence.health import SqlAlchemyDatabaseHealth
from app.adapters.system.clock import SystemClock
from app.api.middleware import RequestContextMiddleware
from app.api.routes.health import router as health_router
from app.application.use_cases.get_liveness import GetLiveness
from app.application.use_cases.get_system_health import GetSystemHealth
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger

DISCLAIMER = (
    "VIOP AI Market Intelligence is an analytical, educational and "
    "decision-support tool. It does not guarantee financial outcomes and it "
    "never places real orders. The user enters every real trade manually."
)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application with all dependencies wired."""
    settings = settings or get_settings()
    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        secrets=settings.secret_values(),
    )
    logger = get_logger("app.startup")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = Database(
            settings.sqlalchemy_url,
            connect_timeout=settings.db_connect_timeout_seconds,
        )
        application.state.database = database
        clock = SystemClock()
        application.state.get_liveness = GetLiveness(
            clock=clock,
            app_env=settings.app_env,
            version=settings.app_version,
        )
        application.state.get_system_health = GetSystemHealth(
            clock=clock,
            database=SqlAlchemyDatabaseHealth(database, settings.safe_database_target),
            app_env=settings.app_env,
            version=settings.app_version,
        )
        logger.info(
            "application started",
            extra={
                "app_env": settings.app_env,
                "version": settings.app_version,
                "database_target": settings.safe_database_target,
                "execution_mode": "ANALYSIS_AND_SIGNALS_ONLY",
            },
        )
        try:
            yield
        finally:
            await database.dispose()
            logger.info("application stopped")

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=DISCLAIMER,
        lifespan=lifespan,
    )
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )
    application.include_router(health_router, prefix=settings.api_prefix)
    return application


app = create_app()
