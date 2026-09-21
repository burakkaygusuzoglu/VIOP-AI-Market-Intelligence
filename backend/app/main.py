"""FastAPI application factory and composition root.

Concrete adapters are constructed here and injected into use cases. No other
module outside app/api/dependencies.py may wire infrastructure.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.adapters.market_data.csv_provider import CsvCandleTextParser
from app.adapters.performance.backtest_source import BacktestRunPerformanceSource
from app.adapters.performance.paper_source import SqlPaperPerformanceSource
from app.adapters.persistence.backtest_store import SqlAlchemyBacktestStore
from app.adapters.persistence.database import Database
from app.adapters.persistence.health import SqlAlchemyDatabaseHealth
from app.adapters.persistence.journal_store import SqlAlchemyJournalStore
from app.adapters.persistence.paper_store import SqlAlchemyPaperStore
from app.adapters.persistence.replay_store import SqlAlchemyReplayStore
from app.adapters.products.futures import FuturesSnapshotCodec
from app.adapters.system.clock import SystemClock
from app.api.limits import RequestSizeLimitMiddleware, bounded_validation_response
from app.api.middleware import RequestContextMiddleware
from app.api.providers import (
    build_screenshot_analyzer,
    build_synthesis_settings,
    build_synthesizer,
)
from app.api.routes.analysis import (
    get_candle_parser,
    get_synthesis_settings,
    get_synthesizer,
)
from app.api.routes.analysis import router as analysis_router
from app.api.routes.backtest import router as backtest_router
from app.api.routes.health import router as health_router
from app.api.routes.paper import router as paper_router
from app.api.routes.performance import router as performance_router
from app.api.routes.replay import router as replay_router
from app.api.routes.screenshots import get_analyzer
from app.api.routes.screenshots import router as screenshots_router
from app.application.performance.service import PerformanceService
from app.application.use_cases.get_liveness import GetLiveness
from app.application.use_cases.get_system_health import GetSystemHealth
from app.application.vision.decode import configure_image_safety
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger

DISCLAIMER = (
    "VIOP AI Market Intelligence is an analytical, educational and "
    "decision-support tool. It does not guarantee financial outcomes and it "
    "never places real orders. The user enters every real trade manually."
)


# ----------------------------------------------------------------------
# Phase 8 wires what Phase 7 deferred.
#
# Phase 7 left synthesis unrouted because nothing could supply it a *trusted*
# `SynthesisContext`: no market-data provider was composed, there was no
# analysis route, and accepting an analysis from the request body would have
# handed a client the ActionEnvelope - the Phase 6 provenance defect rebuilt on
# purpose.
#
# `POST /api/analysis` closes that. It builds a real deterministic analysis
# from user-supplied historical OHLCV using the Phase 1-4 engines, and the
# synthesis context is derived from *that* result, server-side. The client
# supplies candles, an account and risk settings; it cannot supply an
# indicator, a risk verdict or an action.
#
# Both AI providers are optional and are constructed here, once, from
# configuration:
#
#   * unconfigured -> the builder returns None -> a typed NOT_CONFIGURED state.
#     No vendor client is constructed and no request is made.
#   * configured   -> the real adapter, injected through the route's dependency
#     seam. The seam exists so tests can substitute a fake transport; it is no
#     longer the *only* thing that fills it, which was the Phase 8A finding.
# ----------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application with all dependencies wired."""
    settings = settings or get_settings()
    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        secrets=settings.secret_values(),
    )
    # Pillow's process-wide decompression-bomb backstop, set once here rather
    # than per request. Request-scoped mutation of a process global was
    # measured leaking across threads, so per-image limits are enforced by
    # `DecodePolicy` arithmetic instead - this only stops Pillow's own guard
    # being looser than anything this application would accept.
    configure_image_safety()
    logger = get_logger("app.startup")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = Database(
            settings.sqlalchemy_url,
            connect_timeout=settings.db_connect_timeout_seconds,
        )
        application.state.database = database
        # Phase 9 paper trading. The store and the snapshot codec are always
        # composed; the product resolver is not. No verified contract metadata
        # provider exists in this deployment (see `get_contract_metadata`), so
        # every new paper position is refused with PRODUCT_METADATA_UNAVAILABLE
        # rather than opened against assumed specifications.
        application.state.paper_store = SqlAlchemyPaperStore(database)
        application.state.product_codec = FuturesSnapshotCodec()
        application.state.product_resolver = None
        clock = SystemClock()
        application.state.clock = clock
        # Phase 10 journal and performance. Analytics read the append-only
        # ledger, never the mutable projection. The snapshot codec is handed to
        # the source so a still-open position's current mark can be replayed
        # from its own ledger and checked against the stored row.
        # Phase 12 backtesting. The store is composed; the product resolver is
        # not, for the same reason as Phase 9 - so a run that would need to
        # price a simulated trade is refused rather than assumed.
        backtest_store = SqlAlchemyBacktestStore(database)
        application.state.backtest_store = backtest_store
        application.state.journal_store = SqlAlchemyJournalStore(database)
        application.state.performance_source = SqlPaperPerformanceSource(
            database, application.state.product_codec
        )
        # Phase 11 replay. One more store and nothing else: replay drives the
        # engines above rather than owning any of their numbers, and the
        # service is assembled per request from these same objects.
        application.state.replay_store = SqlAlchemyReplayStore(database)
        # A run's outcomes are scoped at construction rather than by a filter,
        # so one Phase 10 service is built per run. The route may not reach an
        # adapter, so the composition root - which may - hands it this.
        journal_store = application.state.journal_store

        def backtest_performance(run_id: str) -> PerformanceService:
            return PerformanceService(
                source=BacktestRunPerformanceSource(
                    backtest_store, application.state.product_codec, run_id
                ),
                journal=journal_store,
                clock=clock,
            )

        application.state.backtest_performance = backtest_performance
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
    # Order matters. Starlette builds the stack with the *last* added
    # middleware outermost, so this reads inside-out:
    #
    #   CORS  ->  RequestSizeLimit  ->  RequestContext  ->  routes
    #
    # The size limiter sits inside CORS so a 413 still carries the headers a
    # browser needs in order to read it, and outside everything that could
    # assemble a body from `receive` - which is the whole point, since it can
    # only bound what it wraps.
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(
        RequestSizeLimitMiddleware,
        max_bytes=settings.max_request_bytes,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    @application.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        """422 bodies are bounded; see `bounded_validation_response`."""
        status, payload = bounded_validation_response(exc)
        return JSONResponse(status_code=status, content=payload)

    application.include_router(health_router, prefix=settings.api_prefix)
    application.include_router(screenshots_router, prefix=settings.api_prefix)
    application.include_router(analysis_router, prefix=settings.api_prefix)
    application.include_router(paper_router, prefix=settings.api_prefix)
    application.include_router(performance_router, prefix=settings.api_prefix)
    application.include_router(replay_router, prefix=settings.api_prefix)
    application.include_router(backtest_router, prefix=settings.api_prefix)

    # The composition root fills the provider seams. Overriding a dependency is
    # how the *application* injects its adapters here, not a test-only hook -
    # but a test that overrides these must not thereby be the only reason they
    # are ever filled, which is what `tests/unit/test_composition_root.py`
    # checks without any override in place.
    # An instance, not the class: FastAPI would otherwise treat the class as a
    # dependency callable and try to build its __init__ parameters as request
    # fields, which fails on `tzinfo | None`.
    candle_parser = CsvCandleTextParser()
    application.dependency_overrides[get_candle_parser] = lambda: candle_parser

    analyzer = build_screenshot_analyzer(settings)
    if analyzer is not None:
        application.dependency_overrides[get_analyzer] = lambda: analyzer

    synthesizer = build_synthesizer(settings)
    application.dependency_overrides[get_synthesizer] = lambda: synthesizer
    synthesis_settings = build_synthesis_settings(settings)
    application.dependency_overrides[get_synthesis_settings] = lambda: synthesis_settings

    logger.info(
        "providers composed",
        extra={
            "vision_configured": settings.vision_is_configured,
            "synthesis_configured": settings.synthesis_is_configured,
        },
    )
    return application


app = create_app()
