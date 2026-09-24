"""Shadow Mode domain (Phase 14): observation identity, vocabulary and bounds.

Master spec section 78. The system watches market data and records what the
existing deterministic policies would have decided; it opens no paper or real
position automatically, and nothing here can.
"""

from app.domain.shadow.decision import (
    EntrySketch,
    FinancialState,
    JournalEntryKind,
    OperationalKind,
    ShadowDecision,
    ShadowEvidence,
    ShadowOutcome,
    TimeframeEvidence,
    TimeframeReadings,
)
from app.domain.shadow.eligibility import Eligibility, eligible_for_evaluation
from app.domain.shadow.identity import (
    configuration_fingerprint,
    decision_key,
    inputs_fingerprint,
    outcome_key,
)
from app.domain.shadow.outcome import (
    OUTCOME_RULES,
    LevelEvent,
    OutcomeState,
    PriceDevelopment,
    ShadowOutcomeRecord,
    coverage_end_of,
    eligible_forward_candles,
    observe_development,
)
from app.domain.shadow.run import EndReason, ShadowError, ShadowLimits, ShadowRunStatus

__all__ = [
    "OUTCOME_RULES",
    "Eligibility",
    "EndReason",
    "EntrySketch",
    "FinancialState",
    "LevelEvent",
    "OutcomeState",
    "PriceDevelopment",
    "JournalEntryKind",
    "OperationalKind",
    "ShadowDecision",
    "ShadowError",
    "ShadowEvidence",
    "ShadowLimits",
    "ShadowOutcome",
    "ShadowOutcomeRecord",
    "ShadowRunStatus",
    "TimeframeEvidence",
    "TimeframeReadings",
    "configuration_fingerprint",
    "coverage_end_of",
    "decision_key",
    "eligible_for_evaluation",
    "eligible_forward_candles",
    "inputs_fingerprint",
    "observe_development",
    "outcome_key",
]
