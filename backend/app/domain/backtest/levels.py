"""Making a derived level executable (Phase 12).

A strategy computes a stop from an ATR reading. That number is arithmetic, not
a price: 111.20 minus 1.5 x 0.7033... lands wherever the arithmetic lands, and
almost never on the exchange's price grid. A stop that cannot be placed where
the risk calculation assumed it is not a stop, so Phase 3 refuses the whole
sizing rather than pretend - which is correct for a level a *person* typed, and
which would make a derived level impossible to trade at all.

So the runner aligns derived levels before proposing them, under three rules
that keep the refusal's original point intact:

1. **Only derived protective levels move.** The entry is a price the market
   actually printed. If it is off the grid, the dataset and the product
   disagree about what this instrument is, and that is a finding to surface -
   not something to round away. Nothing here touches it.
2. **Always away from the entry.** A stop moves further from the entry and a
   target moves further from the entry. Both directions are the unfavourable
   one: the risk per unit grows (so the position sizes smaller, never larger)
   and the reward becomes harder to reach. Rounding to the *nearest* tick would
   sometimes shrink the measured risk distance and silently inflate the size.
3. **Never onto an unconfirmed grid.** Alignment needs a verified increment.
   Without one the levels are left exactly as computed and the existing
   ``UNVERIFIED`` feasibility report stands.

This module is pure ``Decimal`` arithmetic over values the caller supplies. It
holds no market facts and decides nothing about risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.backtest.policy import EntryIntent, TargetLevel

__all__ = ["Alignment", "align_intent", "away_from", "on_grid"]


@dataclass(frozen=True, slots=True)
class Alignment:
    """An intent whose derived levels sit on the grid, and what that cost."""

    intent: EntryIntent
    increment: Decimal
    moved: tuple[str, ...]
    """Which levels the grid actually moved, in the order stop, target 1, ..."""

    @property
    def note(self) -> str:
        """One sentence for the decision trace. Silence would be worse."""
        if not self.moved:
            return f"levels already sit on the {self.increment} grid"
        return f"{', '.join(self.moved)} moved away from the entry onto the {self.increment} grid"


def on_grid(price: Decimal, increment: Decimal) -> bool:
    """Whether ``price`` is a whole number of ``increment``.

    Exact ``Decimal`` remainder, with no tolerance of any kind. An epsilon here
    would be a way of calling an unexecutable price executable, which is the
    single thing this module exists to avoid.
    """
    if increment <= 0:
        raise ValueError(f"increment must be positive, got {increment}")
    return price % increment == 0


def away_from(entry: Decimal, price: Decimal, increment: Decimal) -> Decimal:
    """``price`` on the ``increment`` grid, never nearer to ``entry``.

    A price already on the grid is returned unchanged - the same object's
    value, not a re-quantised one, so an aligned level cannot drift on a second
    pass. A price level with the entry is returned unchanged too: a zero
    distance is a strategy error, and widening it here would hide it from the
    risk engine that is supposed to refuse it.
    """
    if increment <= 0:
        raise ValueError(f"increment must be positive, got {increment}")

    remainder = price % increment
    if remainder == 0 or price == entry:
        return price
    lower = price - remainder
    return lower if price < entry else lower + increment


def align_intent(intent: EntryIntent, increment: Decimal) -> Alignment:
    """Align an intent's stop and targets. The intended entry is never moved."""
    moved: list[str] = []

    stop = away_from(intent.intended_entry, intent.stop, increment)
    if stop != intent.stop:
        moved.append("stop")

    targets: list[TargetLevel] = []
    for ordinal, level in enumerate(intent.targets, start=1):
        price = away_from(intent.intended_entry, level.price, increment)
        if price != level.price:
            moved.append(f"target {ordinal}")
        targets.append(TargetLevel(price=price, quantity=level.quantity))

    aligned = EntryIntent(
        direction=intent.direction,
        intended_entry=intent.intended_entry,
        stop=stop,
        targets=tuple(targets),
        quantity=intent.quantity,
    )
    return Alignment(intent=aligned, increment=increment, moved=tuple(moved))
