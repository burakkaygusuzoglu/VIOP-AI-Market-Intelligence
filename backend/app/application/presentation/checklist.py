"""The Pre-Trade Checklist (master spec §49).

Eleven checks, each PASS / WARNING / FAIL with a stated reason, and a verdict
that reaches **TRADE QUALITY INSUFFICIENT** when a critical check fails.

**It is an assessment, not an action.** §49 describes what to verify *before*
opening a paper trade; it does not open one. Nothing here creates a position,
sends an order, or resolves into LONG / SHORT / WAIT - a test asserts the
absence of all three.

## Missing is never PASS

The rule the whole module turns on. A check with no data to evaluate is
`WARNING` or `FAIL`, never `PASS`, because:

    missing stop     != valid stop
    missing risk     != safe risk
    missing volume   != weak volume
    missing trigger  != confirmed trigger
    missing liquidity data != acceptable liquidity

`Liquidity acceptable` is the sharpest case. §49 requires the check and this
repository has no order-book depth and no bid/ask quotes, so it can only ever
report *"değerlendirilemedi"*. Marking it PASS would be a fabricated
capability; marking it a critical FAIL would block every trade on a gap that is
known and documented. It is therefore a non-critical WARNING by default, and
which checks are critical is a configurable policy rather than a hidden
constant.

## Criticality is a policy, stated once

§49 says critical failures produce TRADE QUALITY INSUFFICIENT but does not say
which are critical, so `ChecklistPolicy` decides and documents it. A missing
entry trigger is deliberately *not* critical: it is a pending condition that
the next candle may resolve, and treating it as a permanent veto would collapse
the WAIT / NO TRADE distinction Phase 4 took care to preserve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.analysis.contradictions import ContradictionSeverity
from app.domain.analysis.engine import MultiTimeframeAnalysis
from app.domain.analysis.evidence import EvidenceCategory, EvidenceDirection
from app.domain.analysis.scenarios import Scenario, ScenarioCase, ScenarioState
from app.domain.market.quality import DataQualityReport, DataQualityVerdict
from app.domain.risk.reward import RiskReward
from app.domain.risk.sizing import PositionSizing, SizingOutcome


@unique
class ChecklistItem(StrEnum):
    """The §49 list, verbatim and in that order."""

    TREND_IDENTIFIED = "TREND_IDENTIFIED"
    SETUP_VALID = "SETUP_VALID"
    ENTRY_TRIGGER_CONFIRMED = "ENTRY_TRIGGER_CONFIRMED"
    STOP_DEFINED = "STOP_DEFINED"
    RISK_CALCULATED = "RISK_CALCULATED"
    RISK_REWARD_ACCEPTABLE = "RISK_REWARD_ACCEPTABLE"
    POSITION_SIZE_VALID = "POSITION_SIZE_VALID"
    VOLUME_CONFIRMATION = "VOLUME_CONFIRMATION"
    NO_MAJOR_CONTRADICTION = "NO_MAJOR_CONTRADICTION"
    DATA_QUALITY_ACCEPTABLE = "DATA_QUALITY_ACCEPTABLE"
    LIQUIDITY_ACCEPTABLE = "LIQUIDITY_ACCEPTABLE"


@unique
class CheckStatus(StrEnum):
    """§49's three outcomes."""

    PASS = "PASS"  # noqa: S105 - a §49 checklist outcome, not a credential
    """Evaluated, and satisfied. Never used for an unevaluated check."""

    WARNING = "WARNING"
    """Either evaluated and imperfect, or not evaluable at all. The reason
    always says which."""

    FAIL = "FAIL"


@unique
class ChecklistVerdict(StrEnum):
    """The overall answer §49 asks for."""

    SUFFICIENT = "SUFFICIENT"
    """No critical check failed. Non-critical warnings may still be present -
    read the items."""

    INCOMPLETE = "INCOMPLETE"
    """A critical check could not be evaluated. Not a pass and not a failure:
    the question is open, and calling it either would be a guess."""

    TRADE_QUALITY_INSUFFICIENT = "TRADE_QUALITY_INSUFFICIENT"
    """§49's named outcome: at least one critical check failed."""


