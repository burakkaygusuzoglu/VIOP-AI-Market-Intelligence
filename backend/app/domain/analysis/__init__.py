"""Multi-timeframe analysis, evidence and scenarios (master spec §10, §16-§19,
§23, §26).

Deterministic domain logic only: no framework, no adapter, no LLM, and no
formula that Phases 1-3 already own. This package **consumes** the technical,
structural, regime and futures engines and never reimplements them.

Four ideas hold it together.

*Timeframes have roles, and roles are not interchangeable.* 1D reads the
regime, 1H the bias, 15M the setup, 5M the timing. They are never averaged, and
a view whose timeframe contradicts its role is refused rather than reordered.

*Evidence is dated to when it became knowable.* Every item carries the Phase 2
confirmation moment, so a swing that pivoted at candle 100 and confirmed at 102
produces evidence dated 102 - and later candles never rewrite it.

*Disagreement is reported, not netted.* A lower timeframe correcting inside an
agreed trend is a pullback; the two slowest timeframes disagreeing is a major
conflict. Both are named, and neither is dissolved into a score.

*Repetition is not confirmation.* Fusion collapses each category on each
timeframe into one voice, so seven records of one divergence argue once. Every
score reads those groups, and each component is capped at its own weight, so
correlated evidence cannot compound.

**Nothing here is a probability, and nothing here is an action.** Setup and
Entry Quality are labelled heuristic; bull and bear are scored independently
and do not sum to 100. There is no LONG, SHORT or WAIT: that synthesis needs
account risk and belongs to a later phase. The veto that does need risk lives
one layer up, in ``app.domain.suitability``, so an account balance can never
reach a technical score.
"""

from app.domain.analysis.contradictions import (
    Contradiction,
    ContradictionConfig,
    ContradictionReport,
    ContradictionSeverity,
    ContradictionType,
    DirectionalReading,
    Pullback,
    detect_contradictions,
)
from app.domain.analysis.engine import (
    AnalysisConfig,
    MultiTimeframeAnalysis,
    analyse_multi_timeframe,
)
from app.domain.analysis.entry import (
    EntryComponent,
    EntryComponentScore,
    EntryConfig,
    EntryQuality,
    EntryWeights,
    score_entry,
)
from app.domain.analysis.evidence import (
    EvidenceCategory,
    EvidenceDirection,
    EvidenceItem,
    EvidenceReliability,
    EvidenceSource,
    EvidenceStrength,
    directional,
    evidence_known_at,
    opposes,
    strongest,
)
from app.domain.analysis.fusion import (
    EvidenceGroup,
    FusedEvidence,
    fuse_evidence,
    strongest_group,
)
from app.domain.analysis.generation import (
    EvidenceConfig,
    build_contract_evidence,
    build_timeframe_evidence,
)
from app.domain.analysis.quality import (
    QUALITY_LABEL,
    SCORING_METHOD_VERSION,
    ComponentAvailability,
    ComponentScore,
    QualityComponent,
    QualityConfig,
    QualityWeights,
    SetupQuality,
    score_setup,
)
from app.domain.analysis.scenarios import (
    Invalidation,
    InvalidationCode,
    Requirement,
    RequirementCode,
    RequirementStatus,
    Scenario,
    ScenarioCase,
    ScenarioConfig,
    ScenarioSet,
    ScenarioState,
    build_scenarios,
)
from app.domain.analysis.timeframes import (
    ROLES_BROADEST_FIRST,
    MultiTimeframeError,
    MultiTimeframeIssue,
    MultiTimeframeView,
    TimeframeRole,
    TimeframeRolePolicy,
    TimeframeView,
)

__all__ = [
    "QUALITY_LABEL",
    "ROLES_BROADEST_FIRST",
    "SCORING_METHOD_VERSION",
    "AnalysisConfig",
    "ComponentAvailability",
    "ComponentScore",
    "Contradiction",
    "ContradictionConfig",
    "ContradictionReport",
    "ContradictionSeverity",
    "ContradictionType",
    "DirectionalReading",
    "EntryComponent",
    "EntryComponentScore",
    "EntryConfig",
    "EntryQuality",
    "EntryWeights",
    "EvidenceCategory",
    "EvidenceConfig",
    "EvidenceDirection",
    "EvidenceGroup",
    "EvidenceItem",
    "EvidenceReliability",
    "EvidenceSource",
    "EvidenceStrength",
    "FusedEvidence",
    "Invalidation",
    "InvalidationCode",
    "MultiTimeframeAnalysis",
    "MultiTimeframeError",
    "MultiTimeframeIssue",
    "MultiTimeframeView",
    "Pullback",
    "QualityComponent",
    "QualityConfig",
    "QualityWeights",
    "Requirement",
    "RequirementCode",
    "RequirementStatus",
    "Scenario",
    "ScenarioCase",
    "ScenarioConfig",
    "ScenarioSet",
    "ScenarioState",
    "SetupQuality",
    "TimeframeRole",
    "TimeframeRolePolicy",
    "TimeframeView",
    "analyse_multi_timeframe",
    "build_contract_evidence",
    "build_scenarios",
    "build_timeframe_evidence",
    "detect_contradictions",
    "directional",
    "evidence_known_at",
    "fuse_evidence",
    "opposes",
    "score_entry",
    "score_setup",
    "strongest",
    "strongest_group",
]
