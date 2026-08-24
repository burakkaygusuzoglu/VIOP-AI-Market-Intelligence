"""Bull, Bear and Neutral scenarios (master spec §23).

Each scenario is a **structured view of evidence that already exists**, not a
narrative. Everything on it points back to a fused evidence group, a
contradiction or a named requirement whose status was actually evaluated. No
prose is generated here, no price is invented, and no order is described.

## The state machine, and where it stops

A scenario can be INACTIVE, FORMING, WAITING_FOR_CONFIRMATION, CONFIRMED or
UNAVAILABLE. `WAITING_FOR_CONFIRMATION` is the state §55 exists to make
legible: the higher timeframes support the case, and something specific and
named has not yet happened.

**This is not the application's final action.** A bull scenario reading
CONFIRMED does not mean "go long", and a scenario set does not resolve into
LONG / SHORT / WAIT. That synthesis weighs scenarios against account risk,
suitability and the user's own constraints, and belongs to the later phase
that owns it. Phase 4 stops at describing each case on its own terms - a test
enforces that nothing here produces a global action.

## Bull and bear are independent

Their qualities are scored separately and do not sum to 100. Both can be poor
at once; that is what a directionless market looks like, and it is information
rather than a gap to fill with a manufactured neutral remainder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, unique

from app.domain.analysis.contradictions import Contradiction, ContradictionSeverity
from app.domain.analysis.entry import EntryConfig, EntryQuality, score_entry
from app.domain.analysis.evidence import EvidenceCategory, EvidenceDirection
from app.domain.analysis.fusion import EvidenceGroup, FusedEvidence
from app.domain.analysis.quality import QualityConfig, SetupQuality, score_setup
from app.domain.analysis.timeframes import MultiTimeframeView, TimeframeRole


@unique
class ScenarioCase(StrEnum):
    BULL = "BULL"
    BEAR = "BEAR"
    NEUTRAL = "NEUTRAL"


@unique
class ScenarioState(StrEnum):
    """How far along a scenario is. Never an instruction."""

    UNAVAILABLE = "UNAVAILABLE"
    """The data needed to judge the case is missing. Not the same as INACTIVE,
    which is a judgement that the case is absent."""

    INACTIVE = "INACTIVE"
    """Measured, and nothing supports this case."""

    FORMING = "FORMING"
    """Some support exists, but the directional bias timeframe does not back
    it."""

    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    """The bias supports the case and a named requirement has not yet been
    met. §55's "what are we waiting for" state."""

    CONFIRMED = "CONFIRMED"
    """Every evaluable requirement is met. Still not an instruction to act."""


@unique
class RequirementStatus(StrEnum):
    MET = "MET"
    UNMET = "UNMET"
    UNKNOWN = "UNKNOWN"
    """Could not be evaluated - the timeframe or the reading is absent. Kept
    apart from UNMET, because "we looked and it has not happened" and "we
    cannot see" are different answers."""


@unique
class RequirementCode(StrEnum):
    """Named, deterministically evaluable confirmation requirements.

    Every member is decidable from data this project already holds. Nothing
    here needs a trigger price, and none is invented.
    """

    BIAS_TIMEFRAME_AGREES = "BIAS_TIMEFRAME_AGREES"
    SETUP_TIMEFRAME_AGREES = "SETUP_TIMEFRAME_AGREES"
    ENTRY_TIMEFRAME_AGREES = "ENTRY_TIMEFRAME_AGREES"
    NO_MAJOR_CONTRADICTION = "NO_MAJOR_CONTRADICTION"
    STRUCTURE_SUPPORTS = "STRUCTURE_SUPPORTS"
    BREAKOUT_OR_RETEST_RESOLVED = "BREAKOUT_OR_RETEST_RESOLVED"


@dataclass(frozen=True, slots=True)
class Requirement:
    """One confirmation condition and whether it currently holds."""

    code: RequirementCode
    status: RequirementStatus
    reason: str

    @property
    def is_met(self) -> bool:
        return self.status is RequirementStatus.MET


