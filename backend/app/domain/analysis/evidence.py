"""The evidence vocabulary (master spec §16).

One `EvidenceItem` is one thing a deterministic engine observed, dated to the
moment it became knowable. Nothing here computes anything: the items are built
in ``generation.py`` from Phase 1-3 output and are read by the contradiction
engine.

Two rules shape the whole module.

**No fake probability.** §19 forbids presenting an analysis score as a
likelihood. `EvidenceStrength` is therefore ordinal and deliberately *not* an
``int``: WEAK / MODERATE / STRONG can be compared through ``rank`` but cannot
be added, averaged, or turned into "78% chance of LONG" by arithmetic that
happens to typecheck. Summing is not merely discouraged here, it is
unavailable.

**Unavailable is not neutral.** `NEUTRAL` says an engine looked and found no
directional information; `UNAVAILABLE` says the engine could not look. The
distinction survives into every downstream reading, because collapsing them
manufactures agreement out of missing data.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique

from app.domain.analysis.timeframes import TimeframeRole
from app.domain.common.enums import Timeframe
from app.domain.common.verification import VerificationStatus


@unique
class EvidenceDirection(StrEnum):
    """What an observation says about direction, if anything."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"

    NEUTRAL = "NEUTRAL"
    """Measured, and it points nowhere - a range, a mixed EMA stack, a basis
    reading that is context rather than direction."""

    UNAVAILABLE = "UNAVAILABLE"
    """Not measurable: warm-up not finished, data absent, provenance refused.
    Never interchangeable with ``NEUTRAL``."""

    @property
    def is_directional(self) -> bool:
        return self in (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH)

    @property
    def opposite(self) -> EvidenceDirection:
        """The mirror of a directional reading; non-directional values are
        their own opposite, since there is nothing to mirror."""
        if self is EvidenceDirection.BULLISH:
            return EvidenceDirection.BEARISH
        if self is EvidenceDirection.BEARISH:
            return EvidenceDirection.BULLISH
        return self


def opposes(first: EvidenceDirection, second: EvidenceDirection) -> bool:
    """True only when both are directional and point opposite ways.

    Neutral does not oppose anything, and unavailable certainly does not.
    """
    return first.is_directional and second.is_directional and first is not second


@unique
class EvidenceStrength(StrEnum):
    """How much weight an observation carries - ordinal, never numeric.

    ``rank`` exists for comparison. There is no arithmetic on strengths
    anywhere in this codebase, and a test enforces that.
    """

    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"

    @property
    def rank(self) -> int:
        return _STRENGTH_RANK[self]

    def at_least(self, other: EvidenceStrength) -> bool:
        return self.rank >= other.rank


_STRENGTH_RANK: dict[EvidenceStrength, int] = {
    EvidenceStrength.WEAK: 1,
    EvidenceStrength.MODERATE: 2,
    EvidenceStrength.STRONG: 3,
}


@unique
class EvidenceCategory(StrEnum):
    """What kind of observation this is.

    Categories let the contradiction engine say *what* disagrees, instead of
    reporting a net score that hides which side of the argument moved.
    """

    TREND = "TREND"
    STRUCTURE = "STRUCTURE"
    REGIME = "REGIME"
    MOMENTUM = "MOMENTUM"
    INTRADAY = "INTRADAY"
    """Session-relative context - the VWAP relationship (master spec §11)."""

    LEVEL = "LEVEL"
    BREAKOUT = "BREAKOUT"
    RETEST = "RETEST"
    VOLUME = "VOLUME"
    DIVERGENCE = "DIVERGENCE"
    BASIS = "BASIS"
    OPEN_INTEREST = "OPEN_INTEREST"


@unique
class EvidenceSource(StrEnum):
    """Which deterministic engine produced the observation.

    Every member names an engine that already exists in Phases 1-3. There is
    no member for an LLM, a screenshot or a news feed, and there will not be
    one in this phase: §16 requires evidence to come from deterministic
    engines.
    """

    EMA_ALIGNMENT = "EMA_ALIGNMENT"
    MARKET_STRUCTURE = "MARKET_STRUCTURE"
    STRUCTURAL_EVENT = "STRUCTURAL_EVENT"
    MARKET_REGIME = "MARKET_REGIME"
    MOMENTUM = "MOMENTUM"
    VWAP = "VWAP"
    SUPPORT_RESISTANCE = "SUPPORT_RESISTANCE"
    BREAKOUT = "BREAKOUT"
    BREAKOUT_VOLUME = "BREAKOUT_VOLUME"
    RETEST = "RETEST"
    VOLUME_DIVERGENCE = "VOLUME_DIVERGENCE"
    BASIS = "BASIS"
    OPEN_INTEREST = "OPEN_INTEREST"


