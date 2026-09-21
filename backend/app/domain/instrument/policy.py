"""The product-policy boundary (Phase 8.5).

## What varies between products, and what does not

The risk engine's *reasoning* is the same for every market: a risk budget, a
stop on the correct side of the entry, a loss per unit of quantity, the largest
whole quantity that loss fits into, a margin constraint when one exists, and a
refusal to merge an unknown constraint into a known one. None of that is a
futures idea.

What differs is a short, concrete list, and it is exactly what the four money
engines (sizing, margin, P&L, what-if) actually read from a futures contract
today:

* **point value** - money per one unit of price movement per one unit of
  quantity. For a linear future, the contract multiplier.
* **price increment** - whether proposed levels are executable. For a future,
  the tick grid.
* **margin requirement** - per unit of quantity, and how well it is known.
* **calculability** - whether this product's economics are ones the engine
  models at all. For a future, linear valuation and self-consistent metadata.
* **quantity semantics** - whether quantity is whole units, and what a unit is
  called.

That is one cohesive abstraction, not five. Splitting it into a
``QuantityPolicy``, a ``MarginPolicy`` and so on would give each interface one
implementation and one caller, and nothing to vary independently yet. It can be
split when a second product shows two of these changing separately.

## What this module deliberately does not do

It holds no market value. No tick, no multiplier, no margin rate, no fee, no
currency. Those arrive through a product's own verified metadata.

It is a ``Protocol``, not a base class. A product policy is composed around its
own metadata record - ``FuturesProductPolicy`` wraps a ``FuturesContract`` -
and inherits nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique
from typing import Protocol, runtime_checkable

from app.domain.common.verification import VerifiedValue
from app.domain.instrument.asset_class import require_implemented
from app.domain.instrument.identity import InstrumentId


@unique
class Support(StrEnum):
    """Three answers, because two would lie.

    A boolean ``supports_funding = False`` cannot distinguish "this product has
    no funding" from "nobody has told us whether it does". The second is the
    normal state for any product not yet modelled, and a risk engine that read
    it as the first would quietly omit a cost.
    """

    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ProductCapabilities:
    """Structural properties of a product *type*.

    These are properties of what kind of instrument something is - a dated
    future has an expiry, a perpetual has funding - not facts about any venue.
    They never carry a number. A venue restriction on shorting, say, is a
    verified market fact and does not belong here.
    """

    margin: Support
    expiry: Support
    short_selling: Support
    fractional_quantity: Support
    funding: Support
    open_interest: Support


@dataclass(frozen=True, slots=True)
class ProductVocabulary:
    """What the product calls its own units, for messages a person reads.

    Carried by the policy because it differs by product - a future is sized in
    contracts, an equity in shares - and because the risk engine's explanations
    must stay exactly as precise as they were before they became generic.
    """

    unit: str
    """Singular quantity noun: ``"contract"``."""

    unit_counted: str
    """Noun as used after a number: ``"contract(s)"``."""

    point_value: str
    """Short name of the point value: ``"multiplier"``."""

    point_value_qualified: str
    """Fuller name, for a refusal: ``"contract multiplier"``."""


@unique
class TickFeasibility(StrEnum):
    """How much is known about whether the levels are actually executable.

    Moved here from ``app.domain.risk.sizing`` in Phase 8.5, unchanged, because
    executability on a price grid is a question every product answers - with
    its own grid. ``risk.sizing`` still exports this same class.
    """

    ON_GRID = "ON_GRID"
    """Tick size is verified and both entry and stop sit on the grid."""

    OFF_GRID = "OFF_GRID"
    """Tick size is verified and a level does not sit on the grid."""

    UNVERIFIED = "UNVERIFIED"
    """A tick size exists but is not a verified current fact."""

    MISSING = "MISSING"
    """No tick size at all."""


@unique
class MarginFeasibility(StrEnum):
    """How much is known about whether the margin permits the position.

    Moved here from ``app.domain.risk.sizing`` in Phase 8.5, unchanged.
    """

    KNOWN = "KNOWN"
    UNVERIFIED = "UNVERIFIED"
    """A margin figure exists but is not a verified current fact."""

    MISSING = "MISSING"
    """No margin figure at all."""


@dataclass(frozen=True, slots=True)
class PriceIncrementCheck:
    """Whether proposed levels sit on the product's price grid, and why."""

    feasibility: TickFeasibility
    detail: str


