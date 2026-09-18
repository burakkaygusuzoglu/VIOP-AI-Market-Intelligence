"""Realized money and completed trades are two different questions.

A position that took one target and still holds the rest has realized that
money - and has not finished a trade. Both statements are true at once, and the
closeout asks for proof that neither hides the other: the realized amount must
be visible in accounting, and the unfinished position must stay out of every
trade statistic.

Every amount here is TEST_FIXTURE data, hand-derived in the test.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.domain.common.enums import Direction
from app.domain.performance import (
    MetricStatus,
    PnlBasis,
    Population,
    PositionOutcome,
    RealizedFill,
    realized_accounting,
    summarise,
)
from tests.factories_performance import START, outcome

D = Decimal
HOUR = timedelta(hours=1)


def fill(amount: str, *, fee: str | None = None, hours: int = 1) -> RealizedFill:
    return RealizedFill(
        amount=D(amount), fee=None if fee is None else D(fee), market_time=START + HOUR * hours
    )


def partially_open(
    *fills: RealizedFill,
    direction: Direction = Direction.LONG,
    index: int = 1,
) -> PositionOutcome:
    """A position that has realized some fills and is still exposed."""
    realized = sum((item.amount for item in fills), D(0))
    costed = [item.fee for item in fills if item.fee is not None]
    fees = sum(costed, D(0)) if len(costed) == len(fills) and fills else None
    return PositionOutcome(
        position_id=f"PP-{index:024d}",
        instrument=outcome(index, "0").instrument,
        direction=direction,
        timeframe=outcome(index, "0").timeframe,
        quantity=4,
        population=Population.ENTERED_PARTIALLY_CLOSED,
        realized_gross=realized,
        fees_total=fees,
        realized_net=None if fees is None else realized - fees,
        unrealized_gross=D("25"),
        fee_mode="NOT_MODELLED" if fees is None else "USER_DEFINED_PER_UNIT",
        decision_time=START,
        entry_time=START,
        terminal_time=None,
        fills=fills,
    )


class TestOpenPositionsKeepTheirRealizedMoney:
    def test_an_open_position_with_no_exit_realized_nothing(self) -> None:
        open_position = outcome(1, "0", population=Population.OPEN)

        result = summarise([open_position])

        assert result.sample_size == 0
        assert result.accounting.fill_count == 0
        assert result.accounting.gross.status is MetricStatus.UNAVAILABLE
        assert result.win_rate.status is MetricStatus.UNAVAILABLE

    @pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
    def test_a_realized_partial_is_counted_in_accounting_and_not_as_a_trade(
        self, direction: Direction
    ) -> None:
        position = partially_open(fill("80"), direction=direction)

        result = summarise([position])

        # The money is visible...
        assert result.accounting.gross.value == D("80")
        assert result.accounting.fill_count == 1
        assert result.accounting.from_open == 1
        assert result.accounting.from_completed == 0
        # ...and the trade statistics are untouched.
        assert result.sample_size == 0
        assert result.wins == 0
        assert result.win_rate.status is MetricStatus.UNAVAILABLE
        assert result.expectancy.status is MetricStatus.UNAVAILABLE
        assert result.streaks.current_length == 0

    def test_a_realized_partial_with_a_modelled_fee_reports_net(self) -> None:
        position = partially_open(fill("80", fee="4"))

        result = summarise([position])

        assert result.accounting.gross.value == D("80")
        assert result.accounting.fees_known.value == D("4")
        assert result.accounting.net.value == D("76")
        assert result.accounting.net.basis is PnlBasis.REALIZED_NET

    def test_a_realized_partial_without_a_modelled_fee_leaves_net_unknown(self) -> None:
        position = partially_open(fill("80"))

        result = summarise([position])

        assert result.accounting.gross.value == D("80")
        assert result.accounting.fees_known.status is MetricStatus.UNAVAILABLE
        assert result.accounting.net.status is MetricStatus.UNAVAILABLE
        assert result.accounting.net.value is None

    def test_mixed_fill_coverage_withholds_the_net_rather_than_inventing_it(self) -> None:
        position = partially_open(fill("80", fee="4"), fill("20", hours=2))

        result = summarise([position])

        assert result.accounting.gross.value == D("100")
        assert result.accounting.net.status is MetricStatus.PARTIAL_COVERAGE
        assert result.accounting.net.value is None
        assert result.accounting.coverage.covered == 1
        assert result.accounting.coverage.total == 2

    def test_a_later_close_does_not_double_count_the_earlier_fill(self) -> None:
        # The same position, now finished: two fills totalling +40, one trade.
        finished = PositionOutcome(
            position_id="PP-000000000000000000000001",
            instrument=outcome(1, "0").instrument,
            direction=Direction.LONG,
            timeframe=outcome(1, "0").timeframe,
            quantity=4,
            population=Population.CLOSED,
            realized_gross=D("40"),
            fees_total=None,
            realized_net=None,
            unrealized_gross=D(0),
            fee_mode="NOT_MODELLED",
            decision_time=START,
            entry_time=START,
            terminal_time=START + HOUR * 3,
            fills=(fill("80"), fill("-40", hours=3)),
        )

        result = summarise([finished])

        assert result.accounting.gross.value == D("40")  # 80 - 40, counted once
        assert result.accounting.fill_count == 2
        assert result.accounting.position_count == 1
        assert result.sample_size == 1
        assert result.realized_gross.value == D("40")


class TestFillsBelongToTheirOwnMarketTime:
    def test_a_range_containing_the_fill_includes_it(self) -> None:
        position = partially_open(fill("80", hours=1))

        inside = realized_accounting([position], start=START, end=START + HOUR * 2)

        assert inside.gross.value == D("80")
        assert inside.fill_count == 1

    def test_a_range_excluding_the_fill_leaves_it_out(self) -> None:
        position = partially_open(fill("80", hours=1))

        outside = realized_accounting([position], start=START + HOUR * 5, end=START + HOUR * 9)

        assert outside.fill_count == 0
        assert outside.gross.status is MetricStatus.UNAVAILABLE

    def test_a_fill_is_not_moved_to_the_position_closing_time(self) -> None:
        """Fill at hour 1, close at hour 9: a range around hour 1 owns the fill."""
        finished = PositionOutcome(
            position_id="PP-000000000000000000000009",
            instrument=outcome(1, "0").instrument,
            direction=Direction.LONG,
            timeframe=outcome(1, "0").timeframe,
            quantity=4,
            population=Population.CLOSED,
            realized_gross=D("60"),
            fees_total=None,
            realized_net=None,
            unrealized_gross=D(0),
            fee_mode="NOT_MODELLED",
            decision_time=START,
            entry_time=START,
            terminal_time=START + HOUR * 9,
            fills=(fill("80", hours=1), fill("-20", hours=9)),
        )

        early = realized_accounting([finished], start=START, end=START + HOUR * 2)
        late = realized_accounting([finished], start=START + HOUR * 8, end=START + HOUR * 10)

        assert early.gross.value == D("80")
        assert early.fill_count == 1
        assert late.gross.value == D("-20")
        assert late.fill_count == 1
        # And the trade sample still belongs to the closing time alone.
        assert summarise([finished], start=START, end=START + HOUR * 2).sample_size == 0
        assert summarise([finished], start=START + HOUR * 8, end=START + HOUR * 10).sample_size == 1


class TestAccountingAndTradeMetricsCoexist:
    def test_a_finished_trade_and_an_open_one_are_both_reported(self) -> None:
        finished = outcome(1, "100")
        open_with_fill = partially_open(fill("25", hours=4), index=2)

        result = summarise([finished, open_with_fill])

        assert result.sample_size == 1  # only the finished one
        assert result.realized_gross.value == D("100")  # completed-trade money
        assert result.accounting.gross.value == D("125")  # every fill so far
        assert result.accounting.from_completed == 1
        assert result.accounting.from_open == 1
        assert result.counts.open_exposure == 1


class TestOutcomeFactsBelongToTheirOwnPosition:
    """A position whose fees turn a small gross win into a net loss.

    This is the case that exposes a basis leak: if the outcome shown for this
    position were chosen by what *else* is in the selection, the same trade
    would read WIN in one filter and LOSS in another.
    """

    def costly_win(self) -> PositionOutcome:
        return PositionOutcome(
            position_id="PP-000000000000000000000042",
            instrument=outcome(1, "0").instrument,
            direction=Direction.LONG,
            timeframe=outcome(1, "0").timeframe,
            quantity=4,
            population=Population.CLOSED,
            realized_gross=D("5"),
            fees_total=D("16"),
            realized_net=D("-11"),
            unrealized_gross=D(0),
            fee_mode="USER_DEFINED_PER_UNIT",
            decision_time=START,
            entry_time=START,
            terminal_time=START + HOUR,
            fills=(fill("5", fee="16"),),
        )

    def test_its_gross_and_net_outcomes_are_both_its_own(self) -> None:
        from app.domain.performance import Outcome, PnlBasis

        position = self.costly_win()

        assert position.outcome(PnlBasis.REALIZED_GROSS) is Outcome.WIN
        assert position.outcome(PnlBasis.REALIZED_NET) is Outcome.LOSS

    def test_the_journal_view_reports_both_and_neither_moves_with_company(self) -> None:
        from app.api.schemas.performance_projection import journal_row
        from app.application.performance.ports import JournalRow
        from app.domain.journal import JournalAnnotation

        position = self.costly_win()
        row = journal_row(
            JournalRow(
                outcome=position, annotation=JournalAnnotation(position_id=position.position_id)
            )
        )

        assert row.outcome_gross == "WIN"
        assert row.outcome_net == "LOSS"
        # The headline classification for this position follows its own fees.
        assert row.outcome == "LOSS"
        assert row.outcome_basis == "REALIZED_NET"
