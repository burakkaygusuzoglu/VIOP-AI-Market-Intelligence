"""The reference strategy: a confirmed EMA crossover with an ATR stop.

**This is a validation instrument, not a recommendation.** It exists so the
backtest runner can be proven correct against a rule simple enough to derive by
hand. Nothing here is a claim that the rule is profitable, and nothing about
being implemented makes it advice.

## Why this rule

Every input already exists as an authoritative Phase 1 indicator, computed by
the one technical engine and aligned candle-for-candle with its source:

* ``ema(fast)`` and ``ema(slow)`` - the crossover itself;
* ``atr`` - the stop distance;
* ``adx`` - an optional trend-strength floor.

No new indicator was written for this strategy. That was the constraint: a
rule that needed a new calculation would have meant a second, strategy-shaped
technical engine, which is exactly what this phase forbids.

## The rule

Evaluated on the driver timeframe, once per closed candle:

* **Warm-up.** ``slow_period + 1`` driver candles must be confirmed, and every
  reading the rule uses must have a value. Otherwise ``WAIT`` - not
  ``NO_SIGNAL``, because "cannot tell" is not "nothing there".
* **Entry.** The fast EMA crossed the slow EMA *between the previous confirmed
  boundary and this one*: it was at or below on the previous bar and is above
  on this one (long), or the mirror image (short). A crossover that already
  existed is not an entry - only the bar it happens on is.
* **Trend filter.** When ``adx_minimum`` is set, ADX at this boundary must be
  at or above it. A rule that fires in every chop is a poor validation
  instrument because almost every trade looks the same.
* **Direction eligibility.** Long, short or both, as configured. A direction
  that is not eligible produces ``NO_SIGNAL``, not a silently flipped trade.
* **Levels.** The intended entry is this bar's close - the last confirmed
  price. The stop is ``atr x atr_stop_multiple`` away from it, and the single
  target is ``atr x atr_target_multiple`` in the other direction. Both come
  from the ATR reading at this boundary, so neither is invented.
* **Exposure.** One position at a time. While one is open the rule returns
  ``NO_SIGNAL`` for new entries and evaluates its exit instead.
* **Exit.** The opposite crossover closes the position. The stop and the target
  are carried by the position itself and are the Phase 9 engine's business, not
  the strategy's.

## What it deliberately does not do

It does not read structure, zones, regime or evidence. Those are real Phase 2-4
outputs, but each would add cost per boundary and a second thing to explain
when a trade looks wrong. If a later phase wants a structure-aware strategy, it
gets its own identifier and version rather than changing this one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.backtest.policy import (
    DecisionKind,
    EntryIntent,
    Readings,
    StrategyContext,
    StrategyDecision,
    StrategyInputError,
    TargetLevel,
)
from app.domain.common.enums import Direction

IDENTIFIER = "ema-crossover-atr"
VERSION = "1.0.0"
"""Rules version. Any change that alters a decision changes this, and a run
pinned to an older version is refused rather than replayed under new rules."""


@dataclass(frozen=True, slots=True)
class EmaCrossoverSettings:
    """Every parameter the rule uses. No hidden defaults that move money."""

    fast_period: int = 9
    slow_period: int = 20
    atr_stop_multiple: Decimal = Decimal("1.5")
    atr_target_multiple: Decimal = Decimal("3.0")
    adx_minimum: float | None = 20.0
    """``None`` disables the trend filter."""

    quantity: int = 1
    """Units the rule asks for. The risk engine decides what is allowed."""

    allow_long: bool = True
    allow_short: bool = True

    def __post_init__(self) -> None:
        if self.fast_period < 1 or self.slow_period < 1:
            raise StrategyInputError("EMA periods are whole numbers of at least one")
        if self.fast_period >= self.slow_period:
            raise StrategyInputError(
                f"the fast EMA must be shorter than the slow one, got "
                f"{self.fast_period} and {self.slow_period}"
            )
        for name, value in (
            ("atr_stop_multiple", self.atr_stop_multiple),
            ("atr_target_multiple", self.atr_target_multiple),
        ):
            if not value.is_finite() or value <= 0:
                raise StrategyInputError(f"{name} must be a positive finite number")
        if self.adx_minimum is not None and not 0 <= self.adx_minimum <= 100:
            raise StrategyInputError("adx_minimum is an ADX reading between 0 and 100")
        if self.quantity < 1:
            raise StrategyInputError("quantity is at least one whole unit")
        if not (self.allow_long or self.allow_short):
            raise StrategyInputError("at least one direction must be eligible")


class EmaCrossoverStrategy:
    """The reference policy. Stateless: every decision comes from its context."""

    def __init__(self, settings: EmaCrossoverSettings | None = None) -> None:
        self._settings = settings or EmaCrossoverSettings()

    @property
    def identifier(self) -> str:
        return IDENTIFIER

    @property
    def version(self) -> str:
        return VERSION

    @property
    def settings(self) -> EmaCrossoverSettings:
        return self._settings

    @property
    def warm_up_bars(self) -> int:
        """One more than the slow period, because the rule compares two bars."""
        return self._settings.slow_period + 1

    def parameters(self) -> dict[str, str]:
        """Canonical text for the fingerprint. Every value that moves a trade."""
        settings = self._settings
        return {
            "fast_period": str(settings.fast_period),
            "slow_period": str(settings.slow_period),
            "atr_stop_multiple": format(settings.atr_stop_multiple.normalize(), "f"),
            "atr_target_multiple": format(settings.atr_target_multiple.normalize(), "f"),
            "adx_minimum": "none" if settings.adx_minimum is None else repr(settings.adx_minimum),
            "quantity": str(settings.quantity),
            "allow_long": str(settings.allow_long).lower(),
            "allow_short": str(settings.allow_short).lower(),
        }

    def decide(self, context: StrategyContext) -> StrategyDecision:
        settings = self._settings
        if context.bars_available < self.warm_up_bars:
            return StrategyDecision.wait(
                f"{context.bars_available} of {self.warm_up_bars} warm-up candles confirmed"
            )
        if not _usable(context.current, settings) or not _usable(context.previous, settings):
            return StrategyDecision.wait("an indicator this rule reads is still warming up")

        crossed = _crossing(context)
        if context.has_open_position:
            if crossed is None:
                return StrategyDecision.no_signal("holding; no opposite crossover")
            return StrategyDecision.exit_now(
                f"{crossed.value.lower()} crossover against the open position"
            )

        if crossed is None:
            return StrategyDecision.no_signal("no crossover on this candle")
        if crossed is Direction.LONG and not settings.allow_long:
            return StrategyDecision.no_signal("long entries are not eligible in this run")
        if crossed is Direction.SHORT and not settings.allow_short:
            return StrategyDecision.no_signal("short entries are not eligible in this run")

        adx = context.current.adx
        if settings.adx_minimum is not None and (adx is None or adx < settings.adx_minimum):
            return StrategyDecision.no_signal(
                f"ADX {adx} is below the {settings.adx_minimum} trend floor"
            )

        atr = context.current.atr
        if atr is None or atr <= 0:
            return StrategyDecision.wait("ATR has no usable reading at this candle")

        entry = context.bar.close
        distance = Decimal(str(atr)) * settings.atr_stop_multiple
        reward = Decimal(str(atr)) * settings.atr_target_multiple
        if crossed is Direction.LONG:
            stop = entry - distance
            target = entry + reward
        else:
            stop = entry + distance
            target = entry - reward
        if stop <= 0 or target <= 0:
            return StrategyDecision.wait(
                "the ATR stop or target would fall at or below zero on this candle"
            )

        return StrategyDecision.enter(
            EntryIntent(
                direction=crossed,
                intended_entry=entry,
                stop=stop,
                targets=(TargetLevel(price=target, quantity=settings.quantity),),
                quantity=settings.quantity,
            ),
            (
                f"{crossed.value.lower()} EMA{settings.fast_period}/"
                f"EMA{settings.slow_period} crossover confirmed on this candle"
            ),
        )


def _usable(readings: Readings, settings: EmaCrossoverSettings) -> bool:
    """Whether the readings this rule actually consults all have values."""
    required = [readings.ema_fast, readings.ema_slow, readings.atr]
    if settings.adx_minimum is not None:
        required.append(readings.adx)
    return all(value is not None for value in required)


def _crossing(context: StrategyContext) -> Direction | None:
    """Which way the fast EMA crossed the slow one *on this candle*.

    A crossover that already existed on the previous boundary is not one here:
    the comparison is between the two confirmed boundaries, so the rule fires
    once, on the bar it happened.
    """
    previous_fast, previous_slow = context.previous.ema_fast, context.previous.ema_slow
    current_fast, current_slow = context.current.ema_fast, context.current.ema_slow
    if None in (previous_fast, previous_slow, current_fast, current_slow):
        return None
    assert previous_fast is not None and previous_slow is not None  # noqa: S101 - narrowed above
    assert current_fast is not None and current_slow is not None  # noqa: S101 - narrowed above
    if previous_fast <= previous_slow and current_fast > current_slow:
        return Direction.LONG
    if previous_fast >= previous_slow and current_fast < current_slow:
        return Direction.SHORT
    return None


__all__ = [
    "IDENTIFIER",
    "VERSION",
    "DecisionKind",
    "EmaCrossoverSettings",
    "EmaCrossoverStrategy",
]
