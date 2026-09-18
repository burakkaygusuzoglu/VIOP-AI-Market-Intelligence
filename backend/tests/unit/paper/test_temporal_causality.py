"""Market time decides financial outcomes; wall-clock time never does.

Phase 9 closeout sections 1-6. The questions these answer are causal ones:
can a simulated entry use a price that was printed *before* the person decided
to trade, can an instruction issued now reach backwards into a bar the position
has already processed, and can the answer to either change tomorrow because the
server clock moved?

Every price here is TEST_FIXTURE data (see ``tests/factories_paper``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.common.enums import Direction, Timeframe
from app.domain.market.candle import Candle
from app.domain.paper import (
    PaperEventType,
    PaperRefusalError,
    PositionState,
    RefusalCode,
    apply_observation,
    inputs_from_events,
    move_stop_to_breakeven,
    open_position,
    rebuild,
    request_close,
)
from app.domain.paper.model import MAX_TARGETS, PaperPosition, PositionSpec
from tests.factories_futures import FIXTURE_SYMBOL
from tests.factories_paper import (
    DECISION,
    HOUR,
    approval_for,
    bar,
    long_spec,
    product,
    run,
    short_spec,
)

D = Decimal
MICROSECOND = timedelta(microseconds=1)


def _wall_clock_reads(package: str) -> list[tuple[str, str]]:
    """Every direct process-clock read in a package, ignoring comments."""
    from pathlib import Path

    banned = ("datetime.now", "datetime.utcnow", "utcnow(", "time.time(", "date.today")
    root = Path(__file__).resolve().parents[3] / package
    found: list[tuple[str, str]] = []
    for module in sorted(root.glob("*.py")):
        for line in module.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("#", '"', "'", "*")):
                continue
            found += [(module.name, name) for name in banned if name in stripped]
    return found


def five_minute_bar(open_time: datetime, o: str, h: str, low: str, c: str) -> Candle:
    return Candle(
        symbol=FIXTURE_SYMBOL,
        timeframe=Timeframe.M5,
        open_time=open_time,
        open=D(o),
        high=D(h),
        low=D(low),
        close=D(c),
        volume=D("1000"),
        is_closed=True,
    )


class TestEntryBarCausality:
    """The simulated entry open may never precede the decision.

    OHLC gives one price per bar for the open, and that price was printed at
    ``open_time``. A bar whose open is earlier than the decision therefore
    carries a price the person could not have traded on, however long after the
    decision the bar finally closed.
    """

    def test_a_bar_opening_exactly_at_the_decision_is_eligible(self) -> None:
        position = run(long_spec(), [bar(0, "100", "101", "99.50", "100.50")])

        assert position.state is PositionState.OPEN
        assert position.entry_fill_price == D("100")
        assert position.events[2].market_time == DECISION

    def test_a_bar_opening_one_microsecond_before_the_decision_is_refused(self) -> None:
        spec = long_spec(decision_time=DECISION + MICROSECOND)
        position = open_position(spec, approval_for(spec), product())
        entry = bar(0, "100", "101", "99.50", "100.50")

        with pytest.raises(PaperRefusalError) as error:
            apply_observation(position, entry, product())

        assert error.value.code is RefusalCode.OBSERVATION_BEFORE_DECISION
        assert position.entry_fill_price is None

    def test_a_decision_inside_a_bar_cannot_use_that_bar_earlier_open(self) -> None:
        """The prompt's example: a 5-minute bar open at 10:05, decision 10:07."""
        opened = datetime(2026, 3, 2, 10, 5, tzinfo=UTC)
        spec = long_spec(
            timeframe=Timeframe.M5,
            decision_time=datetime(2026, 3, 2, 10, 7, tzinfo=UTC),
        )
        position = open_position(spec, approval_for(spec), product())
        inside = five_minute_bar(opened, "100", "101", "99.50", "100.50")

        with pytest.raises(PaperRefusalError) as error:
            apply_observation(position, inside, product())

        assert error.value.code is RefusalCode.OBSERVATION_BEFORE_DECISION
        assert position.entry_fill_price is None

        # The next bar - the first whose open is after the decision - is the entry.
        following = five_minute_bar(
            opened + timedelta(minutes=5), "100.75", "101.50", "100.25", "101"
        )
        entered = apply_observation(position, following, product())
        assert entered.state is PositionState.OPEN
        assert entered.entry_fill_price == D("100.75")

    def test_a_decision_at_the_coverage_end_of_a_bar_takes_the_next_bar(self) -> None:
        """Coverage end of the 10:05 bar is 10:10, which is the next bar's open."""
        spec = long_spec(
            timeframe=Timeframe.M5,
            decision_time=datetime(2026, 3, 2, 10, 10, tzinfo=UTC),
        )
        position = open_position(spec, approval_for(spec), product())
        covering = five_minute_bar(
            datetime(2026, 3, 2, 10, 5, tzinfo=UTC), "100", "101", "99.50", "100.50"
        )

        with pytest.raises(PaperRefusalError):
            apply_observation(position, covering, product())

        next_bar = five_minute_bar(
            datetime(2026, 3, 2, 10, 10, tzinfo=UTC), "100.75", "101", "100.50", "101"
        )
        assert apply_observation(position, next_bar, product()).entry_fill_price == D("100.75")

    def test_a_decision_between_two_bars_takes_the_next_bar_open(self) -> None:
        spec = long_spec(decision_time=DECISION + timedelta(minutes=30))
        position = open_position(spec, approval_for(spec), product())

        with pytest.raises(PaperRefusalError):
            apply_observation(position, bar(0, "100", "101", "99.50", "100.50"), product())

        entered = apply_observation(position, bar(1, "101", "102", "100.50", "101.50"), product())
        assert entered.entry_fill_price == D("101")
        assert entered.events[2].market_time == DECISION + HOUR

    @pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
    def test_eligibility_is_identical_for_long_and_short(self, direction: Direction) -> None:
        make = long_spec if direction is Direction.LONG else short_spec
        spec = make(decision_time=DECISION + MICROSECOND)
        position = open_position(spec, approval_for(spec), product())

        with pytest.raises(PaperRefusalError) as error:
            apply_observation(position, bar(0, "100", "101", "99", "100"), product())
        assert error.value.code is RefusalCode.OBSERVATION_BEFORE_DECISION

        entered = apply_observation(position, bar(1, "100", "100.50", "99.50", "100"), product())
        assert entered.state is PositionState.OPEN
        assert entered.entry_fill_price == D("100")

    def test_no_wall_clock_is_consulted_anywhere_in_the_paper_domain(self) -> None:
        """Entry eligibility is decided by bar timestamps, not by 'now'."""
        for module, name in _wall_clock_reads("app/domain/paper"):
            raise AssertionError(f"{module} reads the wall clock ({name})")

    def test_the_paper_use_cases_read_time_only_through_the_injected_clock(self) -> None:
        """Otherwise a clock-injecting test could not pin the behaviour at all.

        The service is allowed to ask its ``ClockPort`` what time it is - for
        audit stamps and for refusing a bar that has not closed yet. Reading the
        process clock directly would put a time source outside every test's
        reach, so it is banned outright.
        """
        for module, name in _wall_clock_reads("app/application/paper"):
            raise AssertionError(f"{module} reads the process clock directly ({name})")

    def test_rebuilding_a_stored_position_never_touches_the_clock(self) -> None:
        import inspect

        from app.application.paper.service import PaperTradingService

        source = inspect.getsource(PaperTradingService._rebuild)
        assert "_clock" not in source
        assert "now" not in source


