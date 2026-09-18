"""Analytics read the ledger, against real PostgreSQL.

The claim Phase 10 has to earn is that a metric is derived from append-only
facts. These tests earn it three ways: the folded outcome equals what a full
verified rebuild produces; a deliberately corrupted projection row does not
change any number; and every population, fee state and filter behaves the same
through the database as it does in the pure engine.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app.adapters.performance.paper_source import SqlPaperPerformanceSource
from app.adapters.persistence.database import Database
from app.adapters.products.futures import FuturesSnapshotCodec
from app.application.performance.ports import OutcomeFilters
from app.application.performance.service import (
    PerformanceErrorKind,
    PerformanceLimits,
    PerformanceServiceError,
)
from app.domain.common.enums import Direction, Timeframe
from app.domain.performance import MetricStatus, PnlBasis
from tests.integration.paper_support import bars_csv
from tests.integration.paper_support import service as paper_service
from tests.integration.performance_support import (
    CLOCK,
    ENTRY,
    FEES,
    HALT,
    NO_FEES,
    QUIET,
    SHORT_STOP_ON_REST,
    SHORT_TARGET_ONE,
    TARGET_ONE,
    make_position,
    performance_service,
    short_changes,
)

pytestmark = pytest.mark.anyio

D = Decimal
ALL = OutcomeFilters()


class TestLedgerIsTheSource:
    async def test_a_folded_outcome_matches_a_full_verified_rebuild(
        self, database: Database
    ) -> None:
        """The cheap path and the expensive path must agree, or one is wrong."""
        position_id = await make_position(database, "perf-key-000000000001", policy=FEES)

        source = SqlPaperPerformanceSource(database, FuturesSnapshotCodec())
        page = await source.outcomes(ALL, limit=10)
        folded = page.records[0]

        verified = (await paper_service(database, clock=CLOCK).get(position_id)).stored
        assert folded.position_id == verified.position_id
        assert folded.realized_gross == verified.projection.realized_gross
        assert folded.fees_total == verified.projection.fees_total
        assert folded.realized_net == verified.projection.realized_net
        assert folded.population.value == verified.projection.state
        assert folded.direction.value == verified.projection.direction
        assert folded.instrument.symbol == verified.projection.symbol
        assert folded.quantity == verified.projection.quantity

    async def test_the_partial_exit_position_is_one_trade_with_three_fills(
        self, database: Database
    ) -> None:
        # Entry 100 x4; target 1 fills 2 at 104 (+80); the stop takes the rest
        # at 98 (-40). Gross +40. Fees 2/unit over 4 + 2 + 2 units = 16.
        await make_position(database, "perf-key-000000000002", policy=FEES)

        view = await performance_service(database).summary(ALL)

        assert view.summary.sample_size == 1
        assert view.summary.fill_count == 2
        assert view.summary.realized_gross.value == D("40.00")
        assert view.summary.fees_known.value == D("16")
        assert view.summary.realized_net.value == D("24.00")
        assert view.summary.wins == 1

    async def test_a_short_position_is_folded_with_the_same_arithmetic(
        self, database: Database
    ) -> None:
        await make_position(
            database,
            "perf-key-000000000003",
            bars=(ENTRY, SHORT_TARGET_ONE, SHORT_STOP_ON_REST),
            **short_changes(),
        )

        view = await performance_service(database).summary(ALL)
        rows = {
            row.key: row
            for row in (await performance_service(database).breakdowns(ALL)).by_direction.rows
        }

        assert view.summary.sample_size == 1
        assert rows["SHORT"].sample_size == 1
        assert rows["SHORT"].realized_gross.value == view.summary.realized_gross.value

    async def test_analytics_never_read_the_projection_columns(self, database: Database) -> None:
        """Corrupt every projection column; the ledger-derived answer stands."""
        position_id = await make_position(database, "perf-key-000000000004", policy=FEES)
        before = await performance_service(database).summary(ALL)

        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE paper_positions
                    SET realized_gross = 999999,
                        fees_total = 123456,
                        realized_net = 987654,
                        state = 'OPEN',
                        remaining = 4,
                        direction = 'SHORT',
                        quantity = 99
                    WHERE id = :id
                    """
                ),
                {"id": position_id},
            )

        after = await performance_service(database).summary(ALL)

        assert (
            after.summary.realized_gross.value == before.summary.realized_gross.value == D("40.00")
        )
        assert after.summary.realized_net.value == D("24.00")
        assert after.summary.counts.closed == 1
        assert after.summary.sample_size == 1
        assert "999999" not in str(after.summary)

    async def test_a_corrupted_projection_is_still_refused_on_the_open_mark_path(
        self, database: Database
    ) -> None:
        """An open position's mark comes from Phase 9's verified read, which refuses."""
        position_id = await make_position(database, "perf-key-000000000005", bars=(ENTRY, QUIET))

        async with database.engine.begin() as connection:
            await connection.execute(
                text("UPDATE paper_positions SET realized_gross = 4242 WHERE id = :id"),
                {"id": position_id},
            )

        with pytest.raises(PerformanceServiceError) as error:
            await performance_service(database).summary(ALL)

        assert error.value.kind is PerformanceErrorKind.UNAVAILABLE
        assert error.value.code == "PERFORMANCE_SOURCE_UNAVAILABLE"


