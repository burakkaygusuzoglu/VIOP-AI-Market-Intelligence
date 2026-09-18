"""Simulation rules for paper trading (Phase 9).

## Why the rules are an object that travels with each position

A paper fill is not an observation of a market. It is the output of a model:
"given this bar, where would a stop have filled?" Every answer to that question
is a choice - fill at the stop or at the gapped open, assume the stop or the
target came first inside one bar, charge a fee or not. If those choices lived as
constants in the engine, changing one would silently rewrite the history of
every position ever simulated.

So the choices are a ``SimulationPolicy``, frozen, stored with the position when
it is opened, and read back from storage for every later step. The default can
change tomorrow; a position opened today keeps the rules it was opened under.

## What v1 fixes, and what it leaves configurable

Fixed by ``paper-sim/v1`` (changing any of these is a new version):

* **Entry** fills at the *open of the first bar that begins at or after the
  decision time*. No limit, stop or stop-limit entry is modelled, and none is
  claimed. The intended entry price is what the risk engine sized against; the
  fill price is what the next open actually was. They are never synonyms.
* **A stop** is a stop-market exit. Touched inside a bar, it fills at the stop
  price. Gapped through at the open, it fills at the *open* - the first price
  that existed - never at the stop price the market skipped.
* **A target** is a take-profit at its price. Touched inside a bar, or gapped
  through at the open, it fills at the *target price*. Price improvement from a
  gap is deliberately not credited: assuming the better fill would be the
  optimistic choice, and v1 does not make optimistic choices on the user's
  behalf.
* **A manual close** fills at the open of the next bar after it was requested.
* **Slippage** is adverse and applies to market-style fills only: entry, stop
  and manual exit. A target is a price-limited fill and receives none.

Configurable per position:

* ``SameBarPolicy`` - what happens when one bar touches both the stop and a
  target, which OHLC cannot order.
* ``SlippagePolicy`` - none, or a fixed adverse amount in price points.
* ``FeePolicy`` - not modelled, or a user-stated all-in amount per unit per fill.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.enums import Direction

SIMULATION_RULES_VERSION = "paper-sim/v1"
"""The rule set this engine implements. Stored with every position."""

SUPPORTED_RULES_VERSIONS: frozenset[str] = frozenset({SIMULATION_RULES_VERSION})
"""Every version this engine can reproduce exactly. An unknown version is
refused rather than simulated under whatever the current rules happen to be."""

ENTRY_MODEL = "NEXT_BAR_OPEN"
STOP_FILL_MODEL = "STOP_PRICE_OR_GAPPED_OPEN"
TARGET_FILL_MODEL = "TARGET_PRICE_NO_IMPROVEMENT"
MANUAL_EXIT_MODEL = "NEXT_BAR_OPEN"


class SimulationPolicyError(ValueError):
    """A simulation policy that cannot be honoured."""


@unique
class SameBarPolicy(StrEnum):
    """What to do when one bar touches both the stop and a target.

    OHLC records four prices and no sequence. A bar that reached both the stop
    and a target could have done either first, and nothing in the data says
    which. Choosing the target would credit a profit the market may never have
    paid; so neither option here is optimistic.
    """

    STOP_FIRST = "STOP_FIRST"
    """Assume the stop was reached first and exit the whole remaining position
    at the stop. Deterministic and pessimistic. The fill is marked ambiguous and
    names every target that was also touched, so the assumption is never
    hidden."""

    HALT = "HALT"
    """Decide nothing. The position is frozen in ``AMBIGUOUS_HALTED``: no fill
    is simulated, later bars only update the mark, and the only way out is an
    explicit manual close, which fills at the open of the next bar."""


@unique
class SlippageMode(StrEnum):
    ZERO = "ZERO"
    """No slippage. A simulation choice, stated - not an assumption about any
    market's liquidity."""

    FIXED_POINTS = "FIXED_POINTS"
    """A fixed adverse amount in price points on every market-style fill."""


@unique
class FeeMode(StrEnum):
    NOT_MODELLED = "NOT_MODELLED"
    """No fee is charged and no net P&L is reported. Absent cost data is unknown
    cost, not zero cost - the same rule the Phase 3 P&L engine follows."""

    USER_DEFINED_PER_UNIT = "USER_DEFINED_PER_UNIT"
    """An all-in amount per unit of quantity per fill, stated by the user. Not a
    verified commission schedule; reported as user-defined wherever it appears."""


@dataclass(frozen=True, slots=True)
class SlippagePolicy:
    mode: SlippageMode = SlippageMode.ZERO
    points: Decimal | None = None

    def __post_init__(self) -> None:
        if self.mode is SlippageMode.ZERO:
            if self.points is not None:
                raise SimulationPolicyError("ZERO slippage takes no points")
            return
        if self.points is None:
            raise SimulationPolicyError("FIXED_POINTS slippage needs points")
        if not self.points.is_finite() or self.points <= 0:
            raise SimulationPolicyError(
                f"slippage points must be a positive finite amount, got {self.points}"
            )

    @property
    def amount(self) -> Decimal:
        return self.points if self.points is not None else Decimal(0)


@dataclass(frozen=True, slots=True)
class FeePolicy:
    mode: FeeMode = FeeMode.NOT_MODELLED
    per_unit: Decimal | None = None

    def __post_init__(self) -> None:
        if self.mode is FeeMode.NOT_MODELLED:
            if self.per_unit is not None:
                raise SimulationPolicyError("NOT_MODELLED fees take no amount")
            return
        if self.per_unit is None:
            raise SimulationPolicyError("USER_DEFINED_PER_UNIT fees need per_unit")
        if not self.per_unit.is_finite() or self.per_unit < 0:
            raise SimulationPolicyError(
                f"fee per unit must be a non-negative finite amount, got {self.per_unit}"
            )

    @property
    def is_modelled(self) -> bool:
        return self.mode is FeeMode.USER_DEFINED_PER_UNIT

    def fee_for(self, quantity: int) -> Decimal | None:
        """The fee for one fill, or ``None`` when fees are not modelled."""
        if self.per_unit is None:
            return None
        return self.per_unit * quantity


@dataclass(frozen=True, slots=True)
class SimulationPolicy:
    """Every simulation choice for one position, frozen at opening."""

    rules_version: str = SIMULATION_RULES_VERSION
    same_bar: SameBarPolicy = SameBarPolicy.STOP_FIRST
    slippage: SlippagePolicy = SlippagePolicy()
    fees: FeePolicy = FeePolicy()

    def __post_init__(self) -> None:
        if self.rules_version not in SUPPORTED_RULES_VERSIONS:
            raise SimulationPolicyError(
                f"simulation rules {self.rules_version!r} are not supported by this engine; "
                f"supported: {sorted(SUPPORTED_RULES_VERSIONS)}"
            )


def adverse_price(
    price: Decimal, points: Decimal, direction: Direction, *, entering: bool
) -> Decimal:
    """Move a market-style fill against the position by ``points``.

    Buying is worse higher and selling is worse lower. A long enters by buying
    and exits by selling; a short does the reverse. Written out rather than
    derived from a sign so the four cases can be read directly.
    """
    if direction is Direction.LONG:
        return price + points if entering else price - points
    if direction is Direction.SHORT:
        return price - points if entering else price + points
    raise SimulationPolicyError(f"{direction.value} is not a tradeable direction")