class TestEntryBarSameBarInteraction:
    """Entry is at the open, so the rest of that bar is in the position's future.

    Because the entry price is the open, no OHLC information from before the
    entry is used: the high and low of the entry bar happened after it. What
    order they happened in is still unknown, which is what the same-bar policy
    is for.
    """

    def test_the_entry_bar_can_fill_its_own_target(self) -> None:
        position = run(long_spec(), [bar(0, "100", "104.50", "99.50", "104")])

        assert position.state is PositionState.PARTIALLY_CLOSED
        assert position.remaining == 2
        types = [event.type for event in position.events]
        assert types.index(PaperEventType.ENTRY_FILLED) < types.index(PaperEventType.TARGET_FILLED)

    def test_the_entry_bar_can_fill_its_own_stop(self) -> None:
        position = run(long_spec(), [bar(0, "100", "100.50", "97.50", "98")])

        assert position.state is PositionState.CLOSED
        stop = next(e for e in position.events if e.type is PaperEventType.STOP_FILLED)
        assert stop.data["fill_price"] == "98.00"
        assert stop.data["gap"] == "false"

    def test_the_entry_bar_touching_both_is_resolved_by_the_stated_policy(self) -> None:
        position = run(long_spec(), [bar(0, "100", "104.50", "97.50", "99")])

        ambiguity = next(e for e in position.events if e.type is PaperEventType.SAME_BAR_AMBIGUITY)
        assert ambiguity.data["policy"] == "STOP_FIRST"
        assert ambiguity.data["targets_touched"] == "1"
        assert position.state is PositionState.CLOSED
        assert position.realized_gross == D("-80.00")

    def test_an_entry_rejected_by_its_own_bar_fills_nothing_from_that_bar(self) -> None:
        """A fill at or beyond the stop is not an entry, so the bar's range is moot."""
        position = run(long_spec(), [bar(0, "97", "105", "96", "104")])

        assert position.state is PositionState.REJECTED
        assert position.realized_gross == D("0")
        assert not [e for e in position.events if e.type is PaperEventType.TARGET_FILLED]