class TestPopulationsThroughTheDatabase:
    async def test_each_population_is_recognised_from_its_ledger(self, database: Database) -> None:
        svc = paper_service(database, clock=CLOCK)
        await make_position(database, "perf-key-000000000010", bars=())  # pending
        cancelled = await make_position(database, "perf-key-000000000011", bars=())
        await svc.cancel(cancelled)
        await make_position(database, "perf-key-000000000012", bars=(ENTRY, QUIET))  # open
        await make_position(database, "perf-key-000000000013", bars=(ENTRY, TARGET_ONE))  # partial
        await make_position(
            database,
            "perf-key-000000000014",
            bars=(ENTRY, (1, "101", "106.50", "97.50", "99")),
            policy=HALT,
        )  # halted
        await make_position(database, "perf-key-000000000015")  # closed

        view = await performance_service(database).summary(ALL)
        counts = view.summary.counts

        assert counts.total == 6
        assert counts.pending_entry == 1
        assert counts.cancelled == 1
        assert counts.open_positions == 1
        assert counts.partially_closed == 1
        assert counts.ambiguous_halted == 1
        assert counts.closed == 1
        assert view.summary.sample_size == 1  # only the closed one is a trade

    async def test_a_rejected_entry_is_never_a_losing_trade(self, database: Database) -> None:
        # The entry bar opens at 97.50, at or beyond the stop of 98: rejected.
        await make_position(
            database,
            "perf-key-000000000016",
            bars=((0, "97.50", "98.50", "97", "98"),),
        )

        view = await performance_service(database).summary(ALL)

        assert view.summary.counts.rejected == 1
        assert view.summary.sample_size == 0
        assert view.summary.win_rate.status is MetricStatus.UNAVAILABLE
        assert view.summary.losses == 0


class TestFeeCoverageThroughTheDatabase:
    async def test_mixed_coverage_is_reported_not_summed(self, database: Database) -> None:
        await make_position(database, "perf-key-000000000020", policy=FEES)
        await make_position(database, "perf-key-000000000021", policy=NO_FEES)

        view = await performance_service(database).summary(ALL)

        assert view.summary.sample_size == 2
        assert view.summary.basis is PnlBasis.REALIZED_GROSS
        assert view.summary.realized_net.status is MetricStatus.PARTIAL_COVERAGE
        assert view.summary.fee_coverage.covered == 1
        assert view.summary.fee_coverage.total == 2
        assert view.summary.realized_gross.value == D("80.00")

    async def test_a_zero_fee_is_modelled_and_a_missing_fee_is_not(
        self, database: Database
    ) -> None:
        from app.domain.paper import FeeMode, FeePolicy, SimulationPolicy

        zero_fee = SimulationPolicy(
            fees=FeePolicy(mode=FeeMode.USER_DEFINED_PER_UNIT, per_unit=D("0"))
        )
        await make_position(database, "perf-key-000000000022", policy=zero_fee)

        view = await performance_service(database).summary(ALL)

        assert view.summary.basis is PnlBasis.REALIZED_NET
        assert view.summary.fees_known.value == D("0")
        assert view.summary.realized_net.value == D("40.00")


