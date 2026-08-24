"""Contradiction detection (master spec §17).

The whole point of this module is the distinction §17 and §10 draw together: a
lower timeframe moving against the higher ones is **usually a retracement**,
and calling it a conflict would fire a warning on every healthy trend. But a
1H directional structure opposing the 1D is a real disagreement about where the
market is going.

    1D bullish · 1H bullish · 15M bullish · 5M bearish   ->  normal pullback
    1D bullish · 1H bearish                              ->  major conflict

So the engine separates *contradictions* from *pullbacks* and reports both,
rather than netting them into a single number. §17 requires contradictions to
be visible; a score that quietly absorbs them is the failure mode this design
exists to avoid. Nothing here sums, averages or weights anything - each rule is
a named comparison between two directional readings, and every contradiction
carries the evidence that produced it.

**A missing timeframe is not agreement.** Rules referencing an absent role are
skipped and the role is reported as unavailable. Substituting neutral would let
a gap in the data silently satisfy a check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique

from app.domain.analysis.evidence import (
    EvidenceCategory,
    EvidenceDirection,
    EvidenceItem,
    EvidenceSource,
    EvidenceStrength,
    opposes,
)
from app.domain.analysis.timeframes import (
    ROLES_BROADEST_FIRST,
    MultiTimeframeView,
    TimeframeRole,
    TimeframeView,
)
from app.domain.common.enums import Direction, Timeframe
from app.domain.structure.breakouts import BreakoutEventType
from app.domain.structure.events import StructuralEventType
from app.domain.structure.regime import MarketRegime


@unique
class ContradictionType(StrEnum):
    """Named conflicts. Each is a specific comparison, not a score threshold."""

    HIGHER_TIMEFRAME_CONFLICT = "HIGHER_TIMEFRAME_CONFLICT"
    """The regime timeframe and the bias timeframe disagree about direction.
    The most serious case: the two slowest readings cannot both be right."""

    SETUP_AGAINST_BIAS = "SETUP_AGAINST_BIAS"
    """The setup timeframe opposes the directional bias. §17's worked example.
    Real, and less severe than a conflict between the two highest."""

    LOWER_TIMEFRAME_REVERSAL = "LOWER_TIMEFRAME_REVERSAL"
    """The entry timeframe opposes the higher ones by more than a retracement -
    a confirmed change of character against them, or an outright opposing
    trend regime. Only raised when the pullback exemption does not apply."""

    INTRA_TIMEFRAME_CONFLICT = "INTRA_TIMEFRAME_CONFLICT"
    """Inside one timeframe, the regime and the market structure point
    opposite ways. The reading is downgraded to neutral and the disagreement
    is reported rather than resolved by preferring one of them."""

    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
    """Inside one timeframe, substantial evidence exists on both sides -
    a bullish structure alongside a bearish divergence, say."""


@unique
class ContradictionSeverity(StrEnum):
    """How much the conflict matters. Ordinal, and not a probability."""

    MINOR = "MINOR"
    MODERATE = "MODERATE"
    MAJOR = "MAJOR"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK: dict[ContradictionSeverity, int] = {
    ContradictionSeverity.MINOR: 1,
    ContradictionSeverity.MODERATE: 2,
    ContradictionSeverity.MAJOR: 3,
}


@dataclass(frozen=True, slots=True)
class ContradictionConfig:
    """Project heuristics for the pullback/reversal boundary.

    Explicit, configurable and deterministic. They are conventions of this
    project, not exchange rules and not probabilities.
    """

    reversal_lookback: int = 10
    """How many candles back on the entry timeframe a change of character
    still counts as current evidence of a reversal rather than history."""

    conflicting_evidence_strength: EvidenceStrength = EvidenceStrength.MODERATE
    """Both sides must reach at least this strength before coexisting
    directional evidence inside one timeframe is reported as a conflict.
    Without a floor, every trend would conflict with its own pullbacks."""


@dataclass(frozen=True, slots=True)
class DirectionalReading:
    """What one timeframe says, and what it was derived from.

    Derived from exactly two sources - the Phase 2 regime and the Phase 2
    structure bias - and only where they agree. Where they disagree the
    reading is `NEUTRAL` and an `INTRA_TIMEFRAME_CONFLICT` is raised: picking a
    winner would hide a genuine disagreement inside a confident-looking answer.
    """

    role: TimeframeRole
    timeframe: Timeframe
    direction: EvidenceDirection
    regime_direction: EvidenceDirection
    structure_direction: EvidenceDirection
    reason: str
    evidence: tuple[EvidenceItem, ...]
    as_of: datetime

    @property
    def is_directional(self) -> bool:
        return self.direction.is_directional


@dataclass(frozen=True, slots=True)
class Contradiction:
    """One detected conflict, with everything a reader needs to check it."""

    contradiction_type: ContradictionType
    severity: ContradictionSeverity
    category: EvidenceCategory
    roles: tuple[TimeframeRole, ...]
    timeframes: tuple[Timeframe, ...]
    evidence: tuple[EvidenceItem, ...]
    reason: str
    confirmed_at: datetime | None
    """When the conflict became knowable: the latest confirmation among the
    evidence involved. A conflict is only as old as its newest half."""


@dataclass(frozen=True, slots=True)
class Pullback:
    """A lower timeframe moving against higher ones that agree.

    Recorded explicitly, and deliberately **not** a contradiction. §10 reads
    this as "higher timeframes remain bullish, lower timeframes are
    correcting, immediate timing is weak" - information about timing, not a
    disagreement about direction.
    """

    role: TimeframeRole
    timeframe: Timeframe
    against: EvidenceDirection
    reason: str
    as_of: datetime


@dataclass(frozen=True, slots=True)
class ContradictionReport:
    """Readings, conflicts and pullbacks, kept separate on purpose."""

    readings: tuple[DirectionalReading, ...]
    contradictions: tuple[Contradiction, ...]
    pullbacks: tuple[Pullback, ...]
    unavailable_roles: tuple[TimeframeRole, ...]
    """Roles with no view. Absent, not neutral, and never counted as
    agreement by any rule above."""

    def reading_for(self, role: TimeframeRole) -> DirectionalReading | None:
        for reading in self.readings:
            if reading.role is role:
                return reading
        return None

    @property
    def has_contradiction(self) -> bool:
        return bool(self.contradictions)

    @property
    def highest_severity(self) -> ContradictionSeverity | None:
        if not self.contradictions:
            return None
        return max(
            (item.severity for item in self.contradictions),
            key=lambda severity: severity.rank,
        )


def detect_contradictions(
    views: MultiTimeframeView,
    evidence: tuple[EvidenceItem, ...],
    config: ContradictionConfig | None = None,
) -> ContradictionReport:
    """Compare timeframes and report every conflict found.

    ``evidence`` must be the evidence generated from ``views``; the items are
    matched to roles by their ``role`` field, so contract evidence - which has
    none - is simply not consulted here. Basis and open interest carry no
    direction by design (§32, §33), so they cannot contradict anything.
    """
    settings = config if config is not None else ContradictionConfig()

    readings = tuple(
        _read(view, evidence) for role in ROLES_BROADEST_FIRST if (view := views.view_for(role))
    )
    contradictions: list[Contradiction] = []

    contradictions.extend(_intra_timeframe(readings))
    contradictions.extend(_conflicting_evidence(views, evidence, settings))
    contradictions.extend(_higher_timeframe(readings))
    contradictions.extend(_setup_against_bias(readings))

    entry_conflict, pullbacks = _entry_role(views, readings, settings)
    contradictions.extend(entry_conflict)

    return ContradictionReport(
        readings=readings,
        contradictions=tuple(contradictions),
        pullbacks=pullbacks,
        unavailable_roles=views.missing_roles,
    )


# ----------------------------------------------------------------------
# Per-timeframe readings
# ----------------------------------------------------------------------


def _read(view: TimeframeView, evidence: tuple[EvidenceItem, ...]) -> DirectionalReading:
    """Combine the regime and the structure bias into one reading.

    Both come from Phase 2 and neither is recomputed. The rule is deliberately
    conservative: agreement gives a direction, one-sided information gives that
    side, and disagreement gives neutral - never a casting vote.
    """
    regime_item = _item(evidence, view.role, EvidenceSource.MARKET_REGIME)
    structure_item = _item(evidence, view.role, EvidenceSource.MARKET_STRUCTURE)
    regime_direction = regime_item.direction if regime_item else EvidenceDirection.UNAVAILABLE
    structure_direction = (
        structure_item.direction if structure_item else EvidenceDirection.UNAVAILABLE
    )
    involved = tuple(item for item in (regime_item, structure_item) if item is not None)

    if opposes(regime_direction, structure_direction):
        direction = EvidenceDirection.NEUTRAL
        reason = (
            f"the {view.timeframe.value} regime reads {regime_direction.value.lower()} while its "
            f"structure reads {structure_direction.value.lower()}; neither overrules the other"
        )
    elif regime_direction.is_directional:
        direction = regime_direction
        reason = f"{view.timeframe.value} regime and structure agree on {direction.value.lower()}"
        if not structure_direction.is_directional:
            reason = (
                f"{view.timeframe.value} regime reads {direction.value.lower()}; structure is "
                f"{structure_direction.value.lower()}"
            )
    elif structure_direction.is_directional:
        direction = structure_direction
        reason = (
            f"{view.timeframe.value} structure reads {direction.value.lower()}; regime is "
            f"{regime_direction.value.lower()}"
        )
    elif EvidenceDirection.UNAVAILABLE in (regime_direction, structure_direction):
        # Neither component points anywhere and at least one could not be
        # measured at all. "Favours neither side" would be a claim about a
        # market nobody has finished looking at, so the reading stays absent.
        direction = EvidenceDirection.UNAVAILABLE
        reason = (
            f"{view.timeframe.value} cannot be read yet: regime is "
            f"{regime_direction.value.lower()} and structure is "
            f"{structure_direction.value.lower()}"
        )
    else:
        direction = EvidenceDirection.NEUTRAL
        reason = f"{view.timeframe.value} favours neither side"

    return DirectionalReading(
        role=view.role,
        timeframe=view.timeframe,
        direction=direction,
        regime_direction=regime_direction,
        structure_direction=structure_direction,
        reason=reason,
        evidence=involved,
        as_of=view.last_time,
    )


def _item(
    evidence: tuple[EvidenceItem, ...], role: TimeframeRole, source: EvidenceSource
) -> EvidenceItem | None:
    for item in evidence:
        if item.role is role and item.source is source:
            return item
    return None


# ----------------------------------------------------------------------
# Rules
# ----------------------------------------------------------------------


def _intra_timeframe(readings: tuple[DirectionalReading, ...]) -> tuple[Contradiction, ...]:
    return tuple(
        Contradiction(
            contradiction_type=ContradictionType.INTRA_TIMEFRAME_CONFLICT,
            severity=ContradictionSeverity.MODERATE,
            category=EvidenceCategory.REGIME,
            roles=(reading.role,),
            timeframes=(reading.timeframe,),
            evidence=reading.evidence,
            reason=reading.reason,
            confirmed_at=_latest(reading.evidence),
        )
        for reading in readings
        if opposes(reading.regime_direction, reading.structure_direction)
    )


def _conflicting_evidence(
    views: MultiTimeframeView,
    evidence: tuple[EvidenceItem, ...],
    config: ContradictionConfig,
) -> tuple[Contradiction, ...]:
    """Substantial evidence on both sides of the same timeframe.

    Reported as coexistence, which is what it is. Netting the two sides into a
    winner is exactly the summing §17 rules out.
    """
    found: list[Contradiction] = []
    for view in views.views:
        items = tuple(item for item in evidence if item.role is view.role)
        bulls = _side(items, EvidenceDirection.BULLISH, config.conflicting_evidence_strength)
        bears = _side(items, EvidenceDirection.BEARISH, config.conflicting_evidence_strength)
        if not bulls or not bears:
            continue
        involved = bulls + bears
        found.append(
            Contradiction(
                contradiction_type=ContradictionType.EVIDENCE_CONFLICT,
                severity=ContradictionSeverity.MINOR,
                category=EvidenceCategory.STRUCTURE,
                roles=(view.role,),
                timeframes=(view.timeframe,),
                evidence=involved,
                reason=(
                    f"{view.timeframe.value} carries {len(bulls)} bullish and {len(bears)} "
                    f"bearish observations at {config.conflicting_evidence_strength.value} "
                    "strength or above; both are reported, neither is netted away"
                ),
                confirmed_at=_latest(involved),
            )
        )
    return tuple(found)


def _side(
    items: tuple[EvidenceItem, ...],
    direction: EvidenceDirection,
    floor: EvidenceStrength,
) -> tuple[EvidenceItem, ...]:
    return tuple(
        item for item in items if item.direction is direction and item.strength.at_least(floor)
    )


def _higher_timeframe(readings: tuple[DirectionalReading, ...]) -> tuple[Contradiction, ...]:
    regime = _reading(readings, TimeframeRole.REGIME)
    bias = _reading(readings, TimeframeRole.BIAS)
    if regime is None or bias is None or not opposes(regime.direction, bias.direction):
        return ()
    involved = regime.evidence + bias.evidence
    return (
        Contradiction(
            contradiction_type=ContradictionType.HIGHER_TIMEFRAME_CONFLICT,
            severity=ContradictionSeverity.MAJOR,
            category=EvidenceCategory.REGIME,
            roles=(regime.role, bias.role),
            timeframes=(regime.timeframe, bias.timeframe),
            evidence=involved,
            reason=(
                f"{regime.timeframe.value} reads {regime.direction.value.lower()} while "
                f"{bias.timeframe.value} reads {bias.direction.value.lower()}; the two slowest "
                "timeframes disagree about direction, which no lower-timeframe timing can settle"
            ),
            confirmed_at=_latest(involved),
        ),
    )


def _setup_against_bias(readings: tuple[DirectionalReading, ...]) -> tuple[Contradiction, ...]:
    bias = _reading(readings, TimeframeRole.BIAS)
    setup = _reading(readings, TimeframeRole.SETUP)
    if bias is None or setup is None or not opposes(bias.direction, setup.direction):
        return ()
    involved = bias.evidence + setup.evidence
    return (
        Contradiction(
            contradiction_type=ContradictionType.SETUP_AGAINST_BIAS,
            severity=ContradictionSeverity.MODERATE,
            category=EvidenceCategory.STRUCTURE,
            roles=(bias.role, setup.role),
            timeframes=(bias.timeframe, setup.timeframe),
            evidence=involved,
            reason=(
                f"{setup.timeframe.value} structure reads {setup.direction.value.lower()} against "
                f"a {bias.direction.value.lower()} {bias.timeframe.value} bias; a setup forming "
                "against the bias is a conflict, not merely late timing"
            ),
            confirmed_at=_latest(involved),
        ),
    )


def _entry_role(
    views: MultiTimeframeView,
    readings: tuple[DirectionalReading, ...],
    config: ContradictionConfig,
) -> tuple[tuple[Contradiction, ...], tuple[Pullback, ...]]:
    """The pullback exemption, and the conditions that revoke it.

    When every higher role present agrees on a direction and the entry
    timeframe opposes it, that is a retracement inside an agreed trend - the
    §10 case, reported as a `Pullback` and explicitly not a contradiction.

    The exemption is revoked by evidence that the entry timeframe is doing more
    than retracing - see ``_reversal_evidence`` for the two conditions and why
    each was chosen. Both rest on facts Phase 2 already established; neither is
    inferred here.

    Where the higher roles do *not* agree, the exemption never applies - there
    is no consensus for the entry timeframe to be pulling back from.
    """
    entry = _reading(readings, TimeframeRole.ENTRY)
    entry_view = views.view_for(TimeframeRole.ENTRY)
    if entry is None or entry_view is None or not entry.is_directional:
        return (), ()

    higher = tuple(
        reading
        for reading in readings
        if reading.role is not TimeframeRole.ENTRY and reading.is_directional
    )
    if not higher or not any(opposes(reading.direction, entry.direction) for reading in higher):
        return (), ()

    agreed = {reading.direction for reading in higher}
    consensus = agreed.pop() if len(agreed) == 1 else None

    escalation = _reversal_evidence(entry_view, entry.direction, config)
    if consensus is not None and escalation is None:
        return (), (
            Pullback(
                role=entry.role,
                timeframe=entry.timeframe,
                against=consensus,
                reason=(
                    f"{entry.timeframe.value} is moving {entry.direction.value.lower()} while "
                    f"every higher timeframe reads {consensus.value.lower()}; with no confirmed "
                    "change of character and no opposing trend regime this is a retracement, so "
                    "immediate timing is weak rather than the direction being in doubt"
                ),
                as_of=entry.as_of,
            ),
        )

    involved = entry.evidence + tuple(item for reading in higher for item in reading.evidence)
    detail = (
        escalation
        if escalation is not None
        else "the higher timeframes do not agree among themselves, so there is no trend to "
        "retrace within"
    )
    return (
        Contradiction(
            contradiction_type=ContradictionType.LOWER_TIMEFRAME_REVERSAL,
            severity=ContradictionSeverity.MINOR,
            category=EvidenceCategory.STRUCTURE,
            roles=(entry.role,) + tuple(reading.role for reading in higher),
            timeframes=(entry.timeframe,) + tuple(reading.timeframe for reading in higher),
            evidence=involved,
            reason=(
                f"{entry.timeframe.value} reads {entry.direction.value.lower()} against the "
                f"higher timeframes, and this is more than a retracement: {detail}"
            ),
            confirmed_at=_latest(involved),
        ),
    ), ()


def _reversal_evidence(
    view: TimeframeView,
    entry_direction: EvidenceDirection,
    config: ContradictionConfig,
) -> str | None:
    """Why this entry-timeframe move is not merely a pullback, if it is not.

    Two conditions revoke the exemption, and the choice of both matters.

    **A strong opposing trend regime.** A retracement does not classify as a
    strong trend on its own timeframe; when Phase 2 calls the entry timeframe a
    strong trend the other way, it is describing something with more
    persistence than a pullback.

    **An opposing change of character *and* an opposing confirmed breakout,
    both recent.** A CHOCH alone is deliberately not enough: on the entry
    timeframe a change of character is what a pullback *is* structurally, so
    treating it as a reversal would revoke the exemption on every healthy trend
    and make the distinction meaningless. Requiring a level to give way as well
    asks for structure and price to agree before the reading is escalated.

    Returns the explanation, or ``None`` when nothing revokes the exemption.
    """
    regime = view.structure.regime.regime
    if entry_direction is EvidenceDirection.BEARISH and regime in _STRONG_BEARISH_REGIMES:
        return f"the {view.timeframe.value} regime is itself {regime.value}"
    if entry_direction is EvidenceDirection.BULLISH and regime in _STRONG_BULLISH_REGIMES:
        return f"the {view.timeframe.value} regime is itself {regime.value}"

    cutoff = view.last_index - config.reversal_lookback
    wanted = Direction.SHORT if entry_direction is EvidenceDirection.BEARISH else Direction.LONG
    choch = next(
        (
            event
            for event in reversed(view.structure.structural_events)
            if event.event_type is StructuralEventType.CHOCH
            and event.direction is wanted
            and event.confirmed_index >= cutoff
        ),
        None,
    )
    if choch is None:
        return None

    breakout = next(
        (
            event
            for event in reversed(view.structure.breakout_events)
            if event.event_type is BreakoutEventType.CONFIRMED
            and event.direction is wanted
            and event.confirmed_index >= cutoff
        ),
        None,
    )
    if breakout is None:
        return None

    return (
        f"a {wanted.value.lower()} change of character confirmed at candle "
        f"{choch.confirmed_index} and a confirmed break of "
        f"{breakout.zone.low}-{breakout.zone.high} at candle {breakout.confirmed_index}, both "
        f"within the last {config.reversal_lookback} candles on {view.timeframe.value}"
    )


_STRONG_BEARISH_REGIMES = frozenset({MarketRegime.STRONG_DOWNTREND, MarketRegime.BREAKDOWN})
_STRONG_BULLISH_REGIMES = frozenset({MarketRegime.STRONG_UPTREND, MarketRegime.BREAKOUT})


def _reading(
    readings: tuple[DirectionalReading, ...], role: TimeframeRole
) -> DirectionalReading | None:
    for reading in readings:
        if reading.role is role:
            return reading
    return None


def _latest(items: tuple[EvidenceItem, ...]) -> datetime | None:
    times = [item.confirmed_time for item in items if item.confirmed_time is not None]
    return max(times) if times else None