@dataclass(frozen=True, slots=True)
class MarginRequirement:
    """Margin per unit of quantity, and how well it is known.

    ``per_unit`` is set only when ``feasibility`` is ``KNOWN``. An unknown margin
    is ``None``, never zero - zero would read as "no margin needed".
    """

    feasibility: MarginFeasibility
    per_unit: Decimal | None

    def __post_init__(self) -> None:
        if (self.feasibility is MarginFeasibility.KNOWN) != (self.per_unit is not None):
            raise ValueError("per_unit is present exactly when margin feasibility is KNOWN")


@runtime_checkable
class ProductPolicy(Protocol):
    """Everything the generic money engines need to know about a product."""

    @property
    def instrument(self) -> InstrumentId: ...

    @property
    def capabilities(self) -> ProductCapabilities: ...

    @property
    def vocabulary(self) -> ProductVocabulary: ...

    def require_calculable(self, operation: str) -> None:
        """Raise if this product's economics are not ones the engine models."""
        ...

    def point_value(self) -> VerifiedValue[Decimal]:
        """Money per one unit of price per one unit of quantity, with provenance.

        Returned with its status so the caller decides - a sizing engine
        declines an unverified value, a P&L engine raises on one.
        """
        ...

    def price_increment(self) -> VerifiedValue[Decimal] | None:
        """The grid this product's prices move on, with provenance.

        ``None`` means the product has no such concept at all. A value whose
        status is not authoritative means one was supplied but is not a
        verified current fact - a caller must not round to a grid it cannot
        confirm, because a fabricated grid produces fabricated levels.

        Added in Phase 12 for the backtest runner, which derives protective
        levels arithmetically and therefore has to make them executable before
        proposing them. Asking "does this fit?" - which is all
        ``price_increment_check`` answers - cannot tell a caller what to
        propose instead.
        """
        ...

    def price_increment_check(
        self, entry_price: Decimal, stop_price: Decimal
    ) -> PriceIncrementCheck:
        """Whether entry and stop are executable levels. Never rounds either."""
        ...

    def margin_requirement(self) -> MarginRequirement: ...


class UnsupportedQuantitySemanticsError(ValueError):
    """Raised when an integral-quantity engine meets a product it cannot size.

    The engines count quantity in whole units and floor to them, which is the
    conservative, mandatory behaviour for contracts. A product whose quantity
    *may* be fractional - or whose quantity semantics are unknown - cannot be
    sized that way without either under-reporting what is allowed or hiding a
    rule nobody supplied. It is refused rather than approximated.
    """

    def __init__(self, product: ProductPolicy, operation: str) -> None:
        state = product.capabilities.fractional_quantity
        super().__init__(
            f"{operation} counts whole units, but fractional quantity is {state.value} "
            f"for {product.instrument.symbol}"
        )


def require_product_calculable(product: ProductPolicy, operation: str) -> None:
    """The guard every generic money engine runs first, in this order.

    1. The asset class is implemented - nothing falls back to futures rules.
    2. Quantity is established as whole units.
    3. The product's own economics are modelled (for a future: linear valuation
       and self-consistent metadata).

    For a futures product the first two can never raise, so futures behaviour is
    exactly what it was before this guard existed.
    """
    require_implemented(product.instrument.asset_class.value, operation)
    if product.capabilities.fractional_quantity is not Support.UNSUPPORTED:
        raise UnsupportedQuantitySemanticsError(product, operation)
    product.require_calculable(operation)
