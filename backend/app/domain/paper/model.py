"""The paper-trading domain model (Phase 9).

## Deliberately small

One position type, one event type, a few value objects. There is no separate
order object: v1 models exactly one entry style (the next bar's open), so an
"order" would be a second name for the position's pending state. There is no
account, portfolio or journal - those belong to later phases.

## Nothing here is futures-specific

A position has a symbol, a direction, a whole-unit quantity and price levels.
What a unit of price movement is worth, whether the levels sit on the product's
price grid, and whether the product can be calculated at all are answered by a
``ProductPolicy`` - so this package never imports ``app.domain.futures``, and an
import contract says so.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique
from types import MappingProxyType

from app.domain.common.enums import Direction, Timeframe
from app.domain.paper.rules import SimulationPolicy
from app.domain.risk.sizing import PositionSizing, SizingOutcome

MAX_TARGETS = 5
"""Enough for a scaled exit plan; bounded so one position cannot carry an
arbitrary list the ledger and the UI then have to render."""

MAX_NOTE_LENGTH = 280


class PaperInputError(ValueError):
    """A position specification that cannot describe a real trade."""


@unique
class PositionState(StrEnum):
    """Every state a paper position can be in, and no others.

    Each exists because a real transition produces it; a state no event can
    reach would be decoration.
    """

    PENDING_ENTRY = "PENDING_ENTRY"
    """Created; waiting for the first bar at or after the decision time."""

    OPEN = "OPEN"
    """Entry filled; full quantity still open."""

    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    """At least one target filled; some quantity still open."""

    AMBIGUOUS_HALTED = "AMBIGUOUS_HALTED"
    """A bar touched both stop and target under the ``HALT`` policy. No fill was
    simulated for that bar; only a manual close can end the position."""

    CLOSED = "CLOSED"
    """No quantity remains. Terminal."""

    CANCELLED = "CANCELLED"
    """Cancelled by the user before the entry filled. Terminal."""

    REJECTED = "REJECTED"
    """The entry bar made the plan impossible - it opened at or beyond the stop
    or the first target. No position was ever open. Terminal."""

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL

    @property
    def has_exposure(self) -> bool:
        return self in _EXPOSED


_TERMINAL = frozenset({PositionState.CLOSED, PositionState.CANCELLED, PositionState.REJECTED})
_EXPOSED = frozenset(
    {PositionState.OPEN, PositionState.PARTIALLY_CLOSED, PositionState.AMBIGUOUS_HALTED}
)


@unique
class PaperEventType(StrEnum):
    """Every fact the ledger records.

    *Inputs* - things a person or a market supplied - are ``POSITION_CREATED``,
    ``OBSERVATION_APPLIED``, ``CLOSE_REQUESTED``, ``STOP_MOVED_TO_BREAKEVEN`` and
    ``POSITION_CANCELLED``. Everything else is *derived* by the engine from
    those, which is what makes a position replayable: the inputs, fed through
    the same rules, must reproduce every derived event exactly.
    """

    POSITION_CREATED = "POSITION_CREATED"
    OBSERVATION_APPLIED = "OBSERVATION_APPLIED"
    ENTRY_FILLED = "ENTRY_FILLED"
    ENTRY_REJECTED = "ENTRY_REJECTED"
    TARGET_FILLED = "TARGET_FILLED"
    STOP_FILLED = "STOP_FILLED"
    SAME_BAR_AMBIGUITY = "SAME_BAR_AMBIGUITY"
    CLOSE_REQUESTED = "CLOSE_REQUESTED"
    MANUAL_EXIT_FILLED = "MANUAL_EXIT_FILLED"
    STOP_MOVED_TO_BREAKEVEN = "STOP_MOVED_TO_BREAKEVEN"
    POSITION_CANCELLED = "POSITION_CANCELLED"
    POSITION_CLOSED = "POSITION_CLOSED"


INPUT_EVENT_TYPES: frozenset[PaperEventType] = frozenset(
    {
        PaperEventType.POSITION_CREATED,
        PaperEventType.OBSERVATION_APPLIED,
        PaperEventType.CLOSE_REQUESTED,
        PaperEventType.STOP_MOVED_TO_BREAKEVEN,
        PaperEventType.POSITION_CANCELLED,
    }
)


@unique
class PositionOrigin(StrEnum):
    """Who decided this position should exist.

    Phase 9 has exactly one origin. An analysis is ephemeral and is not stored,
    so the server has no record it could check a client's claim against; a
    position is therefore never recorded as "opened by the analysis". A person
    authored it, and the risk engine was re-run server-side before it existed.
    """

    USER_CREATED = "USER_CREATED"


@dataclass(frozen=True, slots=True)
class TargetSpec:
    """One take-profit level and the whole units it closes."""

    price: Decimal
    quantity: int


@dataclass(frozen=True, slots=True)
class PositionSpec:
    """What the user asked for, validated for internal consistency.

    Structural rules only - sides, counts, ordering. Whether the levels are
    executable for this product, and whether the risk engine permits the
    quantity, is checked by the engine against a ``ProductPolicy`` and a sizing
    result, because neither is knowable from the specification alone.
    """

    position_id: str
    symbol: str
    direction: Direction
    quantity: int
    intended_entry: Decimal
    stop: Decimal
    targets: tuple[TargetSpec, ...]
    timeframe: Timeframe
    decision_time: datetime
    """Market time at which the decision was made. The entry fills on the first
    bar that opens at or after it, so no earlier bar can inform the fill."""

    policy: SimulationPolicy
    origin: PositionOrigin = PositionOrigin.USER_CREATED
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.position_id.strip():
            raise PaperInputError("position_id must not be empty")
        if not self.symbol.strip():
            raise PaperInputError("symbol must not be empty")
        if self.direction not in (Direction.LONG, Direction.SHORT):
            raise PaperInputError(f"{self.direction.value} is not a tradeable direction")
        if isinstance(self.quantity, bool) or self.quantity < 1:
            raise PaperInputError(f"quantity must be at least one whole unit, got {self.quantity}")
        for name, price in (("intended entry", self.intended_entry), ("stop", self.stop)):
            _require_price(price, name)
        if self.decision_time.tzinfo is None or self.decision_time.utcoffset() is None:
            raise PaperInputError("decision_time must be timezone-aware")

        if not self.targets:
            raise PaperInputError("at least one target is required")
        if len(self.targets) > MAX_TARGETS:
            raise PaperInputError(f"at most {MAX_TARGETS} targets are allowed")

        long = self.direction is Direction.LONG
        if (self.stop >= self.intended_entry) if long else (self.stop <= self.intended_entry):
            side = "below" if long else "above"
            raise PaperInputError(f"a {self.direction.value} stop must be {side} the entry")

        previous = self.intended_entry
        allocated = 0
        for index, target in enumerate(self.targets, start=1):
            _require_price(target.price, f"target {index}")
            if isinstance(target.quantity, bool) or target.quantity < 1:
                raise PaperInputError(f"target {index} must close at least one whole unit")
            beyond = target.price > previous if long else target.price < previous
            if not beyond:
                word = "above" if long else "below"
                what = "the entry" if index == 1 else f"target {index - 1}"
                raise PaperInputError(
                    f"target {index} must be strictly {word} {what} for a {self.direction.value}"
                )
            previous = target.price
            allocated += target.quantity
        if allocated > self.quantity:
            raise PaperInputError(
                f"targets allocate {allocated} units but the position has {self.quantity}"
            )

        if self.note is not None:
            if len(self.note) > MAX_NOTE_LENGTH:
                raise PaperInputError(f"note must be at most {MAX_NOTE_LENGTH} characters")
            if any(ord(char) < 32 and char not in "\t" for char in self.note):
                raise PaperInputError("note must not contain control characters")


@dataclass(frozen=True, slots=True)
class RiskApproval:
    """The part of a risk-engine sizing result a position depends on.

    Stored with the position, so a replay re-checks the approval that was given
    rather than re-running sizing against an account that has since changed.
    """

    outcome: SizingOutcome
    allowed_units: int | None
    reason: str

    @classmethod
    def from_sizing(cls, sizing: PositionSizing) -> RiskApproval:
        return cls(
            outcome=sizing.outcome,
            allowed_units=sizing.allowed_contracts,
            reason=sizing.reason,
        )


@dataclass(frozen=True, slots=True)
class PaperEvent:
    """One immutable ledger entry.

    ``data`` holds strings only - decimals in plain notation, datetimes in ISO
    8601 - so an event compares, stores and replays identically everywhere,
    with no float, locale or dictionary-ordering behaviour anywhere near it.
    """

    sequence: int
    type: PaperEventType
    market_time: datetime | None
    data: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise PaperInputError("event sequence starts at 1")
        object.__setattr__(self, "data", MappingProxyType(dict(sorted(self.data.items()))))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PaperEvent):
            return NotImplemented
        return (
            self.sequence == other.sequence
            and self.type is other.type
            and self.market_time == other.market_time
            and dict(self.data) == dict(other.data)
        )

    def __hash__(self) -> int:
        return hash((self.sequence, self.type, self.market_time, tuple(self.data.items())))


@dataclass(frozen=True, slots=True)
class TargetState:
    spec: TargetSpec
    filled: bool = False
    fill_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PaperPosition:
    """The state of one position after every event so far.

    Always the result of applying the ledger; never edited in place.
    """

    spec: PositionSpec
    approval: RiskApproval
    state: PositionState
    remaining: int
    stop: Decimal
    targets: tuple[TargetState, ...]
    events: tuple[PaperEvent, ...]
    entry_fill_price: Decimal | None = None
    entry_time: datetime | None = None
    realized_gross: Decimal = Decimal(0)
    fees_total: Decimal | None = None
    last_bar_time: datetime | None = None
    last_mark: Decimal | None = None
    close_pending: bool = False
    bars_applied: int = 0

    @property
    def filled_quantity(self) -> int:
        """Units that were ever open - zero before entry or after a rejection."""
        return self.spec.quantity if self.entry_fill_price is not None else 0

    @property
    def closed_quantity(self) -> int:
        return self.filled_quantity - self.remaining

    @property
    def realized_net(self) -> Decimal | None:
        """Gross minus every fee charged, or ``None`` when fees are not modelled."""
        if self.fees_total is None:
            return None
        return self.realized_gross - self.fees_total

    @property
    def next_sequence(self) -> int:
        return len(self.events) + 1


def _require_price(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise PaperInputError(f"{name} must be a finite decimal, got {value!r}")
    if value <= 0:
        raise PaperInputError(f"{name} must be positive, got {value}")
