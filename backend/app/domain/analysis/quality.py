"""Heuristic Setup Quality, 0-100 (master spec §18, §19).

**This is not a probability, and the type is named so that saying otherwise
takes effort.** §19 is explicit: an analysis score may not be called a
probability, a win rate or an edge until it has been calibrated against
recorded outcomes, and nothing in this project has been. The label carried on
every result is `HEURISTIC SETUP QUALITY`, and a test forbids the fields that
would invite the other reading.

What the number means: *how coherent the evidence for this direction is*. A
bullish quality of 80 says the bullish case hangs together - trend, structure,
regime and levels agree, across timeframes, with few contradictions. It says
nothing whatever about what price will do.

Bull and bear qualities are computed **independently** and do not sum to 100.
Bull 35 with bear 38 is a perfectly ordinary result meaning "neither case is
coherent", and inventing a 27-point neutral remainder would manufacture a
distribution out of two unrelated measurements.

## Scoring model

Each component has a fixed weight, draws from a fixed set of evidence
categories, and is **capped at its weight**. That cap is the whole defence
against correlated evidence, and it works on two different problems:

*Repetition inside a category.* Fusion already collapsed seven divergences
into one group, and components read groups, so the count of records cannot
reach the score at all.

*Shared inputs across categories.* The regime is derived partly from the EMA
stack and partly from the structure bias, so TREND, STRUCTURE and REGIME are
genuinely not three independent confirmations. No correlation model is used -
that would be false precision. Instead each of the three is capped separately
and REGIME is weighted **below** what its apparent importance would suggest,
precisely because most of what it knows has already been counted by the other
two. The weights are documented here as project heuristics, and a test proves
that duplicating evidence cannot move the total.

## Missing components

A component with no evidence available is `UNAVAILABLE` and is excluded from
**both** the numerator and the denominator, so absent data is not scored as
failure. It is not free either: `DATA_AVAILABILITY` is itself a component,
scored on how much of the model could be evaluated, so a setup measured on a
quarter of the evidence cannot reach the same score as one measured on all of
it. The denominator convention is published on every result as
``available_weight``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, unique

from app.domain.analysis.contradictions import ContradictionSeverity
from app.domain.analysis.evidence import (
    EvidenceCategory,
    EvidenceDirection,
    EvidenceStrength,
)
from app.domain.analysis.fusion import EvidenceGroup, FusedEvidence
from app.domain.analysis.timeframes import ROLES_BROADEST_FIRST, TimeframeRole

QUALITY_LABEL = "HEURISTIC SETUP QUALITY"
SCORING_METHOD_VERSION = "setup-quality/1"


@unique
class QualityComponent(StrEnum):
    """The parts of the model. Each reads a fixed, documented set of groups."""

    TREND_ALIGNMENT = "TREND_ALIGNMENT"
    MARKET_STRUCTURE = "MARKET_STRUCTURE"
    MOMENTUM = "MOMENTUM"
    VOLUME = "VOLUME"
    SUPPORT_RESISTANCE = "SUPPORT_RESISTANCE"

    TIMEFRAME_ALIGNMENT = "TIMEFRAME_ALIGNMENT"
    """Whether the timeframes that *could be read* agree with each other.

    A relation between readings, so it needs at least two of them. With fewer
    it is UNAVAILABLE - see ``_timeframe_alignment``."""

    TIMEFRAME_COVERAGE = "TIMEFRAME_COVERAGE"
    """How much of the role hierarchy was supplied at all.

    Kept separate from alignment on purpose: agreement and completeness are
    different facts, and folding the second into the first would report
    missing timeframes as partial disagreement."""

    REGIME_SUITABILITY = "REGIME_SUITABILITY"
    CONTRADICTION_BURDEN = "CONTRADICTION_BURDEN"
    DATA_AVAILABILITY = "DATA_AVAILABILITY"


@unique
class ComponentAvailability(StrEnum):
    """Whether a component could be evaluated at all."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    """No evidence of the kinds this component reads. Excluded from the score
    rather than counted as zero - see the module docstring."""


@dataclass(frozen=True, slots=True)
class QualityWeights:
    """Component maxima.

    **DEFAULT POLICY, NOT MARKET FACT.** Every number below is a project
    convention chosen by this repository. None of them is an exchange fact, a
    fitted parameter, a calibrated coefficient or a probability, and nothing
    in the codebase treats them as any of those. They are defaults precisely
    so a caller can disagree: pass a `QualityWeights` of your own and the
    component contributions change accordingly, which a test proves rather
    than asserts.

    The reasoning behind the relative sizes:

    * `TIMEFRAME_ALIGNMENT` is the largest single component because §10 makes
      agreement across the hierarchy the central question.
    * `TIMEFRAME_COVERAGE` sits beside it so that *how much of the hierarchy
      exists* is scored separately from *whether it agrees*.
    * `TREND_ALIGNMENT` and `MARKET_STRUCTURE` are next, and equal.
    * `REGIME_SUITABILITY` is deliberately small. The regime is computed *from*
      the EMA stack and the structure bias among other things, so most of what
      it contributes has already been counted above. Keeping it below both is
      the correlated-evidence protection, and a test pins that ordering for the
      defaults.
    * `MOMENTUM`, `VOLUME` and `SUPPORT_RESISTANCE` are supporting context.
    * `CONTRADICTION_BURDEN` is scored, not subtracted, so it cannot drive the
      total negative and is visible as its own line.
    * `DATA_AVAILABILITY` makes the coverage of the other components part of
      the answer instead of a footnote.

    The defaults sum to 100, which is convenient rather than required: the
    score normalises over the weight actually available, and a custom profile
    summing to anything positive works identically.
    """

    trend_alignment: int = 15
    market_structure: int = 15
    momentum: int = 8
    volume: int = 8
    support_resistance: int = 10
    timeframe_alignment: int = 16
    timeframe_coverage: int = 8
    regime_suitability: int = 8
    contradiction_burden: int = 8
    data_availability: int = 4

    def __post_init__(self) -> None:
        for component in QualityComponent:
            weight = self.weight_for(component)
            if weight < 0:
                raise ValueError(f"{component.value} weight must not be negative, got {weight}")
        if self.total == 0:
            raise ValueError("at least one component must carry weight")

    def weight_for(self, component: QualityComponent) -> int:
        return {
            QualityComponent.TREND_ALIGNMENT: self.trend_alignment,
            QualityComponent.MARKET_STRUCTURE: self.market_structure,
            QualityComponent.MOMENTUM: self.momentum,
            QualityComponent.VOLUME: self.volume,
            QualityComponent.SUPPORT_RESISTANCE: self.support_resistance,
            QualityComponent.TIMEFRAME_ALIGNMENT: self.timeframe_alignment,
            QualityComponent.TIMEFRAME_COVERAGE: self.timeframe_coverage,
            QualityComponent.REGIME_SUITABILITY: self.regime_suitability,
            QualityComponent.CONTRADICTION_BURDEN: self.contradiction_burden,
            QualityComponent.DATA_AVAILABILITY: self.data_availability,
        }[component]

    @property
    def total(self) -> int:
        return sum(self.weight_for(component) for component in QualityComponent)


@dataclass(frozen=True, slots=True)
class QualityConfig:
    """Every Setup Quality setting in one frozen object."""

    weights: QualityWeights = field(default_factory=QualityWeights)

    major_contradiction_cost: int = 3
    moderate_contradiction_cost: int = 2
    minor_contradiction_cost: int = 1
    """Points deducted from the contradiction component per **distinct**
    contradiction. Distinct means one per (type, roles) pair: the same logical
    conflict reported against several evidence rows is one conflict, and
    charging it repeatedly would let a fixture that generates many correlated
    records crush the score."""

    minimum_roles_for_alignment: int = 2
    """How many readable timeframes alignment needs before it means anything.

    Two, because alignment is a relation. One timeframe has nothing to align
    with, and scoring it partially aligned would be a measurement of a relation
    that does not exist. Configurable, but raising it above two only makes the
    component stricter about when it will speak."""


@dataclass(frozen=True, slots=True)
class ComponentScore:
    """One component's contribution, with everything needed to audit it."""

    component: QualityComponent
    availability: ComponentAvailability
    weight: int
    """The component's maximum. Awarded points can never exceed it."""

    awarded: int | None
    """``None`` when UNAVAILABLE - not zero, which would read as measured and
    bad rather than not measured."""

    reason: str
    groups: tuple[EvidenceGroup, ...] = ()
    """The fused groups this component read, so the number can be traced back
    to the observations that produced it."""

    @property
    def is_available(self) -> bool:
        return self.availability is ComponentAvailability.AVAILABLE


