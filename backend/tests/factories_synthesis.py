"""Fixtures for Phase 7A synthesis tests.

Everything here is TEST_FIXTURE data. No value is an exchange fact, and nothing
is a real instrument.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.application.synthesis.context import NumericFact, SynthesisContext
from app.application.synthesis.draft import (
    DevilsAdvocate,
    ScenarioNarrative,
    SynthesisDraft,
)
from app.domain.analysis.contradictions import (
    Contradiction,
    ContradictionSeverity,
    ContradictionType,
)
from app.domain.analysis.evidence import (
    EvidenceCategory,
    EvidenceDirection,
    EvidenceItem,
    EvidenceSource,
    EvidenceStrength,
)
from app.domain.analysis.quality import (
    ComponentAvailability,
    ComponentScore,
    QualityComponent,
    SetupQuality,
)
from app.domain.analysis.timeframes import TimeframeRole
from app.domain.common.enums import Timeframe
from app.domain.market.quality import DataQualityReport, DataQualityVerdict
from app.domain.risk.sizing import (
    MarginFeasibility,
    PositionSizing,
    SizingOutcome,
    TickFeasibility,
)
from app.domain.suitability.no_trade import (
    FindingSeverity,
    NoTradeAssessment,
    NoTradeFinding,
    NoTradeReason,
)
from app.domain.synthesis.actions import derive_action_envelope
from app.domain.synthesis.references import (
    AuthorityClass,
    ContextRef,
    ReferenceKind,
    make_ref_id,
)

FIXTURE_SYMBOL = "TEST_FIXTURE_FUT"
FIXTURE_NOW = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)


def evidence(
    *,
    direction: EvidenceDirection = EvidenceDirection.BULLISH,
    category: EvidenceCategory = EvidenceCategory.TREND,
    source: EvidenceSource = EvidenceSource.EMA_ALIGNMENT,
    strength: EvidenceStrength = EvidenceStrength.MODERATE,
    reason: str = "fixture evidence",
    timeframe: Timeframe | None = Timeframe.H1,
    role: TimeframeRole | None = TimeframeRole.BIAS,
    confirmed_index: int | None = 12,
    confirmed_time: datetime | None = FIXTURE_NOW,
    point_in_time: bool = False,
) -> EvidenceItem:
    return EvidenceItem(
        source=source,
        category=category,
        direction=direction,
        strength=strength,
        reason=reason,
        timeframe=timeframe,
        role=role,
        confirmed_index=confirmed_index,
        confirmed_time=confirmed_time,
        point_in_time=point_in_time,
    )


def forming_evidence(**kwargs: object) -> EvidenceItem:
    """Evidence that has not been settled by a closed candle."""
    defaults: dict[str, object] = {
        "confirmed_index": None,
        "confirmed_time": None,
        "point_in_time": False,
    }
    defaults.update(kwargs)
    return evidence(**defaults)  # type: ignore[arg-type]


def contradiction(
    *,
    severity: ContradictionSeverity = ContradictionSeverity.MAJOR,
    reason: str = "fixture contradiction",
) -> Contradiction:
    return Contradiction(
        contradiction_type=ContradictionType.HIGHER_TIMEFRAME_CONFLICT,
        severity=severity,
        category=EvidenceCategory.STRUCTURE,
        roles=(TimeframeRole.BIAS,),
        timeframes=(Timeframe.H1,),
        evidence=(),
        reason=reason,
        confirmed_at=FIXTURE_NOW,
    )


def assessment(
    *,
    findings: tuple[NoTradeFinding, ...] = (),
    no_trade: bool | None = False,
    missing: tuple[str, ...] = (),
) -> NoTradeAssessment:
    return NoTradeAssessment(
        no_trade=no_trade,
        findings=findings,
        missing_requirements=missing,
    )


def finding(
    reason: NoTradeReason,
    severity: FindingSeverity,
    detail: str = "fixture finding",
) -> NoTradeFinding:
    return NoTradeFinding(reason=reason, detail=detail, severity=severity)


def blocking_assessment(
    reason: NoTradeReason = NoTradeReason.RISK_NOT_PERMITTED,
) -> NoTradeAssessment:
    return assessment(findings=(finding(reason, FindingSeverity.BLOCKING),), no_trade=True)


def pending_assessment() -> NoTradeAssessment:
    return assessment(
        findings=(finding(NoTradeReason.NO_CONFIRMATION, FindingSeverity.PENDING),),
        no_trade=False,
    )


def clean_assessment() -> NoTradeAssessment:
    return assessment(findings=(), no_trade=False)


def sizing(outcome: SizingOutcome = SizingOutcome.ALLOWED) -> PositionSizing:
    return PositionSizing(
        outcome=outcome,
        reason=f"fixture sizing {outcome.value}",
        risk_amount=Decimal("1000"),
        stop_distance=Decimal("2.5"),
        loss_per_contract=Decimal("250"),
        maximum_by_risk=4 if outcome is SizingOutcome.ALLOWED else 0,
        maximum_by_margin=4 if outcome is SizingOutcome.ALLOWED else 0,
        margin_feasibility=MarginFeasibility.KNOWN,
        tick_feasibility=TickFeasibility.ON_GRID,
        allowed_contracts=4 if outcome is SizingOutcome.ALLOWED else 0,
        policy_limit=10,
    )


def data_quality(verdict: DataQualityVerdict = DataQualityVerdict.ACCEPTED) -> DataQualityReport:
    return DataQualityReport(
        verdict=verdict,
        issues=(),
        candle_count=200,
        symbol=FIXTURE_SYMBOL,
        timeframe=Timeframe.H1,
    )


def setup_quality(score: int = 72) -> SetupQuality:
    return SetupQuality(
        direction=EvidenceDirection.BULLISH,
        score=score,
        components=(
            ComponentScore(
                component=QualityComponent.TREND_ALIGNMENT,
                availability=ComponentAvailability.AVAILABLE,
                weight=15,
                awarded=12,
                reason="fixture component",
                groups=(),
            ),
        ),
        available_weight=100,
        total_weight=100,
        label="HEURISTIC SETUP QUALITY",
        method_version="setup-quality/1",
    )


_DEFAULT_AUTHORITY: dict[ReferenceKind, AuthorityClass] = {
    ReferenceKind.MISSING_INFORMATION: AuthorityClass.MISSING,
    ReferenceKind.VISION_OBSERVATION: AuthorityClass.VISION_EXTRACTION,
    ReferenceKind.RISK_FINDING: AuthorityClass.RISK,
    ReferenceKind.SCENARIO: AuthorityClass.SCENARIO,
}
"""Mirrors what real assembly assigns.

