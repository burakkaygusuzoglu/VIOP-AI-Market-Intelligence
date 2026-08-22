"""Market structure and regime (master spec sections 12, 13, 14, 27, 28).

Deterministic domain logic only. Every engine here depends on the standard
library and on the Phase 1 domain; none of it reaches an adapter, a framework
or an LLM, and none of it recomputes a Phase 1 formula.

The organising discipline of the whole package is the separation of *when a
thing happened* from *when it could first be known*. Swings carry a pivot index
and a confirmation index; structural events, breakouts, false breakouts,
retests and divergences all carry a ``confirmed_index``. Downstream code asks
what was known at a candle, never what turned out to be true later.
"""

from app.domain.structure.breakouts import (
    BreakoutConfig,
    BreakoutEvent,
    BreakoutEventType,
    RetestEvent,
    RetestEventType,
    VolumeConfirmation,
    detect_breakouts,
    detect_retests,
)
from app.domain.structure.divergence import (
    DivergenceConfig,
    DivergenceEvent,
    DivergenceType,
    detect_volume_divergence,
)
from app.domain.structure.engine import (
    StructureConfig,
    StructureSnapshot,
    analyse_structure,
)
from app.domain.structure.events import (
    BreakConfirmation,
    StructuralEvent,
    StructuralEventConfig,
    StructuralEventType,
    detect_structural_events,
)
from app.domain.structure.market_structure import (
    LabelledSwing,
    MarketStructure,
    StructureBias,
    StructureLabel,
    StructureLabelConfig,
    label_swings,
)
from app.domain.structure.regime import (
    EmaStack,
    MarketRegime,
    RegimeAssessment,
    RegimeConfig,
    RegimeEvidence,
    VolatilityState,
    classify_regime,
)
from app.domain.structure.swings import (
    SwingConfig,
    SwingPoint,
    SwingType,
    detect_swings,
    last_swing,
    swings_known_at,
)
from app.domain.structure.zones import (
    Zone,
    ZoneConfig,
    ZoneKind,
    ZoneScoreBreakdown,
    ZoneWeights,
    build_zones,
)

__all__ = [
    "BreakConfirmation",
    "BreakoutConfig",
    "BreakoutEvent",
    "BreakoutEventType",
    "DivergenceConfig",
    "DivergenceEvent",
    "DivergenceType",
    "EmaStack",
    "LabelledSwing",
    "MarketRegime",
    "MarketStructure",
    "RegimeAssessment",
    "RegimeConfig",
    "RegimeEvidence",
    "RetestEvent",
    "RetestEventType",
    "StructuralEvent",
    "StructuralEventConfig",
    "StructuralEventType",
    "StructureBias",
    "StructureConfig",
    "StructureLabel",
    "StructureLabelConfig",
    "StructureSnapshot",
    "SwingConfig",
    "SwingPoint",
    "SwingType",
    "VolatilityState",
    "VolumeConfirmation",
    "Zone",
    "ZoneConfig",
    "ZoneKind",
    "ZoneScoreBreakdown",
    "ZoneWeights",
    "analyse_structure",
    "build_zones",
    "classify_regime",
    "detect_breakouts",
    "detect_retests",
    "detect_structural_events",
    "detect_swings",
    "detect_volume_divergence",
    "label_swings",
    "last_swing",
    "swings_known_at",
]