@dataclass(frozen=True, slots=True)
class SetupQuality:
    """A heuristic coherence score for one direction's case.

    Read the class docstring of this module before reading the number.
    """

    direction: EvidenceDirection
    score: int
    """0-100, normalised over the **available** weight only."""

    components: tuple[ComponentScore, ...]
    available_weight: int
    """The denominator actually used. Published so the normalisation is never
    something a reader has to reconstruct."""

    total_weight: int
    """What the denominator would have been with every component available."""

    label: str = QUALITY_LABEL
    method_version: str = SCORING_METHOD_VERSION

    @property
    def coverage(self) -> float:
        """Share of the model that could be evaluated, 0.0-1.0."""
        return self.available_weight / self.total_weight if self.total_weight else 0.0

    @property
    def unavailable_components(self) -> tuple[QualityComponent, ...]:
        return tuple(score.component for score in self.components if not score.is_available)

    def component(self, component: QualityComponent) -> ComponentScore | None:
        for score in self.components:
            if score.component is component:
                return score
        return None


def score_setup(
    fused: FusedEvidence,
    direction: EvidenceDirection,
    config: QualityConfig | None = None,
) -> SetupQuality:
    """Score how coherent the case for ``direction`` is.

    ``direction`` must be BULLISH or BEARISH: there is no coherent "case for
    neutral" to score here, and the neutral scenario is handled by
    ``scenarios.py`` from the readings rather than by this model.
    """
    if not direction.is_directional:
        raise ValueError(f"setup quality is scored for a directional case, got {direction.value}")
    settings = config if config is not None else QualityConfig()
    weights = settings.weights

    scores = [
        _category_component(
            fused,
            direction,
            QualityComponent.TREND_ALIGNMENT,
            (EvidenceCategory.TREND,),
            weights.trend_alignment,
        ),
        _category_component(
            fused,
            direction,
            QualityComponent.MARKET_STRUCTURE,
            (EvidenceCategory.STRUCTURE,),
            weights.market_structure,
        ),
        _category_component(
            fused,
            direction,
            QualityComponent.MOMENTUM,
            (EvidenceCategory.MOMENTUM, EvidenceCategory.INTRADAY),
            weights.momentum,
        ),
        _category_component(
            fused,
            direction,
            QualityComponent.VOLUME,
            (EvidenceCategory.VOLUME, EvidenceCategory.DIVERGENCE),
            weights.volume,
        ),
        _category_component(
            fused,
            direction,
            QualityComponent.SUPPORT_RESISTANCE,
            (EvidenceCategory.LEVEL, EvidenceCategory.BREAKOUT, EvidenceCategory.RETEST),
            weights.support_resistance,
        ),
        _timeframe_alignment(
            fused,
            direction,
            weights.timeframe_alignment,
            settings.minimum_roles_for_alignment,
        ),
        _timeframe_coverage(fused, weights.timeframe_coverage),
        _regime_suitability(fused, direction, weights.regime_suitability),
        _contradiction_burden(fused, settings),
    ]
    scores.append(_data_availability(tuple(scores), weights.data_availability))

    available = tuple(score for score in scores if score.is_available)
    available_weight = sum(score.weight for score in available)
    awarded = sum(score.awarded or 0 for score in available)
    total = round(100 * awarded / available_weight) if available_weight else 0

    return SetupQuality(
        direction=direction,
        score=total,
        components=tuple(scores),
        available_weight=available_weight,
        total_weight=weights.total,
    )


