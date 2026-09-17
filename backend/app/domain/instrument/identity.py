"""What an instrument *is*, with the provenance of each claim (Phase 8.5).

## Three fields, and why only three

``symbol``, ``asset_class`` and ``quote_currency``. Each has a consumer now or
in the immediately following phase:

* the risk engines name the instrument in their refusals, and match
  observations to metadata by symbol;
* every generic money engine checks the asset class against the
  implementation registry before calculating;
* a paper position (Phase 9) has to say what currency its P&L is in, and that
  currency is a fact about the instrument, not about the account or the locale.

Venue, ISIN, lot size, sessions and the rest were considered and left out. No
current code reads them, and a field nobody reads is a field nobody verifies.

## Provenance is not optional

``asset_class`` and ``quote_currency`` are ``VerifiedValue``s. A user typing
``BTCUSDT`` has asserted a symbol; they have not established an asset class, an
exchange, a quote currency or any trading rule. ``InstrumentId.user_declared``
exists precisely so that a user-supplied classification is representable - and
is *always* ``UNVERIFIED``. There is no constructor path by which text a user
typed becomes a verified fact.

``quote_currency`` defaults to ``None`` rather than to any code. A currency is
attached only when a source establishes one; it is never inferred from the
symbol, the asset class, the locale or the fact that the first market modelled
here was Turkish.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.common.identity import canonical_symbol
from app.domain.common.verification import VerificationStatus, VerifiedValue
from app.domain.instrument.asset_class import AssetClass


class InstrumentIdentityError(ValueError):
    """An instrument identity that cannot be true."""


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """The identity of one tradable instrument."""

    symbol: str
    """Exactly as the source supplied it. Compared through
    ``common.identity.canonical_symbol``, which strips whitespace and assumes
    no exchange naming convention."""

    asset_class: VerifiedValue[AssetClass]
    """What kind of market this is, and how that is known. **Required** - there
    is no default class, so nothing can quietly become a future by omission."""

    quote_currency: VerifiedValue[str] | None = None
    """The currency prices and P&L are denominated in, when a source said so.
    ``None`` means nobody did; it is never filled in."""

    def __post_init__(self) -> None:
        if not canonical_symbol(self.symbol):
            raise InstrumentIdentityError("symbol must not be empty")
        if not isinstance(self.asset_class.value, AssetClass):
            raise InstrumentIdentityError(
                f"asset_class must be an AssetClass, got {self.asset_class.value!r}"
            )
        if self.quote_currency is not None and not self.quote_currency.value.strip():
            raise InstrumentIdentityError("quote_currency, when present, must not be blank")

    @property
    def canonical_symbol(self) -> str:
        return canonical_symbol(self.symbol)

    @property
    def classification_is_verified(self) -> bool:
        return self.asset_class.is_authoritative

    @classmethod
    def user_declared(cls, symbol: str, asset_class: AssetClass) -> InstrumentId:
        """An identity somebody *typed*.

        Always ``UNVERIFIED``, with no quote currency. A user saying an
        instrument is a crypto spot pair is information worth carrying and
        worth nothing as a basis for a calculation.
        """
        return cls(
            symbol=symbol,
            asset_class=VerifiedValue(
                value=asset_class,
                status=VerificationStatus.UNVERIFIED,
                source="user input",
                note="declared by the user; not established by any market source",
            ),
            quote_currency=None,
        )
