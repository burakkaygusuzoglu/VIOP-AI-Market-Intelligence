"""Open interest context (master spec section 33).

The four readings the specification names:

===========  ===========  ==========================================
price        OI           context
===========  ===========  ==========================================
up           up           possible new long participation
up           down         possible short covering
down         up           possible new short participation
down         down         possible liquidation
===========  ===========  ==========================================

Every one of those is prefixed "possible" in the specification, and that word
is the whole content of this module. Rising price on rising open interest is
*consistent with* new buyers committing capital; it is equally consistent with
a squeeze, a roll, or an index rebalance. Section 33 says to treat these as
contextual interpretations and never as absolute rules, so nothing here emits
a direction, a score or a recommendation.

The engine also refuses to guess. If either observation is missing, or if
either quantity did not actually move, the answer says so rather than being
forced into one of the four cells.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.futures.contract import (
    FuturesContract,
    FuturesQuote,
    require_matching_quote,
    require_same_instrument,
)


@unique
class OpenInterestContext(StrEnum):
    """A contextual reading, never a trading rule."""

    NEW_LONG_PARTICIPATION = "NEW_LONG_PARTICIPATION"
    """Price up, open interest up."""

    SHORT_COVERING = "SHORT_COVERING"
    """Price up, open interest down."""

    NEW_SHORT_PARTICIPATION = "NEW_SHORT_PARTICIPATION"
    """Price down, open interest up."""

    LIQUIDATION = "LIQUIDATION"
    """Price down, open interest down."""

    UNCHANGED = "UNCHANGED"
    """Price or open interest did not move beyond tolerance.

    A single state rather than three, with the reason naming which side stood
    still. Forcing a flat reading into one of the four cells would invent a
    direction from a market that did not move."""

    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    """A price or open-interest observation is missing from one of the quotes.

    Not the same as UNCHANGED: one means nothing happened, the other means
    nobody looked."""


@dataclass(frozen=True, slots=True)
class OpenInterestPolicy:
    """How much movement counts as movement.

    Both tolerances are absolute and default to zero, meaning any change at all
    counts. They are **project heuristics** - a choice about what this project
    treats as noise - and describe no exchange rule. Open interest is reported
    in whole contracts, so exact comparison is meaningful; the tolerances exist
    for callers working with smoothed or estimated series.
    """

    price_tolerance: Decimal = Decimal("0")
    open_interest_tolerance: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.price_tolerance < 0:
            raise ValueError("price_tolerance must not be negative")
        if self.open_interest_tolerance < 0:
            raise ValueError("open_interest_tolerance must not be negative")


@dataclass(frozen=True, slots=True)
class OpenInterestReading:
    """The context, the movements behind it, and why it was reached."""

    context: OpenInterestContext
    price_change: Decimal | None
    open_interest_change: Decimal | None
    reason: str

    @property
    def is_informative(self) -> bool:
        """True only for the four section 33 readings."""
        return self.context not in (
            OpenInterestContext.UNCHANGED,
            OpenInterestContext.INSUFFICIENT_DATA,
        )


def read_open_interest(
    previous: FuturesQuote,
    current: FuturesQuote,
    policy: OpenInterestPolicy | None = None,
) -> OpenInterestReading:
    """Classify the price/open-interest combination between two observations.

    Uses ``futures_price`` - the contract's own price, not the underlying -
    because open interest belongs to the contract and comparing it against
    spot would mix two different markets.

    **Both observations must describe the same instrument**, and this is
    checked rather than assumed. Comparing one contract's open interest against
    another's yields a perfectly plausible reading of a change that never
    happened - a front-month roll would read as mass liquidation followed by
    mass new participation.
    """
    require_same_instrument(previous, current, "open interest reading")
    settings = policy if policy is not None else OpenInterestPolicy()

    if previous.futures_price is None or current.futures_price is None:
        return OpenInterestReading(
            context=OpenInterestContext.INSUFFICIENT_DATA,
            price_change=None,
            open_interest_change=None,
            reason="a futures price is missing from one of the observations",
        )
    if previous.open_interest is None or current.open_interest is None:
        return OpenInterestReading(
            context=OpenInterestContext.INSUFFICIENT_DATA,
            price_change=current.futures_price - previous.futures_price,
            open_interest_change=None,
            reason="an open interest reading is missing from one of the observations",
        )

    price_change = current.futures_price - previous.futures_price
    interest_change = current.open_interest - previous.open_interest

    price_moved = abs(price_change) > settings.price_tolerance
    interest_moved = abs(interest_change) > settings.open_interest_tolerance

    if not price_moved or not interest_moved:
        if not price_moved and not interest_moved:
            detail = "neither price nor open interest moved"
        elif not price_moved:
            detail = "price did not move"
        else:
            detail = "open interest did not move"
        return OpenInterestReading(
            context=OpenInterestContext.UNCHANGED,
            price_change=price_change,
            open_interest_change=interest_change,
            reason=f"{detail} beyond the configured tolerance",
        )

    rising_price = price_change > 0
    rising_interest = interest_change > 0

    if rising_price and rising_interest:
        context = OpenInterestContext.NEW_LONG_PARTICIPATION
        phrase = "possible new long participation"
    elif rising_price and not rising_interest:
        context = OpenInterestContext.SHORT_COVERING
        phrase = "possible short covering"
    elif not rising_price and rising_interest:
        context = OpenInterestContext.NEW_SHORT_PARTICIPATION
        phrase = "possible new short participation"
    else:
        context = OpenInterestContext.LIQUIDATION
        phrase = "possible liquidation"

    return OpenInterestReading(
        context=context,
        price_change=price_change,
        open_interest_change=interest_change,
        reason=(
            f"price {'rose' if rising_price else 'fell'} by {abs(price_change)} while open "
            f"interest {'rose' if rising_interest else 'fell'} by {abs(interest_change)}: "
            f"{phrase}"
        ),
    )


def read_contract_open_interest(
    contract: FuturesContract,
    previous: FuturesQuote,
    current: FuturesQuote,
    policy: OpenInterestPolicy | None = None,
) -> OpenInterestReading:
    """Read open interest for a named contract, checking both observations belong to it.

    The contract-aware entry point. Prefer it wherever a contract is in hand:
    it makes the pairing a checked precondition instead of a convention.
    """
    require_matching_quote(contract, previous, "open interest reading")
    require_matching_quote(contract, current, "open interest reading")
    return read_open_interest(previous, current, policy)
