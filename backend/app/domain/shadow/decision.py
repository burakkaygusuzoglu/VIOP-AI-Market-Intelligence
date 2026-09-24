"""What a shadow run writes down (Phase 14 Part 1).

Two kinds of journal entry, and the difference is deliberate:

* a **decision** is what the existing policy answered at one confirmed market
  boundary, with the evidence it was given;
* an **operational** entry is something that happened to the observation
  itself - a gap, a disconnect, a conflicting correction, the run ending.

An entry is never edited. When a later fact invalidates an earlier decision, a
*new* entry supersedes it and says so; the original stays exactly as it was
published, because pretending the rule knew something it could not have known
is the one failure a shadow journal exists to prevent.

## The vocabulary is the strategy's, plus what a strategy cannot say

``NO_SIGNAL``, ``WAIT``, ``ENTRY_INTENT`` and ``EXIT_INTENT`` are Phase 12's
:class:`~app.domain.backtest.policy.DecisionKind`, unchanged. Shadow adds two
states that a policy has no way to express, because they are about the
observation rather than the rule:

* ``UNAVAILABLE`` - the evidence needed was not there, so no rule was run;
* ``REFUSED`` - the rule fired and something independent refused it. In Part 1
  that is the risk engine or the absence of verified product metadata.

## An intent is not a fill

``ENTRY_INTENT`` records what the rule would like to do. Nothing here creates
a paper position, a backtest run or an order, and no field carries a realised
profit: a shadow entry has no fill, so it has no result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.enums import Direction, Timeframe

__all__ = [
    "EntrySketch",
    "FinancialState",
    "JournalEntryKind",
    "OperationalKind",
    "ShadowDecision",
    "ShadowEvidence",
    "ShadowOutcome",
    "TimeframeEvidence",
    "TimeframeReadings",
]


@unique
class ShadowOutcome(StrEnum):
    NO_SIGNAL = "NO_SIGNAL"
    WAIT = "WAIT"
    ENTRY_INTENT = "ENTRY_INTENT"
    EXIT_INTENT = "EXIT_INTENT"
    UNAVAILABLE = "UNAVAILABLE"
    """The evidence was not there. Recorded apart from NO_SIGNAL, because
    "no setup" and "could not look" are different facts."""

    REFUSED = "REFUSED"
    """The rule fired and something independent refused it."""


@unique
class FinancialState(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    """No entry was proposed, so nothing financial was asked."""

    NOT_CONFIGURED = "NOT_CONFIGURED"
    """The run was given no account and no risk policy, so no sizing was
    attempted. An observation-only run is a legitimate way to run Shadow."""

    METADATA_UNAVAILABLE = "METADATA_UNAVAILABLE"
    """No verified contract metadata, so no money arithmetic was attempted.
    This is the default in this deployment, and it is not an approval."""

    APPROVED = "APPROVED"
    """The risk engine allowed a size. Still not a position and not an order."""

    REFUSED = "REFUSED"
    """The risk engine refused, or allowed nothing."""

    UNDETERMINED = "UNDETERMINED"
    """Risk sizing ran but could not state a final count (margin unknown)."""


@unique
class JournalEntryKind(StrEnum):
    DECISION = "DECISION"
    OPERATIONAL = "OPERATIONAL"


@unique
class OperationalKind(StrEnum):
    RUN_OPENED = "RUN_OPENED"
    RUN_ENDED = "RUN_ENDED"
    PROVIDER_DISCONNECTED = "PROVIDER_DISCONNECTED"
    PROVIDER_RECOVERING = "PROVIDER_RECOVERING"
    DATA_GAP = "DATA_GAP"
    CONFLICTING_CORRECTION = "CONFLICTING_CORRECTION"
    LATE_FILL = "LATE_FILL"
    """A candle arrived behind the head and filled a recorded gap. Decisions
    already published are left exactly as they were."""

    OBSERVATION_REFUSED = "OBSERVATION_REFUSED"
    DECISION_SUPERSEDED = "DECISION_SUPERSEDED"
    EVALUATION_FAILED = "EVALUATION_FAILED"


@dataclass(frozen=True, slots=True)
class TimeframeReadings:
    """The scalars the policy saw for one timeframe, as text for the record."""

    timeframe: Timeframe
    ema_fast: str | None = None
    ema_slow: str | None = None
    rsi: str | None = None
    atr: str | None = None
    adx: str | None = None


@dataclass(frozen=True, slots=True)
class TimeframeEvidence:
    """One timeframe's state at the boundary, as Phase 13 reported it."""

    timeframe: Timeframe
    available: bool
    freshness: str
    integrity: str
    confirmed_count: int
    reasons: tuple[str, ...] = ()
    last_coverage_end: datetime | None = None


@dataclass(frozen=True, slots=True)
class ShadowEvidence:
    """Why a decision reads the way it does, from the inputs actually used."""

    provenance: str
    market_currency: str
    connection: str
    bars_available: int
    included: tuple[Timeframe, ...] = ()
    excluded: tuple[TimeframeEvidence, ...] = ()
    readings: tuple[TimeframeReadings, ...] = ()
    timeframes: tuple[TimeframeEvidence, ...] = ()
    regime: str | None = None
    """From the existing analysis, when it was run for this boundary."""

    suitability: str | None = None
    """The existing no-trade assessment for the proposed direction, as text."""

    setup_quality: str | None = None
    """The existing heuristic coherence score for that direction, 0-100."""
    analysis_market_as_of: datetime | None = None


@dataclass(frozen=True, slots=True)
class EntrySketch:
    """What the rule asked for. Not a position, not an order, never filled."""

    direction: Direction
    intended_entry: Decimal
    stop: Decimal
    targets: tuple[tuple[Decimal, int], ...]
    requested_quantity: int
    approved_quantity: int | None = None
    """Only from the risk engine, and only where verified product facts made
    sizing possible. ``None`` means nothing was approved."""


@dataclass(frozen=True, slots=True)
class ShadowDecision:
    """One journal entry: a decision at a boundary, or an operational fact."""

    kind: JournalEntryKind
    sequence: int
    decision_key: str
    """Stable identity: the same run, boundary and inputs produce the same key,
    so a retry or a reconnect cannot write the observation twice."""

    market_boundary: datetime | None
    """Market time: the coverage end the evidence was taken at. ``None`` only
    for operational entries that are not about one boundary."""

    recorded_at: datetime
    """Audit time. Never used for a market decision."""

    outcome: ShadowOutcome | None = None
    reason: str = ""
    strategy_kind: str | None = None
    operational: OperationalKind | None = None
    direction: Direction | None = None
    entry: EntrySketch | None = None
    financial_state: FinancialState = FinancialState.NOT_APPLICABLE
    risk_outcome: str | None = None
    risk_reason: str | None = None
    evidence: ShadowEvidence | None = None
    input_fingerprint: str | None = None
    supersedes: str | None = None
    fields: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind is JournalEntryKind.DECISION and self.outcome is None:
            raise ValueError("a decision entry states an outcome")
        if self.kind is JournalEntryKind.OPERATIONAL and self.operational is None:
            raise ValueError("an operational entry states what happened")
        if (self.entry is not None) and self.outcome not in (
            ShadowOutcome.ENTRY_INTENT,
            ShadowOutcome.REFUSED,
        ):
            raise ValueError("only a proposed entry carries entry levels")
        if self.financial_state is FinancialState.APPROVED and self.entry is None:
            raise ValueError("an approval belongs to a proposed entry")
