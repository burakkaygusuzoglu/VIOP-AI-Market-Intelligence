"""What replay needs from storage, and the shapes it passes around.

Replay orchestrates engines that already exist; what it owns is a cursor and an
immutable dataset. These ports are therefore small: read candles, read and
advance a session, and record which paper positions a session opened.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from app.domain.replay import ReplayCursor, ReplayPlan


class ReplayStoreUnavailableError(RuntimeError):
    """Storage could not be reached. Never a market statement."""


class DatasetIdentityConflictError(RuntimeError):
    """A stored dataset does not describe what its id is being asked to stand for.

    Unreachable while the digest commits to every identity fact, which is the
    point: it turns "the digest covers everything" from an argument into a
    checked condition, and a future change that narrowed the digest would be
    caught here rather than by serving the wrong market's candles.
    """


class DuplicateSessionError(RuntimeError):
    """Two creates raced on one idempotency key; the loser re-reads the winner."""


class SessionConflictError(RuntimeError):
    """The session moved on while this command was being applied."""

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"expected version {expected}, found {actual}")


class UnknownSessionError(LookupError):
    """No such replay session."""


@dataclass(frozen=True, slots=True)
class TimeframeSummary:
    """What one timeframe of a dataset holds. Facts, never a quality score."""

    timeframe: Timeframe
    rows: int
    first_open_time: datetime
    last_open_time: datetime
    last_coverage_end: datetime


@dataclass(frozen=True, slots=True)
class StoredDataset:
    """An immutable dataset, without its candles."""

    dataset_id: str
    symbol: str
    total_rows: int
    timeframes: tuple[TimeframeSummary, ...]
    created_at: datetime

    def summary_for(self, timeframe: Timeframe) -> TimeframeSummary | None:
        for item in self.timeframes:
            if item.timeframe is timeframe:
                return item
        return None


@dataclass(frozen=True, slots=True)
class StoredSession:
    """A persisted replay session: its plan, its cursor, its audit stamps."""

    session_id: str
    plan: ReplayPlan
    cursor: ReplayCursor
    dataset: StoredDataset
    last_command_key: str | None
    command_target_revealed: int | None
    """What the command named by ``last_command_key`` was going to reach."""

    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """One row of the session list. No candles, no analysis, no positions."""

    session_id: str
    dataset_id: str
    symbol: str
    driver: Timeframe
    replay_start: datetime
    as_of: datetime
    revealed_driver_candles: int
    driver_total_candles: int
    state: str
    version: int
    created_at: datetime
    updated_at: datetime


class ReplayStore(Protocol):
    """Datasets, sessions and links. Candles are read in bounded windows."""

    async def save_dataset(
        self, dataset: StoredDataset, candles: Mapping[Timeframe, Sequence[Candle]]
    ) -> StoredDataset:
        """Store a dataset once. An existing identity is returned unchanged -
        identical content is the same dataset, not a second copy."""

    async def get_dataset(self, dataset_id: str) -> StoredDataset | None: ...

    async def list_datasets(
        self, *, offset: int, limit: int
    ) -> tuple[tuple[StoredDataset, ...], int]:
        """One bounded page of stored datasets, newest first, with the total.

        Added in Phase 12 Part 2A so a backtest can be pointed at a dataset
        that already exists. It reads the dataset rows and their per-timeframe
        summaries - never candles.
        """
        ...

    async def candles(
        self,
        dataset_id: str,
        timeframe: Timeframe,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> tuple[Candle, ...]:
        """Candles of one timeframe, in chronological order.

        ``until`` bounds by *coverage end*, which is the availability rule, so
        nothing unrevealed is ever materialised, let alone sent. ``since``
        bounds the other end, for a caller that wants one bar rather than the
        prefix that ends with it.
        """

    async def create_session(
        self,
        session: StoredSession,
        *,
        idempotency_key: str,
        fingerprint: str,
        now: datetime,
    ) -> StoredSession: ...

    async def find_session_by_key(self, idempotency_key: str) -> tuple[StoredSession, str] | None:
        """The session created with this key, with its request fingerprint."""

    async def get_session(self, session_id: str) -> StoredSession | None: ...

    async def list_sessions(
        self, *, offset: int, limit: int
    ) -> tuple[tuple[SessionSummary, ...], int]:
        """One bounded page of sessions, newest first, with the total."""

    async def advance_session(
        self,
        session_id: str,
        cursor: ReplayCursor,
        *,
        expected_version: int,
        command_key: str | None,
        command_target: int | None,
        now: datetime,
    ) -> StoredSession:
        """Move the cursor if the session is still at ``expected_version``.

        ``command_target`` is the revealed count the command is working
        towards; stored with the key so a retry can finish it rather than
        repeating it.

        Raises :class:`SessionConflictError` otherwise, so two tabs stepping at
        once can never advance two candles.
        """

    async def link_position(self, session_id: str, position_id: str, now: datetime) -> None:
        """Record that this paper position belongs to this replay session."""

    async def linked_positions(self, session_id: str) -> tuple[str, ...]:
        """Every paper position id this session opened, in creation order."""

    async def session_of_position(self, position_id: str) -> str | None: ...
