"""Shared support for Phase 11 replay tests against real PostgreSQL.

The service is composed here exactly as the API composes it, with one
deliberate difference: a **test-only** contract metadata provider. Production
composes none, so a replay position there is refused for want of verified
product facts - which is the point of a separate test that proves it.

The clock passed in is the *process* clock, used only for audit stamps. Nothing
financial reads it: replay time comes from the session cursor, and these tests
set it well after every fixture bar precisely so that a leak would show up as a
future candle rather than as a plausible one.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.adapters.contract_metadata.manual_provider import ManualContractMetadataProvider
from app.adapters.market_data.csv_provider import CsvCandleTextParser
from app.adapters.performance.paper_source import SqlPaperPerformanceSource
from app.adapters.persistence.database import Database
from app.adapters.persistence.journal_store import SqlAlchemyJournalStore
from app.adapters.persistence.paper_store import SqlAlchemyPaperStore
from app.adapters.persistence.replay_store import SqlAlchemyReplayStore
from app.adapters.products.futures import FuturesProductResolver, FuturesSnapshotCodec
from app.application.performance.service import PerformanceService
from app.application.replay.service import (
    CreateReplaySession,
    ReplayService,
    TimeframeUpload,
)
from app.domain.common.enums import Timeframe
from app.domain.futures.contract import FuturesContract
from tests.factories_paper import paper_contract
from tests.factories_replay import BASE, FIXTURE_SYMBOL, dataset
from tests.integration.paper_support import FixedClock

WALL = datetime(2026, 6, 1, tzinfo=UTC)
"""Well after every fixture bar. A number that moved with this would be visible."""


def replay_service(
    database: Database,
    *,
    with_products: bool = True,
    contract: FuturesContract | None = None,
    clock: FixedClock | None = None,
) -> ReplayService:
    codec = FuturesSnapshotCodec()
    process_clock = clock or FixedClock(WALL)
    performance = PerformanceService(
        source=SqlPaperPerformanceSource(database, codec),
        journal=SqlAlchemyJournalStore(database),
        clock=process_clock,
    )
    resolver = (
        FuturesProductResolver(
            ManualContractMetadataProvider([contract if contract is not None else paper_contract()])
        )
        if with_products
        else None
    )
    return ReplayService(
        store=SqlAlchemyReplayStore(database),
        paper_store=SqlAlchemyPaperStore(database),
        codec=codec,
        resolver=resolver,
        parser=CsvCandleTextParser(),
        performance=performance,
        clock=process_clock,
    )


def create_command(
    key: str,
    *,
    content: dict[Timeframe, str] | None = None,
    driver: Timeframe = Timeframe.M5,
    replay_start: datetime | None = None,
    symbol: str = FIXTURE_SYMBOL,
) -> CreateReplaySession:
    """A session over the aligned fixture dataset, starting one hour in.

    An hour of warm-up on the driver timeframe is deliberate: it gives the
    analysis engines real history to read, so a test that finds an indicator
    unavailable has found something rather than simply run out of bars.
    """
    data = content if content is not None else dataset(288)
    return CreateReplaySession(
        idempotency_key=key,
        symbol=symbol,
        driver=driver,
        replay_start=replay_start if replay_start is not None else BASE + timedelta(hours=1),
        datasets=tuple(
            TimeframeUpload(timeframe, text, f"{timeframe.value}.csv")
            for timeframe, text in data.items()
        ),
    )


def upload_payload(
    content: dict[Timeframe, str],
    *,
    driver: str = "5M",
    replay_start: datetime | None = None,
    symbol: str = FIXTURE_SYMBOL,
) -> dict[str, object]:
    """The same thing as an HTTP body."""
    return {
        "symbol": symbol,
        "driver_timeframe": driver,
        "replay_start": (
            replay_start if replay_start is not None else BASE + timedelta(hours=1)
        ).isoformat(),
        "datasets": [
            {"timeframe": timeframe.value, "content": text, "source_name": f"{timeframe.value}.csv"}
            for timeframe, text in content.items()
        ],
    }


def window_for(payload: dict[str, Any], timeframe: str) -> dict[str, Any]:
    """One timeframe's availability window out of a decoded session response.

    ``Any`` because the argument is parsed JSON. Pretending a decoded body is
    statically typed would mean writing casts that assert nothing.
    """
    found = next(item for item in payload["availability"] if item["timeframe"] == timeframe)
    assert isinstance(found, dict)
    return found


def all_prices(node: object, found: list[str] | None = None) -> list[str]:
    """Every string in a response body, for leak hunting.

    A future candle that reached the client would have to appear as text
    somewhere in the payload, whatever shape the response takes - so this walks
    the whole thing rather than the fields a test happens to know about.
    """
    collected = [] if found is None else found
    if isinstance(node, str):
        collected.append(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            collected.append(str(key))
            all_prices(value, collected)
    elif isinstance(node, Sequence):
        for value in node:
            all_prices(value, collected)
    elif node is not None:
        collected.append(str(node))
    return collected
