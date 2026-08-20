"""Database engine and session lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    """Owns the async engine and hands out sessions.

    Created once at application startup and disposed at shutdown. Nothing
    outside the persistence adapter is allowed to import SQLAlchemy.
    """

    def __init__(self, url: str, echo: bool = False, connect_timeout: int = 5) -> None:
        self._engine: AsyncEngine = create_async_engine(
            url,
            echo=echo,
            pool_pre_ping=True,
            future=True,
            # Without this, a connection attempt to an unroutable host blocks
            # for the operating system's TCP timeout - measured at over two
            # minutes on Windows - which would hang the health endpoint and
            # any orchestrator readiness probe waiting on it.
            connect_args={"connect_timeout": connect_timeout},
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            autoflush=False,
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session, rolling back on failure."""
        async with self._session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        """Close all pooled connections."""
        await self._engine.dispose()
