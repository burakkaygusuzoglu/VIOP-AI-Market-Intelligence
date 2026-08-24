"""Heuristic Entry Quality, 0-100 (master spec §26).

Deliberately a **separate** model from Setup Quality, because they answer
different questions and routinely disagree. Setup Quality asks whether the
case for a direction is coherent across the hierarchy; Entry Quality asks
whether *right now, on the entry timeframe*, is a favourable moment to act on
it. A coherent daily uptrend that has just run 4 ATR above its mean is a good
setup and a poor entry, and a model that averaged the two would hide exactly
that.

Same conventions as Setup Quality, for the same reasons: 0-100, labelled
`HEURISTIC ENTRY QUALITY`, never a probability, components capped at their
weights, unavailable components excluded from both numerator and denominator
with the coverage published.

**Nothing here invents a level.** No entry price, no stop, no target, no
trigger. §26 asks how favourable the context is, and the answer is built only
from readings that already exist. Where a trigger would be needed and none has
been established, the requirement is reported unavailable rather than filled
in with a plausible number.

**Risk/reward is not a component.** §18 separates setup quality from trade
suitability, and Phase 4A's import contract keeps `app.domain.analysis` clear
of `app.domain.risk` so that an account balance cannot reach an analytical
score. Risk/reward is a Phase 3 result about a proposed trade, so it belongs
to the suitability layer that consumes both - see ``app/domain/suitability``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, unique

from app.domain.analysis.evidence import EvidenceCategory, EvidenceDirection, EvidenceStrength
from app.domain.analysis.fusion import EvidenceGroup, FusedEvidence
from app.domain.analysis.quality import ComponentAvailability
from app.domain.analysis.timeframes import MultiTimeframeView, TimeframeRole, TimeframeView

ENTRY_LABEL = "HEURISTIC ENTRY QUALITY"
ENTRY_METHOD_VERSION = "entry-quality/1"


@unique
class EntryComponent(StrEnum):
    """The parts of the entry model, all read from the entry timeframe."""

    ENTRY_STRUCTURE = "ENTRY_STRUCTURE"
    LEVEL_PROXIMITY = "LEVEL_PROXIMITY"
    BREAKOUT_RETEST = "BREAKOUT_RETEST"
    VOLUME_CONFIRMATION = "VOLUME_CONFIRMATION"
    MOMENTUM_CONTEXT = "MOMENTUM_CONTEXT"
    VWAP_RELATIONSHIP = "VWAP_RELATIONSHIP"
    EXTENSION = "EXTENSION"


@dataclass(frozen=True, slots=True)
class EntryWeights:
    """Component maxima for the entry model.

    **DEFAULT POLICY, NOT MARKET FACT** - the same status as
    `QualityWeights`, and overridable the same way.

    `EXTENSION` carries real weight because §29 treats chasing an extended
    move as one of the more expensive beginner mistakes, and it is the one
    component that can be poor while every other reading is excellent.
    """

    entry_structure: int = 20
    level_proximity: int = 16
    breakout_retest: int = 16
    volume_confirmation: int = 12
    momentum_context: int = 12
    vwap_relationship: int = 8
    extension: int = 16

    def __post_init__(self) -> None:
        for component in EntryComponent:
            weight = self.weight_for(component)
            if weight < 0:
                raise ValueError(f"{component.value} weight must not be negative, got {weight}")
        if self.total == 0:
            raise ValueError("at least one entry component must carry weight")

    def weight_for(self, component: EntryComponent) -> int:
        return {
            EntryComponent.ENTRY_STRUCTURE: self.entry_structure,
            EntryComponent.LEVEL_PROXIMITY: self.level_proximity,
            EntryComponent.BREAKOUT_RETEST: self.breakout_retest,
            EntryComponent.VOLUME_CONFIRMATION: self.volume_confirmation,
            EntryComponent.MOMENTUM_CONTEXT: self.momentum_context,
            EntryComponent.VWAP_RELATIONSHIP: self.vwap_relationship,
            EntryComponent.EXTENSION: self.extension,
        }[component]

    @property
    def total(self) -> int:
        return sum(self.weight_for(component) for component in EntryComponent)


@dataclass(frozen=True, slots=True)
class EntryConfig:
    """Every Entry Quality setting in one frozen object."""

    weights: EntryWeights = field(default_factory=EntryWeights)

    comfortable_extension_atr: float = 1.0
    """Distance of the close from the reference mean, in ATR, at or below
    which extension costs nothing."""

    extended_move_atr: float = 3.0
    """Distance at or beyond which the extension component scores zero. §29
    calls this an extended move; the number is a project heuristic, not a
    market fact, and it is used only to grade a measured distance."""

    extension_reference_ema: int = 20
    """Which EMA the extension is measured from. Read off the Phase 1
    snapshot; if the series does not carry it, extension is unavailable rather
    than estimated from something else."""


@dataclass(frozen=True, slots=True)
class EntryComponentScore:
    component: EntryComponent
    availability: ComponentAvailability
    weight: int
    awarded: int | None
    reason: str
    groups: tuple[EvidenceGroup, ...] = ()

    @property
    def is_available(self) -> bool:
        return self.availability is ComponentAvailability.AVAILABLE


@dataclass(frozen=True, slots=True)
class EntryQuality:
    """How favourable the current entry-timeframe context is. Not a forecast."""

    direction: EvidenceDirection
    score: int | None
    """``None`` when the entry timeframe is absent entirely - the question
    cannot be answered, and 0 would answer it badly."""

    components: tuple[EntryComponentScore, ...]
    available_weight: int
    total_weight: int
    reason: str
    label: str = ENTRY_LABEL
    method_version: str = ENTRY_METHOD_VERSION

    @property
    def is_available(self) -> bool:
        return self.score is not None

    @property
    def coverage(self) -> float:
        return self.available_weight / self.total_weight if self.total_weight else 0.0

    def component(self, component: EntryComponent) -> EntryComponentScore | None:
        for score in self.components:
            if score.component is component:
                return score
        return None


def score_entry(
    views: MultiTimeframeView,
    fused: FusedEvidence,
    direction: EvidenceDirection,
    config: EntryConfig | None = None,
) -> EntryQuality:
    """Score the entry context for ``direction`` on the entry timeframe."""
    if not direction.is_directional:
        raise ValueError(f"entry quality is scored for a directional case, got {direction.value}")
    settings = config if config is not None else EntryConfig()
    weights = settings.weights

    view = views.view_for(TimeframeRole.ENTRY)
    if view is None:
        return EntryQuality(
            direction=direction,
            score=None,
            components=(),
            available_weight=0,
            total_weight=weights.total,
            reason=(
                f"no {views.policy.timeframe_for(TimeframeRole.ENTRY).value} view was supplied, "
                "so the entry context is unknown rather than poor"
            ),
        )

    scores = (
        _from_categories(
            fused,
            direction,
            EntryComponent.ENTRY_STRUCTURE,
            (EvidenceCategory.STRUCTURE,),
            weights.entry_structure,
        ),
        _from_categories(
            fused,
            direction,
            EntryComponent.LEVEL_PROXIMITY,
            (EvidenceCategory.LEVEL,),
            weights.level_proximity,
        ),
        _from_categories(
            fused,
            direction,
            EntryComponent.BREAKOUT_RETEST,
            (EvidenceCategory.BREAKOUT, EvidenceCategory.RETEST),
            weights.breakout_retest,
        ),
        _from_categories(
            fused,
            direction,
            EntryComponent.VOLUME_CONFIRMATION,
            (EvidenceCategory.VOLUME, EvidenceCategory.DIVERGENCE),
            weights.volume_confirmation,
        ),
        _from_categories(
            fused,
            direction,
            EntryComponent.MOMENTUM_CONTEXT,
            (EvidenceCategory.MOMENTUM,),
            weights.momentum_context,
        ),
        _from_categories(
            fused,
            direction,
            EntryComponent.VWAP_RELATIONSHIP,
            (EvidenceCategory.INTRADAY,),
            weights.vwap_relationship,
        ),
        _extension(view, settings),
    )

    available = tuple(score for score in scores if score.is_available)
    available_weight = sum(score.weight for score in available)
    awarded = sum(score.awarded or 0 for score in available)
    total = round(100 * awarded / available_weight) if available_weight else 0

    return EntryQuality(
        direction=direction,
        score=total,
        components=scores,
        available_weight=available_weight,
        total_weight=weights.total,
        reason=(
            f"scored on {view.timeframe.value} over {available_weight} of "
            f"{weights.total} component weight"
        ),
    )


_STRENGTH_SHARE: dict[EvidenceStrength, float] = {
    EvidenceStrength.WEAK: 0.4,
    EvidenceStrength.MODERATE: 0.7,
    EvidenceStrength.STRONG: 1.0,
}


def _from_categories(
    fused: FusedEvidence,
    direction: EvidenceDirection,
    component: EntryComponent,
    categories: tuple[EvidenceCategory, ...],
    weight: int,
) -> EntryComponentScore:
    """Score one component from entry-timeframe groups only.

    Restricted to `TimeframeRole.ENTRY` by construction: this model is about
    the entry context, and reading a higher timeframe here would re-award what
    Setup Quality has already counted.
    """
    groups = tuple(
        group for group in fused.groups_for(TimeframeRole.ENTRY) if group.category in categories
    )
    if not groups:
        return EntryComponentScore(
            component=component,
            availability=ComponentAvailability.UNAVAILABLE,
            weight=weight,
            awarded=None,
            reason=(
                f"no {', '.join(category.value for category in categories)} evidence on the "
                "entry timeframe"
            ),
        )

    graded = tuple(group for group in groups if group.is_directional)
    if not graded:
        return EntryComponentScore(
            component=component,
            availability=ComponentAvailability.AVAILABLE,
            weight=weight,
            awarded=round(weight * 0.5),
            reason="measured, and pointing neither way",
            groups=groups,
        )

    supporting = [group for group in graded if group.direction is direction]
    opposing = [group for group in graded if group.direction is not direction]
    support = max((_STRENGTH_SHARE[g.strength] for g in supporting if g.strength), default=0.0)
    against = max((_STRENGTH_SHARE[g.strength] for g in opposing if g.strength), default=0.0)

    return EntryComponentScore(
        component=component,
        availability=ComponentAvailability.AVAILABLE,
        weight=weight,
        awarded=max(0, min(round(weight * (support - against)), weight)),
        reason=f"{len(supporting)} supporting, {len(opposing)} opposing on the entry timeframe",
        groups=groups,
    )


def _extension(view: TimeframeView, config: EntryConfig) -> EntryComponentScore:
    """How far price has already travelled, in ATR (§29).

    Measured as the distance of the close from the reference EMA divided by
    ATR - both read off the finished Phase 1 snapshot, neither recomputed.
    The measure is direction-agnostic on purpose: being 4 ATR extended is a
    poor place to join, whichever way the move went.
    """
    weight = config.weights.extension
    index = view.last_index
    emas = view.technicals.ema.get(config.extension_reference_ema)
    reference = emas[index] if emas is not None and index < len(emas) else None
    atr_values = view.technicals.atr
    atr = atr_values[index] if index < len(atr_values) else None

    if reference is None or atr is None or atr <= 0:
        return EntryComponentScore(
            component=EntryComponent.EXTENSION,
            availability=ComponentAvailability.UNAVAILABLE,
            weight=weight,
            awarded=None,
            reason=(
                f"extension needs EMA{config.extension_reference_ema} and ATR; at least one is "
                "still in warm-up"
            ),
        )

    distance = abs(float(view.last_close) - reference) / atr
    if distance <= config.comfortable_extension_atr:
        awarded = weight
    elif distance >= config.extended_move_atr:
        awarded = 0
    else:
        span = config.extended_move_atr - config.comfortable_extension_atr
        awarded = round(weight * (config.extended_move_atr - distance) / span)

    return EntryComponentScore(
        component=EntryComponent.EXTENSION,
        availability=ComponentAvailability.AVAILABLE,
        weight=weight,
        awarded=max(0, min(awarded, weight)),
        reason=(
            f"the close sits {distance:.2f} ATR from EMA{config.extension_reference_ema} "
            f"(comfortable at or below {config.comfortable_extension_atr:.1f}, extended at or "
            f"beyond {config.extended_move_atr:.1f})"
        ),
    )