class TestManualCloseIsAnchoredInMarketTime:
    """A close request takes effect after the bars already applied, never within one."""

    def opened(self) -> PaperPosition:
        return run(long_spec(), [bar(0, "100", "101", "99.50", "100.50")])

    def test_the_request_records_the_bar_it_takes_effect_after(self) -> None:
        requested = request_close(self.opened())
        event = requested.events[-1]

        assert event.type is PaperEventType.CLOSE_REQUESTED
        assert event.market_time == DECISION
        assert event.data["effective_after_bar"] == DECISION.isoformat()
        assert event.data["fills_at"] == "NEXT_BAR_OPEN"

    def test_the_already_applied_bar_cannot_fill_the_close_retroactively(self) -> None:
        requested = request_close(self.opened())

        # Re-sending bar N is a no-op, so it cannot become the exit.
        again = apply_observation(requested, bar(0, "100", "101", "99.50", "100.50"), product())
        assert again.close_pending is True
        assert again.state is PositionState.OPEN
        assert len(again.events) == len(requested.events)

    def test_the_next_bar_fills_the_close_at_its_open(self) -> None:
        closed = apply_observation(
            request_close(self.opened()), bar(1, "102", "103", "101", "102.50"), product()
        )

        assert closed.state is PositionState.CLOSED
        exit_event = next(e for e in closed.events if e.type is PaperEventType.MANUAL_EXIT_FILLED)
        assert exit_event.data["fill_price"] == "102"
        assert exit_event.market_time == DECISION + HOUR
        assert closed.realized_gross == D("80.00")

    def test_replaying_the_stored_inputs_reproduces_the_same_fill(self) -> None:
        closed = apply_observation(
            request_close(self.opened()), bar(1, "102", "103", "101", "102.50"), product()
        )
        spec = closed.spec

        replayed = rebuild(spec, approval_for(spec), product(), closed.events)

        assert replayed == closed
        assert replayed.realized_gross == closed.realized_gross

    def test_a_second_close_request_appends_nothing(self) -> None:
        once = request_close(self.opened())
        twice = request_close(once)

        assert twice is once or twice.events == once.events
        assert len(twice.events) == len(once.events)

    def test_a_terminal_position_refuses_a_close_request(self) -> None:
        closed = run(long_spec(), [bar(0, "100", "100.50", "97.50", "98")])

        with pytest.raises(PaperRefusalError) as error:
            request_close(closed)

        assert error.value.code is RefusalCode.INVALID_TRANSITION

    def test_a_pending_position_refuses_a_close_request(self) -> None:
        spec = long_spec()
        with pytest.raises(PaperRefusalError) as error:
            request_close(open_position(spec, approval_for(spec), product()))

        assert error.value.code is RefusalCode.INVALID_TRANSITION


