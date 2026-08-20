"""Database reachability probe implementing DatabaseHealthPort."""

from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.adapters.persistence.database import Database
from app.application.ports.system import DatabaseHealth
from app.core.logging import get_logger

_logger = get_logger("app.adapters.persistence.health")


class SqlAlchemyDatabaseHealth:
    """Runs `SELECT 1` and reports the outcome without raising.

    ``target`` is a host/database description that is safe to display; the
    connection URL itself is never exposed because it carries a password.
    """

    def __init__(self, database: Database, target: str) -> None:
        self._database = database
        self._target = target

    async def check(self) -> DatabaseHealth:
        started = time.perf_counter()
        try:
            async with self._database.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError) as exc:
            # The response carries only the exception type: a driver message
            # can contain the connection URL, and the URL carries a password.
            # The full cause goes to the logs, where redaction is applied.
            _logger.warning(
                "database probe failed",
                extra={"target": self._target, "error_type": type(exc).__name__},
                exc_info=exc,
            )
            return DatabaseHealth(
                reachable=False,
                detail=f"{self._target} unreachable: {type(exc).__name__}",
            )
        latency_ms = (time.perf_counter() - started) * 1000
        return DatabaseHealth(
            reachable=True,
            detail=f"{self._target} reachable",
            latency_ms=round(latency_ms, 2),
        )
