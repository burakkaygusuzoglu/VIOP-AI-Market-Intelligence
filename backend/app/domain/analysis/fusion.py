"""The Evidence Fusion Engine (master spec §16).

Phase 4A generated evidence and concatenated it. That is collection, not
fusion: a flat tuple cannot answer "what supports the bullish case", and it
lets the same observation speak as many times as it happens to be recorded.

Fusion adds two things, and deliberately only two.

**A partition that keeps every kind of evidence.** Bullish, bearish, neutral
and unavailable are all preserved, with role, source, strength, reliability,
confirmed_at and provenance intact. Nothing is discarded to make a total, and
nothing is reduced to a signed number: §16 asks for evidence that can be read,
not a score that hides what produced it.

**Grouping by category, which is the anti-double-counting device.** Seven
volume divergences across seven swings on one timeframe are seven records of
one *kind* of disagreement. Fused, they are one `EvidenceGroup` holding all
seven items, and every consumer downstream reads groups. A category cannot
speak more times than it has kinds of things to say, however many rows it
generated - so a fixture that happens to produce a divergence at every swing
cannot dominate anything.

What fusion does **not** do:

* it does not average timeframes - roles stay separate, and §10 forbids it;
* it does not sum directions into a net verdict;
* it does not promote basis or open-interest context to a direction, which
  §32 and §33 both refuse and Phase 4A deliberately preserved;
* it does not score anything. Scoring is ``quality.py``, and it consumes this.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.domain.analysis.contradictions import ContradictionReport
from app.domain.analysis.evidence import (
    EvidenceCategory,
    EvidenceDirection,
    EvidenceItem,
    EvidenceReliability,
    EvidenceStrength,
    opposes,
)
from app.domain.analysis.timeframes import ROLES_BROADEST_FIRST, TimeframeRole


@dataclass(frozen=True, slots=True)
class EvidenceGroup:
    """Every observation of one category, on one timeframe, as one voice.

    The group's ``direction`` is the category's stance and is decided
    conservatively: the directional items must all agree. If a category holds
    both bullish and bearish observations it is `NEUTRAL` here and the
    disagreement is reported as a contradiction - resolving it by majority
    would let the count of records decide a question about the market.

    ``strength`` is the **maximum** among the agreeing items, never a total.
    Three moderate divergences are moderate evidence of divergence, not strong
    evidence and not triple evidence.
    """

    category: EvidenceCategory
    role: TimeframeRole | None
    direction: EvidenceDirection
    strength: EvidenceStrength | None
    """``None`` when the group carries no directional item to grade."""

    items: tuple[EvidenceItem, ...]
    reliability: EvidenceReliability
    """The weakest reliability among the items that set the direction: a group
    is only as settled as the least settled thing holding it up."""

    confirmed_at: datetime | None
    """The latest confirmation in the group - when this voice last spoke."""

    @property
    def is_directional(self) -> bool:
        return self.direction.is_directional

    @property
    def item_count(self) -> int:
        """How many records back this one voice.

        Exposed so a reader can see the repetition that fusion collapsed. It
        is deliberately not an input to any score.
        """
        return len(self.items)


@dataclass(frozen=True, slots=True)
class FusedEvidence:
    """The whole evidence picture, partitioned and grouped (§16).

    Answers the four questions §16 asks of it - what supports bullish, what
    supports bearish, what is context, what could not be measured - plus the
    contradictions, which are carried through rather than folded in.
    """

    bullish: tuple[EvidenceItem, ...]
    bearish: tuple[EvidenceItem, ...]
    neutral: tuple[EvidenceItem, ...]
    """Measured, and pointing nowhere. Includes the contract context, which is
    context by design and never a direction."""

    unavailable: tuple[EvidenceItem, ...]
    """Could not be measured. Kept apart from ``neutral`` all the way through,
    because a gap in the data is not a balanced market."""

    groups: tuple[EvidenceGroup, ...]
    """One per (role, category) actually present, ordered broadest role first
    and then by category. The unit every downstream consumer reads."""

    contract: tuple[EvidenceItem, ...]
    contradictions: ContradictionReport

    def groups_for(self, role: TimeframeRole) -> tuple[EvidenceGroup, ...]:
        return tuple(group for group in self.groups if group.role is role)

    def group_for(self, role: TimeframeRole, category: EvidenceCategory) -> EvidenceGroup | None:
        for group in self.groups:
            if group.role is role and group.category is category:
                return group
        return None

    def supporting(
        self, direction: EvidenceDirection, role: TimeframeRole | None = None
    ) -> tuple[EvidenceGroup, ...]:
        """Groups whose stance is ``direction``, optionally on one timeframe."""
        return tuple(
            group
            for group in self.groups
            if group.direction is direction and (role is None or group.role is role)
        )

    def opposing(
        self, direction: EvidenceDirection, role: TimeframeRole | None = None
    ) -> tuple[EvidenceGroup, ...]:
        return tuple(
            group
            for group in self.groups
            if opposes(group.direction, direction) and (role is None or group.role is role)
        )

    @property
    def categories_present(self) -> tuple[EvidenceCategory, ...]:
        seen: list[EvidenceCategory] = []
        for group in self.groups:
            if group.category not in seen:
                seen.append(group.category)
        return tuple(seen)

    @property
    def item_total(self) -> int:
        return len(self.bullish) + len(self.bearish) + len(self.neutral) + len(self.unavailable)


def fuse_evidence(
    evidence: Sequence[EvidenceItem],
    contract_evidence: Sequence[EvidenceItem],
    contradictions: ContradictionReport,
) -> FusedEvidence:
    """Partition and group the evidence for one analysis.

    Deterministic in content and order: the partitions preserve generation
    order and the groups are emitted broadest role first, then in
    `EvidenceCategory` declaration order.
    """
    every = tuple(evidence) + tuple(contract_evidence)

    return FusedEvidence(
        bullish=_with_direction(every, EvidenceDirection.BULLISH),
        bearish=_with_direction(every, EvidenceDirection.BEARISH),
        neutral=_with_direction(every, EvidenceDirection.NEUTRAL),
        unavailable=_with_direction(every, EvidenceDirection.UNAVAILABLE),
        groups=_group(evidence),
        contract=tuple(contract_evidence),
        contradictions=contradictions,
    )


def _with_direction(
    items: Iterable[EvidenceItem], direction: EvidenceDirection
) -> tuple[EvidenceItem, ...]:
    return tuple(item for item in items if item.direction is direction)


def _group(evidence: Sequence[EvidenceItem]) -> tuple[EvidenceGroup, ...]:
    groups: list[EvidenceGroup] = []
    for role in ROLES_BROADEST_FIRST:
        for category in EvidenceCategory:
            items = tuple(
                item for item in evidence if item.role is role and item.category is category
            )
            if items:
                groups.append(_build_group(role, category, items))
    return tuple(groups)


def _build_group(
    role: TimeframeRole, category: EvidenceCategory, items: tuple[EvidenceItem, ...]
) -> EvidenceGroup:
    bullish = [item for item in items if item.direction is EvidenceDirection.BULLISH]
    bearish = [item for item in items if item.direction is EvidenceDirection.BEARISH]

    if bullish and bearish:
        # The category argues with itself. `contradictions.py` already reports
        # this; fusion refuses to break the tie by counting rows.
        direction = EvidenceDirection.NEUTRAL
        deciding: list[EvidenceItem] = bullish + bearish
    elif bullish:
        direction = EvidenceDirection.BULLISH
        deciding = bullish
    elif bearish:
        direction = EvidenceDirection.BEARISH
        deciding = bearish
    elif any(item.direction is EvidenceDirection.NEUTRAL for item in items):
        direction = EvidenceDirection.NEUTRAL
        deciding = [item for item in items if item.direction is EvidenceDirection.NEUTRAL]
    else:
        direction = EvidenceDirection.UNAVAILABLE
        deciding = list(items)

    strength = (
        max((item.strength for item in deciding), key=lambda value: value.rank)
        if direction.is_directional
        else None
    )
    times = [item.confirmed_time for item in items if item.confirmed_time is not None]

    return EvidenceGroup(
        category=category,
        role=role,
        direction=direction,
        strength=strength,
        items=items,
        reliability=_weakest_reliability(deciding),
        confirmed_at=max(times) if times else None,
    )


_RELIABILITY_ORDER: dict[EvidenceReliability, int] = {
    EvidenceReliability.UNVERIFIED_SOURCE: 0,
    EvidenceReliability.PROVISIONAL: 1,
    EvidenceReliability.CONFIRMED: 2,
}


def _weakest_reliability(items: Sequence[EvidenceItem]) -> EvidenceReliability:
    if not items:  # pragma: no cover - a group is never built empty
        return EvidenceReliability.PROVISIONAL
    return min((item.reliability for item in items), key=lambda value: _RELIABILITY_ORDER[value])


def strongest_group(groups: Iterable[EvidenceGroup]) -> EvidenceStrength | None:
    """The highest strength among ``groups`` - a maximum, never a total."""
    graded = [group.strength for group in groups if group.strength is not None]
    return max(graded, key=lambda value: value.rank) if graded else None
