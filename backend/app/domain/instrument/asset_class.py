"""Asset classes, and which of them this system actually implements (Phase 8.5).

## An enum value is not an implementation

``AssetClass`` names the markets the architecture is being shaped to hold. It
says nothing about whether any of them *work*. That is a separate fact, kept in
one place - ``IMPLEMENTATION`` below - so that "we have a word for crypto" can
never be mistaken for "crypto is supported".

Today exactly one entry is ``IMPLEMENTED``: futures, with VİOP as the reference
market. Every other class is ``NOT_IMPLEMENTED``, and every generic engine that
turns a product into money calls ``require_implemented`` first. A product
claiming an unimplemented class is refused - it does not fall back to futures
arithmetic, because futures arithmetic applied to a share or a perpetual would
produce a confident, wrong number.

No per-class rule lives here. There is no default fee, lot size, tick, leverage
or session for any class: those are mutable market facts (master spec §118),
and for the unimplemented classes nobody has supplied them.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum, unique
from types import MappingProxyType


@unique
class AssetClass(StrEnum):
    """The kind of market an instrument trades in.

    Deliberately coarse. Anything finer - an index future against a single-stock
    future, a spot pair against a margin pair - is a property of the *product*
    and belongs to its ``ProductPolicy``, not to this enumeration.
    """

    FUTURES = "FUTURES"
    """Dated, exchange-traded, margined futures contracts. **Implemented.**"""

    EQUITY = "EQUITY"
    """Shares. Not implemented."""

    CRYPTO_SPOT = "CRYPTO_SPOT"
    """Spot crypto-asset pairs. Not implemented."""

    CRYPTO_PERPETUAL = "CRYPTO_PERPETUAL"
    """Perpetual swaps with funding. Not implemented."""

    FX = "FX"
    """Foreign-exchange pairs. Not implemented."""


@unique
class ImplementationStatus(StrEnum):
    """Whether the system can compute anything for an asset class."""

    IMPLEMENTED = "IMPLEMENTED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


IMPLEMENTATION: Mapping[AssetClass, ImplementationStatus] = MappingProxyType(
    {
        AssetClass.FUTURES: ImplementationStatus.IMPLEMENTED,
        AssetClass.EQUITY: ImplementationStatus.NOT_IMPLEMENTED,
        AssetClass.CRYPTO_SPOT: ImplementationStatus.NOT_IMPLEMENTED,
        AssetClass.CRYPTO_PERPETUAL: ImplementationStatus.NOT_IMPLEMENTED,
        AssetClass.FX: ImplementationStatus.NOT_IMPLEMENTED,
    }
)
"""The single declaration of what works.

Read-only at runtime. Promoting a class to ``IMPLEMENTED`` is a code change that
must arrive with a real product policy, parity tests against a verified source,
and its own phase - never a registration made to get past a refusal.
"""


class UnsupportedAssetClassError(ValueError):
    """Raised when a calculation is asked for on an unimplemented asset class.

    Fails closed, like ``UnsupportedValuationModelError``: refusing is better
    than producing a plausible number for a market the engine does not model.
    """

    def __init__(self, asset_class: AssetClass, operation: str) -> None:
        self.asset_class = asset_class
        self.operation = operation
        super().__init__(
            f"{operation} is not implemented for asset class {asset_class.value}; "
            "only implemented asset classes can be calculated, and nothing falls back "
            "to another class's rules"
        )


def is_implemented(asset_class: AssetClass) -> bool:
    return IMPLEMENTATION.get(asset_class) is ImplementationStatus.IMPLEMENTED


def require_implemented(asset_class: AssetClass, operation: str) -> None:
    """Refuse any asset class the registry does not declare implemented."""
    if not is_implemented(asset_class):
        raise UnsupportedAssetClassError(asset_class, operation)


def implemented_asset_classes() -> tuple[AssetClass, ...]:
    return tuple(item for item in AssetClass if is_implemented(item))
