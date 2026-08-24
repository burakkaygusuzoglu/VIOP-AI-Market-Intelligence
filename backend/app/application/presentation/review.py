"""The pre-trade review: checklist, warnings and explanations together.

Phase 5A's `present_analysis` describes a *market*. This describes a *proposed
trade against that market* - which needs the Phase 1-3 results the analysis
itself never sees, so it is a separate entry point rather than more parameters
on the old one.

Assembling them in one call is what keeps them consistent: the checklist, the
§109 warnings and the Why explanations all read the same supplied results, so
the checklist cannot report a risk figure the warning contradicts.

**It reviews; it does not trade.** No position is created, no order is
described, and no LONG / SHORT / WAIT is chosen. §49 asks what to verify before
opening a paper trade, and Phase 7 owns the decision that follows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.application.presentation.checklist import (
    ChecklistAssessment,
    ChecklistPolicy,
    assess_checklist,
)
from app.application.presentation.modes import DEFAULT_MODE, ExperienceMode, policy_for
from app.application.presentation.risk_warnings import (
    BeginnerRiskWarning,
    WarningAudience,
    beginner_risk_warnings,
)
from app.application.presentation.why import (
    Explanation,
    WhyTopic,
    why_direction,
    why_entry_quality,
    why_no_trade_block,
    why_pending_confirmation,
    why_position_size,
    why_risk_findings,
    why_scenario_state,
    why_setup_quality,
)
from app.domain.analysis.engine import MultiTimeframeAnalysis
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.analysis.scenarios import ScenarioCase
from app.domain.market.quality import DataQualityReport
from app.domain.risk.margin import MarginAssessment
from app.domain.risk.reward import RiskReward
from app.domain.risk.sizing import PositionSizing
from app.domain.suitability.no_trade import NoTradeAssessment


@dataclass(frozen=True, slots=True)
class TradeReview:
    """Everything §49, §92 and §109 can say about a proposed trade."""

    case: ScenarioCase
    mode: ExperienceMode
    checklist: ChecklistAssessment
    warnings: tuple[BeginnerRiskWarning, ...]
    explanations: tuple[Explanation, ...] = field(default_factory=tuple)

    def explanation(self, topic: WhyTopic) -> Explanation | None:
        for item in self.explanations:
            if item.topic is topic:
                return item
        return None

    @property
    def available_explanations(self) -> tuple[Explanation, ...]:
        return tuple(item for item in self.explanations if item.available)

    def warning_texts(self, audience: WarningAudience | None = None) -> tuple[str, ...]:
        """The warnings phrased for one audience.

        Defaults to the review's own mode. Both audiences always receive the
        same *set*; only the wording differs (§109).
        """
        chosen = audience or (
            WarningAudience.BEGINNER
            if self.mode is ExperienceMode.BEGINNER
            else WarningAudience.PRO
        )
        return tuple(item.text_for(chosen) for item in self.warnings)

    @property
    def shows_warnings(self) -> bool:
        """True in every mode. §109 allows no exception, and a test pins it."""
        return policy_for(self.mode).shows_risk_warnings


def review_trade(
    analysis: MultiTimeframeAnalysis,
    case: ScenarioCase,
    *,
    mode: ExperienceMode = DEFAULT_MODE,
    sizing: PositionSizing | None = None,
    risk_reward: RiskReward | None = None,
    margin: MarginAssessment | None = None,
    data_quality: DataQualityReport | None = None,
    no_trade: NoTradeAssessment | None = None,
    stop_defined: bool | None = None,
    policy: ChecklistPolicy | None = None,
) -> TradeReview:
    """Assess a proposed trade and explain every conclusion reached.

    Every optional argument is a finished Phase 1-3 or Phase 4 result.
    Supplying one enables the checks and explanations that depend on it;
    omitting one leaves them unevaluated rather than satisfied.
    """
    settings = policy if policy is not None else ChecklistPolicy()
    scenario = analysis.scenarios.case(case)
    direction = (
        EvidenceDirection.BULLISH if case is ScenarioCase.BULL else EvidenceDirection.BEARISH
    )

    explanations: list[Explanation] = [
        why_direction(analysis, direction),
        why_scenario_state(scenario),
        why_pending_confirmation(scenario),
    ]
    if scenario.quality is not None:
        explanations.append(why_setup_quality(scenario.quality))
    if scenario.entry is not None:
        explanations.append(why_entry_quality(scenario.entry))
    if sizing is not None:
        explanations.append(why_position_size(sizing))
    if margin is not None:
        explanations.append(why_risk_findings(margin))
    if no_trade is not None:
        explanations.append(why_no_trade_block(no_trade))

    return TradeReview(
        case=case,
        mode=mode,
        checklist=assess_checklist(
            analysis,
            case,
            sizing=sizing,
            risk_reward=risk_reward,
            data_quality=data_quality,
            stop_defined=stop_defined,
            policy=settings,
        ),
        warnings=beginner_risk_warnings(
            margin=margin,
            sizing=sizing,
            risk_reward=risk_reward,
            minimum_risk_reward=_minimum_reward(settings),
            stop_defined=stop_defined,
        ),
        explanations=tuple(explanations),
    )


def _minimum_reward(policy: ChecklistPolicy) -> Decimal:
    """One configured floor, shared by the checklist and the warnings.

    Passed through rather than re-declared so the two can never disagree about
    what "poor risk/reward" means.
    """
    return policy.minimum_risk_reward