@unique
class InvalidationCode(StrEnum):
    """What would falsify the case, where that is deterministically known.

    Stated as **conditions on future observations**, never as prices. "A
    confirmed change of character against this case on the bias timeframe" is
    a fact the engines can later report; "invalidation at 104.20" would be a
    number nobody computed.
    """

    OPPOSING_CHOCH_ON_BIAS = "OPPOSING_CHOCH_ON_BIAS"
    BIAS_TIMEFRAME_REVERSES = "BIAS_TIMEFRAME_REVERSES"
    REGIME_TURNS_AGAINST = "REGIME_TURNS_AGAINST"
    OPPOSING_CONFIRMED_BREAKOUT = "OPPOSING_CONFIRMED_BREAKOUT"
    RANGE_BREAKS_EITHER_WAY = "RANGE_BREAKS_EITHER_WAY"


@dataclass(frozen=True, slots=True)
class Invalidation:
    code: InvalidationCode
    description: str


@dataclass(frozen=True, slots=True)
class Scenario:
    """One case, with the evidence for and against it."""

    case: ScenarioCase
    state: ScenarioState
    supporting: tuple[EvidenceGroup, ...]
    counter: tuple[EvidenceGroup, ...]
    contradictions: tuple[Contradiction, ...]
    requirements: tuple[Requirement, ...]
    invalidations: tuple[Invalidation, ...]
    unavailable: tuple[EvidenceGroup, ...]
    """Groups that could not be measured, kept visible rather than dropped."""

    quality: SetupQuality | None
    """``None`` for the neutral case: §18's model scores the coherence of a
    *directional* case, and there is no bullish-or-bearish evidence to weigh
    for neutral. Its support is described by the readings instead."""

    entry: EntryQuality | None
    reason: str

    @property
    def unmet_requirements(self) -> tuple[Requirement, ...]:
        """Evaluated, and not satisfied."""
        return tuple(item for item in self.requirements if item.status is RequirementStatus.UNMET)

    @property
    def unknown_requirements(self) -> tuple[Requirement, ...]:
        """Could not be evaluated. Not the same as unsatisfied."""
        return tuple(item for item in self.requirements if item.status is RequirementStatus.UNKNOWN)

    @property
    def outstanding_requirements(self) -> tuple[Requirement, ...]:
        """Everything still standing between this case and CONFIRMED.

        Unmet *and* unknown, because an unevaluable requirement is not a met
        one - treating it as satisfied is how a case would confirm itself on
        evidence nobody could see. This is the set the state machine reads, so
        the state and the requirement list can never disagree with each other.
        """
        return tuple(item for item in self.requirements if not item.is_met)


@dataclass(frozen=True, slots=True)
class ScenarioSet:
    """The three cases, side by side and deliberately not normalised."""

    bull: Scenario
    bear: Scenario
    neutral: Scenario

    @property
    def scenarios(self) -> tuple[Scenario, ...]:
        return (self.bull, self.bear, self.neutral)

    def case(self, case: ScenarioCase) -> Scenario:
        return {
            ScenarioCase.BULL: self.bull,
            ScenarioCase.BEAR: self.bear,
            ScenarioCase.NEUTRAL: self.neutral,
        }[case]


@dataclass(frozen=True, slots=True)
class ScenarioConfig:
    quality: QualityConfig = field(default_factory=QualityConfig)
    entry: EntryConfig = field(default_factory=EntryConfig)


def build_scenarios(
    views: MultiTimeframeView,
    fused: FusedEvidence,
    config: ScenarioConfig | None = None,
) -> ScenarioSet:
    """Build all three cases from one fused evidence picture."""
    settings = config if config is not None else ScenarioConfig()
    return ScenarioSet(
        bull=_directional(views, fused, EvidenceDirection.BULLISH, settings),
        bear=_directional(views, fused, EvidenceDirection.BEARISH, settings),
        neutral=_neutral(fused),
    )


def _directional(
    views: MultiTimeframeView,
    fused: FusedEvidence,
    direction: EvidenceDirection,
    config: ScenarioConfig,
) -> Scenario:
    case = ScenarioCase.BULL if direction is EvidenceDirection.BULLISH else ScenarioCase.BEAR
    supporting = fused.supporting(direction)
    counter = fused.opposing(direction)
    unavailable = tuple(
        group for group in fused.groups if group.direction is EvidenceDirection.UNAVAILABLE
    )
    requirements = _requirements(fused, direction)
    quality = score_setup(fused, direction, config.quality)
    entry = score_entry(views, fused, direction, config.entry)
    relevant = _relevant_contradictions(fused, direction)

    state, reason = _state(fused, direction, supporting, requirements)
    return Scenario(
        case=case,
        state=state,
        supporting=supporting,
        counter=counter,
        contradictions=relevant,
        requirements=requirements,
        invalidations=_invalidations(direction),
        unavailable=unavailable,
        quality=quality,
        entry=entry,
        reason=reason,
    )


