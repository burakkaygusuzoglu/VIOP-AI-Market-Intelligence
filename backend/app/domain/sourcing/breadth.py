"""Market breadth over a defined universe, or not at all (Phase 15 Part 2A).

Master spec section 35 describes breadth - advancing against declining
constituents, participation - as context for index futures. A breadth figure
is a fraction, and a fraction is meaningless
without its denominator: "70% advancing" of an unknown population, of a
population silently missing a third of its members, or of a watchlist
somebody assembled, is a number that looks like market evidence and is not.

## What a breadth answer needs

* A :class:`Universe` - its identity, venue, kind, exact constituent list and
  source, verified and dated. Only an index's published constituents or an
  exchange's listing is a market universe; a watchlist is refused.
* :class:`ConstituentMove` observations - one per constituent, for one
  ``observed_at``, each with the time it became available.

## What it refuses

* no universe, an unverified one, a watchlist, or an empty one →
  ``UNAVAILABLE``;
* an observation for a symbol outside the universe → ``UNAVAILABLE``: two
  populations are never mixed into one count;
* two observations of one constituent for one moment → ``UNAVAILABLE``;
* observations available only after ``decision_time`` do not count;
* coverage below ``min_coverage`` → ``UNAVAILABLE``, with the missing
  constituents named and no share computed.

Coverage between ``min_coverage`` and complete is ``PARTIAL``: the counts and
shares are over the *observed* constituents, the denominator says so, and the
missing constituents are listed. Only full coverage is ``AVAILABLE``.

Nothing here decides how a constituent "advanced" - the caller's source says
so for a stated reference (for example the previous session's close) - and
nothing turns breadth into a trade.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.verification import VerifiedValue

__all__ = [
    "BreadthAnswer",
    "BreadthStatus",
    "ConstituentMove",
    "Direction",
    "Universe",
    "UniverseKind",
    "breadth_at",
]


@unique
class UniverseKind(StrEnum):
    INDEX_CONSTITUENTS = "INDEX_CONSTITUENTS"
    EXCHANGE_LISTING = "EXCHANGE_LISTING"
    WATCHLIST = "WATCHLIST"
    """A user's selection. Not a market; breadth over it is refused."""


@unique
class Direction(StrEnum):
    ADVANCING = "ADVANCING"
    DECLINING = "DECLINING"
    UNCHANGED = "UNCHANGED"


@unique
class BreadthStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class Universe:
    universe_id: str
    venue: str
    kind: UniverseKind
    constituents: frozenset[str]
    source: VerifiedValue[str]
    """The constituent list's publication, verified and dated."""

    methodology: str
    """What "advancing" is measured against, stated by the source."""

    @property
    def usable(self) -> bool:
        s = self.source
        return (
            self.kind is not UniverseKind.WATCHLIST
            and bool(self.constituents)
            and s.is_authoritative
            and bool(s.source.strip())
            and s.as_of is not None
            and bool(self.methodology.strip())
        )


@dataclass(frozen=True, slots=True)
class ConstituentMove:
    symbol: str
    direction: Direction
    observed_at: datetime
    available_at: datetime
    revision: int = 0

    def __post_init__(self) -> None:
        for name in ("observed_at", "available_at"):
            if getattr(self, name).utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.available_at < self.observed_at:
            raise ValueError("an observation is not available before it is made")


@dataclass(frozen=True, slots=True)
class BreadthAnswer:
    status: BreadthStatus
    reason: str
    universe_id: str | None = None
    universe_size: int = 0
    observed: int = 0
    advancing: int = 0
    declining: int = 0
    unchanged: int = 0
    missing: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.advancing + self.declining + self.unchanged != self.observed:
            raise ValueError("the counts add up to the observed constituents")
        if self.status is not BreadthStatus.UNAVAILABLE and self.observed == 0:
            raise ValueError("a breadth figure needs observed constituents")

    @property
    def coverage(self) -> Decimal | None:
        if self.universe_size == 0:
            return None
        return Decimal(self.observed) / Decimal(self.universe_size)

    @property
    def advancing_share(self) -> Decimal | None:
        """Advancing over *observed* constituents; ``None`` when unavailable."""
        if self.status is BreadthStatus.UNAVAILABLE:
            return None
        return Decimal(self.advancing) / Decimal(self.observed)

    @property
    def declining_share(self) -> Decimal | None:
        if self.status is BreadthStatus.UNAVAILABLE:
            return None
        return Decimal(self.declining) / Decimal(self.observed)


def _unavailable(
    reason: str, universe: Universe | None = None, missing: frozenset[str] = frozenset()
) -> BreadthAnswer:
    return BreadthAnswer(
        status=BreadthStatus.UNAVAILABLE,
        reason=reason,
        universe_id=universe.universe_id if universe is not None else None,
        universe_size=len(universe.constituents) if universe is not None else 0,
        missing=missing,
    )


def breadth_at(
    universe: Universe | None,
    moves: Iterable[ConstituentMove],
    *,
    observed_at: datetime,
    decision_time: datetime,
    min_coverage: Decimal,
) -> BreadthAnswer:
    """Breadth of ``universe`` at ``observed_at``, as known at ``decision_time``."""
    if not Decimal(0) < min_coverage <= Decimal(1):
        raise ValueError("min_coverage is a fraction in (0, 1]")
    if universe is None:
        return _unavailable("no universe is defined; breadth has no denominator")
    if not universe.usable:
        return _unavailable(
            "a watchlist is not a market; breadth over it is refused"
            if universe.kind is UniverseKind.WATCHLIST
            else "the universe is empty, has no stated methodology, or is not a verified "
            "published list"
        )
    seen: dict[str, ConstituentMove] = {}
    for move in moves:
        if move.symbol not in universe.constituents:
            return _unavailable("an observation lies outside the universe; populations mix")
        if move.observed_at != observed_at or move.available_at > decision_time:
            continue
        held = seen.get(move.symbol)
        if held is None or move.revision > held.revision:
            seen[move.symbol] = move
        elif move.revision == held.revision and move.direction is not held.direction:
            return _unavailable("two observations of one constituent disagree")
    size = len(universe.constituents)
    missing = frozenset(universe.constituents - seen.keys())
    directions = [m.direction for m in seen.values()]
    if not seen or Decimal(len(seen)) / Decimal(size) < min_coverage:
        return _unavailable(
            f"{len(seen)} of {size} constituents observed; below the coverage bound",
            universe,
            missing,
        )
    return BreadthAnswer(
        status=BreadthStatus.PARTIAL if missing else BreadthStatus.AVAILABLE,
        reason=(
            f"{len(seen)} of {size} constituents observed; shares are over the observed"
            if missing
            else f"all {size} constituents observed"
        ),
        universe_id=universe.universe_id,
        universe_size=size,
        observed=len(seen),
        advancing=directions.count(Direction.ADVANCING),
        declining=directions.count(Direction.DECLINING),
        unchanged=directions.count(Direction.UNCHANGED),
        missing=missing,
    )