@unique
class EvidenceReliability(StrEnum):
    """How settled an observation is - §16's ``reliability`` field.

    Distinct from strength, and deliberately so. *Strength* is how much the
    observation says; *reliability* is how likely it is to still say it at the
    next candle. A confirmed structural break is a completed fact about the
    past. A regime reading is a description of the current candle that may
    read differently on the next one. Both can be STRONG; only one of them has
    stopped moving.

    Derived, never stored: it follows from ``point_in_time`` and
    ``provenance``, both of which the item already carries, so it cannot drift
    out of step with them.
    """

    CONFIRMED = "CONFIRMED"
    """A completed event, dated to its confirmation. Later candles add new
    events; they do not revise this one."""

    PROVISIONAL = "PROVISIONAL"
    """A reading of the latest candle. Legitimately different next candle."""

    UNVERIFIED_SOURCE = "UNVERIFIED_SOURCE"
    """Rests on a fact whose provenance is not authoritative under §118 - a
    development default or an unverified value. The observation may be sound;
    its input is not established."""


_PROVENANCE_IS_AUTHORITATIVE = frozenset(
    {VerificationStatus.VERIFIED_CURRENT_FACT, VerificationStatus.TEST_FIXTURE}
)


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """One deterministic observation, with its identity and its date.

    ``confirmed_index`` / ``confirmed_time`` are the Phase 2 causality
    contract: the moment the observation *became knowable*, never the moment
    the underlying price action happened. A swing that pivoted at candle 100
    and confirmed at 102 produces evidence dated 102.

    Indices are positions in one timeframe's series and are meaningless across
    timeframes - compare them only within a single view.
    """

    source: EvidenceSource
    category: EvidenceCategory
    direction: EvidenceDirection
    strength: EvidenceStrength
    reason: str

    timeframe: Timeframe | None
    """``None`` for evidence that is not timeframe-bound, such as a contract's
    basis or open-interest context."""

    role: TimeframeRole | None
    confirmed_index: int | None
    confirmed_time: datetime | None

    point_in_time: bool
    """True when the observation describes the final candle only - a regime, a
    structure bias, the nearest level. Such an item is legitimately different
    at every candle, so it can never be compared against an earlier run the way
    an event can."""

    provenance: VerificationStatus | None = None
    """Carried where the underlying fact has one (§118). ``None`` where the
    observation derives from candles rather than from exchange metadata."""

    @property
    def is_directional(self) -> bool:
        return self.direction.is_directional

    @property
    def reliability(self) -> EvidenceReliability:
        """§16's reliability, derived rather than asserted.

        Provenance is checked first: an observation resting on a value nobody
        has verified is unverified whatever else is true of it.
        """
        if self.provenance is not None and self.provenance not in _PROVENANCE_IS_AUTHORITATIVE:
            return EvidenceReliability.UNVERIFIED_SOURCE
        return (
            EvidenceReliability.PROVISIONAL if self.point_in_time else EvidenceReliability.CONFIRMED
        )


def evidence_known_at(items: Sequence[EvidenceItem], index: int) -> tuple[EvidenceItem, ...]:
    """The subset of ``items`` that had become knowable by ``index``.

    Pass items from **one** timeframe: indices are positions in that
    timeframe's series. Undated items - contract evidence - are excluded,
    because there is no candle index at which to place them.
    """
    return tuple(
        item for item in items if item.confirmed_index is not None and item.confirmed_index <= index
    )


def directional(items: Iterable[EvidenceItem]) -> tuple[EvidenceItem, ...]:
    """Only the items that actually say bullish or bearish."""
    return tuple(item for item in items if item.is_directional)


def strongest(items: Iterable[EvidenceItem]) -> EvidenceStrength | None:
    """The highest strength present, by rank - a maximum, never a total."""
    ranked = sorted(items, key=lambda item: item.strength.rank, reverse=True)
    return ranked[0].strength if ranked else None