def _state(
    fused: FusedEvidence,
    direction: EvidenceDirection,
    supporting: tuple[EvidenceGroup, ...],
    requirements: tuple[Requirement, ...],
) -> tuple[ScenarioState, str]:
    """Where the case stands, decided in a fixed order.

    Unavailability is checked first, because a case that cannot be judged must
    not fall through to INACTIVE and read as "measured and absent".
    """
    bias = fused.contradictions.reading_for(TimeframeRole.BIAS)
    if bias is None:
        return (
            ScenarioState.UNAVAILABLE,
            "no directional-bias timeframe was supplied, so this case cannot be judged",
        )
    if bias.direction is EvidenceDirection.UNAVAILABLE:
        return (
            ScenarioState.UNAVAILABLE,
            f"the {bias.timeframe.value} bias could not be read",
        )
    if not supporting:
        return (ScenarioState.INACTIVE, "no evidence group supports this case")
    if bias.direction is not direction:
        return (
            ScenarioState.FORMING,
            (
                f"some evidence supports this case, but the {bias.timeframe.value} bias reads "
                f"{bias.direction.value.lower()}"
            ),
        )

    outstanding = [item for item in requirements if not item.is_met]
    if outstanding:
        return (
            ScenarioState.WAITING_FOR_CONFIRMATION,
            "waiting on: " + ", ".join(item.code.value for item in outstanding),
        )
    return (ScenarioState.CONFIRMED, "every evaluable confirmation requirement is met")


def _requirements(fused: FusedEvidence, direction: EvidenceDirection) -> tuple[Requirement, ...]:
    items: list[Requirement] = []

    for code, role in (
        (RequirementCode.BIAS_TIMEFRAME_AGREES, TimeframeRole.BIAS),
        (RequirementCode.SETUP_TIMEFRAME_AGREES, TimeframeRole.SETUP),
        (RequirementCode.ENTRY_TIMEFRAME_AGREES, TimeframeRole.ENTRY),
    ):
        reading = fused.contradictions.reading_for(role)
        if reading is None or reading.direction is EvidenceDirection.UNAVAILABLE:
            items.append(
                Requirement(
                    code=code,
                    status=RequirementStatus.UNKNOWN,
                    reason=f"the {role.value} timeframe produced no reading",
                )
            )
        elif reading.direction is direction:
            items.append(
                Requirement(
                    code=code,
                    status=RequirementStatus.MET,
                    reason=f"{reading.timeframe.value} reads {direction.value.lower()}",
                )
            )
        else:
            items.append(
                Requirement(
                    code=code,
                    status=RequirementStatus.UNMET,
                    reason=(f"{reading.timeframe.value} reads {reading.direction.value.lower()}"),
                )
            )

    major = [
        item
        for item in fused.contradictions.contradictions
        if item.severity is ContradictionSeverity.MAJOR
    ]
    items.append(
        Requirement(
            code=RequirementCode.NO_MAJOR_CONTRADICTION,
            status=RequirementStatus.MET if not major else RequirementStatus.UNMET,
            reason=(
                "no major contradiction is present"
                if not major
                else f"{len(major)} major contradiction(s) present"
            ),
        )
    )

    structure = [
        group
        for group in fused.groups
        if group.category is EvidenceCategory.STRUCTURE and group.direction is direction
    ]
    any_structure = [
        group for group in fused.groups if group.category is EvidenceCategory.STRUCTURE
    ]
    items.append(
        Requirement(
            code=RequirementCode.STRUCTURE_SUPPORTS,
            status=(
                RequirementStatus.UNKNOWN
                if not any_structure
                else RequirementStatus.MET
                if structure
                else RequirementStatus.UNMET
            ),
            reason=(
                "no structural reading is available"
                if not any_structure
                else f"{len(structure)} timeframe(s) show supporting structure"
                if structure
                else "no timeframe shows structure supporting this case"
            ),
        )
    )

    resolved = [
        group
        for group in fused.groups
        if group.category in (EvidenceCategory.BREAKOUT, EvidenceCategory.RETEST)
        and group.direction is direction
    ]
    present = [
        group
        for group in fused.groups
        if group.category in (EvidenceCategory.BREAKOUT, EvidenceCategory.RETEST)
    ]
    items.append(
        Requirement(
            code=RequirementCode.BREAKOUT_OR_RETEST_RESOLVED,
            status=(
                RequirementStatus.UNKNOWN
                if not present
                else RequirementStatus.MET
                if resolved
                else RequirementStatus.UNMET
            ),
            reason=(
                "no breakout or retest has resolved on any timeframe"
                if not present
                else "a resolved breakout or retest supports this case"
                if resolved
                else "the resolved breakouts and retests do not support this case"
            ),
        )
    )
    return tuple(items)


