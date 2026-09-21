"""The one place a float becomes money (Phase 12).

Indicators are `float` by design - EMA, Wilder smoothing and standard deviation
are irrational-valued recursions, and `Decimal` would carry precision the
mathematics does not have. Prices and money are `Decimal`. The reference
strategy sits exactly on that seam: it reads an ATR float and produces stop and
target *prices*.

Two things are pinned here.

**The conversion is `Decimal(str(value))`**, which is the policy this repository
already uses at every other float-to-money crossing (see
`app/application/synthesis/context.py`). It is exact with respect to the float
that was computed and invents nothing: `Decimal(0.1)` would drag in the whole
binary expansion, and quantising to some chosen number of places would silently
discard a digit the indicator actually produced.

**The multiplication happens in `Decimal`, not in `float`.** Converting first
and multiplying afterwards keeps the float error at exactly one step. The
alternative - multiplying in float and converting the product - accumulates a
second rounding before the value is ever a price.

Neither of these is a way of making an unexecutable price executable: the
result still has to survive the grid check, which is what the runner's
alignment and Phase 3's refusal are for.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.domain.backtest.levels import on_grid
from app.domain.backtest.policy import DecisionKind, Readings, StrategyContext
from app.domain.backtest.strategies.ema_crossover import (
    EmaCrossoverSettings,
    EmaCrossoverStrategy,
)
from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from tests.factories_replay import BASE, FIXTURE_SYMBOL

pytestmark = pytest.mark.unit

AWKWARD = 0.1 + 0.2
"""0.30000000000000004 - a float that is famously not what it prints as."""


def bar(close: str) -> Candle:
    price = Decimal(close)
    return Candle(
        symbol=FIXTURE_SYMBOL,
        timeframe=Timeframe.M5,
        open_time=BASE,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1000"),
        is_closed=True,
    )


def crossing_up(atr: float, close: str = "100") -> StrategyContext:
    return StrategyContext(
        as_of=BASE + timedelta(minutes=5),
        symbol=FIXTURE_SYMBOL,
        driver=Timeframe.M5,
        bar=bar(close),
        current=Readings(ema_fast=101.0, ema_slow=100.0, rsi=55.0, atr=atr, adx=30.0),
        previous=Readings(ema_fast=99.0, ema_slow=100.0, rsi=55.0, atr=atr, adx=30.0),
        higher={},
        bars_available=100,
        has_open_position=False,
    )


class TestTheConversionIsExactWithRespectToTheFloat:
    def test_the_levels_match_decimal_of_the_printed_float(self) -> None:
        settings = EmaCrossoverSettings()
        decision = EmaCrossoverStrategy(settings).decide(crossing_up(AWKWARD))

        assert decision.kind is DecisionKind.ENTRY_INTENT
        assert decision.entry is not None
        expected = Decimal(str(AWKWARD))
        assert decision.entry.stop == Decimal("100") - expected * settings.atr_stop_multiple
        assert decision.entry.targets[0].price == (
            Decimal("100") + expected * settings.atr_target_multiple
        )

    def test_the_binary_expansion_is_not_dragged_in(self) -> None:
        """`Decimal(float)` would produce a 50-digit tail nobody measured."""
        decision = EmaCrossoverStrategy().decide(crossing_up(AWKWARD))

        assert decision.entry is not None
        assert decision.entry.stop != Decimal("100") - Decimal(AWKWARD) * Decimal("1.5")
        assert len(decision.entry.stop.as_tuple().digits) < 25

    def test_no_value_is_quantised_to_a_chosen_number_of_places(self) -> None:
        """Rounding the ATR here would discard a digit the indicator produced.

        The tail survives all the way into the level. The grid is applied
        later, by the runner, using the product's verified increment - not
        guessed at inside the rule.
        """
        decision = EmaCrossoverStrategy().decide(crossing_up(AWKWARD))

        assert decision.entry is not None
        assert decision.entry.stop == Decimal("99.549999999999999940")
        assert decision.entry.stop != Decimal("99.55")

    def test_the_multiplication_is_done_in_decimal_not_float(self) -> None:
        """Converting the float *product* would round twice, not once."""
        settings = EmaCrossoverSettings()
        decision = EmaCrossoverStrategy(settings).decide(crossing_up(AWKWARD))

        assert decision.entry is not None
        multiplied_in_float = Decimal(str(AWKWARD * float(settings.atr_stop_multiple)))
        assert decision.entry.stop == Decimal("100") - Decimal(str(AWKWARD)) * Decimal("1.5")
        assert decision.entry.stop != Decimal("100") - multiplied_in_float


class TestPrecisionIsNotAWayRoundTheGrid:
    def test_an_awkward_atr_produces_an_off_grid_stop_that_is_not_hidden(self) -> None:
        """The rule does not pretend. Making it placeable is the runner's job."""
        decision = EmaCrossoverStrategy().decide(crossing_up(AWKWARD))

        assert decision.entry is not None
        assert not on_grid(decision.entry.stop, Decimal("0.25"))

    @pytest.mark.parametrize("atr", [2.0, 0.5, 1.5])
    def test_an_atr_whose_multiples_land_on_the_grid_needs_no_alignment(self, atr: float) -> None:
        """1.5x and 3.0x of each of these is a whole number of 0.25 ticks."""
        decision = EmaCrossoverStrategy().decide(crossing_up(atr))

        assert decision.entry is not None
        assert on_grid(decision.entry.stop, Decimal("0.25"))

    def test_the_geometry_survives_an_awkward_atr(self) -> None:
        """Whatever the tail, the stop is below and the target above a long."""
        decision = EmaCrossoverStrategy().decide(crossing_up(AWKWARD))

        assert decision.entry is not None
        assert decision.entry.stop < decision.entry.intended_entry
        assert decision.entry.targets[0].price > decision.entry.intended_entry
        assert decision.entry.stop.is_finite()

    def test_a_vanishingly_small_atr_is_still_a_real_decimal(self) -> None:
        """`str(1e-05)` is exponent notation, and `Decimal` reads it correctly."""
        decision = EmaCrossoverStrategy().decide(crossing_up(1e-05))

        assert decision.entry is not None
        assert decision.entry.stop == Decimal("100") - Decimal("1E-5") * Decimal("1.5")
        assert decision.entry.stop < Decimal("100")
