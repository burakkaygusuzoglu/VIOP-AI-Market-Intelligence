"""Alembic environment.

The connection URL comes from application settings, never from alembic.ini,
so that credentials stay in the environment.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.adapters.persistence import (
    journal_models,  # noqa: F401  (registers Phase 10 table)
    paper_models,  # noqa: F401  (registers Phase 9 tables)
    replay_models,  # noqa: F401  (registers Phase 11 tables)
)
from app.adapters.persistence.base import Base
from app.core.config import get_settings
from app.core.runtime import configure_event_loop_policy

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL without connecting to a database."""
    context.configure(
        url=settings.sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    # Platform trap 1 (CLAUDE.md): psycopg's async mode cannot run on Windows'
    # default ProactorEventLoop. A no-op on Linux, where the container runs this.
    configure_event_loop_policy()
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
