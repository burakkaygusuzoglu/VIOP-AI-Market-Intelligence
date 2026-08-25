"""Screenshot observation model (master spec §38, §39, §65).

Phase 6A. Deterministic domain logic only - standard library, no framework, no
SDK, no network. This package models what a screenshot *is* and what a vision
pass *observed*; it does not call a model, and Phase 6A ships no adapter.

Four rules hold it together.

*Vision observes; it never calculates.* Nothing here computes an indicator, a
price, a size or a margin. An import contract stops the calculation engines
from depending on this package at all, so a visually-read number cannot become
the input to a financial formula.

*A reading is not a judgement.* `ObservationKind` keeps "the chart prints RSI
63.2" apart from "the trend looks bullish", and ranks them differently.

*The stronger source wins, and the weaker one survives.* Precedence reuses the
`DataSourcePriority` Phase 0 already defined; the losing claim is kept as a
`ValueConflict` rather than dropped.

*Unknown is a third answer.* A quality dimension nobody examined is
`NOT_EVALUATED` - excluded from the score, never quietly credited or charged -
and a confidence the model omitted stays `None`.
"""

from app.domain.vision.assets import (
    MAX_DISPLAY_FILENAME,
    ScreenshotAsset,
    ScreenshotId,
    ScreenshotSet,
    content_digest,
    sanitise_filename,
)
from app.domain.vision.corrections import (
    CorrectionLog,
    CorrectionType,
    FieldCorrection,
)
from app.domain.vision.extraction import (
    ConfidenceError,
    ExtractedValue,
    ObservationKind,
    ObservedField,
    UnreadableField,
    VisionConfidence,
    VisionExtraction,
)
from app.domain.vision.precedence import (
    ResolvedValue,
    SourcedValue,
    ValueConflict,
    ValueKind,
    conflicts_of,
    resolve,
    resolve_all,
)
from app.domain.vision.quality import (
    QUALITY_LABEL,
    QUALITY_METHOD_VERSION,
    DimensionResult,
    DimensionState,
    QualityDimension,
    QualityPolicy,
    QualityWeights,
    ScreenshotQuality,
    UnevaluatedPolicy,
    score_quality,
)
from app.domain.vision.slots import (
    SLOTS_BROADEST_FIRST,
    ScreenshotSlot,
    SlotAssignment,
    TimeframeAgreement,
)

__all__ = [
    "MAX_DISPLAY_FILENAME",
    "QUALITY_LABEL",
    "QUALITY_METHOD_VERSION",
    "SLOTS_BROADEST_FIRST",
    "ConfidenceError",
    "CorrectionLog",
    "CorrectionType",
    "DimensionResult",
    "DimensionState",
    "ExtractedValue",
    "FieldCorrection",
    "ObservationKind",
    "ObservedField",
    "QualityDimension",
    "QualityPolicy",
    "QualityWeights",
    "ResolvedValue",
    "ScreenshotAsset",
    "ScreenshotId",
    "ScreenshotQuality",
    "ScreenshotSet",
    "ScreenshotSlot",
    "SlotAssignment",
    "SourcedValue",
    "TimeframeAgreement",
    "UnevaluatedPolicy",
    "UnreadableField",
    "ValueConflict",
    "ValueKind",
    "VisionConfidence",
    "VisionExtraction",
    "conflicts_of",
    "content_digest",
    "resolve",
    "resolve_all",
    "sanitise_filename",
    "score_quality",
]
