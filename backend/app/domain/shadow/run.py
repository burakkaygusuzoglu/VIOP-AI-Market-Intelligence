"""What a shadow run is, and what it is allowed to hold (Phase 14 Part 1).

Shadow Mode watches confirmed market evidence arrive and writes down what the
*existing* deterministic policies would have decided at each boundary. Master
spec section 78: the system analyses market data and "opens no paper or real
position automatically".

## What this module is not

It is not a second strategy, a second analysis engine or a second risk engine.
It holds identity, status and bounds; the decisions come from the Phase 12
policy, the readings from the Phase 1 engine, the availability from Phase 13
and the sizing from Phase 3.

## Pure

Stdlib and ``app.domain.common`` only. No FastAPI, no SQLAlchemy, no broker,
no transport: a shadow run cannot reach anything that could place an order,
and the import contracts hold that open rather than trusting it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

__all__ = [
    "RUN_PREFIX",
    "EndReason",
    "ShadowError",
    "ShadowLimits",
    "ShadowRunStatus",
]

RUN_PREFIX = "SR-"
CONFIGURATION_PREFIX = "SC-"


class ShadowError(Exception):
    """A refusal with a stable code. Never carries a provider's own text."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


@unique
class ShadowRunStatus(StrEnum):
    OBSERVING = "OBSERVING"
    ENDED = "ENDED"
    """No longer observing. Why it ended is :class:`EndReason`; a run that
    ended is never described as complete coverage of an interval, because a
    stream has no promised end."""


@unique
class EndReason(StrEnum):
    STREAM_ENDED = "STREAM_ENDED"
    """The stream said it was over. For a played dataset that is the end of
    the data, not the end of a market."""

    CANCELLED = "CANCELLED"
    OBSERVATION_LIMIT = "OBSERVATION_LIMIT"
    """The run reached its bound and stopped observing, on purpose."""

    PROVIDER_ERROR = "PROVIDER_ERROR"
    EVALUATION_ERROR = "EVALUATION_ERROR"
    """A policy or an engine failed. The run stops rather than continuing with
    an unexplained hole in its journal."""

    SHUTDOWN = "SHUTDOWN"
    """The application stopped cleanly and ended the run on its way out."""

    INTERRUPTED = "INTERRUPTED"
    """Found still marked as observing when the application started: the
    process ended without closing it. Nothing after its last recorded entry
    was observed, and nothing is invented to fill the gap - the run is closed
    exactly where its journal stops."""


@dataclass(frozen=True, slots=True)
class ShadowLimits:
    """Every bound in one place. Reached is refused or recorded, never absorbed."""

    max_runs: int = 4
    """Concurrent runs in one process. Each holds a live session and a task."""

    max_observations: int = 5_000
    """Evaluated boundaries per run. The run ends at the bound and says so."""

    max_journal_page: int = 100
    max_pending_records: int = 512
    """Stream records waiting to be turned into journal entries. Beyond this
    the run stops: a shadow journal with an unrecorded hole is worse than a
    run that admits it could not keep up."""

    max_excluded_reasons: int = 8
    max_reason_length: int = 300
    max_evidence_timeframes: int = 8

    max_outcome_window: int = 24
    """Driver candles a decision's price development is followed for. A window
    has to end somewhere, and an unbounded one would keep every decision of a
    long run open forever. A window that never closed is reported unavailable,
    never as "nothing happened"."""

    max_open_watches: int = 500
    """Decisions being followed at once. Past this, a new entry intent is
    recorded with no outcome rather than silently dropped from the follow-up."""

    max_outcome_page: int = 100

    def __post_init__(self) -> None:
        for name in (
            "max_runs",
            "max_observations",
            "max_journal_page",
            "max_pending_records",
            "max_excluded_reasons",
            "max_reason_length",
            "max_evidence_timeframes",
            "max_outcome_window",
            "max_open_watches",
            "max_outcome_page",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")
