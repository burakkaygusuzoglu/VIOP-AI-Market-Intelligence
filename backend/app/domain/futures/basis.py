"""Basis: the gap between the futures price and its underlying (section 32).

``basis = futures - spot``. A positive basis is a premium, a negative one a
discount. It is **contextual evidence about carry and positioning, never a
direction to trade** - master spec section 32 says so explicitly, and nothing
here returns LONG or SHORT.

**Annualised basis is deliberately not implemented.** Section 32 lists it as
optional, and computing it needs two things this project does not have: a
verified last-trading timestamp (so the time to expiry is a real number rather
than a guess about which hour the contract dies) and a day-count convention
for the exchange's calendar. Both are section 118 facts. The usual shortcuts -
365, 360, 252, "assume the close" - each produce a different, confident,
unfalsifiable percentage. Deferring is the only honest option until the
calendar is verified; see the Phase 3 report.

**Naming.** ``basis_ratio`` is a fraction (``0.012``), ``basis_percent`` is
percentage points (``1.2``). Every value in this codebase says which it is in
its own name, because the two are the most reliable source of factor-of-100
errors in financial code.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.arithmetic import as_percent, safe_ratio
from app.domain.futures.contract import (
    FuturesContract,
    FuturesQuote,
    require_matching_quote,
)


@unique
class BasisContext(StrEnum):
    """How the futures price sits relative to spot."""

    PREMIUM = "PREMIUM"
    """Futures above spot."""

    DISCOUNT = "DISCOUNT"
    """Futures below spot."""

    FLAT = "FLAT"
    """Within the configured tolerance of spot."""

    UNAVAILABLE = "UNAVAILABLE"
    """One of the two prices is missing, or spot is zero. Reported rather than
    defaulted to FLAT, which would claim a measurement nobody took."""


@dataclass(frozen=True, slots=True)
class BasisPolicy:
    """When a basis counts as flat.

    ``flat_ratio_tolerance`` is a **project heuristic**, not an exchange rule.
    It describes how small a gap this project chooses to call "no meaningful
    premium", and different settings give a different - equally deterministic -
    reading. The default of zero means only an exact match is flat, which is
    the honest default: any non-zero choice would be a guess about one
    instrument's normal carry.
    """

    flat_ratio_tolerance: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.flat_ratio_tolerance < 0:
            raise ValueError(
                f"flat_ratio_tolerance must not be negative, got {self.flat_ratio_tolerance}"
            )


@dataclass(frozen=True, slots=True)
class BasisResult:
    """The basis, its size relative to spot, and the context it implies."""

    context: BasisContext
    basis: Decimal | None
    basis_ratio: Decimal | None
    """``basis / spot`` as a fraction."""

    futures_price: Decimal | None
    spot_price: Decimal | None
    reason: str

    @property
    def basis_percent(self) -> Decimal | None:
        """``basis_ratio`` in percentage points."""
        return as_percent(self.basis_ratio)

    @property
    def is_available(self) -> bool:
        return self.context is not BasisContext.UNAVAILABLE


def calculate_basis(
    futures_price: Decimal | None,
    spot_price: Decimal | None,
    policy: BasisPolicy | None = None,
) -> BasisResult:
    """Compute the basis and classify it, or report that it is unavailable.

    A missing price gives ``UNAVAILABLE``, never zero. A spot of zero gives
    ``UNAVAILABLE`` too: the ratio is undefined, and reporting an infinite or
    zero premium would both be fabrications.
    """
    settings = policy if policy is not None else BasisPolicy()

    if futures_price is None or spot_price is None:
        missing = "futures price" if futures_price is None else "spot price"
        return BasisResult(
            context=BasisContext.UNAVAILABLE,
            basis=None,
            basis_ratio=None,
            futures_price=futures_price,
            spot_price=spot_price,
            reason=f"no {missing} available",
        )

    basis = futures_price - spot_price
    ratio = safe_ratio(basis, spot_price)

    if ratio is None:
        return BasisResult(
            context=BasisContext.UNAVAILABLE,
            basis=basis,
            basis_ratio=None,
            futures_price=futures_price,
            spot_price=spot_price,
            reason="spot price is zero, so the basis cannot be expressed relative to it",
        )

    if abs(ratio) <= settings.flat_ratio_tolerance:
        context = BasisContext.FLAT
        reason = f"basis {basis} is within the {settings.flat_ratio_tolerance} flat tolerance"
    elif basis > 0:
        context = BasisContext.PREMIUM
        reason = f"futures {futures_price} trades {basis} above spot {spot_price}"
    else:
        context = BasisContext.DISCOUNT
        reason = f"futures {futures_price} trades {abs(basis)} below spot {spot_price}"

    return BasisResult(
        context=context,
        basis=basis,
        basis_ratio=ratio,
        futures_price=futures_price,
        spot_price=spot_price,
        reason=reason,
    )


def calculate_contract_basis(
    contract: FuturesContract,
    quote: FuturesQuote,
    policy: BasisPolicy | None = None,
) -> BasisResult:
    """Basis for a named contract, checking the quote belongs to it.

    The contract-aware entry point. Pairing contract A's metadata with contract
    B's prices produces a confident, entirely wrong premium, so the pairing is
    a checked precondition here rather than a convention.
    """
    require_matching_quote(contract, quote, "basis calculation")
    return calculate_basis(quote.futures_price, quote.spot_price, policy)
