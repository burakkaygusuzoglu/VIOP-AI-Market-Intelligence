"""Turkish-first presentation of a finished analysis (master spec §3-§8).

Phase 5A. This package turns deterministic analysis results into the two
layers §8 requires, in the language §4 requires, for the two audiences §3
requires. It is the **only** place in the backend where Turkish user-facing
text lives.

Why it sits in the application layer: presentation is neither domain logic nor
infrastructure. The financial engines must stay language-free so the same
calculation can be shown in any language without touching a formula, and the
API layer must stay a thin serialisation boundary. Reading finished domain
results and shaping them for a surface is exactly what the application layer
is for, and `api → application → domain` already runs the right way.

Four rules hold the package together.

*Every sentence is traceable.* A `Statement` carries the evidence items it was
built from and refuses to exist without them - only a data gap may be stated
with no source, because there the absence is the fact.

*The two layers cannot disagree.* Both are built from one
`MultiTimeframeAnalysis` in a single call, and the simple layer's citations are
a subset of what the technical layer reports.

*Simplifying is not softening.* Missing data stays missing, contradictions stay
visible, and no score is renamed a probability.

*Nothing here decides anything.* No al, sat or bekle; §25's decision belongs to
a later phase. There is no LLM in this package and no path to one - evidence is
deterministic under §16, and presentation of it is too.
"""

from app.application.presentation.checklist import (
    DEFAULT_CRITICAL_ITEMS,
    ChecklistAssessment,
    ChecklistItem,
    ChecklistPolicy,
    ChecklistVerdict,
    CheckResult,
    CheckStatus,
    assess_checklist,
)
from app.application.presentation.education import (
    Concept,
    ConceptAvailability,
    ConceptExplanation,
    all_concepts,
    explain,
    measured_concepts,
    unmeasured_concepts,
)
from app.application.presentation.modes import (
    DEFAULT_MODE,
    ExperienceMode,
    ModePolicy,
    policy_for,
)
from app.application.presentation.presenter import AnalysisPresentation, present_analysis
from app.application.presentation.pro import (
    RowAvailability,
    TechnicalDetail,
    TechnicalRow,
    TimeframeDetail,
    build_technical_detail,
)
from app.application.presentation.review import TradeReview, review_trade
from app.application.presentation.risk_warnings import (
    BeginnerRiskWarning,
    WarningAudience,
    beginner_risk_warnings,
    warning_texts,
)
from app.application.presentation.safety import (
    FORBIDDEN_CLAIMS,
    ForbiddenClaim,
    is_safe,
    violations,
)
from app.application.presentation.statements import (
    SimpleExplanation,
    Statement,
    StatementTone,
    StatementTopic,
)
from app.application.presentation.terms import (
    DEFAULT_LOCALE,
    Locale,
    Term,
    TermKey,
    all_terms,
    label,
    term,
)
from app.application.presentation.why import (
    Explanation,
    Reason,
    ReasonSeverity,
    ReasonSource,
    WhyTopic,
    why_contradiction,
    why_direction,
    why_entry_quality,
    why_no_trade_block,
    why_pending_confirmation,
    why_position_size,
    why_risk_findings,
    why_scenario_state,
    why_score_changed,
    why_setup_quality,
    why_zone,
)

__all__ = [
    "DEFAULT_CRITICAL_ITEMS",
    "DEFAULT_LOCALE",
    "DEFAULT_MODE",
    "FORBIDDEN_CLAIMS",
    "AnalysisPresentation",
    "BeginnerRiskWarning",
    "CheckResult",
    "CheckStatus",
    "ChecklistAssessment",
    "ChecklistItem",
    "ChecklistPolicy",
    "ChecklistVerdict",
    "Concept",
    "ConceptAvailability",
    "ConceptExplanation",
    "ExperienceMode",
    "Explanation",
    "ForbiddenClaim",
    "Locale",
    "ModePolicy",
    "Reason",
    "ReasonSeverity",
    "ReasonSource",
    "RowAvailability",
    "SimpleExplanation",
    "Statement",
    "StatementTone",
    "StatementTopic",
    "TechnicalDetail",
    "TechnicalRow",
    "Term",
    "TermKey",
    "TimeframeDetail",
    "TradeReview",
    "WarningAudience",
    "WhyTopic",
    "all_concepts",
    "all_terms",
    "assess_checklist",
    "beginner_risk_warnings",
    "build_technical_detail",
    "explain",
    "is_safe",
    "label",
    "measured_concepts",
    "policy_for",
    "present_analysis",
    "review_trade",
    "term",
    "unmeasured_concepts",
    "violations",
    "warning_texts",
    "why_contradiction",
    "why_direction",
    "why_entry_quality",
    "why_no_trade_block",
    "why_pending_confirmation",
    "why_position_size",
    "why_risk_findings",
    "why_scenario_state",
    "why_score_changed",
    "why_setup_quality",
    "why_zone",
]
