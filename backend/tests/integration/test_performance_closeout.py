"""The four closeout proofs, against real PostgreSQL.

Each one answers a question a reviewer asked about a specific way these numbers
could mislead: money realized by a position that has not finished, an outcome
that changes because of what else is in the filter, a breakdown that looks whole
when it is not, and current exposure vanishing because a date range was applied.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import event, text

from app.adapters.persistence.database import Database
from app.api.schemas.performance_projection import journal_row, performance
from app.application.performance.ports import OutcomeFilters
from app.application.performance.service import PerformanceLimits
from app.domain.performance import MetricStatus, PnlBasis
from tests.integration.paper_support import bars_csv
from tests.integration.paper_support import service as paper_service
from tests.integration.performance_support import (
    CLOCK,
    ENTRY,
    FEES,
    NO_FEES,
    QUIET,
    SHORT_STOP_ON_REST,
    SHORT_TARGET_ONE,
    STOP_ON_REST,
    TARGET_ONE,
    make_position,
    performance_service,
    short_changes,
)

pytestmark = pytest.mark.anyio

D = Decimal
ALL = OutcomeFilters()
DAY_ONE = OutcomeFilters(
    closed_from=datetime(2026, 3, 2, tzinfo=UTC), closed_to=datetime(2026, 3, 3, tzinfo=UTC)
)
LATER_WEEK = OutcomeFilters(
    closed_from=datetime(2026, 4, 1, tzinfo=UTC), closed_to=datetime(2026, 4, 8, tzinfo=UTC)
)


class TestPartialRealizedMoneyOnOpenPositions:
    """Closeout 1 and 5: realized fills count as realized; trades do not."""

    @pytest.mark.parametrize("direction", ["LONG", "SHORT"])
    async def test_a_partial_exit_is_realized_accounting_but_not_a_trade(
        self, database: Database, direction: str
    ) -> None:
        short = direction == "SHORT"
        await make_position(
            database,
            f"closeout-partial-{direction[:4].lower()}01",
            bars=(ENTRY, SHORT_TARGET_ONE if short else TARGET_ONE),
            **(short_changes() if short else {}),
        )

        view = await performance_service(database).summary(ALL)
        summary = view.summary

        # Target 1 realized +80 on 2 units; 2 units are still open.
        assert summary.counts.partially_closed == 1
        assert summary.counts.closed == 0
        assert summary.accounting.gross.value == D("80.00")
        assert summary.accounting.fill_count == 1
        assert summary.accounting.from_open == 1
        # ...and no trade statistic moved.
        assert summary.sample_size == 0
        assert summary.win_rate.status is MetricStatus.UNAVAILABLE
        assert summary.expectancy.status is MetricStatus.UNAVAILABLE
        assert summary.realized_gross.status is MetricStatus.UNAVAILABLE

    async def test_a_modelled_fee_on_a_partial_exit_gives_a_net(self, database: Database) -> None:
        await make_position(
            database, "closeout-partial-fee-01", bars=(ENTRY, TARGET_ONE), policy=FEES
        )

        summary = (await performance_service(database).summary(ALL)).summary

        # Entry charged 4 units x 2, target 1 charged 2 x 2: 12 so far.
        assert summary.accounting.gross.value == D("80.00")
        assert summary.accounting.fees_known.value == D("4")
        assert summary.accounting.net.value == D("76.00")

    async def test_an_unmodelled_fee_on_a_partial_exit_leaves_net_unknown(
        self, database: Database
    ) -> None:
        await make_position(
            database, "closeout-partial-nofee1", bars=(ENTRY, TARGET_ONE), policy=NO_FEES
        )

        summary = (await performance_service(database).summary(ALL)).summary

        assert summary.accounting.gross.value == D("80.00")
        assert summary.accounting.net.status is MetricStatus.UNAVAILABLE
        assert summary.accounting.net.value is None

    async def test_closing_later_does_not_count_the_earlier_fill_twice(
        self, database: Database
    ) -> None:
        position_id = await make_position(
            database, "closeout-partial-then01", bars=(ENTRY, TARGET_ONE)
        )
        before = (await performance_service(database).summary(ALL)).summary
        await paper_service(database, clock=CLOCK).observe(
            position_id, bars_csv(STOP_ON_REST), "stop.csv"
        )

        after = (await performance_service(database).summary(ALL)).summary

        assert before.accounting.gross.value == D("80.00")
        # +80 then -40: the ledger's own total, counted once.
        assert after.accounting.gross.value == D("40.00")
        assert after.accounting.fill_count == 2
        assert after.sample_size == 1
        assert after.realized_gross.value == D("40.00")


class TestOutcomeFactsDoNotMoveWithTheFilter:
    """Closeout 2: a position's own outcome depends on that position alone."""

    async def test_a_position_keeps_its_gross_and_net_outcome_whatever_else_is_selected(
        self, database: Database
    ) -> None:
        # A: gross +40, fees 16 -> net +24. B: no fees modelled at all.
        await make_position(database, "closeout-stable-a0001", policy=FEES)
        service = performance_service(database)
        alone = await service.journal_page(ALL, offset=0, limit=10)
        a_alone = journal_row(alone.rows[0])

        await make_position(database, "closeout-stable-b0001", policy=NO_FEES)
        together = await service.journal_page(ALL, offset=0, limit=10)
        a_together = next(
            journal_row(row)
            for row in together.rows
            if row.outcome.position_id == alone.rows[0].outcome.position_id
        )
        filtered = await service.journal_page(
            OutcomeFilters(symbol="TEST_FIXTURE_FUT"), offset=0, limit=10
        )
        a_filtered = next(
            journal_row(row)
            for row in filtered.rows
            if row.outcome.position_id == alone.rows[0].outcome.position_id
        )

        for view in (a_alone, a_together, a_filtered):
            assert view.outcome_gross == "WIN"
            assert view.outcome_net == "WIN"
            assert view.realized_gross == "40.00"
            assert view.realized_net == "24.00"
        assert a_alone == a_together == a_filtered

    async def test_the_aggregate_basis_changes_but_says_so(self, database: Database) -> None:
        await make_position(database, "closeout-basis-a00001", policy=FEES)
        service = performance_service(database)
        alone = (await service.summary(ALL)).summary
        await make_position(database, "closeout-basis-b00001", policy=NO_FEES)
        together = (await service.summary(ALL)).summary

        assert alone.basis is PnlBasis.REALIZED_NET
        assert "every completed position" in alone.basis_reason
        assert together.basis is PnlBasis.REALIZED_GROSS
        assert "1 of 2" in together.basis_reason
        # The response states the basis every time, so a reader cannot mistake one for the other.
        assert (
            performance(
                type("V", (), {"summary": together, "filters": ALL, "total_matching": 2})()
            ).basis
            == "REALIZED_GROSS"
        )


