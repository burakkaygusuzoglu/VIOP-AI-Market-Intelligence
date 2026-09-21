"""Shared support for Phase 9 integration tests against real PostgreSQL.

The schema is created by running the real Alembic migrations in a subprocess -
not by ``metadata.create_all`` - so every test exercises the migration that will
actually run in the container, including its append-only trigger.

A subprocess, because Alembic's ``env.py`` reconfigures logging from
``alembic.ini``, and doing that inside the test process would silence loggers
other tests assert on.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from app.adapters.contract_metadata.manual_provider import ManualContractMetadataProvider
from app.adapters.market_data.csv_provider import CsvCandleTextParser
from app.adapters.persistence.database import Database
from app.adapters.persistence.health import SqlAlchemyDatabaseHealth
from app.adapters.persistence.paper_store import SqlAlchemyPaperStore
from app.adapters.products.futures import FuturesProductResolver, FuturesSnapshotCodec
from app.application.paper.service import CreatePaperPosition, PaperTradingService
from app.core.config import Settings
from app.domain.common.enums import Direction, Timeframe
from app.domain.futures.contract import FuturesContract
from app.domain.paper import SimulationPolicy, TargetSpec
from app.domain.risk.sizing import AccountState, RiskMode, RiskPolicy
from tests.factories_futures import FIXTURE_SYMBOL
from tests.factories_paper import paper_contract

BACKEND = Path(__file__).resolve().parents[2]
DECISION = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)


class FixedClock:
    """A server clock well after every fixture bar, so every bar has closed."""

    def __init__(self, now: datetime = datetime(2026, 6, 1, tzinfo=UTC)) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


async def require_database(settings: Settings) -> None:
    database = Database(settings.sqlalchemy_url)
    try:
        result = await SqlAlchemyDatabaseHealth(database, settings.safe_database_target).check()
    finally:
        await database.dispose()
    if not result.reachable:
        pytest.skip(f"no PostgreSQL reachable at {settings.safe_database_target}")


def migrate_to_head() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"alembic upgrade failed:\n{completed.stderr[-2000:]}")


async def truncate(database: Database) -> None:
    """TRUNCATE is not a row-level DELETE, so the append-only trigger allows it.

    The Phase 10 journal table and the Phase 11 replay links reference positions,
    so they are emptied in the same statement - PostgreSQL refuses to truncate a
    referenced table alone. The Phase 12 tables join the list for the same
    reason: a backtest run references the replay dataset it read, so leaving
    them out would make every test that truncates a dataset fail once any run
    exists.
    """
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE backtest_position_events, backtest_positions, "
                "backtest_decisions, backtest_runs, "
                "replay_position_links, replay_sessions, replay_candles, "
                "replay_datasets, paper_journal_annotations, paper_position_events, "
                "paper_positions"
            )
        )


def service(
    database: Database,
    *,
    with_products: bool = True,
    clock: FixedClock | None = None,
    contract: FuturesContract | None = None,
) -> PaperTradingService:
    """The service as composed for tests.

    ``contract`` replaces what the metadata provider currently answers, which is
    how a test can ask what happens to an existing position when the *current*
    contract facts change underneath it.
    """
    resolver = (
        FuturesProductResolver(
            ManualContractMetadataProvider([contract if contract is not None else paper_contract()])
        )
        if with_products
        else None
    )
    return PaperTradingService(
        store=SqlAlchemyPaperStore(database),
        codec=FuturesSnapshotCodec(),
        resolver=resolver,
        parser=CsvCandleTextParser(),
        clock=clock or FixedClock(),
    )


BASE_COMMAND = CreatePaperPosition(
    idempotency_key="placeholder-key-000",
    symbol=FIXTURE_SYMBOL,
    direction=Direction.LONG,
    quantity=4,
    intended_entry=Decimal("100.00"),
    stop=Decimal("98.00"),
    targets=(TargetSpec(Decimal("104.00"), 2), TargetSpec(Decimal("106.00"), 2)),
    timeframe=Timeframe.H1,
    decision_time=DECISION,
    account=AccountState(equity=Decimal("100000")),
    risk=RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("1000")),
    policy=SimulationPolicy(),
)
"""Long 4 at 100, stop 98, targets 104 x2 and 106 x2 - the golden long scenario."""


def create_command(key: str, **changes: Any) -> CreatePaperPosition:
    return replace(BASE_COMMAND, idempotency_key=key, **changes)


def bars_csv(*rows: tuple[int, str, str, str, str]) -> str:
    """CSV of 1H bars, ``hour`` hours after the decision time."""
    lines = ["open_time,open,high,low,close,volume"]
    for hour, o, h, low, c in rows:
        stamp = (DECISION + timedelta(hours=hour)).isoformat()
        lines.append(f"{stamp},{o},{h},{low},{c},1000")
    return "\n".join(lines) + "\n"


ENTRY = (0, "100", "101", "99.50", "100.50")
TARGET_ONE = (1, "101", "104.50", "100.75", "104.25")
STOP_ON_REST = (2, "103", "103.50", "97.50", "98")


@pytest.fixture(scope="session")
def migrated() -> Iterator[Settings]:
    settings = Settings()
    import asyncio

    asyncio.run(require_database(settings))
    migrate_to_head()
    yield settings


@pytest.fixture
async def database(migrated: Settings) -> AsyncIterator[Database]:
    db = Database(migrated.sqlalchemy_url)
    await truncate(db)
    try:
        yield db
    finally:
        await db.dispose()
