"""The futures product policy - the one implemented ``ProductPolicy`` (Phase 8.5).

A thin adapter from ``FuturesContract`` to the generic boundary. It performs no
new arithmetic and holds no market value: every answer is read from the
contract's own verified metadata or delegated to the Phase 3 functions that
already computed it. The tick-grid check below is the Phase 3 code moved here
unchanged from ``risk.sizing``, because executability on *this* grid is a
futures fact the generic sizer should not know how to decide.

``test_futures_parity.py`` holds this module to the Phase 8 behaviour exactly,
against a baseline recorded before it existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.common.verification import VerificationStatus, VerifiedValue
from app.domain.futures.contract import FuturesContract
from app.domain.futures.validation import check_tick_grid, require_calculable
from app.domain.instrument.asset_class import AssetClass
from app.domain.instrument.identity import InstrumentId
from app.domain.instrument.policy import (
    MarginFeasibility,
    MarginRequirement,
    PriceIncrementCheck,
    ProductCapabilities,
    ProductVocabulary,
    Support,
    TickFeasibility,
)

FUTURES_CAPABILITIES = ProductCapabilities(
    margin=Support.SUPPORTED,
    expiry=Support.SUPPORTED,
    short_selling=Support.SUPPORTED,
    fractional_quantity=Support.UNSUPPORTED,
    funding=Support.UNSUPPORTED,
    open_interest=Support.SUPPORTED,
)
"""Structural properties of a dated, margined futures contract.

Each follows from the product type, not from any exchange: a dated future has an
expiry and open interest, is margined, can be sold as readily as bought, is
held in whole contracts (the sizing engine has always floored to them), and has
no periodic funding payment - that is what distinguishes it from a perpetual.
No venue rule, number or session is implied by any of them.
"""

FUTURES_VOCABULARY = ProductVocabulary(
    unit="contract",
    unit_counted="contract(s)",
    point_value="multiplier",
    point_value_qualified="contract multiplier",
)


@dataclass(frozen=True, slots=True)
class FuturesProductPolicy:
    """``ProductPolicy`` for one ``FuturesContract``."""

    contract: FuturesContract

    @property
    def instrument(self) -> InstrumentId:
        return InstrumentId(
            symbol=self.contract.symbol,
            asset_class=_classification(self.contract),
            quote_currency=None,
        )

    @property
    def capabilities(self) -> ProductCapabilities:
        return FUTURES_CAPABILITIES

    @property
    def vocabulary(self) -> ProductVocabulary:
        return FUTURES_VOCABULARY

    def require_calculable(self, operation: str) -> None:
        """Linear valuation first, then metadata consistency - the Phase 3 order."""
        self.contract.requires_linear_valuation(operation)
        require_calculable(self.contract, operation)

    def point_value(self) -> VerifiedValue[Decimal]:
        """The contract multiplier, with its provenance intact."""
        return self.contract.multiplier

    def price_increment_check(
        self, entry_price: Decimal, stop_price: Decimal
    ) -> PriceIncrementCheck:
        """Whether the proposed levels are actually executable on this contract.

        A stop that cannot be placed where the risk calculation assumed it is not
        a stop. When the tick size is verified, an off-grid entry or stop makes
        the whole sizing answer fictional, so it is refused - and the supplied
        prices are returned untouched, never snapped. When the tick size is
        missing or unverified the arithmetic is still sound but execution
        feasibility is unknown, which is reported rather than assumed away.
        """
        contract = self.contract
        tick = contract.authoritative_tick_size()
        if tick is None:
            if contract.tick_size.is_authoritative:
                return PriceIncrementCheck(TickFeasibility.MISSING, "no tick size is available")
            return PriceIncrementCheck(
                TickFeasibility.UNVERIFIED,
                f"the tick size is {contract.tick_size.status.value}, so it cannot be "
                "confirmed that these levels are executable",
            )

        offenders = [
            (name, price)
            for name, price in (("entry", entry_price), ("stop", stop_price))
            if not check_tick_grid(price, tick).on_grid
        ]
        if offenders:
            detail = ", ".join(f"{name} {price}" for name, price in offenders)
            return PriceIncrementCheck(
                TickFeasibility.OFF_GRID,
                f"{detail} is not a whole number of {tick} ticks",
            )
        return PriceIncrementCheck(
            TickFeasibility.ON_GRID, f"entry and stop sit on the {tick} tick grid"
        )

    def margin_requirement(self) -> MarginRequirement:
        """Initial margin per contract: MISSING, UNVERIFIED, or KNOWN with a value."""
        if self.contract.initial_margin is None:
            return MarginRequirement(MarginFeasibility.MISSING, None)
        margin = self.contract.authoritative_initial_margin()
        if margin is None:
            return MarginRequirement(MarginFeasibility.UNVERIFIED, None)
        return MarginRequirement(MarginFeasibility.KNOWN, margin)


def _classification(contract: FuturesContract) -> VerifiedValue[AssetClass]:
    """How well "this instrument is a future" is known - and nothing else.

    **Asset-class provenance is not product calculability.** A Phase 8.5 draft
    derived this status from the multiplier and tick size, so a verified
    multiplier "verified" the product class and an unverified tick size
    "unverified" it. Those are different facts from different sources, and the
    coupling was removed after review.

    The classification now follows only the source that establishes the class
    itself - ``FuturesContract.classification``. When no such source exists, the
    class is reported ``UNVERIFIED``: that the record *is* a futures record is a
    fact about how this server models it, not a verified market fact, and no
    quantity of verified numbers turns it into one. Conversely a trusted
    classification is never downgraded because a multiplier, tick size or
    margin is missing; those only make calculations unavailable.
    """
    if contract.classification is not None:
        return contract.classification
    return VerifiedValue(
        value=AssetClass.FUTURES,
        status=VerificationStatus.UNVERIFIED,
        source="futures contract record",
        note=(
            "the record is modelled as a futures contract, but no source classified "
            "it; numeric specification facts do not establish a product class"
        ),
    )