class TestDateRangeSemantics:
    """Closeout 4: what a date filter does to each population."""

    async def test_open_exposure_survives_a_date_range(self, database: Database) -> None:
        await make_position(database, "closeout-range-open01", bars=(ENTRY, QUIET))
        await make_position(database, "closeout-range-closed1")
        service = performance_service(database)

        everything = (await service.summary(ALL)).summary
        in_range = (await service.summary(DAY_ONE)).summary
        other_week = (await service.summary(LATER_WEEK)).summary

        assert everything.counts.open_exposure == 1
        # A range selects completed trades; it never hides what is open now.
        assert in_range.counts.open_exposure == 1
        assert other_week.counts.open_exposure == 1
        assert in_range.sample_size == 1
        assert other_week.sample_size == 0
        assert other_week.unrealized_gross_open.status is MetricStatus.AVAILABLE

    async def test_realized_accounting_follows_fill_time_not_the_range_of_closes(
        self, database: Database
    ) -> None:
        await make_position(database, "closeout-range-fills01", bars=(ENTRY, TARGET_ONE))
        service = performance_service(database)

        containing = await service.summary(
            OutcomeFilters(
                closed_from=datetime(2026, 3, 2, tzinfo=UTC),
                closed_to=datetime(2026, 3, 2, 12, tzinfo=UTC),
            )
        )
        excluding = await service.summary(LATER_WEEK)

        assert containing.summary.accounting.gross.value == D("80.00")
        assert excluding.summary.accounting.fill_count == 0
        assert excluding.summary.accounting.gross.status is MetricStatus.UNAVAILABLE

    async def test_a_realized_fill_keeps_its_own_time_after_the_position_closes(
        self, database: Database
    ) -> None:
        """Target 1 fills at 11:00, the stop closes the rest at 12:00."""
        await make_position(database, "closeout-range-twofill1")
        service = performance_service(database)

        around_first = await service.summary(
            OutcomeFilters(
                closed_from=datetime(2026, 3, 2, 10, 30, tzinfo=UTC),
                closed_to=datetime(2026, 3, 2, 11, 30, tzinfo=UTC),
            )
        )
        around_second = await service.summary(
            OutcomeFilters(
                closed_from=datetime(2026, 3, 2, 11, 30, tzinfo=UTC),
                closed_to=datetime(2026, 3, 2, 12, 30, tzinfo=UTC),
            )
        )

        # +80 belongs to 11:00 and -40 to 12:00; neither moves to the other.
        assert around_first.summary.accounting.gross.value == D("80.00")
        assert around_first.summary.accounting.fill_count == 1
        assert around_second.summary.accounting.gross.value == D("-40.00")
        assert around_second.summary.accounting.fill_count == 1
        # The completed trade itself belongs to its closing time only.
        assert around_first.summary.sample_size == 0
        assert around_second.summary.sample_size == 1

    async def test_a_boundary_timestamp_is_inclusive_at_both_ends(self, database: Database) -> None:
        await make_position(database, "closeout-range-bound01")
        service = performance_service(database)
        closed_at = datetime(2026, 3, 2, 12, tzinfo=UTC)

        exact = await service.summary(OutcomeFilters(closed_from=closed_at, closed_to=closed_at))
        just_after = await service.summary(
            OutcomeFilters(closed_from=datetime(2026, 3, 2, 12, 0, 1, tzinfo=UTC))
        )

        assert exact.summary.sample_size == 1
        assert just_after.summary.sample_size == 0


