"""The reference strategy, rule by rule, with every expectation hand-derived.

The policy is pure, so these need no database, no clock and no engine: a
context in, a decision out. Every expected answer below was worked out from the
rule as written, not from running the implementation and recording what it did.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.backtest.policy import (
    DecisionKind,
    Readings,
    StrategyContext,
    StrategyInputError,
)
from app.domain.backtest.strategies.ema_crossover import (
    EmaCrossoverSettings,
    EmaCrossoverStrategy,
)
from app.domain.common.enums import Direction, Timeframe
from app.domain.market.candle import Candle

BASE = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
SYMBOL = "TEST_FIXTURE_FUT"


def bar(close: str = "100", minutes: int = 0) -> Candle:
    price = Decimal(close)
    return Candle(
        symbol=SYMBOL,
        timeframe=Timeframe.M5,
        open_time=BASE + timedelta(minutes=minutes),
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=Decimal("1000"),
        is_closed=True,
    )


def context(
    *,
    current: Readings,
    previous: Readings,
    bars: int = 100,
    open_position: bool = False,
    close: str = "100",
) -> StrategyContext:
    return StrategyContext(
        as_of=BASE + timedelta(minutes=5),
        symbol=SYMBOL,
        driver=Timeframe.M5,
        bar=bar(close),
        current=current,
        previous=previous,
        higher={},
        bars_available=bars,
        has_open_position=open_position,
    )


def readings(fast: float, slow: float, *, atr: float = 2.0, adx: float = 30.0) -> Readings:
    return Readings(ema_fast=fast, ema_slow=slow, rsi=55.0, atr=atr, adx=adx)


STRATEGY = EmaCrossoverStrategy()


@pytest.mark.unit
class TestWarmUpAndUsability:
    def test_too_few_candles_is_wait_not_no_signal(self) -> None:
        """ "Cannot tell" and "nothing there" are different facts."""
        decision = STRATEGY.decide(
            context(current=readings(101, 100), previous=readings(99, 100), bars=5)
        )
        assert decision.kind is DecisionKind.WAIT
        assert "warm-up" in decision.reason

    def test_warm_up_is_one_more_than_the_slow_period(self) -> None:
        assert STRATEGY.warm_up_bars == EmaCrossoverSettings().slow_period + 1

    def test_exactly_the_warm_up_count_is_enough(self) -> None:
        decision = STRATEGY.decide(
            context(
                current=readings(101, 100),
                previous=readings(99, 100),
                bars=STRATEGY.warm_up_bars,
            )
        )
        assert decision.kind is DecisionKind.ENTRY_INTENT

    def test_a_missing_indicator_is_wait(self) -> None:
        decision = STRATEGY.decide(
            context(
                current=Readings(ema_fast=101, ema_slow=100, atr=None, adx=30.0),
                previous=readings(99, 100),
            )
        )
        assert decision.kind is DecisionKind.WAIT

    def test_a_missing_previous_reading_is_wait(self) -> None:
        decision = STRATEGY.decide(
            current_previous_missing := context(current=readings(101, 100), previous=Readings())
        )
        assert current_previous_missing.previous.ema_fast is None
        assert decision.kind is DecisionKind.WAIT


@pytest.mark.unit
class TestTheCrossoverItself:
    def test_a_long_crossover_on_this_candle_is_an_entry(self) -> None:
        decision = STRATEGY.decide(context(current=readings(101, 100), previous=readings(99, 100)))
        assert decision.kind is DecisionKind.ENTRY_INTENT
        assert decision.entry is not None
        assert decision.entry.direction is Direction.LONG

    def test_a_short_crossover_on_this_candle_is_an_entry(self) -> None:
        decision = STRATEGY.decide(context(current=readings(99, 100), previous=readings(101, 100)))
        assert decision.entry is not None
        assert decision.entry.direction is Direction.SHORT

    def test_a_crossover_that_already_existed_is_not_an_entry(self) -> None:
        """The rule fires on the bar it happens, once."""
        decision = STRATEGY.decide(context(current=readings(102, 100), previous=readings(101, 100)))
        assert decision.kind is DecisionKind.NO_SIGNAL
        assert "no crossover" in decision.reason

    def test_touching_without_crossing_is_not_an_entry(self) -> None:
        decision = STRATEGY.decide(context(current=readings(100, 100), previous=readings(99, 100)))
        assert decision.kind is DecisionKind.NO_SIGNAL

    def test_crossing_up_from_exactly_equal_is_an_entry(self) -> None:
        """At-or-below on the previous bar counts, so a touch then a cross fires."""
        decision = STRATEGY.decide(context(current=readings(101, 100), previous=readings(100, 100)))
        assert decision.kind is DecisionKind.ENTRY_INTENT


@pytest.mark.unit
class TestFiltersAndEligibility:
    def test_adx_below_the_floor_blocks_the_entry(self) -> None:
        decision = STRATEGY.decide(
            context(
                current=readings(101, 100, adx=15.0),
                previous=readings(99, 100, adx=15.0),
            )
        )
        assert decision.kind is DecisionKind.NO_SIGNAL
        assert "trend floor" in decision.reason

    def test_adx_exactly_at_the_floor_is_allowed(self) -> None:
        decision = STRATEGY.decide(
            context(
                current=readings(101, 100, adx=20.0),
                previous=readings(99, 100, adx=20.0),
            )
        )
        assert decision.kind is DecisionKind.ENTRY_INTENT

    def test_the_filter_can_be_turned_off(self) -> None:
        strategy = EmaCrossoverStrategy(EmaCrossoverSettings(adx_minimum=None))
        decision = strategy.decide(
            context(
                current=Readings(ema_fast=101, ema_slow=100, atr=2.0, adx=None),
                previous=Readings(ema_fast=99, ema_slow=100, atr=2.0, adx=None),
            )
        )
        assert decision.kind is DecisionKind.ENTRY_INTENT

    def test_an_ineligible_direction_is_no_signal_not_a_flipped_trade(self) -> None:
        strategy = EmaCrossoverStrategy(EmaCrossoverSettings(allow_short=False))
        decision = strategy.decide(context(current=readings(99, 100), previous=readings(101, 100)))
        assert decision.kind is DecisionKind.NO_SIGNAL
        assert "not eligible" in decision.reason


@pytest.mark.unit
class TestLevelsComeFromTheAtrReading:
    def test_a_long_stop_and_target_are_hand_checkable(self) -> None:
        """close 100, ATR 2, multiples 1.5 and 3 -> stop 97, target 106."""
        decision = STRATEGY.decide(
            context(current=readings(101, 100, atr=2.0), previous=readings(99, 100), close="100")
        )
        assert decision.entry is not None
        assert decision.entry.intended_entry == Decimal("100")
        assert decision.entry.stop == Decimal("97.0")
        assert decision.entry.targets[0].price == Decimal("106.0")

    def test_a_short_stop_and_target_mirror_it(self) -> None:
        decision = STRATEGY.decide(
            context(current=readings(99, 100, atr=2.0), previous=readings(101, 100), close="100")
        )
        assert decision.entry is not None
        assert decision.entry.stop == Decimal("103.0")
        assert decision.entry.targets[0].price == Decimal("94.0")

    def test_multiples_are_configurable_and_used_exactly(self) -> None:
        strategy = EmaCrossoverStrategy(
            EmaCrossoverSettings(atr_stop_multiple=Decimal("2"), atr_target_multiple=Decimal("4"))
        )
        decision = strategy.decide(
            context(current=readings(101, 100, atr=3.0), previous=readings(99, 100), close="50")
        )
        assert decision.entry is not None
        assert decision.entry.stop == Decimal("44.0")
        assert decision.entry.targets[0].price == Decimal("62.0")

    def test_a_stop_that_would_fall_at_or_below_zero_is_refused(self) -> None:
        decision = STRATEGY.decide(
            context(current=readings(101, 100, atr=100.0), previous=readings(99, 100), close="10")
        )
        assert decision.kind is DecisionKind.WAIT
        assert "at or below zero" in decision.reason

    def test_a_zero_atr_is_wait_not_a_zero_width_stop(self) -> None:
        decision = STRATEGY.decide(
            context(current=readings(101, 100, atr=0.0), previous=readings(99, 100))
        )
        assert decision.kind is DecisionKind.WAIT


@pytest.mark.unit
class TestExposureAndExit:
    def test_while_a_position_is_open_a_crossover_does_not_open_another(self) -> None:
        decision = STRATEGY.decide(
            context(current=readings(101, 100), previous=readings(99, 100), open_position=True)
        )
        assert decision.kind is DecisionKind.EXIT_INTENT

    def test_holding_without_a_crossover_asks_for_nothing(self) -> None:
        decision = STRATEGY.decide(
            context(current=readings(102, 100), previous=readings(101, 100), open_position=True)
        )
        assert decision.kind is DecisionKind.NO_SIGNAL
        assert "holding" in decision.reason

    def test_the_opposite_crossover_asks_the_position_to_leave(self) -> None:
        decision = STRATEGY.decide(
            context(current=readings(99, 100), previous=readings(101, 100), open_position=True)
        )
        assert decision.kind is DecisionKind.EXIT_INTENT
        assert decision.entry is None


@pytest.mark.unit
class TestDeterminismAndIdentity:
    def test_the_same_context_always_gives_the_same_decision(self) -> None:
        shape = context(current=readings(101, 100), previous=readings(99, 100))
        first = STRATEGY.decide(shape)
        second = STRATEGY.decide(shape)
        assert (first.kind, first.reason) == (second.kind, second.reason)
        assert first.entry == second.entry

    def test_parameters_are_canonical_text(self) -> None:
        strategy = EmaCrossoverStrategy(EmaCrossoverSettings(atr_stop_multiple=Decimal("1.50")))
        assert strategy.parameters()["atr_stop_multiple"] == "1.5"
        assert EmaCrossoverStrategy().parameters()["atr_stop_multiple"] == "1.5"

    def test_the_version_is_stated(self) -> None:
        assert STRATEGY.identifier == "ema-crossover-atr"
        assert STRATEGY.version == "1.0.0"


@pytest.mark.unit
class TestSettingsRefuseNonsense:
    @pytest.mark.parametrize(
        "changes",
        [
            {"fast_period": 20, "slow_period": 9},
            {"fast_period": 20, "slow_period": 20},
            {"fast_period": 0},
            {"atr_stop_multiple": Decimal("0")},
            {"atr_target_multiple": Decimal("-1")},
            {"adx_minimum": 140.0},
            {"quantity": 0},
            {"allow_long": False, "allow_short": False},
        ],
    )
    def test_an_impossible_setting_is_refused(self, changes: dict[str, object]) -> None:
        with pytest.raises(StrategyInputError):
            EmaCrossoverSettings(**changes)  # type: ignore[arg-type]


@pytest.mark.unit
class TestTheShippedParametersArePinned:
    """A tuning change must be a visible, deliberate edit - never a drift.

    "We did not tune the parameters to flatter a fixture" is not a claim a
    report can carry on its own. This is the enforceable version: the shipped
    defaults are written down here, so changing one to make a backtest look
    better fails a test and has to be argued for.

    None of these numbers is a recommendation. They are ordinary textbook
    values chosen so the reference rule has something to do.
    """

    def test_the_defaults_are_exactly_these(self) -> None:
        settings = EmaCrossoverSettings()

        assert settings.fast_period == 9
        assert settings.slow_period == 20
        assert settings.atr_stop_multiple == Decimal("1.5")
        assert settings.atr_target_multiple == Decimal("3.0")
        assert settings.adx_minimum == 20.0
        assert settings.quantity == 1
        assert settings.allow_long is True
        assert settings.allow_short is True

    def test_the_identity_states_the_rules_version(self) -> None:
        """A change that alters decisions must change this string."""
        assert STRATEGY.identifier == "ema-crossover-atr"
        assert STRATEGY.version == "1.0.0"

    def test_the_reward_multiple_is_not_quietly_larger_than_documented(self) -> None:
        """Widening the target is the cheapest way to flatter a win rate."""
        settings = EmaCrossoverSettings()

        assert settings.atr_target_multiple == settings.atr_stop_multiple * 2