def _relevant_contradictions(
    fused: FusedEvidence, direction: EvidenceDirection
) -> tuple[Contradiction, ...]:
    """Every contradiction is shown on both directional cases.

    A timeframe conflict is a fact about the market rather than about one
    side's argument, and hiding it from whichever case it inconveniences is
    exactly what §17 forbids.
    """
    del direction
    return fused.contradictions.contradictions


def _invalidations(direction: EvidenceDirection) -> tuple[Invalidation, ...]:
    opposite = direction.opposite.value.lower()
    return (
        Invalidation(
            code=InvalidationCode.OPPOSING_CHOCH_ON_BIAS,
            description=(f"a confirmed {opposite} change of character on the bias timeframe"),
        ),
        Invalidation(
            code=InvalidationCode.BIAS_TIMEFRAME_REVERSES,
            description=f"the bias timeframe reading turning {opposite}",
        ),
        Invalidation(
            code=InvalidationCode.REGIME_TURNS_AGAINST,
            description=f"the regime timeframe classifying a {opposite} trend",
        ),
        Invalidation(
            code=InvalidationCode.OPPOSING_CONFIRMED_BREAKOUT,
            description=f"a confirmed {opposite} breakout of a zone on any timeframe",
        ),
    )


def _neutral(fused: FusedEvidence) -> Scenario:
    """The case that the market favours neither side.

    Deliberately not scored by §18's model: that model weighs the coherence of
    a directional argument, and applying it to "no direction" would produce a
    number about the wrong question. Neutral is described by the readings and
    the range regimes instead.
    """
    readings = fused.contradictions.readings
    if not readings:
        return Scenario(
            case=ScenarioCase.NEUTRAL,
            state=ScenarioState.UNAVAILABLE,
            supporting=(),
            counter=(),
            contradictions=fused.contradictions.contradictions,
            requirements=(),
            invalidations=(
                Invalidation(
                    code=InvalidationCode.RANGE_BREAKS_EITHER_WAY,
                    description="a confirmed breakout of the range in either direction",
                ),
            ),
            unavailable=tuple(
                group for group in fused.groups if group.direction is EvidenceDirection.UNAVAILABLE
            ),
            quality=None,
            entry=None,
            reason="no timeframe produced a reading",
        )

    neutral_readings = [
        reading for reading in readings if reading.direction is EvidenceDirection.NEUTRAL
    ]
    directional_readings = [reading for reading in readings if reading.is_directional]
    supporting = fused.supporting(EvidenceDirection.NEUTRAL)

    if neutral_readings and not directional_readings:
        state = ScenarioState.CONFIRMED
        reason = (
            f"every timeframe that could be read ({len(neutral_readings)}) favours neither side"
        )
    elif neutral_readings and directional_readings:
        state = ScenarioState.FORMING
        reason = (
            f"{len(neutral_readings)} timeframe(s) favour neither side while "
            f"{len(directional_readings)} read directionally"
        )
    else:
        state = ScenarioState.INACTIVE
        reason = "every timeframe reading is directional"

    return Scenario(
        case=ScenarioCase.NEUTRAL,
        state=state,
        supporting=supporting,
        counter=tuple(group for group in fused.groups if group.is_directional),
        contradictions=fused.contradictions.contradictions,
        requirements=(),
        invalidations=(
            Invalidation(
                code=InvalidationCode.RANGE_BREAKS_EITHER_WAY,
                description="a confirmed breakout of the range in either direction",
            ),
        ),
        unavailable=tuple(
            group for group in fused.groups if group.direction is EvidenceDirection.UNAVAILABLE
        ),
        quality=None,
        entry=None,
        reason=reason,
    )