A fixture that stamped everything CALCULATED_METRIC would let an authority bug
pass unnoticed - the first version of this file did exactly that, and a test
asserting MISSING caught it.
"""


def numeric_fact(
    name: str,
    value: Decimal,
    authority: AuthorityClass = AuthorityClass.CALCULATED_METRIC,
    unit: str = "",
) -> NumericFact:
    """A citable authoritative number, shaped the way assembly builds one."""
    return NumericFact(
        ref=ContextRef(
            ref_id=make_ref_id(ReferenceKind.NUMERIC_FACT, name),
            kind=ReferenceKind.NUMERIC_FACT,
            label=name,
            authority=authority,
        ),
        name=name,
        value=value,
        unit=unit,
    )


def make_ref(
    kind: ReferenceKind,
    suffix: str | int,
    label: str = "fixture",
    authority: AuthorityClass | None = None,
) -> ContextRef:
    return ContextRef(
        ref_id=make_ref_id(kind, suffix),
        kind=kind,
        label=label,
        authority=authority or _DEFAULT_AUTHORITY.get(kind, AuthorityClass.CALCULATED_METRIC),
    )


def minimal_context(
    *,
    direction: EvidenceDirection = EvidenceDirection.BULLISH,
    no_trade_assessment: NoTradeAssessment | None = None,
    position_sizing: PositionSizing | None = None,
    quality: DataQualityReport | None = None,
) -> SynthesisContext:
    """A context with an envelope and nothing else, for envelope-focused tests."""
    from app.application.synthesis.context import CONTEXT_SCHEMA_VERSION  # noqa: PLC0415

    verdict = no_trade_assessment if no_trade_assessment is not None else clean_assessment()
    return SynthesisContext(
        schema_version=CONTEXT_SCHEMA_VERSION,
        symbol=FIXTURE_SYMBOL,
        direction=direction,
        envelope=derive_action_envelope(
            verdict, direction, sizing=position_sizing, data_quality=quality
        ),
        no_trade_state=verdict.no_trade,
    )


def draft(
    *,
    action: object = None,
    summary: str = "Fixture synthesis summary describing the current reading.",
    supporting_refs: tuple[str, ...] = (),
    opposing_refs: tuple[str, ...] = (),
    missing_refs: tuple[str, ...] = (),
    confirmation_refs: tuple[str, ...] = (),
    invalidation_refs: tuple[str, ...] = (),
    challenge: str = "The opposing reading deserves weight.",
    advocate_opposing: tuple[str, ...] = (),
    advocate_contradictions: tuple[str, ...] = (),
    advocate_missing: tuple[str, ...] = (),
    evidence_is_limited: bool = True,
    caveats: tuple[str, ...] = (),
    bull_text: str = "The bullish reading of the same evidence.",
    bear_text: str = "The bearish reading of the same evidence.",
    neutral_text: str = "The neutral reading of the same evidence.",
) -> SynthesisDraft:
    from app.domain.analysis.scenarios import ScenarioCase  # noqa: PLC0415
    from app.domain.synthesis.actions import FinalAction  # noqa: PLC0415

    return SynthesisDraft(
        proposed_action=action if action is not None else FinalAction.WAIT,  # type: ignore[arg-type]
        summary=summary,
        bull=ScenarioNarrative(case=ScenarioCase.BULL, narrative=bull_text),
        bear=ScenarioNarrative(case=ScenarioCase.BEAR, narrative=bear_text),
        neutral=ScenarioNarrative(case=ScenarioCase.NEUTRAL, narrative=neutral_text),
        devils_advocate=DevilsAdvocate(
            challenge=challenge,
            opposing_refs=advocate_opposing,
            contradiction_refs=advocate_contradictions,
            missing_refs=advocate_missing,
            evidence_is_limited=evidence_is_limited,
        ),
        supporting_refs=supporting_refs,
        opposing_refs=opposing_refs,
        missing_refs=missing_refs,
        confirmation_refs=confirmation_refs,
        invalidation_refs=invalidation_refs,
        caveats=caveats,
    )
