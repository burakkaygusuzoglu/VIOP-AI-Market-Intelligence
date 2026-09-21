"""What one backtest run is, and what it may cost.

A run is a **bounded, reproducible evaluation of one frozen configuration
against one immutable dataset**. Everything here is vocabulary and limits; the
arithmetic belongs to the engines the runner drives.

## Why the lifecycle is this small

``PENDING -> RUNNING -> COMPLETED | FAILED`` and nothing else. The run is
computed in memory and published in one transaction, so there is no partially
written result to represent, no checkpoint to resume from, and no scheduler to
build. A state like ``PARTIAL`` would be a promise the design does not need to
make - and a person reading ``COMPLETED`` must be able to trust that the
metrics describe the whole requested range.

Pure: stdlib and ``app.domain.common`` only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique

from app.domain.common.enums import Timeframe

RUNNER_RULES_VERSION = "backtest-runner/v1"
"""How the runner walks a dataset and orders its work. Part of the semantic
fingerprint: a change to the causal order is a change to the results, even when
every engine underneath is untouched."""


@unique
class RunStatus(StrEnum):
    PENDING = "PENDING"
    """Accepted and recorded, not yet evaluated."""

    RUNNING = "RUNNING"
    FAILED = "FAILED"
    """Stopped before publication. Carries a reason and **no metrics**."""

    COMPLETED = "COMPLETED"
    """Every boundary in the requested range was evaluated and the whole result
    was published in one transaction. There is no other way to reach it."""

    @property
    def is_terminal(self) -> bool:
        return self in (RunStatus.COMPLETED, RunStatus.FAILED)


class BacktestError(ValueError):
    """A run that cannot be honoured, with a stable code."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")


@dataclass(frozen=True, slots=True)
class RunBounds:
    """What one run may consume. Every limit is refused, never truncated.

    A backtest evaluates a rule at every boundary, so its cost grows with the
    range rather than with the answer. These are the numbers that keep one
    request from becoming an unbounded amount of work - and a request beyond
    them is rejected with its own code, because silently running the first N
    candles and calling it the requested range is a lie about the result.
    """

    max_boundaries: int = 2_500
    """Evaluation boundaries in one run - the Phase 8 analytical ceiling."""

    max_positions: int = 200
    """Simulated positions one run may open. Each carries its own ledger."""

    max_warm_up_bars: int = 500
    """History a strategy may demand before it will decide anything.

    There is deliberately no separate limit on the decision trace. Exactly one
    record is written per boundary, so ``max_boundaries`` already bounds it; a
    second number would be a limit that can never be reached, which reads like
    a protection while protecting nothing.
    """

    def check_boundaries(self, count: int) -> None:
        if count > self.max_boundaries:
            raise BacktestError(
                "RESOURCE_LIMIT",
                (
                    f"this configuration would evaluate {count} boundaries; "
                    f"a run covers at most {self.max_boundaries}. Narrow the interval "
                    "rather than receiving part of it"
                ),
            )

    def check_positions(self, count: int) -> None:
        if count > self.max_positions:
            raise BacktestError(
                "RESOURCE_LIMIT",
                f"this run opened {count} positions; a run holds at most {self.max_positions}",
            )


@dataclass(frozen=True, slots=True)
class RunInterval:
    """The market-time window a run evaluates.

    ``start`` is the first boundary the strategy is asked about, and ``end`` the
    last. Both are *requested* moments; the runner snaps them to real candle
    boundaries the same way replay does, backwards only, and reports what it
    resolved to.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        for name, moment in (("start", self.start), ("end", self.end)):
            if moment.tzinfo is None or moment.utcoffset() is None:
                raise BacktestError("INTERVAL_INVALID", f"{name} must be timezone-aware")
        if self.end <= self.start:
            raise BacktestError(
                "INTERVAL_INVALID",
                f"the interval ends at {self.end.isoformat()}, at or before it starts",
            )


@unique
class DecisionOutcome(StrEnum):
    """What became of one boundary. The trace's vocabulary.

    Separating ``REFUSED_BY_RISK`` from ``REFUSED_BY_ENGINE`` matters: the first
    is the risk engine doing its job on a valid intent, the second is the paper
    engine saying the intent could not be a position at all. Both are refusals,
    and neither is a trade.
    """

    NO_SIGNAL = "NO_SIGNAL"
    WAIT = "WAIT"
    ENTERED = "ENTERED"
    """An intent was approved and a simulated position was created."""

    REFUSED_BY_RISK = "REFUSED_BY_RISK"
    REFUSED_BY_ENGINE = "REFUSED_BY_ENGINE"
    EXIT_REQUESTED = "EXIT_REQUESTED"
    HOLDING = "HOLDING"
    """A position is open and the rule asked for nothing."""


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """One evaluated boundary, and why it ended the way it did.

    Enough to explain a run without re-running it, and no more: the confirmed
    inputs the rule read, the rule's own reason, what the risk engine said, and
    the position it produced. Not a stored copy of the analysis - that would be
    a second, unverifiable record of numbers the ledger already holds.
    """

    sequence: int
    as_of: datetime
    outcome: DecisionOutcome
    reason: str
    """The strategy's own words, or the engine's refusal."""

    bars_available: int
    position_id: str | None = None
    risk_outcome: str | None = None
    risk_reason: str | None = None
    direction: str | None = None

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise BacktestError("TRACE_INVALID", "decision sequence starts at one")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise BacktestError("TRACE_INVALID", "a decision is stamped with market time")
        if (self.outcome is DecisionOutcome.ENTERED) and self.position_id is None:
            raise BacktestError("TRACE_INVALID", "an entered decision names its position")


@dataclass(frozen=True, slots=True)
class RunPlan:
    """The frozen description of what a run evaluates.

    Everything that changes a financial result is here, and nothing that does
    not: no audit timestamps, no request ids, no display preferences.
    """

    dataset_id: str
    symbol: str
    driver: Timeframe
    interval: RunInterval
    strategy_id: str
    strategy_version: str
    strategy_parameters: dict[str, str]
    runner_version: str = RUNNER_RULES_VERSION


__all__ = [
    "RUNNER_RULES_VERSION",
    "BacktestError",
    "DecisionOutcome",
    "DecisionRecord",
    "RunBounds",
    "RunInterval",
    "RunPlan",
    "RunStatus",
]
