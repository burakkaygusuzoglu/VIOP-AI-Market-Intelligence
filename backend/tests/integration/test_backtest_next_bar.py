"""Next-bar entry, to the exact timestamp and the exact price (Phase 12).

An earlier report said ``decision_time == entry_time`` "to the minute". That
is true, and without the timestamps it reads exactly like the hindsight bug it
is supposed to rule out. So this file writes the timeline out:

    10:00  a candle opens
    10:05  the SIGNAL candle opens at 100.00
    10:10  the signal candle's coverage ends; it closed at 101.00
           -> the strategy decides here, and only here
           -> the NEXT candle opens here, at 102.00
    10:15  another candle opens

Three prices are deliberately different - the signal candle's open (100.00),
its close (101.00) and the next candle's open (102.00) - so an entry filled
from the wrong candle cannot pass for a right one by coincidence.

The resulting ledger is then rebuilt directly through the Phase 9 engine from
the same spec, approval, frozen product and candles, and compared entry by
entry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.adapters.persistence.database import Database
from app.adapters.products.futures import FuturesSnapshotCodec
from app.application.backtest.ports import StoredRun
from app.application.backtest.service import RunRequest
from app.domain.backtest.policy import StrategyDecision
from app.domain.backtest.run import RunInterval
from app.domain.common.enums import Direction, Timeframe
from app.domain.paper import SimulationPolicy
from app.domain.paper.engine import apply_observation, open_position
from app.domain.paper.model import PaperEvent, PaperEventType
from app.domain.replay import coverage_end
from tests.factories_replay import Row
from tests.integration.backtest_support import (
    ACCOUNT,
    RISK,
    ScriptedStrategy,
    candles_of,
    intent,
    runner,
    seed_dataset,
)

pytestmark = pytest.mark.integration

T10_00 = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
T10_05 = T10_00 + timedelta(minutes=5)
T10_10 = T10_00 + timedelta(minutes=10)
T10_15 = T10_00 + timedelta(minutes=15)
T10_20 = T10_00 + timedelta(minutes=20)

SIGNAL_OPEN = "100.00"
SIGNAL_CLOSE = "101.00"
NEXT_OPEN = "102.00"


def row(opened: datetime, o: str, h: str, low: str, c: str) -> Row:
    return Row(
        open_time=opened,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal("1000"),
    )


ROWS = [
    row(T10_00, "99.00", "99.00", "99.00", "99.00"),
    row(T10_05, SIGNAL_OPEN, "101.25", "100.00", SIGNAL_CLOSE),  # the signal candle
    row(T10_10, NEXT_OPEN, "102.50", "101.75", "102.25"),  # the next, eligible candle
    row(T10_15, "102.25", "102.50", "102.00", "102.25"),
]
"""Stop 95 and target 120 below are out of reach, so the entry is the only fill."""

SIGNAL_INDEX = 1


async def run_it(database: Database, key: str) -> tuple[StoredRun, ScriptedStrategy]:
    dataset = await seed_dataset(database, ROWS, timeframes=(Timeframe.M5,))
    strategy = ScriptedStrategy(
        plan={
            SIGNAL_INDEX: StrategyDecision.enter(
                intent(Direction.LONG, entry=SIGNAL_CLOSE, stop="95", targets=(("120", 1),)),
                "scripted entry on the 10:05 candle",
            )
        }
    )
    stored = await runner(database).run(
        RunRequest(
            attempt_key=key,
            dataset_id=dataset.dataset_id,
            driver=Timeframe.M5,
            # Every boundary from the first candle's close to the last's.
            interval=RunInterval(start=T10_05, end=T10_20),
            strategy=strategy,
            account=ACCOUNT,
            risk=RISK,
            simulation=SimulationPolicy(),
        )
    )
    return stored, strategy


def entry_event(events: tuple[PaperEvent, ...]) -> PaperEvent:
    (found,) = [event for event in events if event.type is PaperEventType.ENTRY_FILLED]
    return found


class TestTheTimelineIsExact:
    async def test_the_signal_candle_opens_at_10_05_and_ends_at_10_10(
        self, database: Database
    ) -> None:
        _, strategy = await run_it(database, "nextbar-timeline-001")

        (signal,) = [c for c in strategy.seen if c.bars_available - 1 == SIGNAL_INDEX]
        assert signal.bar.open_time == T10_05
        assert coverage_end(signal.bar) == T10_10
        assert signal.as_of == T10_10

    async def test_the_decision_is_stamped_10_10(self, database: Database) -> None:
        stored, _ = await run_it(database, "nextbar-decision-001")

        assert stored.result is not None
        (position,) = stored.result.positions
        assert position.spec.decision_time == T10_10

    async def test_the_entry_fills_at_the_10_10_candles_open(self, database: Database) -> None:
        stored, _ = await run_it(database, "nextbar-fill-000001")

        assert stored.result is not None
        (position,) = stored.result.positions
        entry = entry_event(position.events)
        assert entry.market_time == T10_10
        assert entry.data["fill_price"] == NEXT_OPEN
        # The price the fill was taken from is that candle's own open.
        assert entry.data["reference_price"] == NEXT_OPEN

    async def test_the_signal_candles_open_never_becomes_the_entry(
        self, database: Database
    ) -> None:
        """100.00 is the hindsight price. It must not appear as a fill."""
        stored, _ = await run_it(database, "nextbar-noopen-0001")

        assert stored.result is not None
        (position,) = stored.result.positions
        entry = entry_event(position.events)
        assert entry.data["fill_price"] != SIGNAL_OPEN
        assert entry.market_time != T10_05

    async def test_the_signal_candles_close_is_not_the_fill_either(
        self, database: Database
    ) -> None:
        """The intended entry was 101.00. The market gave 102.00, and that is
        what the ledger records - a plan is not an execution."""
        stored, _ = await run_it(database, "nextbar-noclose-001")

        assert stored.result is not None
        (position,) = stored.result.positions
        assert position.spec.intended_entry == Decimal(SIGNAL_CLOSE)
        assert entry_event(position.events).data["fill_price"] != SIGNAL_CLOSE

    async def test_no_fill_happens_before_the_decision(self, database: Database) -> None:
        stored, _ = await run_it(database, "nextbar-noearly-001")

        assert stored.result is not None
        (position,) = stored.result.positions
        fill_times = [
            event.market_time
            for event in position.events
            if event.market_time is not None and "fill_price" in event.data
        ]
        assert fill_times
        assert all(moment >= T10_10 for moment in fill_times)


class TestTheLedgerIsThePhase9Ledger:
    async def test_the_run_ledger_equals_a_direct_phase_9_replay(self, database: Database) -> None:
        """Same spec, approval, frozen product and candles - straight into
        Phase 9's `open_position` and `apply_observation`, no runner involved.
        """
        stored, _ = await run_it(database, "nextbar-parity-0001")
        assert stored.result is not None
        (position,) = stored.result.positions

        product = FuturesSnapshotCodec().restore(position.product_snapshot)
        direct = open_position(position.spec, position.approval, product)
        # Observations from the first candle that opens at or after the
        # decision - exactly what the runner handed the position.
        for candle in candles_of(ROWS, Timeframe.M5):
            if candle.open_time >= T10_10:
                direct = apply_observation(direct, candle, product)

        def comparable(
            events: tuple[PaperEvent, ...],
        ) -> list[tuple[str, datetime | None, dict[str, str]]]:
            return [
                (event.type.value, event.market_time, dict(event.data))
                for event in events
                if event.type is not PaperEventType.POSITION_CREATED
            ]

        assert comparable(position.events) == comparable(tuple(direct.events))
        assert entry_event(tuple(direct.events)).data["fill_price"] == NEXT_OPEN
        assert entry_event(tuple(direct.events)).market_time == T10_10
