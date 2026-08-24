"""The NO TRADE engine (master spec §25, §43).

A deterministic **veto**, and only a veto. It answers "is there a reason not to
act on this analysis", which is a different and much safer question than "what
should I do". Phase 4 does not answer the second one: there is no LONG, no
SHORT and no WAIT here, and a test enforces their absence. That synthesis
weighs the analysis, the account, the user's constraints and the scenarios
together, and belongs to the phase that owns it.

## Why this lives outside ``app.domain.analysis``

§43 separates **setup quality** from **trade suitability**, and Phase 4A backed
that with an import contract keeping the analysis package clear of
`app.domain.risk`. That contract is deliberately preserved: an account balance
must never be able to reach a technical score, or the same chart would grade
differently for two users.

But a veto genuinely needs both halves - a flawless setup the account cannot
fund is still not a trade. So the dependency is resolved one level up:

        app.domain.analysis  (technical, account-blind)
                    \\
                     ->  app.domain.suitability  (this package)
                    /
        app.domain.risk      (money, analysis-blind)

Both inputs arrive as finished typed results. No Phase 3 formula is
reimplemented here and no evidence is regenerated.

## Reasons are only as real as their data

Every reason below can actually be evaluated from something this project
holds. §25 also lists reasons that need data sources nobody has wired up -
liquidity, event risk, news. Those are enumerated separately as
`DeferredNoTradeReason` so they are visible as *known gaps* rather than
quietly missing, and they can never fire.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.analysis.contradictions import ContradictionSeverity
from app.domain.analysis.engine import MultiTimeframeAnalysis
from app.domain.analysis.entry import EntryComponent
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.analysis.scenarios import Scenario, ScenarioCase, ScenarioState
from app.domain.analysis.timeframes import TimeframeRole
from app.domain.market.quality import DataQualityReport, DataQualityVerdict
from app.domain.risk.reward import RiskReward
from app.domain.risk.sizing import PositionSizing, SizingOutcome
from app.domain.structure.regime import MarketRegime


@unique
class NoTradeReason(StrEnum):
    """Reasons this engine can actually evaluate today."""

    CONFLICTING_TIMEFRAMES = "CONFLICTING_TIMEFRAMES"
    CHAOTIC_REGIME = "CHAOTIC_REGIME"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    MIDDLE_OF_RANGE = "MIDDLE_OF_RANGE"
    EXTENDED_MOVE = "EXTENDED_MOVE"
    NO_CONFIRMATION = "NO_CONFIRMATION"
    UNCLEAR_STRUCTURE = "UNCLEAR_STRUCTURE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    BAD_DATA = "BAD_DATA"
    POOR_RISK_REWARD = "POOR_RISK_REWARD"
    """Only when an explicit Phase 3 ``RiskReward`` was supplied."""

    RISK_NOT_PERMITTED = "RISK_NOT_PERMITTED"
    """Only when an explicit Phase 3 ``PositionSizing`` was supplied and it
    permits zero contracts."""

    RISK_UNDETERMINED = "RISK_UNDETERMINED"
    """Sizing could not reach an answer - unverified margin, unknown tick
    grid. Not the same as being refused, and not the same as being allowed."""


@unique
class DeferredNoTradeReason(StrEnum):
    """§25 reasons with no authoritative data source in this repository.

    Listed so the gap is documented rather than invisible. **None of these can
    fire**, and a test proves that no code path returns one: implementing them
    against invented inputs would produce a veto nobody could audit.
    """

    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    """Needs order-book depth or a verified volume floor. Neither exists."""

    EVENT_RISK = "EVENT_RISK"
    """Needs an economic calendar."""

    NEWS_RISK = "NEWS_RISK"
    """Needs a news feed - Phase 12+."""

    ORDER_BOOK_IMBALANCE = "ORDER_BOOK_IMBALANCE"
    """Needs level 2 data this project does not receive."""

    CORRELATED_EXPOSURE = "CORRELATED_EXPOSURE"
    """Needs open positions across instruments - paper trading, Phase 9."""


@unique
class FindingSeverity(StrEnum):
    """What *kind* of obstacle a reason is - not merely how big.

    Phase 7 must eventually distinguish **WAIT** from **NO TRADE**, and it can
    only do that if Phase 4 preserved the difference. A boolean cannot: it
    collapses "this may resolve on the next candle" and "this cannot be
    resolved by waiting at all" into the same non-blocking bucket.

    Phase 4 does not make that decision. It records which kind each finding is
    and leaves the decision to the phase that owns it.
    """

    BLOCKING = "BLOCKING"
    """Cannot proceed, and waiting does not help. Risk that permits zero
    contracts, data that failed integrity checks, a structure nobody can read.
    Waiting for a 5M candle does not fix any of them."""

    PENDING = "PENDING"
    """A condition that a future candle could genuinely resolve. A missing
    entry confirmation is the archetype: the setup may be sound and simply has
    not triggered. This is the raw material of a future WAIT, and it is
    deliberately **not** a veto."""

    CAUTION = "CAUTION"
    """Measured, disclosed, and neither blocking nor pending. High volatility
    and accepted-with-warnings data are true of the market right now; they do
    not stop anything and they will not be resolved by waiting either."""


@dataclass(frozen=True, slots=True)
class NoTradeFinding:
    """One reason, with the observation that produced it."""

    reason: NoTradeReason
    detail: str
    severity: FindingSeverity

    @property
    def blocking(self) -> bool:
        """True only for BLOCKING. Derived, so it can never disagree with the
        severity it is read from."""
        return self.severity is FindingSeverity.BLOCKING

    @property
    def is_pending(self) -> bool:
        return self.severity is FindingSeverity.PENDING


@dataclass(frozen=True, slots=True)
class NoTradeConfig:
    """Project heuristics for the veto. Not exchange facts."""

    minimum_risk_reward: Decimal = Decimal("1.5")
    """§25 treats a target closer than the stop as a poor trade. The number is
    a project default; it only ever grades a ratio Phase 3 computed."""

    extended_move_component_floor: int = 1
    """Entry extension must award more than this to avoid an extended-move
    veto. Reads the Phase 4 entry component rather than remeasuring."""

    range_middle_band: float = 0.4
    """How much of the middle of a range counts as "no man's land", as a
    fraction of the range height."""


@dataclass(frozen=True, slots=True)
class NoTradeAssessment:
    """The veto result. Never a direction, never an action."""

    no_trade: bool | None
    """``True`` when something blocks, ``False`` when nothing does, and
    ``None`` when the question could not be answered - missing timeframes,
    undetermined sizing. Three states, because "we could not tell" must not
    collapse into "go ahead"."""

    findings: tuple[NoTradeFinding, ...]
    missing_requirements: tuple[str, ...]
    """What would have to be supplied to reach a definite answer."""

    evaluated_reasons: tuple[NoTradeReason, ...] = field(
        default_factory=lambda: tuple(NoTradeReason)
    )
    deferred_reasons: tuple[DeferredNoTradeReason, ...] = field(
        default_factory=lambda: tuple(DeferredNoTradeReason)
    )

    @property
    def blocking(self) -> tuple[NoTradeFinding, ...]:
        return tuple(item for item in self.findings if item.blocking)

    @property
    def pending(self) -> tuple[NoTradeFinding, ...]:
        """Conditions a future candle could resolve.

        Phase 7's raw material for WAIT. Phase 4 surfaces them and stops: a
        pending finding is never a veto, and ``no_trade is False`` alongside a
        non-empty ``pending`` means "nothing blocks, and something has not
        happened yet" - two facts, deliberately kept apart.
        """
        return tuple(item for item in self.findings if item.is_pending)

    @property
    def cautions(self) -> tuple[NoTradeFinding, ...]:
        return tuple(item for item in self.findings if item.severity is FindingSeverity.CAUTION)

    @property
    def reasons(self) -> tuple[NoTradeReason, ...]:
        return tuple(item.reason for item in self.findings)

    @property
    def is_waitable(self) -> bool:
        """Nothing blocks, but something is outstanding.

        Descriptive, not a decision: it reports the *shape* of the findings so
        Phase 7 can tell a pending setup from a clean one without re-deriving
        it from reason codes.
        """
        return not self.blocking and bool(self.pending)


def assess_no_trade(
    analysis: MultiTimeframeAnalysis,
    direction: EvidenceDirection,
    *,
    sizing: PositionSizing | None = None,
    risk_reward: RiskReward | None = None,
    data_quality: DataQualityReport | None = None,
    config: NoTradeConfig | None = None,
) -> NoTradeAssessment:
    """Look for reasons not to act on ``direction``.

    ``sizing``, ``risk_reward`` and ``data_quality`` are optional Phase 1-3
    results. Supplying them enables the reasons that depend on them; omitting
    them leaves those reasons unevaluated and recorded in
    ``missing_requirements``. **Absence never counts as a pass** - a missing
    sizing result does not mean the risk is acceptable.
    """
    settings = config if config is not None else NoTradeConfig()
    if not direction.is_directional:
        raise ValueError(f"a veto is assessed for a directional case, got {direction.value}")

    findings: list[NoTradeFinding] = []
    missing: list[str] = []
    scenario = analysis.scenarios.case(
        ScenarioCase.BULL if direction is EvidenceDirection.BULLISH else ScenarioCase.BEAR
    )

    findings.extend(_timeframe_findings(analysis))
    findings.extend(_regime_findings(analysis))
    findings.extend(_structure_findings(analysis, scenario))
    findings.extend(_entry_findings(analysis, direction, settings))

    if data_quality is not None:
        findings.extend(_data_findings(data_quality))
    else:
        missing.append("a Data Quality verdict for the underlying candles")

    if risk_reward is not None:
        findings.extend(_reward_findings(risk_reward, settings))
    else:
        missing.append("an explicit Phase 3 risk/reward result for a proposed trade")

    if sizing is not None:
        findings.extend(_sizing_findings(sizing))
    else:
        missing.append("an explicit Phase 3 position-sizing result for the account")

    undetermined = analysis.views.missing_roles or any(
        item.reason is NoTradeReason.RISK_UNDETERMINED for item in findings
    )
    blocking = [item for item in findings if item.blocking]

    if blocking:
        verdict: bool | None = True
    elif undetermined:
        verdict = None
    else:
        verdict = False

    return NoTradeAssessment(
        no_trade=verdict,
        findings=tuple(findings),
        missing_requirements=tuple(missing),
    )


# ----------------------------------------------------------------------
# Reason evaluation
# ----------------------------------------------------------------------


def _timeframe_findings(analysis: MultiTimeframeAnalysis) -> tuple[NoTradeFinding, ...]:
    found: list[NoTradeFinding] = []
    major = [
        item
        for item in analysis.contradictions.contradictions
        if item.severity is ContradictionSeverity.MAJOR
    ]
    if major:
        found.append(
            NoTradeFinding(
                reason=NoTradeReason.CONFLICTING_TIMEFRAMES,
                detail=major[0].reason,
                severity=FindingSeverity.BLOCKING,
            )
        )

    if analysis.views.missing_roles:
        names = ", ".join(role.value for role in analysis.views.missing_roles)
        found.append(
            NoTradeFinding(
                reason=NoTradeReason.INSUFFICIENT_DATA,
                detail=f"no view was supplied for: {names}",
                severity=FindingSeverity.CAUTION,
            )
        )
    return tuple(found)


def _regime_findings(analysis: MultiTimeframeAnalysis) -> tuple[NoTradeFinding, ...]:
    found: list[NoTradeFinding] = []
    for view in analysis.views.views:
        regime = view.structure.regime.regime
        if regime is MarketRegime.CHAOTIC:
            found.append(
                NoTradeFinding(
                    reason=NoTradeReason.CHAOTIC_REGIME,
                    detail=f"{view.timeframe.value} is classified CHAOTIC",
                    severity=FindingSeverity.BLOCKING,
                )
            )
        elif regime is MarketRegime.HIGH_VOLATILITY_RANGE:
            found.append(
                NoTradeFinding(
                    reason=NoTradeReason.HIGH_VOLATILITY,
                    detail=(
                        f"{view.timeframe.value} is a high-volatility range, where stops sit "
                        "inside the noise"
                    ),
                    severity=FindingSeverity.CAUTION,
                )
            )
    return tuple(found)


def _structure_findings(
    analysis: MultiTimeframeAnalysis, scenario: Scenario
) -> tuple[NoTradeFinding, ...]:
    found: list[NoTradeFinding] = []

    bias = analysis.contradictions.reading_for(TimeframeRole.BIAS)
    if bias is not None and bias.direction is EvidenceDirection.UNAVAILABLE:
        found.append(
            NoTradeFinding(
                reason=NoTradeReason.UNCLEAR_STRUCTURE,
                detail=f"the {bias.timeframe.value} bias could not be read",
                severity=FindingSeverity.BLOCKING,
            )
        )

    ranging = [
        view
        for view in analysis.views.views
        if view.structure.regime.regime in (MarketRegime.RANGE, MarketRegime.LOW_VOLATILITY_RANGE)
        and view.role in (TimeframeRole.BIAS, TimeframeRole.SETUP)
    ]
    if ranging and scenario.state is not ScenarioState.CONFIRMED:
        found.append(
            NoTradeFinding(
                reason=NoTradeReason.MIDDLE_OF_RANGE,
                detail=(
                    f"{ranging[0].timeframe.value} is ranging and this case is "
                    f"{scenario.state.value}, so there is no edge of the range to work from"
                ),
                severity=FindingSeverity.PENDING,
            )
        )

    if scenario.state in (ScenarioState.WAITING_FOR_CONFIRMATION, ScenarioState.FORMING):
        outstanding = ", ".join(item.code.value for item in scenario.outstanding_requirements)
        found.append(
            NoTradeFinding(
                reason=NoTradeReason.NO_CONFIRMATION,
                detail=f"the case is {scenario.state.value}; outstanding: {outstanding or 'none'}",
                severity=FindingSeverity.PENDING,
            )
        )
    elif scenario.state is ScenarioState.UNAVAILABLE:
        found.append(
            NoTradeFinding(
                reason=NoTradeReason.INSUFFICIENT_DATA,
                detail=scenario.reason,
                severity=FindingSeverity.BLOCKING,
            )
        )
    return tuple(found)


def _entry_findings(
    analysis: MultiTimeframeAnalysis,
    direction: EvidenceDirection,
    config: NoTradeConfig,
) -> tuple[NoTradeFinding, ...]:
    case = analysis.scenarios.case(
        ScenarioCase.BULL if direction is EvidenceDirection.BULLISH else ScenarioCase.BEAR
    )
    entry = case.entry
    if entry is None or not entry.is_available:
        return ()
    extension = entry.component(EntryComponent.EXTENSION)
    if (
        extension is not None
        and extension.is_available
        and extension.awarded is not None
        and extension.awarded <= config.extended_move_component_floor
    ):
        return (
            NoTradeFinding(
                reason=NoTradeReason.EXTENDED_MOVE,
                detail=extension.reason,
                severity=FindingSeverity.PENDING,
            ),
        )
    return ()


def _data_findings(report: DataQualityReport) -> tuple[NoTradeFinding, ...]:
    """A blocked dataset vetoes; a warned one is disclosed, not suppressed.

    Phase 1's engine already decided this - the verdict is read, never
    re-derived.
    """
    if report.is_blocked:
        codes = ", ".join(issue.code.value for issue in report.blocking_issues)
        return (
            NoTradeFinding(
                reason=NoTradeReason.BAD_DATA,
                detail=f"the Data Quality Engine returned {report.verdict.value}: {codes}",
                severity=FindingSeverity.BLOCKING,
            ),
        )
    if report.verdict is DataQualityVerdict.ACCEPTED_WITH_WARNINGS:
        return (
            NoTradeFinding(
                reason=NoTradeReason.BAD_DATA,
                detail=f"the candles were accepted with {len(report.issues)} warning(s)",
                severity=FindingSeverity.CAUTION,
            ),
        )
    return ()


def _reward_findings(reward: RiskReward, config: NoTradeConfig) -> tuple[NoTradeFinding, ...]:
    if reward.ratio is None:
        return (
            NoTradeFinding(
                reason=NoTradeReason.POOR_RISK_REWARD,
                detail=f"the risk/reward geometry is invalid: {reward.reason}",
                severity=FindingSeverity.BLOCKING,
            ),
        )
    if reward.ratio < config.minimum_risk_reward:
        return (
            NoTradeFinding(
                reason=NoTradeReason.POOR_RISK_REWARD,
                detail=(
                    f"risk/reward {reward.ratio} is below the configured minimum "
                    f"{config.minimum_risk_reward}"
                ),
                severity=FindingSeverity.BLOCKING,
            ),
        )
    return ()


def _sizing_findings(sizing: PositionSizing) -> tuple[NoTradeFinding, ...]:
    """Read the Phase 3 outcome; never recompute it.

    `NOT_PERMITTED` and `INVALID` block. `UNDETERMINED` does not block, but it
    does prevent a clean pass: an unknown constraint is not a satisfied one.
    """
    if sizing.outcome in (SizingOutcome.NOT_PERMITTED, SizingOutcome.INVALID):
        return (
            NoTradeFinding(
                reason=NoTradeReason.RISK_NOT_PERMITTED,
                detail=f"position sizing returned {sizing.outcome.value}: {sizing.reason}",
                severity=FindingSeverity.BLOCKING,
            ),
        )
    if sizing.outcome is SizingOutcome.UNDETERMINED:
        return (
            NoTradeFinding(
                reason=NoTradeReason.RISK_UNDETERMINED,
                detail=f"position sizing could not be determined: {sizing.reason}",
                severity=FindingSeverity.CAUTION,
            ),
        )
    return ()
