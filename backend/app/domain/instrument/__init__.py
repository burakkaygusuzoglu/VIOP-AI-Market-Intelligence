"""Asset-agnostic instrument identity and the product-policy boundary (Phase 8.5).

The generic core - analysis, risk reasoning, synthesis - is written once. What
differs between markets arrives through a ``ProductPolicy``; which markets work
at all is declared once, in ``IMPLEMENTATION``. Futures, with VİOP as the
reference market, is the only implemented asset class.

Depends on ``app.domain.common`` and nothing else.
"""

from app.domain.instrument.asset_class import (
    IMPLEMENTATION,
    AssetClass,
    ImplementationStatus,
    UnsupportedAssetClassError,
    implemented_asset_classes,
    is_implemented,
    require_implemented,
)
from app.domain.instrument.identity import InstrumentId, InstrumentIdentityError
from app.domain.instrument.policy import (
    MarginFeasibility,
    MarginRequirement,
    PriceIncrementCheck,
    ProductCapabilities,
    ProductPolicy,
    ProductVocabulary,
    Support,
    TickFeasibility,
    UnsupportedQuantitySemanticsError,
    require_product_calculable,
)

__all__ = [
    "IMPLEMENTATION",
    "AssetClass",
    "ImplementationStatus",
    "InstrumentId",
    "InstrumentIdentityError",
    "MarginFeasibility",
    "MarginRequirement",
    "PriceIncrementCheck",
    "ProductCapabilities",
    "ProductPolicy",
    "ProductVocabulary",
    "Support",
    "TickFeasibility",
    "UnsupportedAssetClassError",
    "UnsupportedQuantitySemanticsError",
    "implemented_asset_classes",
    "is_implemented",
    "require_implemented",
    "require_product_calculable",
]
