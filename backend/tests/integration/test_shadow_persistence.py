"""The shadow journal against real PostgreSQL (Phase 14 Part 1).

Three properties that only the real database can demonstrate: the journal is
append-only, writing the same observation twice records it once, and what was
written survives a restart exactly as it was written. The last test drives the
real runner over a real live session and reads the journal back.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.adapters.persistence.database import Database
from app.adapters.persistence.shadow_store import SqlAlchemyShadowStore
from app.application.shadow.ports import StoredShadowRun
from app.core.config import Settings
from app.domain.common.enums import Direction, Timeframe
from app.domain.shadow.decision import (
    EntrySketch,
    FinancialState,
    JournalEntryKind,
    OperationalKind,
    ShadowDecision,
    ShadowEvidence,
    ShadowOutcome,
    TimeframeEvidence,
    TimeframeReadings,
)
from app.domain.shadow.run import EndReason, ShadowError, ShadowRunStatus
from tests.integration.paper_support import truncate
from tests.unit.live.support import RECEIVE_START, at
from tests.unit.shadow.support import (
    CONNECTED,
    END,
    RUN_ID,
    ScriptedStrategy,
    bar,
    observe,
    request_for,
)

pytestmark = pytest.mark.integration

FIRST = "SR-" + "1" * 24
SECOND = "SR-" + "2" * 24


@pytest.fixture
async def store(migrated: Settings) -> AsyncIterator[SqlAlchemyShadowStore]:
    database = Database(migrated.sqlalchemy_url)
    await truncate(database)
    try:
        yield SqlAlchemyShadowStore(database)
    finally:
        await database.dispose()


def a_run(run_id: str = FIRST) -> StoredShadowRun:
    return StoredShadowRun(
        run_id=run_id,
        configuration="SC-" + "f" * 32,
        source_id="RD-" + "a" * 32,
        instrument_label="TEST_FIXTURE_FUT",
        provenance="SIMULATED_HISTORICAL_STREAM",
        market_currency="HISTORICAL",
        strategy_id="test-shadow",
        strategy_version="1.0.0",
        strategy_parameters={"warm_up": "1"},
        driver=Timeframe.M5,
        timeframes=(Timeframe.M5, Timeframe.M15),
        required_timeframes=(Timeframe.M5,),
        risk={"mode": "PERCENTAGE", "risk_ratio": "0.01"},
        account={"equity": "500000", "used_margin": "0"},
        status=ShadowRunStatus.OBSERVING,
        started_at=RECEIVE_START,
    )


def a_decision(sequence: int, key: str, *, run: str = FIRST) -> ShadowDecision:
    del run
    return ShadowDecision(
        kind=JournalEntryKind.DECISION,
        sequence=sequence,
        decision_key=key,
        market_boundary=at(5 * sequence),
        recorded_at=RECEIVE_START,
        outcome=ShadowOutcome.ENTRY_INTENT,
        reason="scripted entry",
        strategy_kind="ENTRY_INTENT",
        direction=Direction.LONG,
        entry=EntrySketch(
            direction=Direction.LONG,
            intended_entry=Decimal("100.12345"),
            stop=Decimal("99.10"),
            targets=((Decimal("102.75"), 1),),
            requested_quantity=2,
            approved_quantity=1,
        ),
        financial_state=FinancialState.APPROVED,
        risk_outcome="ALLOWED",
        risk_reason="sized against verified contract facts",
        evidence=ShadowEvidence(
            provenance="SIMULATED_HISTORICAL_STREAM",
            market_currency="HISTORICAL",
            connection="CONNECTED",
            bars_available=sequence,
            included=(Timeframe.M5,),
            excluded=(
                TimeframeEvidence(
                    timeframe=Timeframe.M15,
                    available=False,
                    freshness="NO_DATA",
                    integrity="COMPLETE",
                    confirmed_count=0,
                    reasons=("no valid observation has been received",),
                ),
            ),
            readings=(TimeframeReadings(timeframe=Timeframe.M5, ema_fast="100.5", rsi="55.25"),),
            timeframes=(),
            regime="TRENDING_UP",
            suitability="FALSE",
            setup_quality="61",
        ),
        input_fingerprint="f" * 64,
    )


class TestTheJournalKeepsWhatWasWritten:
    async def test_a_run_and_its_entries_survive_a_restart_unchanged(
        self, store: SqlAlchemyShadowStore, migrated: Settings
    ) -> None:
        await store.create_run(a_run())
        written = await store.append(FIRST, [a_decision(1, "k1"), a_decision(2, "k2")])
        assert written == 2

        # A new Database is a new process as far as the journal is concerned.
        database = Database(migrated.sqlalchemy_url)
        try:
            reopened = SqlAlchemyShadowStore(database)
            run = await reopened.get_run(FIRST)
            entries, total = await reopened.read_entries(FIRST, after_sequence=0, limit=100)
        finally:
            await database.dispose()

        assert run is not None
        assert run.provenance == "SIMULATED_HISTORICAL_STREAM"
        assert run.market_currency == "HISTORICAL"
        assert run.timeframes == (Timeframe.M5, Timeframe.M15)
        assert total == 2
        first = entries[0]
        assert first.entry is not None
        assert first.entry.intended_entry == Decimal("100.12345")  # exact, never a float
        assert first.entry.targets == ((Decimal("102.75"), 1),)
        assert first.entry.approved_quantity == 1
        assert first.evidence is not None
        assert first.evidence.regime == "TRENDING_UP"
        assert first.evidence.excluded[0].reasons == ("no valid observation has been received",)
        assert first.outcome is ShadowOutcome.ENTRY_INTENT
        assert first.financial_state is FinancialState.APPROVED

    async def test_writing_the_same_observation_twice_records_it_once(
        self, store: SqlAlchemyShadowStore
    ) -> None:
        """P: an HTTP retry, a reconnect or a resumed consumer."""
        await store.create_run(a_run())
        first = await store.append(FIRST, [a_decision(1, "k1")])
        again = await store.append(FIRST, [a_decision(1, "k1")])
        mixed = await store.append(FIRST, [a_decision(1, "k1"), a_decision(2, "k2")])

        entries, total = await store.read_entries(FIRST, after_sequence=0, limit=100)
        assert (first, again, mixed) == (1, 0, 1)
        assert total == 2
        assert [entry.decision_key for entry in entries] == ["k1", "k2"]

    async def test_a_published_entry_cannot_be_edited_or_removed(
        self, store: SqlAlchemyShadowStore, migrated: Settings
    ) -> None:
        await store.create_run(a_run())
        await store.append(FIRST, [a_decision(1, "k1")])
        database = Database(migrated.sqlalchemy_url)
        try:
            for statement in (
                "UPDATE shadow_journal SET outcome = 'NO_SIGNAL'",
                "DELETE FROM shadow_journal",
            ):
                # A refusal aborts its transaction, so each attempt gets its own.
                with pytest.raises(Exception) as caught:  # noqa: B017, PT011
                    async with database.engine.begin() as connection:
                        await connection.execute(text(statement))
                assert "append-only" in str(caught.value)
        finally:
            await database.dispose()

        entries, _ = await store.read_entries(FIRST, after_sequence=0, limit=10)
        assert entries[0].outcome is ShadowOutcome.ENTRY_INTENT

    async def test_an_ended_run_must_say_why(
        self, store: SqlAlchemyShadowStore, migrated: Settings
    ) -> None:
        await store.create_run(a_run())
        database = Database(migrated.sqlalchemy_url)
        try:
            async with database.engine.begin() as connection:
                with pytest.raises(Exception) as caught:  # noqa: B017, PT011
                    await connection.execute(
                        text("UPDATE shadow_runs SET status = 'ENDED' WHERE id = :id"),
                        {"id": FIRST},
                    )
                assert "ended_runs_state_why" in str(caught.value)
        finally:
            await database.dispose()

    async def test_two_runs_never_share_a_journal(self, store: SqlAlchemyShadowStore) -> None:
        await store.create_run(a_run(FIRST))
        await store.create_run(a_run(SECOND))
        await store.append(FIRST, [a_decision(1, "shared-key")])
        await store.append(SECOND, [a_decision(1, "shared-key")])  # same key, other run

        first, first_total = await store.read_entries(FIRST, after_sequence=0, limit=10)
        second, second_total = await store.read_entries(SECOND, after_sequence=0, limit=10)
        runs, total = await store.list_runs(offset=0, limit=10)

        assert first_total == second_total == 1
        assert first[0].decision_key == second[0].decision_key  # keys are per run
        assert total == 2
        assert {run.run_id for run in runs} == {FIRST, SECOND}

    async def test_a_run_id_is_never_reused(self, store: SqlAlchemyShadowStore) -> None:
        await store.create_run(a_run())
        with pytest.raises(ShadowError) as caught:
            await store.create_run(a_run())
        assert caught.value.code == "SHADOW_RUN_EXISTS"

    async def test_entries_are_read_back_in_order_and_by_the_page(
        self, store: SqlAlchemyShadowStore
    ) -> None:
        await store.create_run(a_run())
        await store.append(FIRST, [a_decision(index, f"k{index}") for index in range(1, 8)])

        page, total = await store.read_entries(FIRST, after_sequence=0, limit=3)
        rest, _ = await store.read_entries(FIRST, after_sequence=page[-1].sequence, limit=3)

        assert total == 7
        assert [item.sequence for item in page] == [1, 2, 3]
        assert [item.sequence for item in rest] == [4, 5, 6]
        assert await store.last_sequence(FIRST) == 7

    async def test_finishing_a_run_records_what_it_observed(
        self, store: SqlAlchemyShadowStore
    ) -> None:
        await store.create_run(a_run())
        finished = await store.finish_run(
            FIRST,
            status=ShadowRunStatus.ENDED,
            end_reason=EndReason.STREAM_ENDED,
            ended_at=RECEIVE_START,
            observations=12,
            decisions=9,
            entries=14,
            first_boundary=at(5),
            last_boundary=at(60),
        )

        assert finished.status is ShadowRunStatus.ENDED
        assert finished.end_reason is EndReason.STREAM_ENDED
        assert (finished.observations, finished.decisions) == (12, 9)
        assert finished.first_boundary == at(5)


class TestTheRunnerAgainstTheRealJournal:
    async def test_an_observation_is_recorded_and_read_back(
        self, store: SqlAlchemyShadowStore, migrated: Settings
    ) -> None:
        from app.adapters.live.mock_stream import ManualClock
        from app.adapters.market_data.csv_provider import CsvCandleTextParser
        from app.application.shadow.service import ShadowRunner

        runner = ShadowRunner(
            store=store,
            clock=ManualClock(RECEIVE_START),
            parser=CsvCandleTextParser(),
            supported={"test-shadow": frozenset({"1.0.0"})},
        )
        strategy = ScriptedStrategy(["NO_SIGNAL", "ENTRY_INTENT", "WAIT"])

        run, _ = await observe(
            runner,
            request_for(strategy),
            [CONNECTED, bar(0), bar(1), bar(2), END],
            run_id=RUN_ID,
        )

        stored = await store.get_run(RUN_ID)
        entries, total = await store.read_entries(RUN_ID, after_sequence=0, limit=100)
        decisions = [item for item in entries if item.kind is JournalEntryKind.DECISION]
        operational = [item.operational for item in entries if item.operational is not None]

        assert stored is not None
        assert stored.status is ShadowRunStatus.ENDED
        assert stored.end_reason is EndReason.STREAM_ENDED
        assert stored.observations == 3 == run.observations
        assert total == stored.entries
        assert [item.outcome for item in decisions] == [
            ShadowOutcome.NO_SIGNAL,
            ShadowOutcome.ENTRY_INTENT,
            ShadowOutcome.WAIT,
        ]
        assert [item.market_boundary for item in decisions] == [at(5), at(10), at(15)]
        assert OperationalKind.RUN_OPENED in operational
        assert OperationalKind.RUN_ENDED in operational
        intent = decisions[1]
        assert intent.entry is not None
        assert intent.entry.approved_quantity is None
        assert intent.financial_state is FinancialState.NOT_CONFIGURED

    async def test_shadow_creates_no_position_and_no_backtest_run(
        self, store: SqlAlchemyShadowStore, migrated: Settings
    ) -> None:
        from app.adapters.live.mock_stream import ManualClock
        from app.adapters.market_data.csv_provider import CsvCandleTextParser
        from app.application.shadow.service import ShadowRunner

        runner = ShadowRunner(
            store=store,
            clock=ManualClock(RECEIVE_START),
            parser=CsvCandleTextParser(),
            supported={"test-shadow": frozenset({"1.0.0"})},
        )

        await observe(
            runner,
            request_for(ScriptedStrategy(["ENTRY_INTENT", "ENTRY_INTENT"])),
            [CONNECTED, bar(0), bar(1), END],
            run_id=RUN_ID,
        )

        database = Database(migrated.sqlalchemy_url)
        try:
            async with database.engine.begin() as connection:
                counts = (
                    await connection.execute(
                        text(
                            "SELECT (SELECT count(*) FROM paper_positions),"
                            " (SELECT count(*) FROM paper_position_events),"
                            " (SELECT count(*) FROM backtest_runs),"
                            " (SELECT count(*) FROM backtest_positions),"
                            " (SELECT count(*) FROM replay_sessions),"
                            " (SELECT count(*) FROM shadow_journal)"
                        )
                    )
                ).one()
        finally:
            await database.dispose()

        positions, events, runs, backtest_positions, replays, journal = counts
        assert (positions, events) == (0, 0)  # shadow never opens a paper position
        assert (runs, backtest_positions) == (0, 0)  # nor a backtest run
        assert replays == 0
        assert journal > 0  # it only wrote to its own journal
