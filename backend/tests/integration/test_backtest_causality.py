"""Nothing that had not finished may influence a decision or a fill (Phase 12).

Phase 11 proved this for a human stepping through a replay. A backtest walks
the same rule thousands of times without anybody watching, which is exactly
when a leak stops being noticed - so it is proven again here, against the
runner, from four directions:

* what the strategy is *offered* at a boundary;
* what the indicator readings at that boundary were computed from;
* when a higher timeframe becomes visible at all;
* what price an entry can possibly be filled at.

The adversarial fixtures put a candle in the dataset that no market would
print. A leak then cannot pass for a plausible number: it arrives as 999999999.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.adapters.persistence.database import Database
from app.domain.backtest.policy import Readings, StrategyDecision
from app.domain.backtest.run import DecisionOutcome
from app.domain.common.enums import Direction, Timeframe
from app.domain.replay import coverage_end
from tests.factories_replay import BASE, with_absurd_future_candle
from tests.integration.backtest_support import (
    ScriptedStrategy,
    bars,
    flat_rows,
    intent,
    runner,
    scripted_request,
    seed_dataset,
    trending,
)

pytestmark = pytest.mark.integration

ABSURD = Decimal("999999999")
M5_ONLY = (Timeframe.M5,)


class TestTheStrategyIsOfferedNothingUnfinished:
    async def test_every_bar_offered_had_already_closed_at_its_boundary(
        self, database: Database
    ) -> None:
        dataset = await seed_dataset(database, flat_rows(12), timeframes=M5_ONLY)
        strategy = ScriptedStrategy()

        await runner(database).run(
            scripted_request(dataset, strategy, key="causal-closed-00001", last=11)
        )

        assert strategy.seen
        for context in strategy.seen:
            assert context.bar.is_closed
            assert coverage_end(context.bar) == context.as_of

    async def test_a_later_candle_is_never_the_bar_offered(self, database: Database) -> None:
        """The offered bar is the one that finished, never the one forming."""
        dataset = await seed_dataset(database, flat_rows(12), timeframes=M5_ONLY)
        strategy = ScriptedStrategy()

        await runner(database).run(
            scripted_request(dataset, strategy, key="causal-notnext-0001", last=11)
        )

        for context in strategy.seen:
            assert context.bar.open_time + timedelta(minutes=5) == context.as_of

    async def test_an_absurd_future_candle_never_reaches_an_earlier_decision(
        self, database: Database
    ) -> None:
        rows = with_absurd_future_candle(trending(60), 40)
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        strategy = ScriptedStrategy()

        await runner(database).run(
            scripted_request(dataset, strategy, key="causal-absurd-0001", last=39)
        )

        assert len(strategy.seen) == 40
        for context in strategy.seen:
            assert context.bar.high < ABSURD
            for reading in _numbers(context.current) + _numbers(context.previous):
                assert reading < float(ABSURD)

    async def test_the_window_start_is_respected_but_history_before_it_is_not_hidden(
        self, database: Database
    ) -> None:
        """A run starting at bar 20 evaluates from there - and 20 bars of
        history are still *confirmed facts* the indicators may use. Warm-up is
        a past, not a leak."""
        dataset = await seed_dataset(database, flat_rows(30), timeframes=M5_ONLY)
        strategy = ScriptedStrategy()

        stored = await runner(database).run(
            scripted_request(dataset, strategy, key="causal-window-0001", first=20, last=29)
        )

        assert stored.result is not None
        assert stored.result.boundaries_evaluated == 10
        assert strategy.seen[0].bars_available == 21
        assert strategy.seen[0].as_of == BASE + timedelta(minutes=5 * 21)


class TestHigherTimeframesAppearOnlyOnceClosed:
    async def test_a_forming_hourly_candle_is_absent_rather_than_partial(
        self, database: Database
    ) -> None:
        """At 00:35 the 1H candle has not closed, so there is no 1H reading.

        Absent, not present-with-partial-values: a partial higher-timeframe
        value is the most plausible-looking leak there is.
        """
        dataset = await seed_dataset(database, trending(48))
        strategy = ScriptedStrategy()

        await runner(database).run(
            scripted_request(dataset, strategy, key="causal-forming-001", last=11)
        )

        early = [context for context in strategy.seen if context.as_of < BASE + timedelta(hours=1)]
        assert early
        assert all(Timeframe.H1 not in context.higher for context in early)

    async def test_an_hourly_reading_appears_at_the_boundary_it_closed_on(
        self, database: Database
    ) -> None:
        dataset = await seed_dataset(database, trending(48))
        strategy = ScriptedStrategy()

        await runner(database).run(
            scripted_request(dataset, strategy, key="causal-hourly-0001", last=23)
        )

        at_or_after = [
            context for context in strategy.seen if context.as_of >= BASE + timedelta(hours=1)
        ]
        assert at_or_after
        assert all(Timeframe.H1 in context.higher for context in at_or_after)


class TestAFillCannotUseAPriceFromBeforeTheDecision:
    async def test_the_entry_uses_the_next_bar_open_even_when_it_is_worse(
        self, database: Database
    ) -> None:
        """The signal bar closed at 100. The next bar opens at 110.

        A backtest that filled at 100 here would report a profit the market
        never offered, so the unfavourable open is the one used.
        """
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("110", "112", "110", "111"),
            ("111", "112", "110", "111"),
        )
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        strategy = ScriptedStrategy(
            plan={
                1: StrategyDecision.enter(
                    intent(Direction.LONG, entry="100", stop="97", targets=(("120", 1),)),
                    "scripted entry at an unfavourable open",
                )
            }
        )

        stored = await runner(database).run(
            scripted_request(dataset, strategy, key="causal-worse-00001", last=3)
        )

        assert stored.result is not None
        (position,) = stored.result.positions
        entry = [event for event in position.events if event.type.value == "ENTRY_FILLED"]
        assert [event.data["fill_price"] for event in entry] == ["110"]

    async def test_a_target_reached_before_the_decision_does_not_fill(
        self, database: Database
    ) -> None:
        """Bar 0 traded up to 120; the position is decided at bar 1's close.

        The 120 print is in the past of the position, so the target it would
        have hit is not filled from it.
        """
        rows = bars(
            ("100", "120", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "101", "100", "100"),
            ("100", "101", "100", "100"),
        )
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        strategy = ScriptedStrategy(
            plan={
                1: StrategyDecision.enter(
                    intent(Direction.LONG, entry="100", stop="97", targets=(("115", 1),)),
                    "scripted entry after a spike that already happened",
                )
            }
        )

        stored = await runner(database).run(
            scripted_request(dataset, strategy, key="causal-pasthigh-01", last=3)
        )

        assert stored.result is not None
        (position,) = stored.result.positions
        assert not any(event.type.value == "TARGET_FILLED" for event in position.events)


class TestWarmUpIsRefusedNotApproximated:
    async def test_a_strategy_needing_more_history_than_exists_is_never_asked_to_trade(
        self, database: Database
    ) -> None:
        """The runner offers the boundary; the count says the history is short.

        ``bars_available`` is what a policy checks. It is a fact about the
        dataset, so a rule cannot mistake an unwarmed indicator for a value.
        """
        dataset = await seed_dataset(database, flat_rows(8), timeframes=M5_ONLY)
        strategy = ScriptedStrategy(warm_up=40)

        stored = await runner(database).run(
            scripted_request(dataset, strategy, key="causal-warmup-0001", last=7)
        )

        assert stored.result is not None
        assert all(context.bars_available <= 8 for context in strategy.seen)
        assert all(
            record.outcome is DecisionOutcome.NO_SIGNAL for record in stored.result.decisions
        )

    async def test_unwarmed_readings_are_none_rather_than_zero(self, database: Database) -> None:
        """Zero would be a number a rule could compare against. None is not."""
        dataset = await seed_dataset(database, trending(30), timeframes=M5_ONLY)
        strategy = ScriptedStrategy()

        await runner(database).run(
            scripted_request(dataset, strategy, key="causal-none-000001", first=0, last=1)
        )

        first = strategy.seen[0]
        assert first.previous == Readings()
        assert not first.current.complete


def _numbers(readings: Readings) -> tuple[float, ...]:
    return tuple(
        value
        for value in (
            readings.ema_fast,
            readings.ema_slow,
            readings.rsi,
            readings.atr,
            readings.adx,
        )
        if value is not None
    )
