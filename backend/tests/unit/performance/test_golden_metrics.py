"""Hand-derived performance goldens.

Every expected value in this file was worked out on paper from the fixture
amounts and written here as a literal. Nothing calls the engine to decide what
the engine should say.

The cases that matter most are the ones where an honest answer is *no answer*:
a profit factor with no losses, a net total over positions whose fees were never
modelled, a win rate with nothing completed. Those must not arrive as zero.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.common.enums import Direction, Timeframe
from app.domain.performance import (
    MetricStatus,
    Outcome,
    PnlBasis,
    Population,
    by_direction,
    by_instrument,
    by_timeframe,
    summarise,
)
from tests.factories_performance import (
    FIXTURE_INSTRUMENT,
    OTHER_INSTRUMENT,
    outcome,
    sequence,
)

D = Decimal


class TestOnePositionIsOneTrade:
    """A position that exited in pieces is still one sample."""

    def test_a_position_closed_by_three_fills_is_one_trade(self) -> None:
        # Long 4 at 100: target 110 closes 1 (+10), target 120 closes 1 (+20),
        # stop 95 closes 2 (-10). Realized gross = 10 + 20 - 10 = +20.
        position = outcome(1, "20")

        result = summarise([position], fill_count=3)

        assert result.sample_size == 1
        assert result.wins == 1
        assert result.fill_count == 3
        assert result.realized_gross.value == D("20")
        assert result.expectancy.value == D("20.0000")
        assert result.win_rate.numerator == 1
        assert result.win_rate.denominator == 1

    def test_the_short_mirror_is_also_one_trade(self) -> None:
        # Short 4 at 100: target 90 closes 1 (+10), target 80 closes 1 (+20),
        # stop 105 closes 2 (-10). Same arithmetic, opposite side.
        position = outcome(1, "20", direction=Direction.SHORT)

        result = summarise([position], fill_count=3)

        assert result.sample_size == 1
        assert result.fill_count == 3
        assert result.realized_gross.value == D("20")

    def test_fill_count_never_reaches_a_trade_level_statistic(self) -> None:
        result = summarise(sequence("20", "-5"), fill_count=7)

        assert result.sample_size == 2
        assert result.win_rate.denominator == 2
        assert result.expectancy.sample_size == 2


class TestPopulations:
    """Who is counted, and who is not."""

    def test_only_closed_positions_are_completed_trades(self) -> None:
        records = [
            outcome(1, "100"),
            outcome(2, "0", population=Population.OPEN),
            outcome(3, "50", population=Population.ENTERED_PARTIALLY_CLOSED),
            outcome(4, "0", population=Population.AMBIGUOUS_HALTED),
            outcome(5, "0", population=Population.CANCELLED),
            outcome(6, "0", population=Population.REJECTED),
            outcome(7, "0", population=Population.PENDING_ENTRY),
        ]

        result = summarise(records)

        assert result.counts.total == 7
        assert result.counts.entered == 4
        assert result.counts.open_exposure == 3
        assert result.counts.never_entered == 3
        assert result.sample_size == 1
        assert result.win_rate.denominator == 1

    def test_a_cancelled_position_is_not_a_loss(self) -> None:
        result = summarise([outcome(1, "100"), outcome(2, "0", population=Population.CANCELLED)])

        assert result.wins == 1
        assert result.losses == 0
        assert result.win_rate.value == D("100.0000")

    def test_an_open_position_is_not_a_completed_win(self) -> None:
        result = summarise([outcome(1, "-10"), outcome(2, "500", population=Population.OPEN)])

        assert result.sample_size == 1
        assert result.wins == 0
        assert result.win_rate.value == D("0.0000")
        assert result.realized_gross.value == D("-10")

    def test_nothing_completed_reports_unavailable_not_zero_percent(self) -> None:
        result = summarise([outcome(1, "0", population=Population.OPEN)])

        assert result.sample_size == 0
        assert result.win_rate.status is MetricStatus.UNAVAILABLE
        assert result.win_rate.value is None
        assert "no completed positions" in (result.win_rate.reason or "")
        assert result.expectancy.status is MetricStatus.UNAVAILABLE
        assert result.realized_gross.status is MetricStatus.UNAVAILABLE


class TestOutcomeClassification:
    def test_exact_zero_is_breakeven_and_not_a_win(self) -> None:
        result = summarise(sequence("10", "0", "-10"))

        assert (result.wins, result.losses, result.breakevens) == (1, 1, 1)
        assert result.win_rate.numerator == 1
        assert result.win_rate.denominator == 3

    def test_the_basis_travels_with_the_classification(self) -> None:
        gross_only = summarise(sequence("10", "-4"))
        fully_costed = summarise(sequence("10", "-4", fees="2"))

        assert gross_only.basis is PnlBasis.REALIZED_GROSS
        assert gross_only.win_rate.basis is PnlBasis.REALIZED_GROSS
        assert fully_costed.basis is PnlBasis.REALIZED_NET
        assert fully_costed.win_rate.basis is PnlBasis.REALIZED_NET

    def test_fees_can_turn_a_gross_win_into_a_net_loss(self) -> None:
        # +5 gross, 8 of fees: the trade made money before costs and lost after.
        result = summarise([outcome(1, "5", fees="8")])

        assert result.basis is PnlBasis.REALIZED_NET
        assert result.losses == 1
        assert result.wins == 0
        assert result.realized_net.value == D("-3")
        assert result.realized_gross.value == D("5")


class TestFeeCoverage:
    """Four coverage states, four different truthful answers."""

    def test_every_position_costed_gives_a_net_answer(self) -> None:
        result = summarise(sequence("100", "-40", fees="2"))

        assert result.fee_coverage.covered == 2
        assert result.fee_coverage.complete
        assert result.realized_net.status is MetricStatus.AVAILABLE
        assert result.realized_net.value == D("56")  # (100-2) + (-40-2)
        assert result.fees_known.value == D("4")

    def test_no_position_costed_leaves_net_unknown(self) -> None:
        result = summarise(sequence("100", "-40"))

        assert result.basis is PnlBasis.REALIZED_GROSS
        assert result.realized_net.status is MetricStatus.UNAVAILABLE
        assert result.realized_net.value is None
        assert result.fees_known.status is MetricStatus.UNAVAILABLE
        assert result.realized_gross.value == D("60")

    def test_mixed_coverage_refuses_to_invent_a_total(self) -> None:
        mixed = [outcome(1, "100", fees="2"), outcome(2, "-40")]

        result = summarise(mixed)

        assert result.basis is PnlBasis.REALIZED_GROSS
        assert result.realized_net.status is MetricStatus.PARTIAL_COVERAGE
        assert result.realized_net.value is None
        assert result.realized_net.coverage is not None
        assert (result.realized_net.coverage.covered, result.realized_net.coverage.total) == (1, 2)
        # The fees that *were* modelled are still reported, with their coverage.
        assert result.fees_known.value == D("2")
        assert result.fees_known.coverage.covered == 1  # type: ignore[union-attr]
        # And nothing was dropped: gross still covers both positions.
        assert result.realized_gross.value == D("60")
        assert result.realized_gross.sample_size == 2

    def test_a_zero_user_defined_fee_is_not_the_same_as_not_modelled(self) -> None:
        costed = summarise([outcome(1, "100", fees="0")])
        uncosted = summarise([outcome(1, "100")])

        assert costed.basis is PnlBasis.REALIZED_NET
        assert costed.realized_net.status is MetricStatus.AVAILABLE
        assert costed.realized_net.value == D("100")
        assert costed.fees_known.value == D("0")
        assert uncosted.basis is PnlBasis.REALIZED_GROSS
        assert uncosted.realized_net.status is MetricStatus.UNAVAILABLE


class TestRealizedAndUnrealizedStaySeparate:
    def test_open_marks_are_reported_apart_from_realized_money(self) -> None:
        records = [
            outcome(1, "100"),
            outcome(2, "0", population=Population.OPEN, unrealized="250"),
        ]

        result = summarise(records)

        assert result.realized_gross.value == D("100")
        assert result.unrealized_gross_open.value == D("250")
        assert result.unrealized_gross_open.sample_size == 1

    def test_an_open_position_without_a_mark_makes_the_open_total_unavailable(self) -> None:
        records = [
            outcome(1, "0", population=Population.OPEN, unrealized="10"),
            outcome(2, "0", population=Population.OPEN),
        ]

        result = summarise(records)

        assert result.unrealized_gross_open.status is MetricStatus.PARTIAL_COVERAGE
        assert result.unrealized_gross_open.value is None

    def test_no_open_position_means_no_open_total(self) -> None:
        result = summarise([outcome(1, "10")])

        assert result.unrealized_gross_open.status is MetricStatus.UNAVAILABLE
        assert "no position is currently open" in (result.unrealized_gross_open.reason or "")


class TestAverages:
    def test_average_win_and_average_loss_are_separate_magnitudes(self) -> None:
        # Wins 100 and 40 -> mean 70. Losses -30 and -10 -> mean magnitude 20.
        result = summarise(sequence("100", "-30", "40", "-10"))

        assert result.average_win.value == D("70.0000")
        assert result.average_loss.value == D("20.0000")

    def test_no_wins_leaves_average_win_unavailable(self) -> None:
        result = summarise(sequence("-30", "-10"))

        assert result.average_win.status is MetricStatus.UNAVAILABLE
        assert result.average_win.value is None
        assert result.average_loss.value == D("20.0000")

    def test_no_losses_leaves_average_loss_unavailable(self) -> None:
        result = summarise(sequence("30", "10"))

        assert result.average_loss.status is MetricStatus.UNAVAILABLE
        assert result.average_win.value == D("20.0000")


class TestProfitFactor:
    def test_wins_and_losses_give_a_ratio(self) -> None:
        # Gains 100 + 20 = 120; losses 40 + 20 = 60; 120 / 60 = 2.
        result = summarise(sequence("100", "-40", "20", "-20"))

        assert result.profit_factor.value == D("2.0000")
        assert result.profit_factor.basis is PnlBasis.REALIZED_GROSS

    def test_only_wins_has_no_finite_ratio(self) -> None:
        result = summarise(sequence("100", "20"))

        assert result.profit_factor.status is MetricStatus.UNAVAILABLE
        assert result.profit_factor.value is None
        assert "no losing completed position" in (result.profit_factor.reason or "")

    def test_only_losses_is_zero_and_that_is_a_real_answer(self) -> None:
        result = summarise(sequence("-100", "-20"))

        assert result.profit_factor.status is MetricStatus.AVAILABLE
        assert result.profit_factor.value == D("0.0000")

    def test_only_breakevens_has_no_ratio(self) -> None:
        result = summarise(sequence("0", "0"))

        assert result.profit_factor.status is MetricStatus.UNAVAILABLE

    def test_nothing_completed_has_no_ratio(self) -> None:
        result = summarise([])

        assert result.profit_factor.status is MetricStatus.UNAVAILABLE
        assert result.profit_factor.value is None


class TestExpectancy:
    def test_one_trade_reports_its_sample_size(self) -> None:
        result = summarise([outcome(1, "35")])

        assert result.expectancy.value == D("35.0000")
        assert result.expectancy.sample_size == 1

    def test_mixed_trades_average_the_realized_amounts(self) -> None:
        # (100 - 40 + 0) / 3 = 20.
        result = summarise(sequence("100", "-40", "0"))

        assert result.expectancy.value == D("20.0000")
        assert result.expectancy.sample_size == 3

    def test_all_breakeven_is_zero_expectancy_from_a_real_sample(self) -> None:
        result = summarise(sequence("0", "0"))

        assert result.expectancy.status is MetricStatus.AVAILABLE
        assert result.expectancy.value == D("0.0000")

    def test_no_trades_is_unavailable_rather_than_zero(self) -> None:
        result = summarise([])

        assert result.expectancy.status is MetricStatus.UNAVAILABLE
        assert result.expectancy.value is None

    def test_a_fully_costed_dataset_reports_net_expectancy(self) -> None:
        # (100-2) + (-40-2) = 56 over two trades -> 28.
        result = summarise(sequence("100", "-40", fees="2"))

        assert result.expectancy.basis is PnlBasis.REALIZED_NET
        assert result.expectancy.value == D("28.0000")


class TestDrawdown:
    """Hand-derived curve from the prompt's sequence."""

    def test_the_cumulative_curve_and_its_worst_fall(self) -> None:
        # +100 -40 -80 +30 +200 -250
        # cumulative: 100, 60, -20, 10, 210, -40
        # peak 100 -> trough -20 is 120; peak 210 -> trough -40 is 250.
        result = summarise(sequence("100", "-40", "-80", "30", "200", "-250"))

        assert [point.cumulative for point in result.timeline] == [
            D("100"),
            D("60"),
            D("-20"),
            D("10"),
            D("210"),
            D("-40"),
        ]
        assert result.max_drawdown_absolute.value == D("250")

    def test_a_rising_curve_has_no_drawdown(self) -> None:
        result = summarise(sequence("10", "20", "30"))

        assert result.max_drawdown_absolute.value == D("0")

    def test_drawdown_is_unavailable_without_completed_positions(self) -> None:
        result = summarise([outcome(1, "0", population=Population.OPEN)])

        assert result.max_drawdown_absolute.status is MetricStatus.UNAVAILABLE

    def test_equal_terminal_times_order_deterministically_by_position_id(self) -> None:
        first = outcome(1, "100", hours=5, position_id="PP-000000000000000000000002")
        second = outcome(2, "-40", hours=5, position_id="PP-000000000000000000000001")

        one = summarise([first, second])
        other = summarise([second, first])

        assert [p.position_id for p in one.timeline] == [
            "PP-000000000000000000000001",
            "PP-000000000000000000000002",
        ]
        assert [p.cumulative for p in one.timeline] == [D("-40"), D("60")]
        assert one.timeline == other.timeline
        assert one.max_drawdown_absolute.value == other.max_drawdown_absolute.value

    def test_the_curve_follows_closing_time_not_the_order_trades_were_opened(self) -> None:
        """A trade decided first can finish last; the curve follows the market."""
        # Decided in the order A, B - but A closes at hour 9 and B at hour 2.
        early_decision_late_close = outcome(1, "-100", hours=9)
        late_decision_early_close = outcome(2, "100", hours=2)

        result = summarise([early_decision_late_close, late_decision_early_close])

        assert [point.amount for point in result.timeline] == [D("100"), D("-100")]
        assert [point.cumulative for point in result.timeline] == [D("100"), D("0")]
        # Ordered by opening instead, the curve would fall to -100 first and the
        # worst drawdown would be 100 rather than 100 from a peak of 100.
        assert result.max_drawdown_absolute.value == D("100")
        assert result.streaks.current_kind is Outcome.LOSS

    def test_percentage_drawdown_is_unavailable_by_design(self) -> None:
        result = summarise(sequence("100", "-40"))

        assert result.drawdown_percentage.status is MetricStatus.UNAVAILABLE
        assert "capital" in (result.drawdown_percentage.reason or "")