DEFAULT_CRITICAL_ITEMS: frozenset[ChecklistItem] = frozenset(
    {
        ChecklistItem.STOP_DEFINED,
        ChecklistItem.RISK_CALCULATED,
        ChecklistItem.RISK_REWARD_ACCEPTABLE,
        ChecklistItem.POSITION_SIZE_VALID,
        ChecklistItem.NO_MAJOR_CONTRADICTION,
        ChecklistItem.DATA_QUALITY_ACCEPTABLE,
    }
)
"""Which failures produce TRADE QUALITY INSUFFICIENT. **Project policy.**

Chosen on one principle: a check is critical when proceeding without it risks
money in a way the next candle cannot fix.

* Stop, risk, risk/reward and position size are the money checks. Without any
  of them the trade's downside is undefined.
* A major timeframe contradiction and failed data integrity make the analysis
  itself unreliable, so everything downstream is unreliable too.

Deliberately **not** critical: the entry trigger (a pending condition, not a
defect), volume confirmation (informative, often absent), trend identification
(a WARNING says the picture is unclear without claiming it is wrong), and
liquidity (a documented capability gap - see the module docstring).
"""


@dataclass(frozen=True, slots=True)
class ChecklistPolicy:
    """Configurable criticality and thresholds. Not exchange facts."""

    critical_items: frozenset[ChecklistItem] = DEFAULT_CRITICAL_ITEMS
    minimum_risk_reward: Decimal = Decimal("1.5")
    """Only ever used to grade a ratio Phase 3 computed; this module performs
    no risk arithmetic of its own."""

    warn_on_unevaluated_liquidity: bool = True
    """When False the liquidity check FAILs instead of warning. Either way it
    can never PASS without data."""


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One check, its outcome, and why."""

    item: ChecklistItem
    status: CheckStatus
    reason: str
    """Plain Turkish - shown to a beginner unchanged."""

    detail: str = ""
    """Exact values for Pro. Empty when the check had nothing to measure."""

    critical: bool = False
    evaluated: bool = True
    """False when no input existed. Kept separate from ``status`` so
    "not measured" is never confused with "measured and imperfect"."""

    @property
    def passed(self) -> bool:
        return self.status is CheckStatus.PASS


@dataclass(frozen=True, slots=True)
class ChecklistAssessment:
    """The §49 result. A quality assessment, never an instruction."""

    results: tuple[CheckResult, ...]
    verdict: ChecklistVerdict
    policy: ChecklistPolicy = field(default_factory=ChecklistPolicy)

    def result_for(self, item: ChecklistItem) -> CheckResult | None:
        for result in self.results:
            if result.item is item:
                return result
        return None

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(item for item in self.results if item.status is CheckStatus.FAIL)

    @property
    def warnings(self) -> tuple[CheckResult, ...]:
        return tuple(item for item in self.results if item.status is CheckStatus.WARNING)

    @property
    def critical_failures(self) -> tuple[CheckResult, ...]:
        return tuple(item for item in self.failures if item.critical)

    @property
    def unevaluated(self) -> tuple[CheckResult, ...]:
        return tuple(item for item in self.results if not item.evaluated)

    @property
    def all_passed(self) -> bool:
        """Every check evaluated and satisfied.

        Distinct from a SUFFICIENT verdict, which tolerates non-critical
        warnings - so a warning can never be read as a pass.
        """
        return all(item.passed for item in self.results)

    @property
    def is_insufficient(self) -> bool:
        return self.verdict is ChecklistVerdict.TRADE_QUALITY_INSUFFICIENT


def assess_checklist(
    analysis: MultiTimeframeAnalysis,
    case: ScenarioCase,
    *,
    sizing: PositionSizing | None = None,
    risk_reward: RiskReward | None = None,
    data_quality: DataQualityReport | None = None,
    stop_defined: bool | None = None,
    policy: ChecklistPolicy | None = None,
) -> ChecklistAssessment:
    """Run all eleven §49 checks against what is actually available.

    Every optional argument is a finished Phase 1-3 result. Omitting one does
    not make its check pass - it makes the check unevaluated, which is a
    WARNING or a FAIL depending on whether the item is critical.

    ``stop_defined`` is a tri-state on purpose: ``None`` means nobody said,
    which is not the same as ``False`` meaning explicitly absent.
    """
    settings = policy if policy is not None else ChecklistPolicy()
    scenario = analysis.scenarios.case(case)
    direction = (
        EvidenceDirection.BULLISH if case is ScenarioCase.BULL else EvidenceDirection.BEARISH
    )

    results = (
        _trend(analysis, direction, settings),
        _setup(scenario, settings),
        _trigger(scenario, settings),
        _stop(stop_defined, sizing, settings),
        _risk(sizing, settings),
        _reward(risk_reward, settings),
        _size(sizing, settings),
        _volume(analysis, direction, settings),
        _contradiction(analysis, settings),
        _data(data_quality, settings),
        _liquidity(settings),
    )
    return ChecklistAssessment(results=results, verdict=_verdict(results), policy=settings)


def _critical(item: ChecklistItem, policy: ChecklistPolicy) -> bool:
    return item in policy.critical_items


def _verdict(results: tuple[CheckResult, ...]) -> ChecklistVerdict:
    """§49: a critical failure produces TRADE QUALITY INSUFFICIENT.

    A critical check that could not be *evaluated* is reported as INCOMPLETE
    rather than folded into either outcome - it is neither a demonstrated
    failure nor a satisfied requirement.
    """
    if any(item.critical and item.status is CheckStatus.FAIL for item in results):
        return ChecklistVerdict.TRADE_QUALITY_INSUFFICIENT
    if any(item.critical and not item.evaluated for item in results):
        return ChecklistVerdict.INCOMPLETE
    return ChecklistVerdict.SUFFICIENT


# ----------------------------------------------------------------------
# The eleven checks
# ----------------------------------------------------------------------


def _trend(
    analysis: MultiTimeframeAnalysis,
    direction: EvidenceDirection,
    policy: ChecklistPolicy,
) -> CheckResult:
    item = ChecklistItem.TREND_IDENTIFIED
    readings = [item for item in analysis.contradictions.readings if item.is_directional]
    if not readings:
        return CheckResult(
            item=item,
            status=CheckStatus.WARNING,
            reason="Hiçbir zaman diliminde net bir yön okunamadı.",
            critical=_critical(item, policy),
            evaluated=False,
        )
    agreeing = [reading for reading in readings if reading.direction is direction]
    if agreeing:
        return CheckResult(
            item=item,
            status=CheckStatus.PASS,
            reason=f"{len(agreeing)} zaman dilimi bu yönü destekliyor.",
            detail=", ".join(
                f"{reading.timeframe.value}={reading.direction.value}" for reading in readings
            ),
            critical=_critical(item, policy),
        )
    return CheckResult(
        item=item,
        status=CheckStatus.FAIL,
        reason="Okunabilen zaman dilimlerinin hiçbiri bu yönü desteklemiyor.",
        detail=", ".join(
            f"{reading.timeframe.value}={reading.direction.value}" for reading in readings
        ),
        critical=_critical(item, policy),
    )


def _setup(scenario: Scenario, policy: ChecklistPolicy) -> CheckResult:
    item = ChecklistItem.SETUP_VALID
    if scenario.state is ScenarioState.UNAVAILABLE:
        return CheckResult(
            item=item,
            status=CheckStatus.WARNING,
            reason="Senaryo değerlendirilemedi; gerekli zaman dilimi verisi yok.",
            detail=scenario.reason,
            critical=_critical(item, policy),
            evaluated=False,
        )
    if scenario.state is ScenarioState.INACTIVE:
        return CheckResult(
            item=item,
            status=CheckStatus.FAIL,
            reason="Bu senaryoyu destekleyen kanıt yok.",
            detail=scenario.reason,
            critical=_critical(item, policy),
        )
    if scenario.state is ScenarioState.FORMING:
        return CheckResult(
            item=item,
            status=CheckStatus.WARNING,
            reason="Kurulum henüz oluşuyor; ana yön onu desteklemiyor.",
            detail=scenario.reason,
            critical=_critical(item, policy),
        )
    return CheckResult(
        item=item,
        status=CheckStatus.PASS,
        reason="Kurulum geçerli.",
        detail=f"state={scenario.state.value}. {scenario.reason}",
        critical=_critical(item, policy),
    )


def _trigger(scenario: Scenario, policy: ChecklistPolicy) -> CheckResult:
    """A pending trigger is a WARNING, deliberately not a FAIL.

    Nothing is broken when a setup has simply not triggered yet, and treating
    it as a failure would turn every forming trade into an insufficient one.
    """
    item = ChecklistItem.ENTRY_TRIGGER_CONFIRMED
    if scenario.state is ScenarioState.CONFIRMED:
        return CheckResult(
            item=item,
            status=CheckStatus.PASS,
            reason="Teyit koşullarının tamamı sağlandı.",
            detail=scenario.reason,
            critical=_critical(item, policy),
        )
    outstanding = scenario.outstanding_requirements
    if not outstanding:
        return CheckResult(
            item=item,
            status=CheckStatus.WARNING,
            reason="Giriş tetiği değerlendirilemedi.",
            detail=scenario.reason,
            critical=_critical(item, policy),
            evaluated=False,
        )
    return CheckResult(
        item=item,
        status=CheckStatus.WARNING,
        reason=f"{len(outstanding)} teyit koşulu henüz sağlanmadı.",
        detail=", ".join(f"{one.code.value}={one.status.value}" for one in outstanding),
        critical=_critical(item, policy),
    )


def _stop(
    stop_defined: bool | None, sizing: PositionSizing | None, policy: ChecklistPolicy
) -> CheckResult:
    """A missing stop is not a valid stop.

    Accepts the caller's explicit statement first; failing that, a sizing
    result carrying a stop distance is proof a stop existed when it was
    computed.
    """
    item = ChecklistItem.STOP_DEFINED
    critical = _critical(item, policy)

    if stop_defined is False:
        return CheckResult(
            item=item,
            status=CheckStatus.FAIL,
            reason="Zarar kes seviyesi tanımlanmamış.",
            critical=critical,
        )
    if stop_defined is True:
        return CheckResult(
            item=item,
            status=CheckStatus.PASS,
            reason="Zarar kes seviyesi tanımlı.",
            critical=critical,
        )
    if sizing is not None and sizing.stop_distance is not None:
        return CheckResult(
            item=item,
            status=CheckStatus.PASS,
            reason="Zarar kes seviyesi tanımlı.",
            detail=f"stop_distance={sizing.stop_distance}",
            critical=critical,
        )
    return CheckResult(
        item=item,
        status=CheckStatus.WARNING,
        reason="Zarar kes seviyesi bildirilmedi; tanımlı olduğu varsayılamaz.",
        critical=critical,
        evaluated=False,
    )


def _risk(sizing: PositionSizing | None, policy: ChecklistPolicy) -> CheckResult:
    """Missing risk is not safe risk."""
    item = ChecklistItem.RISK_CALCULATED
    critical = _critical(item, policy)

    if sizing is None:
        return CheckResult(
            item=item,
            status=CheckStatus.WARNING,
            reason="Risk hesabı sağlanmadı; riskin uygun olduğu varsayılamaz.",
            critical=critical,
            evaluated=False,
        )
    if sizing.risk_amount is None or sizing.loss_per_contract is None:
        return CheckResult(
            item=item,
            status=CheckStatus.FAIL,
            reason="Risk hesaplanamadı.",
            detail=sizing.reason,
            critical=critical,
        )
    return CheckResult(
        item=item,
        status=CheckStatus.PASS,
        reason="Risk hesaplandı.",
        detail=(f"risk_amount={sizing.risk_amount}, loss_per_contract={sizing.loss_per_contract}"),
        critical=critical,
    )


def _reward(reward: RiskReward | None, policy: ChecklistPolicy) -> CheckResult:
    item = ChecklistItem.RISK_REWARD_ACCEPTABLE
    critical = _critical(item, policy)

    if reward is None:
        return CheckResult(
            item=item,
            status=CheckStatus.WARNING,
            reason="Risk/ödül oranı sağlanmadı.",
            critical=critical,
            evaluated=False,
        )
    if reward.ratio is None:
        return CheckResult(
            item=item,
            status=CheckStatus.FAIL,
            reason="Risk/ödül geometrisi geçersiz.",
            detail=reward.reason,
            critical=critical,
        )
    if reward.ratio < policy.minimum_risk_reward:
        return CheckResult(
            item=item,
            status=CheckStatus.FAIL,
            reason="Risk/ödül oranı ayarlanan alt sınırın altında.",
            detail=f"ratio={reward.ratio}, minimum={policy.minimum_risk_reward}",
            critical=critical,
        )
    return CheckResult(
        item=item,
        status=CheckStatus.PASS,
        reason="Risk/ödül oranı yeterli.",
        detail=f"ratio={reward.ratio}, minimum={policy.minimum_risk_reward}",
        critical=critical,
    )


def _size(sizing: PositionSizing | None, policy: ChecklistPolicy) -> CheckResult:
    item = ChecklistItem.POSITION_SIZE_VALID
    critical = _critical(item, policy)

    if sizing is None:
        return CheckResult(
            item=item,
            status=CheckStatus.WARNING,
            reason="Pozisyon büyüklüğü hesaplanmadı.",
            critical=critical,
            evaluated=False,
        )
    if sizing.outcome is SizingOutcome.ALLOWED and sizing.allowed_contracts:
        return CheckResult(
            item=item,
            status=CheckStatus.PASS,
            reason=f"Hesap {sizing.allowed_contracts} sözleşmeye izin veriyor.",
            detail=f"outcome={sizing.outcome.value}",
            critical=critical,
        )
    if sizing.outcome is SizingOutcome.UNDETERMINED:
        return CheckResult(
            item=item,
            status=CheckStatus.WARNING,
            reason="Pozisyon büyüklüğü belirlenemedi; bilinmeyen bir kısıt var.",
            detail=sizing.reason,
            critical=critical,
            evaluated=False,
        )
    return CheckResult(
        item=item,
        status=CheckStatus.FAIL,
        reason="Geçerli bir pozisyon büyüklüğü yok.",
        detail=f"outcome={sizing.outcome.value}. {sizing.reason}",
        critical=critical,
    )


def _volume(
    analysis: MultiTimeframeAnalysis,
    direction: EvidenceDirection,
    policy: ChecklistPolicy,
) -> CheckResult:
    """Missing volume is not weak volume."""
    item = ChecklistItem.VOLUME_CONFIRMATION
    critical = _critical(item, policy)
    groups = [group for group in analysis.fused.groups if group.category is EvidenceCategory.VOLUME]

    if not groups:
        return CheckResult(
            item=item,
            status=CheckStatus.WARNING,
            reason="Hacim teyidi ölçülemedi; hacmin zayıf olduğu anlamına gelmez.",
            critical=critical,
            evaluated=False,
        )
    supporting = [group for group in groups if group.direction is direction]
    if supporting:
        return CheckResult(
            item=item,
            status=CheckStatus.PASS,
            reason="Hareket hacimle destekleniyor.",
            detail=", ".join(
                f"{group.role.value if group.role else '-'}={group.direction.value}"
                for group in groups
            ),
            critical=critical,
        )
    return CheckResult(
        item=item,
        status=CheckStatus.WARNING,
        reason="Hacim bu yönü teyit etmiyor.",
        detail=", ".join(
            f"{group.role.value if group.role else '-'}={group.direction.value}" for group in groups
        ),
        critical=critical,
    )


def _contradiction(analysis: MultiTimeframeAnalysis, policy: ChecklistPolicy) -> CheckResult:
    item = ChecklistItem.NO_MAJOR_CONTRADICTION
    critical = _critical(item, policy)
    major = [
        one
        for one in analysis.contradictions.contradictions
        if one.severity is ContradictionSeverity.MAJOR
    ]
    if major:
        return CheckResult(
            item=item,
            status=CheckStatus.FAIL,
            reason="Zaman dilimleri arasında büyük bir çelişki var.",
            detail=major[0].reason,
            critical=critical,
        )
    return CheckResult(
        item=item,
        status=CheckStatus.PASS,
        reason="Büyük bir çelişki yok.",
        detail=f"{len(analysis.contradictions.contradictions)} contradiction(s), none MAJOR",
        critical=critical,
    )


def _data(report: DataQualityReport | None, policy: ChecklistPolicy) -> CheckResult:
    item = ChecklistItem.DATA_QUALITY_ACCEPTABLE
    critical = _critical(item, policy)

    if report is None:
        return CheckResult(
            item=item,
            status=CheckStatus.WARNING,
            reason="Veri kalitesi raporu sağlanmadı.",
            critical=critical,
            evaluated=False,
        )
    if report.is_blocked:
        return CheckResult(
            item=item,
            status=CheckStatus.FAIL,
            reason="Veri bütünlüğü kontrolünden geçmedi.",
            detail=", ".join(one.code.value for one in report.blocking_issues),
            critical=critical,
        )
    if report.verdict is DataQualityVerdict.ACCEPTED_WITH_WARNINGS:
        return CheckResult(
            item=item,
            status=CheckStatus.WARNING,
            reason="Veri uyarılarla kabul edildi.",
            detail=f"{len(report.issues)} issue(s)",
            critical=critical,
        )
    return CheckResult(
        item=item,
        status=CheckStatus.PASS,
        reason="Veri kalitesi uygun.",
        detail=f"verdict={report.verdict.value}",
        critical=critical,
    )


def _liquidity(policy: ChecklistPolicy) -> CheckResult:
    """Always unevaluated, and therefore never PASS.

    §49 requires the check. This repository receives no order-book depth and
    no bid/ask quotes, so the only honest outcome is that it was not
    evaluated. It is stated every run rather than omitted, so the gap stays
    visible instead of looking like a satisfied requirement.
    """
    item = ChecklistItem.LIQUIDITY_ACCEPTABLE
    return CheckResult(
        item=item,
        status=CheckStatus.WARNING if policy.warn_on_unevaluated_liquidity else CheckStatus.FAIL,
        reason=("Likidite değerlendirilmedi: emir defteri ve alış/satış verisi bu projede yok."),
        detail="no order-book depth or bid/ask feed exists in this repository",
        critical=_critical(item, policy),
        evaluated=False,
    )
