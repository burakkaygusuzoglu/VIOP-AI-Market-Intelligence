"""The journal against real PostgreSQL: mutable, versioned, and inert.

A note is the one thing in this application a person can overwrite. These tests
check that overwriting it is safe - two editors cannot silently clobber each
other - and that it stays exactly what it is: text attached to a position,
unable to move a single financial value.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

from app.adapters.persistence import (
    backtest_models,  # noqa: F401  (registers Phase 12 tables)
    journal_models,  # noqa: F401  (registers the table)
    replay_models,  # noqa: F401  (registers Phase 11 tables)
    shadow_models,  # noqa: F401  (registers Phase 14 tables)
)
from app.adapters.persistence.base import Base
from app.adapters.persistence.database import Database
from app.application.performance.ports import OutcomeFilters
from app.application.performance.service import (
    PerformanceErrorKind,
    PerformanceServiceError,
)
from app.domain.journal import MAX_NOTE_LENGTH
from tests.integration.performance_support import make_position, performance_service

pytestmark = pytest.mark.anyio

D = Decimal
ALL = OutcomeFilters()
HOSTILE = "<script>alert('xss')</script><img src=x onerror=alert(1)>"


class TestSchema:
    async def test_the_migration_matches_the_model_exactly(self, database: Database) -> None:
        async with database.engine.connect() as connection:
            diff = await connection.run_sync(
                lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata)
            )

        assert diff == []

    async def test_phase_10_added_exactly_one_table(self, database: Database) -> None:
        async with database.engine.connect() as connection:
            names = await connection.run_sync(lambda sync: sync.dialect.get_table_names(sync))

        assert "paper_journal_annotations" in names
        assert sorted(name for name in names if name.startswith("paper_")) == [
            "paper_journal_annotations",
            "paper_position_events",
            "paper_positions",
        ]

    async def test_no_metrics_cache_table_exists(self, database: Database) -> None:
        """Performance is computed from the ledger, so nothing caches numbers."""
        async with database.engine.connect() as connection:
            names = await connection.run_sync(lambda sync: sync.dialect.get_table_names(sync))

        for suspicious in ("performance", "metrics", "statistics", "equity", "journal_history"):
            assert not any(suspicious in name for name in names)


class TestWritingAndReading:
    async def test_a_first_write_creates_version_one(self, database: Database) -> None:
        position_id = await make_position(database, "journal-key-0000000001")
        service = performance_service(database)

        saved = await service.write_annotation(
            position_id, note="Plana sadık kaldım.", tags=["Breakout"], expected_version=0
        )

        assert saved.version == 1
        assert saved.note == "Plana sadık kaldım."
        assert saved.tags == ("breakout",)
        assert saved.created_at is not None and saved.updated_at is not None

    async def test_a_position_without_a_note_reads_as_empty_version_zero(
        self, database: Database
    ) -> None:
        position_id = await make_position(database, "journal-key-0000000002")

        annotation = await performance_service(database).annotation(position_id)

        assert annotation.empty
        assert annotation.version == 0
        assert annotation.tags == ()

    async def test_a_second_write_bumps_the_version_and_keeps_created_at(
        self, database: Database
    ) -> None:
        position_id = await make_position(database, "journal-key-0000000003")
        service = performance_service(database)
        first = await service.write_annotation(position_id, note="ilk", tags=[], expected_version=0)

        second = await service.write_annotation(
            position_id, note="ikinci", tags=["trend"], expected_version=1
        )

        assert second.version == 2
        assert second.note == "ikinci"
        assert second.created_at == first.created_at
        assert second.updated_at >= first.updated_at  # type: ignore[operator]

    async def test_a_note_can_be_cleared_without_deleting_history(self, database: Database) -> None:
        position_id = await make_position(database, "journal-key-0000000004")
        service = performance_service(database)
        await service.write_annotation(position_id, note="yazdım", tags=["a"], expected_version=0)

        cleared = await service.write_annotation(
            position_id, note=None, tags=[], expected_version=1
        )

        assert cleared.empty
        assert cleared.version == 3 - 1  # version 2: the row remains, the text is gone
        view = await service.summary(ALL)
        assert view.summary.counts.total == 1

    async def test_tags_round_trip_normalised_and_ordered(self, database: Database) -> None:
        position_id = await make_position(database, "journal-key-0000000005")
        service = performance_service(database)

        saved = await service.write_annotation(
            position_id,
            note=None,
            tags=["Zeta", "alpha", "ZETA ", "Trend Following"],
            expected_version=0,
        )
        reread = await service.annotation(position_id)

        assert saved.tags == ("alpha", "trend following", "zeta")
        assert reread.tags == saved.tags

    async def test_hostile_text_is_stored_verbatim_and_inertly(self, database: Database) -> None:
        position_id = await make_position(database, "journal-key-0000000006")
        service = performance_service(database)

        saved = await service.write_annotation(
            position_id, note=HOSTILE, tags=[], expected_version=0
        )

        assert saved.note == HOSTILE  # stored as text; the frontend renders it as text
        async with database.engine.connect() as connection:
            stored = await connection.scalar(
                text("SELECT note FROM paper_journal_annotations WHERE position_id = :id"),
                {"id": position_id},
            )
        assert stored == HOSTILE

    async def test_a_note_over_the_bound_is_refused_by_the_service(
        self, database: Database
    ) -> None:
        position_id = await make_position(database, "journal-key-0000000007")

        with pytest.raises(PerformanceServiceError) as error:
            await performance_service(database).write_annotation(
                position_id, note="a" * (MAX_NOTE_LENGTH + 1), tags=[], expected_version=0
            )

        assert error.value.kind is PerformanceErrorKind.INVALID
        assert error.value.code == "JOURNAL_CONTENT_INVALID"

    async def test_a_journal_for_an_unknown_position_is_not_found(self, database: Database) -> None:
        service = performance_service(database)

        with pytest.raises(PerformanceServiceError) as error:
            await service.write_annotation(
                "PP-000000000000000000000000", note="x", tags=[], expected_version=0
            )

        assert error.value.kind is PerformanceErrorKind.NOT_FOUND


class TestConcurrency:
    async def test_a_stale_version_is_refused_rather_than_overwriting(
        self, database: Database
    ) -> None:
        position_id = await make_position(database, "journal-key-0000000010")
        service = performance_service(database)
        await service.write_annotation(position_id, note="A yazdı", tags=[], expected_version=0)
        # Client A still believes it holds version 1 after B writes version 2.
        await service.write_annotation(position_id, note="B yazdı", tags=[], expected_version=1)

        with pytest.raises(PerformanceServiceError) as error:
            await service.write_annotation(
                position_id, note="A üzerine yazmaya çalıştı", tags=[], expected_version=1
            )

        assert error.value.kind is PerformanceErrorKind.CONFLICT
        assert error.value.code == "JOURNAL_VERSION_CONFLICT"
        assert (await service.annotation(position_id)).note == "B yazdı"

    async def test_two_first_writes_race_and_the_loser_is_told(self, database: Database) -> None:
        import asyncio

        position_id = await make_position(database, "journal-key-0000000011")
        service = performance_service(database)

        results = await asyncio.gather(
            service.write_annotation(position_id, note="first", tags=[], expected_version=0),
            service.write_annotation(position_id, note="second", tags=[], expected_version=0),
            return_exceptions=True,
        )

        failures = [item for item in results if isinstance(item, PerformanceServiceError)]
        successes = [item for item in results if not isinstance(item, Exception)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].kind is PerformanceErrorKind.CONFLICT

    async def test_a_failed_write_leaves_the_stored_note_untouched(
        self, database: Database
    ) -> None:
        position_id = await make_position(database, "journal-key-0000000012")
        service = performance_service(database)
        await service.write_annotation(position_id, note="kalıcı", tags=["a"], expected_version=0)

        with pytest.raises(PerformanceServiceError):
            await service.write_annotation(
                position_id, note="b" * (MAX_NOTE_LENGTH + 1), tags=[], expected_version=1
            )

        current = await service.annotation(position_id)
        assert current.note == "kalıcı"
        assert current.tags == ("a",)
        assert current.version == 1


class TestJournalCannotTouchMoney:
    async def test_writing_a_note_changes_no_financial_value(self, database: Database) -> None:
        position_id = await make_position(database, "journal-key-0000000020")
        service = performance_service(database)
        before = await service.summary(ALL)

        async with database.engine.connect() as connection:
            position_before = (
                await connection.execute(
                    text("SELECT * FROM paper_positions WHERE id = :id"), {"id": position_id}
                )
            ).all()
            ledger_before = (
                await connection.execute(
                    text(
                        "SELECT sequence, event_type, data FROM paper_position_events "
                        "WHERE position_id = :id ORDER BY sequence"
                    ),
                    {"id": position_id},
                )
            ).all()

        # Both write paths: the first insert and a later update.
        await service.write_annotation(
            position_id, note="bu not hiçbir rakamı değiştirmez", tags=["x"], expected_version=0
        )
        await service.write_annotation(
            position_id, note="düzenleme de değiştirmez", tags=["x", "y"], expected_version=1
        )

        after = await service.summary(ALL)
        async with database.engine.connect() as connection:
            position_after = (
                await connection.execute(
                    text("SELECT * FROM paper_positions WHERE id = :id"), {"id": position_id}
                )
            ).all()
            ledger_after = (
                await connection.execute(
                    text(
                        "SELECT sequence, event_type, data FROM paper_position_events "
                        "WHERE position_id = :id ORDER BY sequence"
                    ),
                    {"id": position_id},
                )
            ).all()

        assert ledger_after == ledger_before
        assert position_after == position_before  # the row itself, column for column
        assert after.summary.realized_gross.value == before.summary.realized_gross.value
        assert after.summary.sample_size == before.summary.sample_size
        assert after.summary.win_rate.value == before.summary.win_rate.value

    async def test_a_tag_never_becomes_an_outcome_or_a_setup(self, database: Database) -> None:
        position_id = await make_position(database, "journal-key-0000000021")
        service = performance_service(database)
        await service.write_annotation(
            position_id, note=None, tags=["breakout", "win"], expected_version=0
        )

        page = await service.journal_page(ALL, offset=0, limit=10)
        row = page.rows[0]

        assert row.annotation.tags == ("breakout", "win")
        assert row.annotation.provenance == "USER_AUTHORED"
        # The outcome is computed from the ledger, not from the tag text.
        assert row.outcome.realized_gross == D("40.00")
        assert row.outcome.population.value == "CLOSED"


class TestTagsAndFiltering:
    async def test_filtering_by_tag_selects_only_tagged_positions(self, database: Database) -> None:
        tagged = await make_position(database, "journal-key-0000000030")
        await make_position(database, "journal-key-0000000031")
        service = performance_service(database)
        await service.write_annotation(tagged, note=None, tags=["Breakout"], expected_version=0)

        view = await service.summary(OutcomeFilters(tag="breakout"))
        everything = await service.summary(ALL)

        assert view.summary.counts.total == 1
        assert everything.summary.counts.total == 2

    async def test_tag_counts_are_ordered_by_use_then_name(self, database: Database) -> None:
        service = performance_service(database)
        first = await make_position(database, "journal-key-0000000032")
        second = await make_position(database, "journal-key-0000000033")
        await service.write_annotation(
            first, note=None, tags=["common", "rare"], expected_version=0
        )
        await service.write_annotation(second, note=None, tags=["common"], expected_version=0)

        counts, is_complete = await service.tags_in_use()

        assert counts == (("common", 2), ("rare", 1))
        assert is_complete is True

    async def test_journal_pages_are_bounded_and_deterministic(self, database: Database) -> None:
        for index in range(4):
            await make_position(database, f"journal-key-000000004{index}")
        service = performance_service(database)

        first = await service.journal_page(ALL, offset=0, limit=2)
        again = await service.journal_page(ALL, offset=0, limit=2)
        second = await service.journal_page(ALL, offset=2, limit=2)

        assert first.total == 4
        assert len(first.rows) == 2
        assert [row.outcome.position_id for row in first.rows] == [
            row.outcome.position_id for row in again.rows
        ]
        assert not {row.outcome.position_id for row in first.rows} & {
            row.outcome.position_id for row in second.rows
        }

    async def test_an_enormous_page_request_is_bounded_not_refused(
        self, database: Database
    ) -> None:
        await make_position(database, "journal-key-0000000050")

        page = await performance_service(database).journal_page(ALL, offset=0, limit=10_000)

        assert page.limit == 50