class TestBreakdownCompleteness:
    """Closeout 3: a bounded breakdown says it is bounded."""

    async def test_a_complete_breakdown_says_so(self, database: Database) -> None:
        await make_position(database, "closeout-complete-0001")

        view = await performance_service(database).breakdowns(ALL)

        assert view.by_instrument.is_complete is True
        assert view.by_instrument.omitted == 0
        assert view.by_direction.total == len(view.by_direction.rows)

    async def test_an_over_bound_breakdown_is_marked_incomplete_with_its_count(
        self, database: Database
    ) -> None:
        # Two directions and a bound of one: the second group must not vanish
        # silently, and the headline must still cover both.
        for index in range(3):
            await make_position(database, f"closeout-bound-{index:010d}")
        await make_position(
            database,
            "closeout-bound-short001",
            bars=(ENTRY, SHORT_TARGET_ONE, SHORT_STOP_ON_REST),
            **short_changes(),
        )
        service = performance_service(database)
        service._limits = PerformanceLimits(max_breakdown_rows=1)  # noqa: SLF001

        view = await service.breakdowns(ALL)
        summary = (await service.summary(ALL)).summary

        assert view.by_direction.is_complete is False
        assert view.by_direction.total == 2
        assert len(view.by_direction.rows) == 1
        assert view.by_direction.omitted == 1
        # The headline still describes the whole population, not the shown rows.
        assert summary.sample_size == 4
        assert summary.realized_gross.value == D("160.00")

    async def test_tag_suggestions_report_whether_they_are_all_of_them(
        self, database: Database
    ) -> None:
        service = performance_service(database)
        first = await make_position(database, "closeout-tags-00000001")
        await service.write_annotation(
            first, note=None, tags=["alpha", "beta", "gamma"], expected_version=0
        )
        service._limits = PerformanceLimits(max_tag_rows=2)  # noqa: SLF001

        tags, is_complete = await service.tags_in_use()

        assert len(tags) == 2
        assert is_complete is False


class TestOpenPositionReadIsBounded:
    """Closeout 6: verified open-position marks without an N+1."""

    @pytest.mark.parametrize("open_positions", [0, 1, 5])
    async def test_the_query_count_does_not_grow_with_open_positions(
        self, database: Database, open_positions: int
    ) -> None:
        for index in range(open_positions):
            await make_position(database, f"closeout-openq-{index:09d}", bars=(ENTRY, QUIET))
        await make_position(database, "closeout-openq-closed01")
        statements: list[str] = []

        def record(*arguments: object) -> None:
            statements.append(str(arguments[2]))

        engine = database.engine.sync_engine
        event.listen(engine, "before_cursor_execute", record)
        try:
            view = await performance_service(database).summary(ALL)
        finally:
            event.remove(engine, "before_cursor_execute", record)

        selects = [s for s in statements if s.lstrip().upper().startswith(("SELECT", "WITH"))]
        # Three for the ledger; two more only when something is open.
        assert len(selects) == (3 if open_positions == 0 else 5), selects
        assert view.summary.counts.open_exposure == open_positions

    async def test_the_open_total_is_the_replayed_one(self, database: Database) -> None:
        await make_position(database, "closeout-openmark-001", bars=(ENTRY, QUIET))

        summary = (await performance_service(database).summary(ALL)).summary

        # Mark 100.75 against an entry of 100, 4 units, point value 10.
        assert summary.unrealized_gross_open.status is MetricStatus.AVAILABLE
        assert summary.unrealized_gross_open.value == D("30.00")

    async def test_a_corrupted_row_is_refused_on_the_open_path(self, database: Database) -> None:
        from app.application.performance.service import (
            PerformanceErrorKind,
            PerformanceServiceError,
        )

        position_id = await make_position(database, "closeout-openmark-002", bars=(ENTRY, QUIET))
        async with database.engine.begin() as connection:
            await connection.execute(
                text("UPDATE paper_positions SET unrealized_gross = 999999 WHERE id = :id"),
                {"id": position_id},
            )

        with pytest.raises(PerformanceServiceError) as error:
            await performance_service(database).summary(ALL)

        assert error.value.kind is PerformanceErrorKind.UNAVAILABLE
        assert "999999" not in error.value.detail
