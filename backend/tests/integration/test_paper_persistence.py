"""Paper trading against real PostgreSQL (Phase 9).

No SQLite, no in-memory store, no mocked session: every test here runs the real
migration, the real repository and the real service against the configured
database. They skip only when no PostgreSQL is reachable, and the final gate
runs with one configured, so none of them skip there.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.adapters.persistence import paper_models
from app.adapters.persistence.base import Base
from app.adapters.persistence.database import Database
from app.adapters.persistence.paper_models import PaperEventRow, PaperPositionRow
from app.adapters.persistence.paper_store import SqlAlchemyPaperStore
from app.adapters.products.futures import FuturesSnapshotCodec
from app.application.paper.service import PaperErrorKind, PaperServiceError
from app.application.ports.paper import ConcurrentModificationError
from app.domain.common.enums import Direction
from app.domain.paper import (
    FeeMode,
    FeePolicy,
    PaperEventType,
    SameBarPolicy,
    SimulationPolicy,
    TargetSpec,
    rebuild,
    request_close,
)
from tests.integration.paper_support import (
    DECISION,
    ENTRY,
    STOP_ON_REST,
    TARGET_ONE,
    FixedClock,
    bars_csv,
    create_command,
    service,
)

pytestmark = pytest.mark.integration

KEY = "integration-key-0001"


class TestSchema:
    async def test_the_migration_matches_the_models_exactly(self, database: Database) -> None:
        assert paper_models.PaperPositionRow.__tablename__ == "paper_positions"
        async with database.engine.connect() as connection:
            diff = await connection.run_sync(
                lambda sync: compare_metadata(
                    MigrationContext.configure(sync, opts={"compare_type": True}), Base.metadata
                )
            )
        assert diff == []

    async def test_no_future_phase_table_exists(self, database: Database) -> None:
        async with database.engine.connect() as connection:
            names = set(
                (
                    await connection.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                    )
                ).scalars()
            )
        assert names == {"alembic_version", "paper_positions", "paper_position_events"}

    async def test_money_columns_are_exact_numeric(self, database: Database) -> None:
        async with database.engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_name = 'paper_positions' AND column_name IN "
                        "('intended_entry','entry_fill_price','stop','last_mark','realized_gross',"
                        "'fees_total','realized_net','unrealized_gross')"
                    )
                )
            ).all()
        assert len(rows) == 8
        assert {data_type for _, data_type in rows} == {"numeric"}


class TestRoundTrip:
    async def test_create_and_retrieve(self, database: Database) -> None:
        paper = service(database)
        created = await paper.create(create_command(KEY))
        fetched = await paper.get(created.stored.position_id)

        assert fetched.stored.spec == created.stored.spec
        assert fetched.stored.approval == created.stored.approval
        assert fetched.stored.events == created.stored.events
        assert fetched.stored.projection.state == "PENDING_ENTRY"
        assert fetched.stored.version == 1

    async def test_decimal_scale_and_timezone_survive_exactly(self, database: Database) -> None:
        paper = service(database)
        command = create_command(
            KEY,
            intended_entry=Decimal("100.00"),
            policy=SimulationPolicy(
                fees=FeePolicy(FeeMode.USER_DEFINED_PER_UNIT, Decimal("1.2500"))
            ),
        )
        created = await paper.create(command)
        position_id = created.stored.position_id
        await paper.observe(position_id, bars_csv(ENTRY, TARGET_ONE), "bars.csv")
        fetched = await paper.get(position_id)

        spec = fetched.stored.spec
        assert str(spec.intended_entry) == "100.00"
        assert spec.policy.fees.per_unit == Decimal("1.2500")
        assert str(spec.policy.fees.per_unit) == "1.2500"
        assert spec.decision_time == DECISION
        assert spec.decision_time.utcoffset() is not None
        assert fetched.stored.projection.realized_gross == Decimal("80.00")
        assert str(fetched.stored.projection.realized_gross) == "80.00"
        assert fetched.stored.projection.fees_total == Decimal("7.5000")
        assert fetched.stored.projection.last_bar_time is not None
        assert fetched.stored.projection.last_bar_time.utcoffset() is not None

    async def test_the_stored_ledger_replays_to_the_stored_projection(
        self, database: Database
    ) -> None:
        paper = service(database)
        created = await paper.create(create_command(KEY))
        await paper.observe(
            created.stored.position_id, bars_csv(ENTRY, TARGET_ONE, STOP_ON_REST), "bars.csv"
        )
        stored = (await paper.get(created.stored.position_id)).stored
        product = FuturesSnapshotCodec().restore(stored.product_snapshot)

        replayed = rebuild(stored.spec, stored.approval, product, stored.events)

        assert replayed.state.value == stored.projection.state == "CLOSED"
        assert replayed.realized_gross == stored.projection.realized_gross == Decimal("40.00")
        assert replayed.remaining == stored.projection.remaining == 0
        assert len(replayed.events) == stored.projection.event_count

    async def test_events_come_back_in_sequence_order(self, database: Database) -> None:
        paper = service(database)
        created = await paper.create(create_command(KEY))
        position_id = created.stored.position_id
        await paper.observe(position_id, bars_csv(ENTRY, TARGET_ONE, STOP_ON_REST), "bars.csv")

        page = await paper.events(position_id, after_sequence=0, limit=200)
        sequences = [item.event.sequence for item in page.items]

        assert sequences == sorted(sequences) == list(range(1, page.total + 1))
        second = await paper.events(position_id, after_sequence=3, limit=2)
        assert [item.event.sequence for item in second.items] == [4, 5]


class TestLedgerIsAppendOnly:
    async def test_update_of_an_event_is_refused_by_the_database(self, database: Database) -> None:
        paper = service(database)
        created = await paper.create(create_command(KEY))

        async with database.engine.begin() as connection:
            with pytest.raises(DBAPIError, match="append-only"):
                await connection.execute(
                    text("UPDATE paper_position_events SET event_type = 'POSITION_CLOSED'")
                )
        stored = await paper.get(created.stored.position_id)
        assert stored.stored.events[0].type is PaperEventType.POSITION_CREATED

    async def test_delete_of_an_event_is_refused_by_the_database(self, database: Database) -> None:
        await service(database).create(create_command(KEY))

        async with database.engine.begin() as connection:
            with pytest.raises(DBAPIError, match="append-only"):
                await connection.execute(text("DELETE FROM paper_position_events"))

    async def test_a_second_event_at_the_same_sequence_is_refused(self, database: Database) -> None:
        created = await service(database).create(create_command(KEY))
        first = created.stored.events[0]

        async with database.session() as session:
            session.add(
                PaperEventRow(
                    position_id=created.stored.position_id,
                    sequence=first.sequence,
                    event_type="POSITION_CLOSED",
                    market_time=None,
                    data={},
                    recorded_at=DECISION,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()

    async def test_the_database_refuses_an_impossible_remaining_quantity(
        self, database: Database
    ) -> None:
        created = await service(database).create(create_command(KEY))

        async with database.engine.begin() as connection:
            with pytest.raises(IntegrityError, match="remaining_in_range"):
                await connection.execute(
                    text("UPDATE paper_positions SET remaining = quantity + 1 WHERE id = :id"),
                    {"id": created.stored.position_id},
                )


class TestTransactions:
    async def test_a_partial_exit_commits_state_and_events_together(
        self, database: Database
    ) -> None:
        paper = service(database)
        created = await paper.create(create_command(KEY))
        view = await paper.observe(created.stored.position_id, bars_csv(ENTRY, TARGET_ONE), "b.csv")

        async with database.session() as session:
            row = await session.get(PaperPositionRow, created.stored.position_id)
            events = (
                await session.scalars(
                    select(PaperEventRow).where(
                        PaperEventRow.position_id == created.stored.position_id
                    )
                )
            ).all()
        assert row is not None
        assert row.state == "PARTIALLY_CLOSED"
        assert row.remaining == 2
        assert row.realized_gross == Decimal("80.00")
        assert row.event_count == len(events) == len(view.stored.events)
        assert row.version == 2

    async def test_a_batch_with_one_bad_bar_writes_nothing(self, database: Database) -> None:
        paper = service(database)
        created = await paper.create(create_command(KEY))
        position_id = created.stored.position_id
        await paper.observe(position_id, bars_csv(ENTRY), "a.csv")

        # hour 1 is fine, hour 0 conflicts with the bar already applied
        conflicting = bars_csv(TARGET_ONE, (0, "100", "101", "99", "100.50"))
        with pytest.raises(PaperServiceError):
            await paper.observe(position_id, conflicting, "b.csv")

        after = await paper.get(position_id)
        assert after.stored.projection.state == "OPEN"
        assert after.stored.projection.bars_applied == 1
        assert after.stored.version == 2

    async def test_close_then_stop_does_not_close_twice(self, database: Database) -> None:
        paper = service(database)
        created = await paper.create(create_command(KEY))
        position_id = created.stored.position_id
        await paper.observe(position_id, bars_csv(ENTRY, TARGET_ONE, STOP_ON_REST), "b.csv")

        with pytest.raises(PaperServiceError) as error:
            await paper.request_close(position_id)
        assert error.value.kind is PaperErrorKind.REFUSED
        final = await paper.get(position_id)
        exits = [
            e
            for e in final.stored.events
            if e.type in (PaperEventType.STOP_FILLED, PaperEventType.MANUAL_EXIT_FILLED)
        ]
        assert len(exits) == 1
        assert final.stored.projection.remaining == 0


class TestConcurrency:
    async def test_racing_bars_cannot_double_exit(self, migrated: object) -> None:
        """Two uploads race to exit the same remaining units; exactly one exit exists.

        Each request uses its own connection pool, so the row lock - not the
        event loop - is what serialises them.
        """
        from app.core.config import Settings

        settings = Settings()
        setup = Database(settings.sqlalchemy_url)
        from tests.integration.paper_support import truncate

        await truncate(setup)
        paper = service(setup)
        created = await paper.create(create_command(KEY))
        position_id = created.stored.position_id
        await paper.observe(position_id, bars_csv(ENTRY, TARGET_ONE), "b.csv")

        first_db = Database(settings.sqlalchemy_url)
        second_db = Database(settings.sqlalchemy_url)
        try:
            results = await asyncio.gather(
                service(first_db).observe(position_id, bars_csv(STOP_ON_REST), "x.csv"),
                service(second_db).observe(
                    position_id, bars_csv((3, "99", "99.50", "96", "97")), "y.csv"
                ),
                return_exceptions=True,
            )
            final = await paper.get(position_id)
        finally:
            await first_db.dispose()
            await second_db.dispose()
            await setup.dispose()

        exits = [e for e in final.stored.events if e.type is PaperEventType.STOP_FILLED]
        assert len(exits) == 1
        assert final.stored.projection.remaining == 0
        assert final.stored.projection.realized_gross == Decimal("40.00")
        failures = [r for r in results if isinstance(r, BaseException)]
        assert len(failures) <= 1
        for failure in failures:
            assert isinstance(failure, PaperServiceError)

    async def test_the_same_bar_racing_itself_applies_once(self, migrated: object) -> None:
        from app.core.config import Settings
        from tests.integration.paper_support import truncate

        settings = Settings()
        setup = Database(settings.sqlalchemy_url)
        await truncate(setup)
        paper = service(setup)
        created = await paper.create(create_command(KEY))
        position_id = created.stored.position_id
        await paper.observe(position_id, bars_csv(ENTRY), "b.csv")

        pools = [Database(settings.sqlalchemy_url) for _ in range(4)]
        try:
            await asyncio.gather(
                *(service(db).observe(position_id, bars_csv(TARGET_ONE), "t.csv") for db in pools)
            )
            final = await paper.get(position_id)
        finally:
            for db in pools:
                await db.dispose()
            await setup.dispose()

        targets = [e for e in final.stored.events if e.type is PaperEventType.TARGET_FILLED]
        assert len(targets) == 1
        assert final.stored.projection.realized_gross == Decimal("80.00")

    async def test_a_stale_version_is_refused_rather_than_overwritten(
        self, database: Database
    ) -> None:
        paper = service(database)
        created = await paper.create(create_command(KEY))
        position_id = created.stored.position_id
        await paper.observe(position_id, bars_csv(ENTRY), "b.csv")
        store = SqlAlchemyPaperStore(database)

        stale = await store.get(position_id)
        assert stale is not None
        await paper.request_close(position_id)  # moves the version on

        product = FuturesSnapshotCodec().restore(stale.product_snapshot)
        position = request_close(rebuild(stale.spec, stale.approval, product, stale.events))
        async with store.unit_of_work() as work:
            with pytest.raises(ConcurrentModificationError):
                await work.append(stale, position, stale.projection, DECISION)


class TestIdempotency:
    async def test_a_retry_returns_the_same_position(self, database: Database) -> None:
        paper = service(database)
        first = await paper.create(create_command(KEY))
        second = await paper.create(create_command(KEY))

        assert second.replayed is True
        assert second.stored.position_id == first.stored.position_id
        assert (await paper.list(offset=0, limit=50)).total == 1

    async def test_the_same_key_with_a_different_payload_conflicts(
        self, database: Database
    ) -> None:
        paper = service(database)
        await paper.create(create_command(KEY))

        with pytest.raises(PaperServiceError) as error:
            await paper.create(create_command(KEY, quantity=3))
        assert error.value.kind is PaperErrorKind.CONFLICT
        assert error.value.code == "IDEMPOTENCY_KEY_REUSED"

    async def test_concurrent_identical_creates_make_one_position(self, migrated: object) -> None:
        from app.core.config import Settings
        from tests.integration.paper_support import truncate

        settings = Settings()
        pools = [Database(settings.sqlalchemy_url) for _ in range(4)]
        await truncate(pools[0])
        try:
            views = await asyncio.gather(*(service(db).create(create_command(KEY)) for db in pools))
            total = (await service(pools[0]).list(offset=0, limit=50)).total
        finally:
            for db in pools:
                await db.dispose()

        assert total == 1
        assert len({view.stored.position_id for view in views}) == 1
        assert sum(not view.replayed for view in views) == 1

    async def test_a_duplicate_observation_writes_nothing(self, database: Database) -> None:
        paper = service(database)
        created = await paper.create(create_command(KEY))
        position_id = created.stored.position_id
        once = await paper.observe(position_id, bars_csv(ENTRY, TARGET_ONE), "b.csv")
        twice = await paper.observe(position_id, bars_csv(ENTRY, TARGET_ONE), "b.csv")

        assert twice.stored.version == once.stored.version
        assert twice.stored.events == once.stored.events

    async def test_refused_creation_persists_nothing(self, database: Database) -> None:
        paper = service(database, with_products=False)

        with pytest.raises(PaperServiceError) as error:
            await paper.create(create_command(KEY))
        assert error.value.code == "PRODUCT_METADATA_UNAVAILABLE"
        assert (await paper.list(offset=0, limit=50)).total == 0


class TestStoredPolicyIsHonoured:
    async def test_a_halt_policy_survives_storage(self, database: Database) -> None:
        """The stored policy - not today's default - decides an ambiguous bar."""
        paper = service(database)
        created = await paper.create(
            create_command(KEY, policy=SimulationPolicy(same_bar=SameBarPolicy.HALT))
        )
        view = await paper.observe(
            created.stored.position_id,
            bars_csv(ENTRY, (1, "100", "104.50", "97.50", "100")),
            "b.csv",
        )

        assert view.stored.projection.state == "AMBIGUOUS_HALTED"
        assert view.stored.projection.remaining == 4

    async def test_a_short_round_trips(self, database: Database) -> None:
        paper = service(database)
        created = await paper.create(
            create_command(
                KEY,
                direction=Direction.SHORT,
                stop=Decimal("102.00"),
                targets=(TargetSpec(Decimal("96.00"), 2), TargetSpec(Decimal("94.00"), 2)),
            )
        )
        view = await paper.observe(
            created.stored.position_id,
            bars_csv((0, "100", "100.50", "99", "99.50"), (1, "105", "106", "104", "105.50")),
            "b.csv",
        )

        # gap stop at open 105: (100 - 105) x 10 x 4 = -200
        assert view.stored.projection.realized_gross == Decimal("-200")
        assert view.stored.projection.state == "CLOSED"


class TestServerClock:
    async def test_a_bar_that_has_not_closed_is_refused(self, database: Database) -> None:
        clock = FixedClock(DECISION.replace(minute=30))
        paper = service(database, clock=clock)
        created = await paper.create(create_command(KEY))

        with pytest.raises(PaperServiceError) as error:
            await paper.observe(created.stored.position_id, bars_csv(ENTRY), "b.csv")
        assert error.value.code == "OBSERVATION_NOT_CLOSED"

    async def test_a_future_decision_time_is_refused(self, database: Database) -> None:
        paper = service(database, clock=FixedClock(DECISION.replace(hour=9)))

        with pytest.raises(PaperServiceError) as error:
            await paper.create(create_command(KEY))
        assert error.value.code == "DECISION_IN_FUTURE"