class TestStreaks:
    def test_the_documented_sequence(self) -> None:
        # W W L L L B W
        result = summarise(sequence("10", "20", "-5", "-6", "-7", "0", "40"))

        assert result.streaks.max_win_streak == 2
        assert result.streaks.max_loss_streak == 3
        assert result.streaks.current_kind is Outcome.WIN
        assert result.streaks.current_length == 1

    def test_a_breakeven_ends_both_runs(self) -> None:
        result = summarise(sequence("10", "0"))

        assert result.streaks.current_kind is None
        assert result.streaks.current_length == 0
        assert result.streaks.max_win_streak == 1
        assert result.streaks.policy == "BREAKEVEN_BREAKS_BOTH_STREAKS"

    def test_open_and_cancelled_positions_do_not_participate(self) -> None:
        records = [
            *sequence("10", "20"),
            outcome(9, "0", population=Population.CANCELLED),
            outcome(10, "-500", population=Population.OPEN),
        ]

        result = summarise(records)

        assert result.streaks.current_kind is Outcome.WIN
        assert result.streaks.current_length == 2
        assert result.streaks.max_loss_streak == 0


class TestBreakdowns:
    def test_long_and_short_use_the_same_engine(self) -> None:
        records = [
            outcome(1, "100", direction=Direction.LONG),
            outcome(2, "-40", direction=Direction.LONG),
            outcome(3, "60", direction=Direction.SHORT),
        ]

        rows = {row.key: row for row in by_direction(records)}

        assert rows["LONG"].sample_size == 2
        assert rows["LONG"].wins == 1
        assert rows["LONG"].losses == 1
        assert rows["LONG"].realized_gross.value == D("60")
        assert rows["LONG"].win_rate.value == D("50.0000")
        assert rows["SHORT"].sample_size == 1
        assert rows["SHORT"].wins == 1
        assert rows["SHORT"].losses == 0
        assert rows["SHORT"].breakevens == 0
        assert rows["SHORT"].realized_gross.value == D("60")
        assert rows["SHORT"].win_rate.value == D("100.0000")
        assert rows["SHORT"].expectancy.value == D("60.0000")

    def test_instrument_identity_is_symbol_and_asset_class(self) -> None:
        records = [
            outcome(1, "10", instrument=FIXTURE_INSTRUMENT),
            outcome(2, "20", instrument=OTHER_INSTRUMENT),
            outcome(3, "30", instrument=FIXTURE_INSTRUMENT),
        ]

        rows = by_instrument(records)

        assert [row.key for row in rows] == [
            "FUTURES:TEST_FIXTURE_FUT",
            "FUTURES:TEST_FIXTURE_FUT_TWO",
        ]
        assert rows[0].sample_size == 2
        assert rows[0].realized_gross.value == D("40")

    def test_two_instruments_sharing_a_symbol_but_not_an_identity_stay_apart(self) -> None:
        from app.domain.performance import InstrumentIdentity

        same_text = InstrumentIdentity(symbol="TEST_FIXTURE_FUT", asset_class="EQUITY")
        records = [
            outcome(1, "10", instrument=FIXTURE_INSTRUMENT),
            outcome(2, "20", instrument=same_text),
        ]

        rows = by_instrument(records)

        assert len(rows) == 2
        assert {row.key for row in rows} == {
            "FUTURES:TEST_FIXTURE_FUT",
            "EQUITY:TEST_FIXTURE_FUT",
        }

    def test_timeframe_breakdown_uses_the_stored_plan(self) -> None:
        records = [
            outcome(1, "10", timeframe=Timeframe.H1),
            outcome(2, "-5", timeframe=Timeframe.M15),
            outcome(3, "7", timeframe=Timeframe.H1),
        ]

        rows = {row.key: row for row in by_timeframe(records)}

        assert rows["1H"].sample_size == 2
        assert rows["15M"].sample_size == 1
        assert rows["15M"].losses == 1

    def test_a_group_may_report_net_when_the_whole_selection_cannot(self) -> None:
        records = [
            outcome(1, "100", fees="2", direction=Direction.LONG),
            outcome(2, "-40", direction=Direction.SHORT),
        ]

        whole = summarise(records)
        rows = {row.key: row for row in by_direction(records)}

        assert whole.realized_net.status is MetricStatus.PARTIAL_COVERAGE
        assert rows["LONG"].realized_net.status is MetricStatus.AVAILABLE
        assert rows["LONG"].realized_net.value == D("98")
        assert rows["SHORT"].realized_net.status is MetricStatus.UNAVAILABLE

    def test_rows_are_ordered_deterministically(self) -> None:
        records = [
            outcome(1, "10", instrument=OTHER_INSTRUMENT),
            outcome(2, "20", instrument=FIXTURE_INSTRUMENT),
            outcome(3, "30", instrument=FIXTURE_INSTRUMENT),
        ]

        assert [row.key for row in by_instrument(records)] == [
            row.key for row in by_instrument(list(reversed(records)))
        ]


class TestDeterminism:
    def test_the_same_records_in_any_order_give_the_same_answer(self) -> None:
        records = sequence("100", "-40", "0", "30", "-10")
        shuffled = [records[3], records[0], records[4], records[2], records[1]]

        assert summarise(records) == summarise(shuffled)

    @pytest.mark.parametrize(
        "name",
        [
            "realized_r_expectancy",
            "mae",
            "mfe",
            "sharpe_ratio",
            "sortino_ratio",
            "annualised_return",
        ],
    )
    def test_metrics_without_honest_semantics_are_not_implemented(self, name: str) -> None:
        result = summarise(sequence("100", "-40"))
        metric = getattr(result, name)

        assert metric.status is MetricStatus.NOT_IMPLEMENTED
        assert metric.value is None
        assert metric.reason