# ----------------------------------------------------------------------
# Components
# ----------------------------------------------------------------------

_STRENGTH_SHARE: dict[EvidenceStrength, float] = {
    EvidenceStrength.WEAK: 0.4,
    EvidenceStrength.MODERATE: 0.7,
    EvidenceStrength.STRONG: 1.0,
}
"""Share of a component's weight earned by a supporting group of each
strength. Ordinal input, fixed mapping, no arithmetic on the enum itself."""


def _category_component(
    fused: FusedEvidence,
    direction: EvidenceDirection,
    component: QualityComponent,
    categories: tuple[EvidenceCategory, ...],
    weight: int,
) -> ComponentScore:
    """Score one component from the categories it owns.

    Each *category* contributes at most once per role, because it arrives as
    one fused group however many records it holds. Supporting groups earn a
    share of the weight; opposing groups on the same categories subtract from
    it. The result is clamped to the component's weight in both directions -
    which is where correlated evidence stops being able to inflate anything.
    """
    relevant = tuple(group for group in fused.groups if group.category in categories)
    if not relevant:
        return ComponentScore(
            component=component,
            availability=ComponentAvailability.UNAVAILABLE,
            weight=weight,
            awarded=None,
            reason=(
                f"no {', '.join(category.value for category in categories)} evidence was "
                "available on any timeframe"
            ),
        )

    graded = tuple(group for group in relevant if group.is_directional)
    if not graded:
        return ComponentScore(
            component=component,
            availability=ComponentAvailability.AVAILABLE,
            weight=weight,
            awarded=0,
            reason="the evidence was measured and points neither way",
            groups=relevant,
        )

    supporting = tuple(group for group in graded if group.direction is direction)
    opposing = tuple(group for group in graded if group.direction is not direction)

    support = _share(supporting)
    against = _share(opposing)
    awarded = _clamp(round(weight * (support - against)), weight)

    return ComponentScore(
        component=component,
        availability=ComponentAvailability.AVAILABLE,
        weight=weight,
        awarded=awarded,
        reason=(
            f"{len(supporting)} supporting and {len(opposing)} opposing group(s) across "
            f"{len({group.role for group in graded})} timeframe(s)"
        ),
        groups=relevant,
    )