class TestBreakevenIsAnchoredInMarketTime:
    """The moved stop guards later bars; it never reaches back into the bar that earned it."""

    @pytest.mark.parametrize(
        ("maker", "entry_bar", "favourable", "retro_low_or_high"),
        [
            (
                long_spec,
                (0, "100", "101", "99.50", "100.50"),
                (1, "101", "103", "99.75", "102.50"),
                "99.75",
            ),
            (
                short_spec,
                (0, "100", "100.50", "99", "99.50"),
                (1, "99", "100.25", "97", "97.50"),
                "100.25",
            ),
        ],
    )
    def test_the_bar_that_made_the_move_possible_does_not_trigger_the_new_stop(
        self,
        maker: Callable[..., PositionSpec],
        entry_bar: tuple[int, str, str, str, str],
        favourable: tuple[int, str, str, str, str],
        retro_low_or_high: str,
    ) -> None:
        spec = maker()
        position = run(spec, [bar(*entry_bar), bar(*favourable)])
        entry = position.entry_fill_price
        assert entry == D("100")

        moved = move_stop_to_breakeven(position)

        # The favourable bar traded through 100 (99.75 low for the long,
        # 100.25 high for the short) before the stop was moved there.
        assert Decimal(retro_low_or_high) != entry
        assert moved.stop == entry
        assert moved.state is position.state
        assert moved.remaining == position.remaining
        assert moved.realized_gross == position.realized_gross
        assert moved.events[-1].type is PaperEventType.STOP_MOVED_TO_BREAKEVEN
        assert moved.events[-1].data["effective_after_bar"] == (DECISION + HOUR).isoformat()

    def test_the_next_bar_is_what_the_moved_stop_guards(self) -> None:
        position = run(
            long_spec(),
            [bar(0, "100", "101", "99.50", "100.50"), bar(1, "101", "103", "100.25", "102.50")],
        )
        moved = move_stop_to_breakeven(position)

        after = apply_observation(moved, bar(2, "101", "101.50", "99", "99.50"), product())

        assert after.state is PositionState.CLOSED
        stop = next(e for e in after.events if e.type is PaperEventType.STOP_FILLED)
        assert stop.data["fill_price"] == "100"
        assert stop.market_time == DECISION + HOUR * 2

    def test_re_sending_the_bar_that_earned_the_move_does_not_trigger_the_new_stop(
        self,
    ) -> None:
        """Its low traded through the new stop - before that stop existed."""
        favourable = bar(1, "101", "103", "99.75", "102.50")
        position = run(long_spec(), [bar(0, "100", "101", "99.50", "100.50"), favourable])
        moved = move_stop_to_breakeven(position)
        assert moved.stop == D("100") > favourable.low

        again = apply_observation(moved, favourable, product())

        assert again.state is PositionState.OPEN
        assert again.remaining == 4
        assert again.realized_gross == D("0")
        assert len(again.events) == len(moved.events)

    def test_a_second_breakeven_request_appends_nothing(self) -> None:
        position = run(
            long_spec(),
            [bar(0, "100", "101", "99.50", "100.50"), bar(1, "101", "103", "100.25", "102.50")],
        )
        once = move_stop_to_breakeven(position)
        twice = move_stop_to_breakeven(once)

        assert len(twice.events) == len(once.events)

    def test_replay_reproduces_the_moved_stop_in_the_same_place(self) -> None:
        position = run(
            long_spec(),
            [bar(0, "100", "101", "99.50", "100.50"), bar(1, "101", "103", "100.25", "102.50")],
        )
        moved = move_stop_to_breakeven(position)
        after = apply_observation(moved, bar(2, "101", "101.50", "99", "99.50"), product())

        replayed = rebuild(after.spec, approval_for(after.spec), product(), after.events)

        assert replayed == after
        kinds = [type(item).__name__ for item in inputs_from_events(after.spec, after.events)]
        assert kinds == ["Candle", "Candle", "BreakevenInput", "Candle"]


class TestLedgerGrowthIsBounded:
    """No-op commands append nothing, so a caller cannot inflate the ledger."""

    def test_repeated_no_op_commands_do_not_grow_the_ledger(self) -> None:
        position = run(
            long_spec(),
            [bar(0, "100", "101", "99.50", "100.50"), bar(1, "101", "103", "100.25", "102.50")],
        )
        moved = move_stop_to_breakeven(position)
        before = len(moved.events)

        for _ in range(50):
            moved = move_stop_to_breakeven(moved)
        requested = request_close(moved)
        for _ in range(50):
            requested = request_close(requested)

        assert len(moved.events) == before
        assert len(requested.events) == before + 1

    def test_re_sending_the_same_bar_many_times_appends_nothing(self) -> None:
        position = run(long_spec(), [bar(0, "100", "101", "99.50", "100.50")])
        before = len(position.events)

        for _ in range(50):
            position = apply_observation(
                position, bar(0, "100", "101", "99.50", "100.50"), product()
            )

        assert len(position.events) == before

    def test_one_bar_appends_at_most_a_fixed_number_of_events(self) -> None:
        """The busiest possible bar: entry, every target, and the close."""
        spec = long_spec()
        position = run(spec, [bar(0, "100", "107", "99.60", "106.50")])

        # POSITION_CREATED, OBSERVATION_APPLIED, ENTRY_FILLED,
        # TARGET_FILLED per target, POSITION_CLOSED.
        assert position.state is PositionState.CLOSED
        assert len(position.events) == 4 + len(spec.targets)
        # So one bar can add at most 3 + MAX_TARGETS events, and the bar cap
        # bounds the ledger: no input can append an unbounded number.
        assert len(position.events) - 1 <= 3 + MAX_TARGETS