class TestFiltersAreConsistent:
    async def test_the_same_filters_drive_summary_breakdowns_and_journal(
        self, database: Database
    ) -> None:
        await make_position(database, "perf-key-000000000030")  # LONG closed +40
        await make_position(
            database,
            "perf-key-000000000031",
            bars=(ENTRY, SHORT_TARGET_ONE, SHORT_STOP_ON_REST),
            **short_changes(),
        )
        service = performance_service(database)
        longs = OutcomeFilters(direction=Direction.LONG)

        summary = await service.summary(longs)
        breakdowns = await service.breakdowns(longs)
        journal = await service.journal_page(longs, offset=0, limit=20)

        assert summary.summary.sample_size == 1
        assert {row.key for row in breakdowns.by_direction.rows} == {"LONG"}
        assert journal.total == 1
        assert all(row.outcome.direction is Direction.LONG for row in journal.rows)

    async def test_a_market_time_range_selects_by_the_closing_fill(
        self, database: Database
    ) -> None:
        await make_position(database, "perf-key-000000000032")
        service = performance_service(database)

        inside = await service.summary(
            OutcomeFilters(
                closed_from=CLOCK.now().replace(year=2026, month=3, day=2, hour=0),
                closed_to=CLOCK.now().replace(year=2026, month=3, day=3, hour=0),
            )
        )
        outside = await service.summary(
            OutcomeFilters(
                closed_from=CLOCK.now().replace(year=2026, month=4, day=1, hour=0),
                closed_to=CLOCK.now().replace(year=2026, month=4, day=2, hour=0),
            )
        )

        assert inside.summary.sample_size == 1
        assert outside.summary.sample_size == 0
        assert outside.summary.win_rate.status is MetricStatus.UNAVAILABLE

    async def test_an_unknown_symbol_selects_nothing_rather_than_everything(
        self, database: Database
    ) -> None:
        await make_position(database, "perf-key-000000000033")

        view = await performance_service(database).summary(OutcomeFilters(symbol="NOT_A_FIXTURE"))

        assert view.summary.counts.total == 0
        assert view.total_matching == 0

    async def test_a_timeframe_filter_uses_the_stored_plan(self, database: Database) -> None:
        await make_position(database, "perf-key-000000000034")

        matching = await performance_service(database).summary(
            OutcomeFilters(timeframe=Timeframe.H1)
        )
        other = await performance_service(database).summary(OutcomeFilters(timeframe=Timeframe.M15))

        assert matching.summary.sample_size == 1
        assert other.summary.counts.total == 0


class TestBoundsAndOrdering:
    async def test_an_oversized_selection_is_refused_rather_than_truncated(
        self, database: Database
    ) -> None:
        await make_position(database, "perf-key-000000000040")
        await make_position(database, "perf-key-000000000041")
        service = performance_service(database)
        service._limits = PerformanceLimits(max_positions_per_analysis=1)  # noqa: SLF001

        with pytest.raises(PerformanceServiceError) as error:
            await service.summary(ALL)

        assert error.value.kind is PerformanceErrorKind.TOO_LARGE
        assert error.value.code == "ANALYSIS_RANGE_TOO_LARGE"
        assert "2 positions match" in error.value.detail

    async def test_ordering_is_stable_across_repeated_reads(self, database: Database) -> None:
        for index in range(4):
            await make_position(database, f"perf-key-00000000005{index}")
        service = performance_service(database)

        first = await service.summary(ALL)
        second = await service.summary(ALL)

        assert [p.position_id for p in first.summary.timeline] == [
            p.position_id for p in second.summary.timeline
        ]
        assert first.summary.timeline == second.summary.timeline

    async def test_a_summary_costs_a_fixed_number_of_queries(self, database: Database) -> None:
        """Three statements, whatever the number of positions.

        A count to check the request is within bounds, the matching ids, and one
        read of their financial events. Nothing is issued per position.
        """
        from sqlalchemy import event

        for index in range(8):
            await make_position(database, f"perf-key-00000000007{index}")
        statements: list[str] = []

        def record(*event_arguments: object) -> None:
            statements.append(str(event_arguments[2]))

        engine = database.engine.sync_engine
        event.listen(engine, "before_cursor_execute", record)
        try:
            await performance_service(database).summary(ALL)
        finally:
            event.remove(engine, "before_cursor_execute", record)

        selects = [s for s in statements if s.lstrip().upper().startswith(("SELECT", "WITH"))]
        assert len(selects) == 3, selects

    async def test_the_cost_of_analytics_does_not_follow_the_bar_count(
        self, database: Database
    ) -> None:
        """A position fed many bars carries the same few financial events."""
        position_id = await make_position(database, "perf-key-000000000060", bars=(ENTRY,))
        svc = paper_service(database, clock=CLOCK)
        rows = tuple((hour, "100.50", "101", "100", "100.75") for hour in range(1, 60))
        await svc.observe(position_id, bars_csv(*rows), "quiet.csv")

        async with database.engine.begin() as connection:
            total = await connection.scalar(
                text("SELECT count(*) FROM paper_position_events WHERE position_id = :id"),
                {"id": position_id},
            )
            financial = await connection.scalar(
                text(
                    "SELECT count(*) FROM paper_position_events "
                    "WHERE position_id = :id AND event_type <> 'OBSERVATION_APPLIED'"
                ),
                {"id": position_id},
            )

        assert total >= 61
        assert financial <= 3