def _share(groups: tuple[EvidenceGroup, ...]) -> float:
    """The fraction of a component's weight a set of groups earns.

    The **strongest** group sets the base; each additional group of a
    *different* category adds a diminishing amount and the total is capped at
    1.0. Repetition of the same category cannot add anything, because fusion
    already made it one group.
    """
    if not groups:
        return 0.0
    ranked = sorted(
        groups,
        key=lambda group: _STRENGTH_SHARE[group.strength] if group.strength else 0.0,
        reverse=True,
    )
    share = _STRENGTH_SHARE[ranked[0].strength] if ranked[0].strength else 0.0
    seen = {ranked[0].category}
    for group in ranked[1:]:
        if group.category in seen:
            continue
        seen.add(group.category)
        share += 0.15
    return min(share, 1.0)


_ROLE_SHARE: dict[TimeframeRole, float] = {
    TimeframeRole.REGIME: 0.35,
    TimeframeRole.BIAS: 0.35,
    TimeframeRole.SETUP: 0.20,
    TimeframeRole.ENTRY: 0.10,
}
"""Each role's share of the hierarchy. **Project policy, not market fact.**

Unequal by design: §10 forbids treating timeframes equally. Used by both the
alignment and the coverage components so the two always describe the same
hierarchy.
"""


