"""Replay storage against real PostgreSQL (Phase 11).

A replay is only worth running if it is still the same replay tomorrow. These
tests are therefore about the storage guarantees rather than the market rules:
the dataset cannot change under a session, the cursor moves once per accepted
step, two tabs cannot both step it, and closing the browser loses nothing.

Real PostgreSQL, real migrations, real triggers. SQLite would answer differently
for every one of them.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import insert, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.adapters.persistence import (  # noqa: F401  (registers the tables)
    backtest_models,
    replay_models,
)
from app.adapters.persistence.base import Base
from app.adapters.persistence.database import Database
from app.adapters.persistence.replay_models import ReplayCandleRow, ReplaySessionRow
from app.adapters.persistence.replay_store import SqlAlchemyReplayStore
from app.application.replay.ports import SessionConflictError
from app.application.replay.service import ReplayErrorKind, ReplayServiceError
from app.domain.common.enums import Direction, Timeframe
from app.domain.paper import SimulationPolicy, TargetSpec
from app.domain.replay import ReplayStatus, advance
from app.domain.risk.sizing import AccountState, RiskMode, RiskPolicy
from tests.factories_replay import BASE, aggregate, csv_of, dataset, five_minute
from tests.integration.replay_support import create_command, replay_service

pytestmark = pytest.mark.integration


class TestDatasetIsWrittenOnceAndNeverAgain:
    async def test_identical_content_is_one_dataset_not_two(self, database: Database) -> None:
        service = replay_service(database)
        first, _ = await service.create(create_command("persist-same-data-0001"))
        second, _ = await service.create(create_command("persist-same-data-0002"))
        assert first.dataset.dataset_id == second.dataset.dataset_id

        async with database.engine.connect() as connection:
            rows = await connection.execute(text("SELECT count(*) FROM replay_datasets"))
            assert rows.scalar_one() == 1

    async def test_one_changed_candle_is_a_different_dataset(self, database: Database) -> None:
        service = replay_service(database)
        first, _ = await service.create(create_command("persist-diff-data-0001"))

        rows = five_minute(288)
        rows[100] = type(rows[100])(
            open_time=rows[100].open_time,
            open=rows[100].open,
            high=rows[100].high + Decimal("5"),
            low=rows[100].low,
            close=rows[100].close,
            volume=rows[100].volume,
        )
        changed = {
            Timeframe.M5: csv_of(rows),
            Timeframe.M15: csv_of(aggregate(rows, Timeframe.M15)),
            Timeframe.H1: csv_of(aggregate(rows, Timeframe.H1)),
        }
        second, _ = await service.create(create_command("persist-diff-data-0002", content=changed))
        assert first.dataset.dataset_id != second.dataset.dataset_id

    async def test_a_stored_candle_cannot_be_updated(self, database: Database) -> None:
        """The trigger, not the application, is what makes this true."""
        service = replay_service(database)
        session, _ = await service.create(create_command("persist-immutable-001"))
        with pytest.raises(DBAPIError) as error:
            async with database.engine.begin() as connection:
                await connection.execute(
                    text("UPDATE replay_candles SET high = 999999 WHERE dataset_id = :id"),
                    {"id": session.dataset.dataset_id},
                )
        assert "immutable" in str(error.value).lower()

    async def test_a_stored_candle_cannot_be_deleted(self, database: Database) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("persist-immutable-002"))
        with pytest.raises(DBAPIError):
            async with database.engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM replay_candles WHERE dataset_id = :id"),
                    {"id": session.dataset.dataset_id},
                )

    async def test_a_dataset_in_use_cannot_be_dropped(self, database: Database) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("persist-restrict-001"))
        with pytest.raises(IntegrityError):
            async with database.engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM replay_datasets WHERE id = :id"),
                    {"id": session.dataset.dataset_id},
                )


class TestValuesSurviveTheRoundTrip:
    async def test_prices_come_back_as_exact_decimals(self, database: Database) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("persist-decimal-0001"))
        store = SqlAlchemyReplayStore(database)
        candles = await store.candles(session.dataset.dataset_id, Timeframe.M5, limit=5)
        expected = five_minute(288)[:5]
        assert [candle.close for candle in candles] == [row.close for row in expected]
        assert all(isinstance(candle.close, Decimal) for candle in candles)

    async def test_market_times_come_back_aware_and_in_order(self, database: Database) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("persist-tz-000000001"))
        store = SqlAlchemyReplayStore(database)
        candles = await store.candles(session.dataset.dataset_id, Timeframe.M5, limit=10)
        times = [candle.open_time for candle in candles]
        assert all(item.tzinfo is not None for item in times)
        assert times == sorted(times)
        assert times[0] == BASE


class TestTheCursorIsPersistentAndForwardOnly:
    async def test_a_session_is_resumed_exactly_where_it_stopped(self, database: Database) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("persist-resume-00001"))
        result = await service.step(
            session.session_id, steps=3, command_key=None, expected_version=None
        )
        expected = result.view.session.cursor

        reopened = replay_service(database)
        view = await reopened.get(session.session_id)
        assert view.session.cursor == expected

    async def test_resuming_later_does_not_move_market_time(self, database: Database) -> None:
        """The wall clock advanced by months between these two reads."""
        from tests.integration.paper_support import FixedClock

        service = replay_service(database)
        session, _ = await service.create(create_command("persist-frozen-00001"))
        await service.step(session.session_id, command_key=None, expected_version=None)
        first = (await service.get(session.session_id)).session.cursor.as_of

        much_later = replay_service(database, clock=FixedClock(BASE.replace(year=2030)))
        second = (await much_later.get(session.session_id)).session.cursor.as_of
        assert first == second

    async def test_each_step_writes_one_version(self, database: Database) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("persist-version-0001"))
        for _ in range(4):
            await service.step(session.session_id, command_key=None, expected_version=None)

        async with database.engine.connect() as connection:
            row = await connection.execute(
                select(ReplaySessionRow.version, ReplaySessionRow.revealed_driver_candles).where(
                    ReplaySessionRow.id == session.session_id
                )
            )
            version, revealed = row.one()
        assert version == 5
        assert revealed == session.cursor.revealed_driver_candles + 4

    async def test_the_end_of_the_dataset_is_persisted_as_a_state(self, database: Database) -> None:
        rows = five_minute(15)
        content = {Timeframe.M5: csv_of(rows)}
        service = replay_service(database)
        session, _ = await service.create(
            create_command(
                "persist-end-000000001",
                content=content,
                replay_start=BASE + timedelta(minutes=50),
            )
        )
        while True:
            result = await service.step(session.session_id, command_key=None, expected_version=None)
            if result.view.session.cursor.status is ReplayStatus.END_OF_DATASET:
                break

        reopened = await replay_service(database).get(session.session_id)
        assert reopened.session.cursor.status is ReplayStatus.END_OF_DATASET
        with pytest.raises(ReplayServiceError) as error:
            await service.step(session.session_id, command_key=None, expected_version=None)
        assert error.value.code == "REPLAY_END"
        assert error.value.kind is ReplayErrorKind.REFUSED


class TestCommandsAreIdempotentAndOrdered:
    async def test_the_same_create_returns_the_same_session(self, database: Database) -> None:
        service = replay_service(database)
        first, replayed_first = await service.create(create_command("persist-idem-000001"))
        second, replayed_second = await service.create(create_command("persist-idem-000001"))
        assert first.session_id == second.session_id
        assert (replayed_first, replayed_second) == (False, True)

        async with database.engine.connect() as connection:
            count = await connection.execute(text("SELECT count(*) FROM replay_sessions"))
            assert count.scalar_one() == 1

    async def test_the_same_key_for_a_different_request_is_a_conflict(
        self, database: Database
    ) -> None:
        service = replay_service(database)
        await service.create(create_command("persist-idem-000002"))
        with pytest.raises(ReplayServiceError) as error:
            await service.create(
                create_command("persist-idem-000002", replay_start=BASE + timedelta(hours=2))
            )
        assert error.value.code == "IDEMPOTENCY_CONFLICT"
        assert error.value.kind is ReplayErrorKind.CONFLICT

    async def test_a_retried_step_does_not_advance_a_second_candle(
        self, database: Database
    ) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("persist-steprety-001"))
        first = await service.step(
            session.session_id, command_key="step-command-key-001", expected_version=None
        )
        again = await service.step(
            session.session_id, command_key="step-command-key-001", expected_version=None
        )
        assert again.replayed is True
        assert again.view.session.cursor == first.view.session.cursor
        assert again.revealed == ()

    async def test_a_new_command_key_does_advance(self, database: Database) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("persist-steprety-002"))
        first = await service.step(
            session.session_id, command_key="step-command-key-002", expected_version=None
        )
        second = await service.step(
            session.session_id, command_key="step-command-key-003", expected_version=None
        )
        assert second.view.session.cursor.as_of > first.view.session.cursor.as_of
        assert second.view.session.cursor.version == first.view.session.cursor.version + 1


class TestConcurrency:
    async def test_two_steps_at_the_same_version_advance_one_candle(
        self, database: Database
    ) -> None:
        """The store, not the service, is what refuses the loser."""
        service = replay_service(database)
        session, _ = await service.create(create_command("persist-concurrent-01"))
        store = SqlAlchemyReplayStore(database)
        loaded = await store.get_session(session.session_id)
        assert loaded is not None
        driver = await store.candles(loaded.plan.dataset_id, loaded.plan.driver)
        cursor, _ = advance(loaded.cursor, driver, 1)

        winner = await store.advance_session(
            session.session_id,
            cursor,
            expected_version=loaded.cursor.version,
            command_key="tab-one-key-000001",
            command_target=None,
            now=BASE,
        )
        with pytest.raises(SessionConflictError):
            await store.advance_session(
                session.session_id,
                cursor,
                expected_version=loaded.cursor.version,
                command_key="tab-two-key-000001",
                command_target=None,
                now=BASE,
            )

        reread = await store.get_session(session.session_id)
        assert reread is not None
        assert reread.cursor == winner.cursor
        assert reread.cursor.revealed_driver_candles == loaded.cursor.revealed_driver_candles + 1

    async def test_a_stale_expected_version_is_refused(self, database: Database) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("persist-stale-00001"))
        await service.step(session.session_id, command_key=None, expected_version=None)
        with pytest.raises(ReplayServiceError) as error:
            await service.step(
                session.session_id, command_key=None, expected_version=session.cursor.version
            )
        assert error.value.code == "VERSION_CONFLICT"
        assert error.value.kind is ReplayErrorKind.CONFLICT

    async def test_a_refused_step_leaves_the_cursor_where_it_was(self, database: Database) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("persist-rollback-001"))
        moved = await service.step(session.session_id, command_key=None, expected_version=None)
        with pytest.raises(ReplayServiceError):
            await service.step(session.session_id, command_key=None, expected_version=999)
        after = await service.get(session.session_id)
        assert after.session.cursor == moved.view.session.cursor


class TestSessionsAreIsolated:
    async def test_two_sessions_over_one_dataset_step_independently(
        self, database: Database
    ) -> None:
        service = replay_service(database)
        first, _ = await service.create(create_command("persist-isolate-001a"))
        second, _ = await service.create(create_command("persist-isolate-001b"))
        assert first.dataset.dataset_id == second.dataset.dataset_id

        await service.step(first.session_id, steps=5, command_key=None, expected_version=None)
        ahead = await service.get(first.session_id)
        behind = await service.get(second.session_id)
        assert ahead.session.cursor.as_of > behind.session.cursor.as_of
        assert behind.session.cursor == second.cursor

    async def test_the_session_list_is_bounded_and_newest_first(self, database: Database) -> None:
        service = replay_service(database)
        for index in range(4):
            await service.create(create_command(f"persist-listing-000{index}"))
        items, total = await service.list(offset=0, limit=2)
        assert total == 4
        assert len(items) == 2
        assert items[0].created_at >= items[1].created_at

    async def test_an_unknown_session_is_not_found(self, database: Database) -> None:
        service = replay_service(database)
        with pytest.raises(ReplayServiceError) as error:
            await service.get("RS-000000000000000000000000")
        assert error.value.code == "SESSION_NOT_FOUND"
        assert error.value.kind is ReplayErrorKind.NOT_FOUND


class TestSchema:
    async def test_the_models_match_the_migrated_database(self, database: Database) -> None:
        """The replay tables are in the migration, not only in the models."""

        def diff(connection: Connection) -> list[object]:
            context = MigrationContext.configure(connection)
            return list(compare_metadata(context, Base.metadata))

        async with database.engine.connect() as connection:
            differences = await connection.run_sync(diff)
        assert differences == []

    async def test_a_candle_window_is_indexed_for_the_read_replay_makes(
        self, database: Database
    ) -> None:
        async with database.engine.connect() as connection:
            rows = await connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'replay_candles'")
            )
            names = {row[0] for row in rows}
        assert "ix_replay_candles_window" in names

    async def test_a_dataset_must_hold_rows(self, database: Database) -> None:
        with pytest.raises(IntegrityError):
            async with database.engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO replay_datasets (id, symbol, timeframes, total_rows) "
                        "VALUES ('RD-empty', 'X', '[]'::jsonb, 0)"
                    )
                )

    async def test_a_candle_cannot_have_a_high_below_its_low(self, database: Database) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("persist-check-000001"))
        with pytest.raises(IntegrityError):
            async with database.engine.begin() as connection:
                await connection.execute(
                    insert(ReplayCandleRow),
                    {
                        "dataset_id": session.dataset.dataset_id,
                        "timeframe": "5M",
                        "open_time": BASE - timedelta(days=1),
                        "sequence": 0,
                        "open": Decimal("100"),
                        "high": Decimal("1"),
                        "low": Decimal("100"),
                        "close": Decimal("100"),
                        "volume": Decimal("1"),
                    },
                )

    async def test_a_session_state_outside_the_vocabulary_is_refused(
        self, database: Database
    ) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("persist-check-000002"))
        with pytest.raises(IntegrityError):
            async with database.engine.begin() as connection:
                await connection.execute(
                    text("UPDATE replay_sessions SET state = 'REWINDING' WHERE id = :id"),
                    {"id": session.session_id},
                )


class TestBounds:
    async def test_a_dataset_beyond_the_row_limit_is_refused(self, database: Database) -> None:
        service = replay_service(database)
        oversized = {Timeframe.M5: csv_of(five_minute(3000))}
        with pytest.raises(ReplayServiceError) as error:
            await service.create(create_command("persist-toobig-00001", content=oversized))
        assert error.value.kind in (ReplayErrorKind.TOO_LARGE, ReplayErrorKind.INVALID)

    async def test_a_missing_driver_timeframe_is_refused(self, database: Database) -> None:
        service = replay_service(database)
        content = dataset(288, timeframes=(Timeframe.M5, Timeframe.M15))
        with pytest.raises(ReplayServiceError) as error:
            await service.create(
                create_command("persist-nodriver-0001", content=content, driver=Timeframe.H1)
            )
        assert error.value.code == "DRIVER_TIMEFRAME_MISSING"

    async def test_a_session_holds_a_bounded_number_of_positions(self, database: Database) -> None:
        """Every open position is fed every revealed bar, so the count is bounded.

        The limit is lowered here rather than opening fifty positions: the rule
        under test is that the service refuses past its own bound, not what the
        bound happens to be.
        """
        from dataclasses import replace

        from app.application.replay.service import ReplayLimits

        service = replay_service(database)
        service._limits = replace(ReplayLimits(), max_session_positions=1)  # noqa: SLF001
        session, _ = await service.create(
            create_command("persist-posbound-001", replay_start=BASE + timedelta(hours=8))
        )

        async def open_one(key: str) -> None:
            await service.open_paper_position(
                session.session_id,
                idempotency_key=key,
                direction=Direction.LONG,
                quantity=2,
                intended_entry=Decimal("100"),
                stop=Decimal("90"),
                targets=(TargetSpec(Decimal("130"), 2),),
                account=AccountState(equity=Decimal("100000")),
                risk=RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("1000")),
                policy=SimulationPolicy(),
            )

        await open_one("persist-posbound-p001")
        with pytest.raises(ReplayServiceError) as error:
            await open_one("persist-posbound-p002")
        assert error.value.code == "RESOURCE_LIMIT"
        assert error.value.kind is ReplayErrorKind.TOO_LARGE

    async def test_an_empty_upload_is_refused(self, database: Database) -> None:
        service = replay_service(database)
        with pytest.raises(ReplayServiceError) as error:
            await service.create(
                create_command(
                    "persist-empty-000001",
                    content={Timeframe.M5: "open_time,open,high,low,close,volume\n"},
                )
            )
        assert error.value.kind is ReplayErrorKind.INVALID
