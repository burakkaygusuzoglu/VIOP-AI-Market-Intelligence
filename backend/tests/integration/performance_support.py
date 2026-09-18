"""Shared support for Phase 10 integration tests.

Positions are created through the real Phase 9 service against real PostgreSQL,
so the ledger these tests analyse is the one the simulator actually writes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.adapters.performance.paper_source import SqlPaperPerformanceSource
from app.adapters.persistence.database import Database
from app.adapters.persistence.journal_store import SqlAlchemyJournalStore
from app.adapters.products.futures import FuturesSnapshotCodec
from app.application.performance.service import PerformanceService
from app.domain.common.enums import Direction
from app.domain.paper import FeeMode, FeePolicy, SameBarPolicy, SimulationPolicy
from tests.integration.paper_support import (
    FixedClock,
    bars_csv,
    create_command,
)
from tests.integration.paper_support import (
    service as paper_service,
)

D = Decimal
DECISION = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
CLOCK = FixedClock(datetime(2026, 6, 1, tzinfo=UTC))

# Bars that drive the standard Phase 9 long position (entry 100, stop 98,
# targets 104 x2 and 106 x2) to each outcome. Hand-checked amounts in the tests.
ENTRY = (0, "100", "101", "99.50", "100.50")
TARGET_ONE = (1, "101", "104.50", "100.75", "104.25")
BOTH_TARGETS = (1, "101", "106.50", "100.75", "106.25")
STOP_ON_REST = (2, "103", "103.50", "97.50", "98")
STOP_FROM_ENTRY = (1, "100.50", "100.75", "97.50", "98")
QUIET = (1, "100.50", "101", "100", "100.75")

FEES = SimulationPolicy(fees=FeePolicy(mode=FeeMode.USER_DEFINED_PER_UNIT, per_unit=D("2")))
NO_FEES = SimulationPolicy()
HALT = SimulationPolicy(same_bar=SameBarPolicy.HALT)


def performance_service(database: Database) -> PerformanceService:
    """The service as composed for tests, over the same adapters as production."""
    return PerformanceService(
        source=SqlPaperPerformanceSource(database, FuturesSnapshotCodec()),
        journal=SqlAlchemyJournalStore(database),
        clock=CLOCK,
    )


async def make_position(
    database: Database,
    key: str,
    *,
    bars: tuple[tuple[int, str, str, str, str], ...] = (ENTRY, TARGET_ONE, STOP_ON_REST),
    policy: SimulationPolicy = NO_FEES,
    direction: Direction = Direction.LONG,
    **changes: Any,
) -> str:
    """One paper position driven through real bars. Returns its id."""
    svc = paper_service(database, clock=CLOCK)
    command = create_command(key, policy=policy, direction=direction, **changes)
    created = await svc.create(command)
    position_id = created.stored.position_id
    if bars:
        await svc.observe(position_id, bars_csv(*bars), "bars.csv")
    return position_id


def short_changes() -> dict[str, Any]:
    """The mirror plan: short 4 at 100, stop 102, targets 96 x2 and 94 x2."""
    from app.domain.paper import TargetSpec

    return {
        "direction": Direction.SHORT,
        "stop": D("102.00"),
        "targets": (TargetSpec(D("96.00"), 2), TargetSpec(D("94.00"), 2)),
    }


SHORT_TARGET_ONE = (1, "99", "99.50", "95.50", "96")
SHORT_STOP_ON_REST = (2, "97", "102.50", "96.50", "102")