def _timeframe_alignment(
    fused: FusedEvidence,
    direction: EvidenceDirection,
    weight: int,
    minimum_roles: int,
) -> ComponentScore:
    """Whether the timeframes that could be read agree - never averaged.

    §10 forbids treating timeframes equally, so each role carries its own
    share and the shares are unequal by design: the regime and bias roles
    together hold most of the weight, and the entry role holds least because
    §10 reads a lower timeframe moving the other way as timing rather than
    direction.

    **Alignment is a relation, so it needs at least two readings.** Below that
    the component is UNAVAILABLE and says so. Two earlier drafts both got this
    wrong in the same way, from opposite directions: the first normalised over
    the readable roles and gave a lone daily view full marks for agreeing with
    itself; the second scored against the whole hierarchy and gave it a
    *partial* mark, which still claims to have measured a relation that does
    not exist. A missing timeframe is neither agreement nor disagreement.

    Missing roles are therefore excluded from this component entirely and are
    scored by `TIMEFRAME_COVERAGE` instead, so that "how much do we have" never
    disguises itself as "how much agrees".
    """
    share = _ROLE_SHARE
    readable = 0.0
    earned = 0.0
    agreeing: list[str] = []
    disagreeing: list[str] = []
    readable_roles: list[str] = []
    for role in ROLES_BROADEST_FIRST:
        reading = fused.contradictions.reading_for(role)
        if reading is None or reading.direction is EvidenceDirection.UNAVAILABLE:
            continue
        readable += share[role]
        readable_roles.append(role.value)
        if reading.direction is direction:
            earned += share[role]
            agreeing.append(role.value)
        elif reading.direction is EvidenceDirection.NEUTRAL:
            earned += share[role] * 0.5
        else:
            disagreeing.append(role.value)

    if len(readable_roles) < minimum_roles:
        return ComponentScore(
            component=QualityComponent.TIMEFRAME_ALIGNMENT,
            availability=ComponentAvailability.UNAVAILABLE,
            weight=weight,
            awarded=None,
            reason=(
                f"alignment is a relation between timeframes and needs at least "
                f"{minimum_roles} readable readings; {len(readable_roles)} "
                f"({', '.join(readable_roles) or 'none'}) could be read, so agreement "
                "cannot be measured - this is neither agreement nor disagreement"
            ),
        )

    return ComponentScore(
        component=QualityComponent.TIMEFRAME_ALIGNMENT,
        availability=ComponentAvailability.AVAILABLE,
        weight=weight,
        awarded=_clamp(round(weight * earned / readable), weight),
        reason=(
            f"of the {len(readable_roles)} readable timeframe(s) "
            f"({', '.join(readable_roles)}), agreeing with {direction.value.lower()}: "
            f"{', '.join(agreeing) or 'none'}; disagreeing: "
            f"{', '.join(disagreeing) or 'none'}. Scored over the readable roles by role "
            "weight, never averaged; absent roles are scored by TIMEFRAME_COVERAGE"
        ),
    )


def _timeframe_coverage(fused: FusedEvidence, weight: int) -> ComponentScore:
    """How much of the role hierarchy was actually supplied and readable.

    The other half of the split. Alignment answers "do the timeframes I have
    agree"; this answers "how many do I have", weighted by role so a missing
    1D costs more than a missing 5M.

    It exists so that excluding absent roles from the alignment component
    cannot quietly improve the score. Analysing one timeframe out of four
    leaves alignment unmeasurable *and* leaves this component low, which is the
    honest pair of statements.
    """
    share = _ROLE_SHARE
    readable = 0.0
    present: list[str] = []
    missing: list[str] = []
    for role in ROLES_BROADEST_FIRST:
        reading = fused.contradictions.reading_for(role)
        if reading is None or reading.direction is EvidenceDirection.UNAVAILABLE:
            missing.append(role.value)
            continue
        readable += share[role]
        present.append(role.value)

    return ComponentScore(
        component=QualityComponent.TIMEFRAME_COVERAGE,
        availability=ComponentAvailability.AVAILABLE,
        weight=weight,
        awarded=_clamp(round(weight * readable), weight),
        reason=(
            f"{len(present)} of {len(ROLES_BROADEST_FIRST)} roles readable "
            f"({', '.join(present) or 'none'}), {readable:.0%} of the hierarchy by role "
            f"weight" + (f"; missing or unreadable: {', '.join(missing)}" if missing else "")
        ),
    )


_FAVOURABLE_REGIME_DIRECTION = "a trending regime in the same direction"


