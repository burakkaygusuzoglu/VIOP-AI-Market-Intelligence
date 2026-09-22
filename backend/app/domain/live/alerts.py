"""Alert candidates about the data itself (Phase 13 Part 1).

These describe the health of a stream: stale, disconnected, gapped,
discontinuous, conflicted, overloaded, receiving invalid events. None of them
is a trade idea, and none of them can become one - there is no path from this
module to a position, a paper order or a risk calculation.

Derived from a snapshot rather than accumulated, so the same state always
yields the same candidates and nothing has to be deduplicated or expired.
Delivery - to a screen, a timeline, a notification - is Part 2's.

Market-setup alerts are deliberately absent. They depend on running the
analysis pipeline, which Part 1 does only on explicit request, and an alert
kind with nothing that produces it would be a promise the build cannot keep.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from app.domain.common.enums import Timeframe
from app.domain.live.book import Integrity
from app.domain.live.state import (
    ConnectionState,
    Freshness,
    MarketStateSnapshot,
    TerminationReason,
)

__all__ = ["AlertCandidate", "AlertKind", "alert_candidates"]


@unique
class AlertKind(StrEnum):
    DATA_STALE = "DATA_STALE"
    PROVIDER_DISCONNECTED = "PROVIDER_DISCONNECTED"
    RECOVERY_PENDING = "RECOVERY_PENDING"
    DATA_GAP = "DATA_GAP"
    DATA_DISCONTINUITY = "DATA_DISCONTINUITY"
    DATA_CONFLICT = "DATA_CONFLICT"
    CONTINUITY_UNPROVEN = "CONTINUITY_UNPROVEN"
    """A forming candle shows closed intervals that were never received."""

    INVALID_OBSERVATIONS = "INVALID_OBSERVATIONS"
    STREAM_OVERLOADED = "STREAM_OVERLOADED"
    STREAM_ENDED = "STREAM_ENDED"


@dataclass(frozen=True, slots=True)
class AlertCandidate:
    kind: AlertKind
    timeframe: Timeframe | None
    detail: str


def alert_candidates(snapshot: MarketStateSnapshot) -> tuple[AlertCandidate, ...]:
    found: list[AlertCandidate] = []
    connection = snapshot.connection
    if connection is ConnectionState.DISCONNECTED:
        found.append(
            AlertCandidate(AlertKind.PROVIDER_DISCONNECTED, None, "the provider disconnected")
        )
    elif connection is ConnectionState.RECOVERING:
        found.append(
            AlertCandidate(
                AlertKind.RECOVERY_PENDING,
                None,
                "reconnected; continuity since the disconnect is not yet proven",
            )
        )
    elif connection is ConnectionState.TERMINATED:
        if snapshot.termination_reason is TerminationReason.OVERLOADED:
            found.append(
                AlertCandidate(
                    AlertKind.STREAM_OVERLOADED,
                    None,
                    "the stream outran its bounded buffer; events were lost and it was stopped",
                )
            )
        else:
            reason = snapshot.termination_reason.value if snapshot.termination_reason else "?"
            found.append(AlertCandidate(AlertKind.STREAM_ENDED, None, f"stream ended: {reason}"))

    for item in snapshot.timeframes:
        timeframe = item.book.timeframe
        if item.freshness is Freshness.STALE:
            found.append(
                AlertCandidate(AlertKind.DATA_STALE, timeframe, "no recent valid observation")
            )
        integrity = item.book.integrity
        if integrity is Integrity.GAPPED:
            found.append(
                AlertCandidate(
                    AlertKind.DATA_GAP,
                    timeframe,
                    f"{item.book.missing_sequences} closed candle(s) known to be missing"
                    + (" (more than recorded)" if item.book.missing_overflowed else ""),
                )
            )
        elif integrity is Integrity.DISCONTINUOUS:
            found.append(
                AlertCandidate(
                    AlertKind.DATA_DISCONTINUITY,
                    timeframe,
                    f"{item.book.temporal_gaps} unexplained jump(s) in time and "
                    f"{item.book.sequence_mismatches} sequence/time disagreement(s); "
                    "no jump is assumed to be a session break",
                )
            )
        elif integrity is Integrity.UNVERIFIED and item.book.forming_ahead:
            found.append(
                AlertCandidate(
                    AlertKind.CONTINUITY_UNPROVEN,
                    timeframe,
                    "a forming candle is ahead of the confirmed candles",
                )
            )
        elif integrity is Integrity.CONFLICTED:
            found.append(
                AlertCandidate(
                    AlertKind.DATA_CONFLICT,
                    timeframe,
                    "a conflicting correction was received and quarantined",
                )
            )

    rejected = sum(snapshot.rejection_counts.values())
    if rejected:
        found.append(
            AlertCandidate(
                AlertKind.INVALID_OBSERVATIONS, None, f"{rejected} observation(s) refused"
            )
        )
    return tuple(found)