def _regime_suitability(
    fused: FusedEvidence, direction: EvidenceDirection, weight: int
) -> ComponentScore:
    """Whether the regime suits a directional setup at all.

    Small by design: the regime is derived partly from the EMA stack and the
    structure bias, both already scored, so this must not re-award what those
    components have counted.
    """
    groups = tuple(group for group in fused.groups if group.category is EvidenceCategory.REGIME)
    if not groups:
        return ComponentScore(
            component=QualityComponent.REGIME_SUITABILITY,
            availability=ComponentAvailability.UNAVAILABLE,
            weight=weight,
            awarded=None,
            reason="no regime classification was available",
        )

    supporting = tuple(group for group in groups if group.direction is direction)
    opposing = tuple(
        group
        for group in groups
        if group.direction.is_directional and group.direction is not direction
    )
    if supporting and not opposing:
        awarded, note = weight, _FAVOURABLE_REGIME_DIRECTION
    elif opposing and not supporting:
        awarded, note = 0, "the regime trends against this case"
    elif supporting and opposing:
        awarded, note = round(weight * 0.3), "regimes differ between timeframes"
    else:
        awarded, note = round(weight * 0.4), "a non-trending regime neither helps nor blocks"

    return ComponentScore(
        component=QualityComponent.REGIME_SUITABILITY,
        availability=ComponentAvailability.AVAILABLE,
        weight=weight,
        awarded=_clamp(awarded, weight),
        reason=note,
        groups=groups,
    )


def _contradiction_burden(fused: FusedEvidence, config: QualityConfig) -> ComponentScore:
    """Full marks with no conflicts, falling as **distinct** conflicts appear.

    Distinctness is the point. A single logical disagreement recorded against
    a dozen correlated evidence rows is one conflict here, so a fixture that
    produces a divergence at every swing cannot flatten the score through
    repetition. Scored as a component rather than subtracted from the total,
    so it is visible and cannot push the result negative.
    """
    weight = config.weights.contradiction_burden
    cost_of = {
        ContradictionSeverity.MAJOR: config.major_contradiction_cost,
        ContradictionSeverity.MODERATE: config.moderate_contradiction_cost,
        ContradictionSeverity.MINOR: config.minor_contradiction_cost,
    }

    distinct: dict[tuple[str, tuple[str, ...]], ContradictionSeverity] = {}
    for item in fused.contradictions.contradictions:
        key = (item.contradiction_type.value, tuple(role.value for role in item.roles))
        existing = distinct.get(key)
        if existing is None or item.severity.rank > existing.rank:
            distinct[key] = item.severity

    charged = sum(cost_of[severity] for severity in distinct.values())
    return ComponentScore(
        component=QualityComponent.CONTRADICTION_BURDEN,
        availability=ComponentAvailability.AVAILABLE,
        weight=weight,
        awarded=_clamp(weight - charged, weight),
        reason=(
            f"{len(distinct)} distinct contradiction(s) charged {charged} of {weight} "
            f"points, counted once per conflict rather than once per evidence record"
        ),
    )


def _data_availability(scores: tuple[ComponentScore, ...], weight: int) -> ComponentScore:
    """How much of the model could be evaluated.

    This is what stops the exclude-from-the-denominator convention from making
    missing data free: a setup measured on a quarter of the evidence keeps a
    fair score on what was measured, and loses here for what was not.
    """
    measurable = sum(score.weight for score in scores)
    available = sum(score.weight for score in scores if score.is_available)
    coverage = available / measurable if measurable else 0.0
    missing = [score.component.value for score in scores if not score.is_available]

    return ComponentScore(
        component=QualityComponent.DATA_AVAILABILITY,
        availability=ComponentAvailability.AVAILABLE,
        weight=weight,
        awarded=_clamp(round(weight * coverage), weight),
        reason=(
            f"{available} of {measurable} component weight was evaluable"
            + (f"; unavailable: {', '.join(missing)}" if missing else "")
        ),
    )


def _clamp(value: int, weight: int) -> int:
    return max(0, min(value, weight))
