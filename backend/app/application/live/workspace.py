"""The live workspace: sessions, their tasks, a timeline and subscribers.

Phase 13 Part 2A. Part 1 built one :class:`LiveSession` and the pure market
state beneath it. This module is what an API needs around that session, and
nothing more:

* **lifecycle** - create, inspect, cancel, remove, shut down. One task per
  session, owned here, cancelled and *awaited* here;
* **a timeline** - an ordered, bounded record of what actually happened on
  the stream, written as it happened (never rebuilt from current state);
* **subscribers** - bounded fan-out to transports, with an explicit
  resynchronisation signal when a subscriber falls behind;
* **bounds** - sessions, subscribers, queues, timeline, creation rate,
  concurrent analyses, window size, session duration.

It computes no market fact. Integrity, freshness, availability and alerts are
Part 1's; the analysis is Phase 8's, through ``LiveSession``.

## A subscriber is a reader, never an owner

A subscriber that disconnects, overflows or is closed removes itself and
nothing else. A session ends only when its stream ends, when somebody cancels
or removes it, when it reaches its maximum duration, or when the process shuts
down - and each of those is recorded with its origin.

## Overflow loses notifications, never observations

The authoritative state is the session's candle books, which every observation
reaches before any subscriber hears of it. A subscriber queue that overflows is
cleared and marked: its next item is a resynchronisation demand, so the client
re-reads the authoritative snapshot rather than believing an event history it
did not receive in full.

## Ephemeral

Nothing here is persisted. A process restart loses every session, and a client
that asks for one afterwards is told it does not exist - never handed a new
stream presented as the old one.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum, unique

from app.application.live.catalog import (
    OpenedSource,
    PlaybackPace,
    SimulatedSourceCatalog,
    SourceError,
    SourceErrorKind,
    SourceSummary,
)
from app.application.live.records import RecordKind, StreamRecord
from app.application.live.registry import LiveCapacityError, LiveSessionRegistry
from app.application.live.session import (
    LiveAnalysis,
    LiveAnalysisUnavailableError,
    LiveSession,
)
from app.application.ports.contract_metadata import ContractMetadataProvider
from app.application.ports.market_data import CandleTextParser
from app.application.ports.system import ClockPort
from app.domain.common.enums import Timeframe
from app.domain.live.alerts import AlertCandidate, alert_candidates
from app.domain.live.book import ApplyOutcome
from app.domain.live.events import Observation
from app.domain.live.limits import LiveLimits
from app.domain.live.state import (
    ConnectionState,
    FreshnessPolicy,
    MarketStateSnapshot,
    TerminationReason,
)
from app.domain.risk.sizing import AccountState, RiskPolicy

__all__ = [
    "AnalysisResult",
    "AnalysisStatus",
    "EndOrigin",
    "Lifecycle",
    "LiveWorkspace",
    "LiveWorkspaceError",
    "Notification",
    "NotificationKind",
    "ResyncReason",
    "SessionView",
    "Subscriber",
    "TimelineEntry",
    "TimelineKind",
    "TimelinePage",
    "WorkspaceErrorKind",
    "WorkspaceLimits",
    "playback_freshness",
]

_LOG = logging.getLogger(__name__)

_SESSION_PREFIX = "LS-"


# ----------------------------------------------------------------------
# Bounds
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkspaceLimits:
    max_sessions: int = 8
    """Part 1's registry cap. Ended sessions count until removed or evicted."""

    max_subscribers_per_session: int = 4
    max_subscribers_total: int = 16
    subscriber_queue: int = 64
    """Notifications held for one slow subscriber before it must resync."""

    timeline_retention: int = 500
    """Timeline entries kept per session; older ones are dropped and counted."""

    timeline_page_max: int = 100
    max_session_seconds: float = 1800.0
    """A playback is finite anyway; this bounds a slow one."""

    creations_per_minute: int = 12
    max_concurrent_analyses: int = 2
    min_window_candles: int = 50
    max_window_candles: int = 1000

    def __post_init__(self) -> None:
        for name in (
            "max_sessions",
            "max_subscribers_per_session",
            "max_subscribers_total",
            "subscriber_queue",
            "timeline_retention",
            "timeline_page_max",
            "creations_per_minute",
            "max_concurrent_analyses",
            "min_window_candles",
            "max_window_candles",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")
        if self.max_session_seconds <= 0:
            raise ValueError("max_session_seconds must be positive")
        if self.min_window_candles > self.max_window_candles:
            raise ValueError("min_window_candles exceeds max_window_candles")


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------


@unique
class WorkspaceErrorKind(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    INVALID = "INVALID"
    CAPACITY = "CAPACITY"
    RATE_LIMITED = "RATE_LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    ANALYSIS_UNAVAILABLE = "ANALYSIS_UNAVAILABLE"


class LiveWorkspaceError(RuntimeError):
    def __init__(
        self,
        kind: WorkspaceErrorKind,
        code: str,
        detail: str,
        reasons: dict[Timeframe, tuple[str, ...]] | None = None,
    ) -> None:
        self.kind = kind
        self.code = code
        self.detail = detail
        self.reasons = reasons or {}
        super().__init__(code)


# ----------------------------------------------------------------------
# Timeline
# ----------------------------------------------------------------------


@unique
class TimelineKind(StrEnum):
    SESSION_STARTED = "SESSION_STARTED"
    PROVIDER_SIGNAL = "PROVIDER_SIGNAL"
    CONNECTION_CHANGED = "CONNECTION_CHANGED"
    CANDLE_CONFIRMED = "CANDLE_CONFIRMED"
    CANDLE_LATE_FILL = "CANDLE_LATE_FILL"
    FORMING_UPDATED = "FORMING_UPDATED"
    DUPLICATE_IGNORED = "DUPLICATE_IGNORED"
    CONFLICT_QUARANTINED = "CONFLICT_QUARANTINED"
    OBSERVATION_REJECTED = "OBSERVATION_REJECTED"
    TIMEFRAME_STATUS_CHANGED = "TIMEFRAME_STATUS_CHANGED"
    ANALYSIS_COMPLETED = "ANALYSIS_COMPLETED"
    ANALYSIS_UNAVAILABLE = "ANALYSIS_UNAVAILABLE"
    SESSION_ENDED = "SESSION_ENDED"


_OBSERVATION_KIND: dict[str, TimelineKind] = {
    ApplyOutcome.ACCEPTED.value: TimelineKind.CANDLE_CONFIRMED,
    ApplyOutcome.LATE_FILL.value: TimelineKind.CANDLE_LATE_FILL,
    ApplyOutcome.FORMING_UPDATED.value: TimelineKind.FORMING_UPDATED,
    ApplyOutcome.DUPLICATE.value: TimelineKind.DUPLICATE_IGNORED,
    ApplyOutcome.CONFLICT.value: TimelineKind.CONFLICT_QUARANTINED,
}


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    seq: int
    """Stable order. Assigned once, strictly increasing within a session."""

    kind: TimelineKind
    recorded_at: datetime
    """Server clock: when this was observed or decided. Never market time."""

    code: str
    timeframe: Timeframe | None = None
    market_open_time: datetime | None = None
    market_event_time: datetime | None = None
    sequence: int | None = None
    before: str | None = None
    after: str | None = None
    backfill: bool = False


@dataclass(frozen=True, slots=True)
class TimelinePage:
    entries: tuple[TimelineEntry, ...]
    cursor: int
    """The newest entry's seq, whether or not it is on this page."""

    oldest_retained: int
    """The oldest seq still held. Anything older was dropped for the bound."""

    gap: bool
    """True when the page could not start right after ``after``: entries the
    caller asked for were dropped, and it must not believe it has them all."""


# ----------------------------------------------------------------------
# Views
# ----------------------------------------------------------------------


@unique
class Lifecycle(StrEnum):
    RUNNING = "RUNNING"
    ENDED = "ENDED"


@unique
class EndOrigin(StrEnum):
    STREAM = "STREAM"
    """The stream ended by itself: end of data, a provider failure, overflow."""

    USER_CANCELLED = "USER_CANCELLED"
    DEADLINE = "DEADLINE"
    SHUTDOWN = "SHUTDOWN"


@dataclass(frozen=True, slots=True)
class AnalysisStatus:
    analyses_run: int
    last_market_as_of: datetime | None
    last_requested_at: datetime | None
    last_current: bool
    """Recomputed from the stream at every read (Part 1 rule). About the
    stream - never a claim that the market is current."""


@dataclass(frozen=True, slots=True)
class SessionView:
    session_id: str
    lifecycle: Lifecycle
    end_origin: EndOrigin | None
    created_at: datetime
    ended_at: datetime | None
    source: OpenedSource
    pace: PlaybackPace
    snapshot: MarketStateSnapshot
    alerts: tuple[AlertCandidate, ...]
    latest_confirmed: dict[Timeframe, Observation | None]
    forming: dict[Timeframe, Observation | None]
    freshness: FreshnessPolicy
    limits: LiveLimits
    cursor: int
    oldest_retained: int
    subscribers: int
    analysis: AnalysisStatus


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    analysis: LiveAnalysis
    reused: bool
    """True when the cached calculation was served: its inputs are identical."""

    view: SessionView


# ----------------------------------------------------------------------
# Subscribers
# ----------------------------------------------------------------------


@unique
class NotificationKind(StrEnum):
    TIMELINE = "TIMELINE"
    """One new timeline entry, with the state right after it."""

    STATE = "STATE"
    """The authoritative state, sent after a catch-up."""

    RESYNC_REQUIRED = "RESYNC_REQUIRED"
    END = "END"
    """The session has ended. The transport may close after this."""


@unique
class ResyncReason(StrEnum):
    SUBSCRIBER_OVERFLOW = "SUBSCRIBER_OVERFLOW"
    CATCH_UP_TOO_LARGE = "CATCH_UP_TOO_LARGE"
    """More retained entries to replay than a reader's queue can hold."""

    CURSOR_NOT_RETAINED = "CURSOR_NOT_RETAINED"
    CURSOR_UNKNOWN = "CURSOR_UNKNOWN"


@dataclass(frozen=True, slots=True)
class Notification:
    kind: NotificationKind
    session_id: str
    cursor: int
    entry: TimelineEntry | None = None
    view: SessionView | None = None
    reason: ResyncReason | None = None


class Subscriber:
    """One reader's bounded queue. Overflow clears it and demands a resync."""

    def __init__(
        self, session_id: str, capacity: int, on_close: Callable[[Subscriber], None]
    ) -> None:
        self.session_id = session_id
        self._capacity = capacity
        self._items: deque[Notification] = deque()
        self._ready = asyncio.Event()
        self._closed = False
        self._on_close = on_close
        self.overflows = 0

    @property
    def closed(self) -> bool:
        return self._closed

    def __len__(self) -> int:
        return len(self._items)

    def push(self, item: Notification) -> None:
        if self._closed:
            return
        if len(self._items) >= self._capacity:
            # Behind by a whole queue. The notifications are dropped - never
            # the observations, which are already in the books - and the one
            # thing left to say is that the reader must re-read the truth. The
            # reader is then closed: anything delivered after this would skip
            # ahead of what it holds, so its transport ends once the demand
            # has been read, and it comes back from a snapshot.
            self.overflows += 1
            self._items.clear()
            self._items.append(
                Notification(
                    kind=NotificationKind.RESYNC_REQUIRED,
                    session_id=self.session_id,
                    cursor=item.cursor,
                    reason=ResyncReason.SUBSCRIBER_OVERFLOW,
                )
            )
            self._ready.set()
            self.close()
            return
        self._items.append(item)
        self._ready.set()

    async def next(self, timeout: float) -> Notification | None:
        """The next notification, or ``None`` after ``timeout`` seconds idle."""
        if not self._items and not self._closed:
            self._ready.clear()
            try:
                await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            except TimeoutError:
                return None
        if self._items:
            return self._items.popleft()
        return None

    def close(self) -> None:
        """Stop accepting. Anything already queued - an END notice, say - can
        still be read, so a reader learns *why* its stream stopped."""
        if self._closed:
            return
        self._closed = True
        self._ready.set()
        self._on_close(self)


# ----------------------------------------------------------------------
# Freshness for a playback
# ----------------------------------------------------------------------


def playback_freshness(opened: OpenedSource) -> FreshnessPolicy:
    """A stated receive-clock rule, derived from the playback plan.

    For each timeframe: three times the average receive-time gap between two
    of its candles in this playback, and never under ten seconds. It depends
    on how fast the server plays events, never on any market's hours - there
    is no market session to consult, and none is assumed.
    """
    thresholds: dict[Timeframe, timedelta] = {}
    for timeframe in opened.timeframes:
        count = max(opened.candles_per_timeframe.get(timeframe, 0), 1)
        gap = opened.event_spacing_seconds * opened.total_events / count
        thresholds[timeframe] = timedelta(seconds=max(10.0, 3.0 * gap))
    return FreshnessPolicy(thresholds)


# ----------------------------------------------------------------------
# One session's bookkeeping
# ----------------------------------------------------------------------


@dataclass
class _Entry:
    session_id: str
    session: LiveSession
    source: OpenedSource
    pace: PlaybackPace
    freshness: FreshnessPolicy
    created_at: datetime
    timeline: deque[TimelineEntry]
    task: asyncio.Task[None] | None = None
    seq: int = 0
    ended_at: datetime | None = None
    end_origin: EndOrigin | None = None
    requested_end: EndOrigin | None = None
    subscribers: list[Subscriber] = field(default_factory=list)
    readers: dict[str, Callable[[StreamRecord], None]] = field(default_factory=dict)
    """Extra observers of this session's records, by name (Phase 14 attaches
    shadow runs here). They read; none of them can affect the stream."""

    status: dict[Timeframe, tuple[str, str, str]] = field(default_factory=dict)
    connection: ConnectionState = ConnectionState.INITIALIZING
    analysis_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def lifecycle(self) -> Lifecycle:
        return Lifecycle.ENDED if self.ended_at is not None else Lifecycle.RUNNING


# ----------------------------------------------------------------------
# The workspace
# ----------------------------------------------------------------------


class LiveWorkspace:
    def __init__(
        self,
        *,
        catalog: SimulatedSourceCatalog,
        clock: ClockPort,
        parser: CandleTextParser,
        limits: WorkspaceLimits | None = None,
        live_limits: LiveLimits | None = None,
        contracts: ContractMetadataProvider | None = None,
    ) -> None:
        self._catalog = catalog
        self._clock = clock
        self._parser = parser
        self._limits = limits or WorkspaceLimits()
        self._live_limits = live_limits or LiveLimits()
        self._contracts = contracts
        self._registry = LiveSessionRegistry(max_sessions=self._limits.max_sessions)
        self._entries: dict[str, _Entry] = {}
        self._create_lock = asyncio.Lock()
        self._analyses = asyncio.Semaphore(self._limits.max_concurrent_analyses)
        self._creations: deque[datetime] = deque()
        self._closed = False

    # -- reading ---------------------------------------------------------

    @property
    def limits(self) -> WorkspaceLimits:
        return self._limits

    @property
    def live_limits(self) -> LiveLimits:
        return self._live_limits

    def __len__(self) -> int:
        return len(self._entries)

    def subscriber_count(self) -> int:
        return sum(len(entry.subscribers) for entry in self._entries.values())

    def attach_reader(
        self, session_id: str, name: str, reader: Callable[[StreamRecord], None]
    ) -> None:
        """Let another component read this session's records as they arrive.

        Used by Phase 14 so a shadow run observes an *existing* session rather
        than starting a second stream over the same data. Attaching does not
        keep the session alive and cannot pace, pause or end it.
        """
        entry = self._entry(session_id)
        if entry.lifecycle is Lifecycle.ENDED:
            raise LiveWorkspaceError(
                WorkspaceErrorKind.INVALID,
                "LIVE_SESSION_ENDED",
                "this session has ended and produces no further records",
            )
        entry.readers[name] = reader

    def detach_reader(self, session_id: str, name: str) -> None:
        entry = self._entries.get(session_id)
        if entry is not None:
            entry.readers.pop(name, None)

    def session_object(self, session_id: str) -> LiveSession:
        """The session itself, for a reader that needs its confirmed books.

        Handed out read-only by convention: a :class:`LiveSession` exposes
        state and snapshots, and the workspace remains the only caller of
        ``run()``.
        """
        return self._entry(session_id).session

    def session_ended(self, session_id: str) -> bool:
        """Whether this session is over, for a reader deciding to stop."""
        entry = self._entries.get(session_id)
        return entry is None or entry.lifecycle is Lifecycle.ENDED

    async def sources(self, *, offset: int, limit: int) -> tuple[tuple[SourceSummary, ...], int]:
        try:
            return await self._catalog.list_sources(offset=offset, limit=limit)
        except SourceError as error:
            raise _from_source(error) from None

    def sessions(self) -> tuple[SessionView, ...]:
        """Every session held, newest first. Bounded by the registry cap."""
        ordered = sorted(self._entries.values(), key=lambda e: e.created_at, reverse=True)
        return tuple(self._view(entry) for entry in ordered)

    def get(self, session_id: str) -> SessionView:
        entry = self._entry(session_id)
        self._evaluate(entry)
        return self._view(entry)

    def timeline(self, session_id: str, *, after: int | None, limit: int) -> TimelinePage:
        entry = self._entry(session_id)
        self._evaluate(entry)
        limit = max(1, min(limit, self._limits.timeline_page_max))
        oldest = entry.timeline[0].seq if entry.timeline else entry.seq + 1
        if after is None:
            # The newest page: the last ``limit`` entries, oldest first.
            chosen = list(entry.timeline)[-limit:]
            gap = bool(entry.timeline) and oldest > 1
        else:
            chosen = [item for item in entry.timeline if item.seq > after][:limit]
            gap = after < oldest - 1
        return TimelinePage(
            entries=tuple(chosen), cursor=entry.seq, oldest_retained=oldest, gap=gap
        )

    # -- lifecycle -------------------------------------------------------

    async def create(
        self,
        *,
        source_id: str,
        timeframes: tuple[Timeframe, ...],
        window_candles: int,
        pace: PlaybackPace,
    ) -> SessionView:
        self._refuse_if_closed()
        if not timeframes or len(set(timeframes)) != len(timeframes):
            raise LiveWorkspaceError(
                WorkspaceErrorKind.INVALID,
                "INVALID_TIMEFRAMES",
                "choose one or more distinct timeframes",
            )
        low, high = self._limits.min_window_candles, self._limits.max_window_candles
        if not low <= window_candles <= high:
            raise LiveWorkspaceError(
                WorkspaceErrorKind.INVALID,
                "INVALID_WINDOW",
                f"window_candles must be between {low} and {high}",
            )
        async with self._create_lock:
            self._refuse_if_closed()
            self._admit()
            try:
                opened = await self._catalog.open(
                    source_id,
                    timeframes=timeframes,
                    window_candles=min(window_candles, self._live_limits.max_closed_candles),
                    pace=pace,
                )
            except SourceError as error:
                raise _from_source(error) from None
            except Exception as error:  # noqa: BLE001 - reported safely, below
                # A source that fails in a way it did not describe. The message
                # may carry a connection string or a path, so only the type is
                # recorded, and the caller gets a typed refusal - never a 500
                # with a traceback in the server log.
                _LOG.error(
                    "live source failed to open",
                    extra={"error_type": type(error).__name__},
                )
                raise LiveWorkspaceError(
                    WorkspaceErrorKind.UNAVAILABLE,
                    "SOURCE_OPEN_FAILED",
                    "the dataset could not be opened for streaming",
                ) from None
            # Shutdown may have begun while the source was being read; a task
            # started now would outlive it.
            self._refuse_if_closed()
            return self._start(opened, pace)

    def _refuse_if_closed(self) -> None:
        if self._closed:
            raise LiveWorkspaceError(
                WorkspaceErrorKind.UNAVAILABLE, "LIVE_SHUTTING_DOWN", "the server is stopping"
            )

    async def cancel(self, session_id: str) -> SessionView:
        """End a session's stream and keep it for inspection. Idempotent."""
        entry = self._entry(session_id)
        await self._stop(entry, EndOrigin.USER_CANCELLED)
        return self._view(entry)

    async def remove(self, session_id: str) -> None:
        """End a session (if running) and release its slot. Idempotent-ish:
        a second remove is a NOT_FOUND, because the session no longer exists."""
        entry = self._entry(session_id)
        await self._stop(entry, EndOrigin.USER_CANCELLED)
        self._forget(entry)

    async def shutdown(self) -> None:
        """Cancel every session task and wait for all of them. Nothing orphaned.

        Closing first, then taking the creation lock, means a creation already
        in flight finishes - and, seeing the workspace closed, refuses - before
        the sessions are collected; none can start after this returns.
        """
        self._closed = True
        async with self._create_lock:
            entries = list(self._entries.values())
        for entry in entries:
            self._request_end(entry, EndOrigin.SHUTDOWN)
        tasks = [entry.task for entry in entries if entry.task is not None]
        if tasks:
            await asyncio.wait(tasks)
        for entry in entries:
            # A task cancelled before its first step never ran its own
            # handler, so every session is finished here as well.
            self._finish(entry)
            for subscriber in list(entry.subscribers):
                subscriber.close()

    # -- analysis --------------------------------------------------------

    async def analyse(
        self,
        session_id: str,
        *,
        account: AccountState | None,
        risk_policy: RiskPolicy | None,
    ) -> AnalysisResult:
        """Phase 8's analysis over what is confirmed and available now.

        One analysis at a time per session - a second request waits and is then
        served from the Part 1 cache if nothing changed - and a small global
        bound across sessions. Never triggered by an event or a heartbeat.
        """
        entry = self._entry(session_id)
        async with entry.analysis_lock, self._analyses:
            before = entry.session.analyses_run
            try:
                analysis = await entry.session.confirmed_analysis(
                    account=account, risk_policy=risk_policy
                )
            except LiveAnalysisUnavailableError as error:
                self._append(
                    entry,
                    TimelineEntry(
                        seq=0,
                        kind=TimelineKind.ANALYSIS_UNAVAILABLE,
                        recorded_at=self._clock.now(),
                        code=error.code,
                    ),
                )
                raise LiveWorkspaceError(
                    WorkspaceErrorKind.ANALYSIS_UNAVAILABLE,
                    error.code,
                    "no timeframe is available for a confirmed analysis right now",
                    reasons=error.reasons,
                ) from None
            reused = entry.session.analyses_run == before
            self._append(
                entry,
                TimelineEntry(
                    seq=0,
                    kind=TimelineKind.ANALYSIS_COMPLETED,
                    recorded_at=self._clock.now(),
                    code="REUSED" if reused else "COMPUTED",
                    market_event_time=analysis.market_as_of,
                    after=",".join(tf.value for tf in analysis.included),
                ),
            )
            return AnalysisResult(analysis=analysis, reused=reused, view=self._view(entry))

    # -- subscribing -----------------------------------------------------

    def subscribe(self, session_id: str, *, after: int | None) -> Subscriber:
        """A bounded reader, primed so it cannot miss what happened since
        ``after`` without being told.

        * ``after`` is the current cursor, or absent - nothing to catch up;
        * older, and still retained - the missed entries;
        * older than retention, or newer than anything this process issued
          (a cursor from before a restart) - a resynchronisation demand.

        Every subscription then receives the authoritative state, so a reader
        never starts from what it remembers.
        """
        entry = self._entry(session_id)
        if len(entry.subscribers) >= self._limits.max_subscribers_per_session:
            raise LiveWorkspaceError(
                WorkspaceErrorKind.CAPACITY,
                "LIVE_SUBSCRIBERS_FULL",
                f"at most {self._limits.max_subscribers_per_session} readers per session",
            )
        if self.subscriber_count() >= self._limits.max_subscribers_total:
            raise LiveWorkspaceError(
                WorkspaceErrorKind.CAPACITY,
                "LIVE_SUBSCRIBERS_FULL",
                f"at most {self._limits.max_subscribers_total} readers in total",
            )
        subscriber = Subscriber(
            session_id,
            self._limits.subscriber_queue,
            on_close=lambda item: _discard(entry.subscribers, item),
        )
        entry.subscribers.append(subscriber)
        self._evaluate(entry)
        oldest = entry.timeline[0].seq if entry.timeline else entry.seq + 1
        if after is not None and after > entry.seq:
            subscriber.push(self._resync(entry, ResyncReason.CURSOR_UNKNOWN))
        elif after is not None and after < oldest - 1:
            subscriber.push(self._resync(entry, ResyncReason.CURSOR_NOT_RETAINED))
        elif after is not None and sum(1 for item in entry.timeline if item.seq > after) >= (
            self._limits.subscriber_queue - 1
        ):
            # Replaying this much would overflow the queue on the way in. The
            # reader re-reads the timeline over REST instead; the state below
            # brings it to the current cursor.
            subscriber.push(self._resync(entry, ResyncReason.CATCH_UP_TOO_LARGE))
        elif after is not None and after < entry.seq:
            for item in entry.timeline:
                if item.seq > after:
                    subscriber.push(
                        Notification(
                            kind=NotificationKind.TIMELINE,
                            session_id=entry.session_id,
                            cursor=item.seq,
                            entry=item,
                        )
                    )
        subscriber.push(self._state_notice(entry))
        if entry.lifecycle is Lifecycle.ENDED:
            subscriber.push(self._end_notice(entry))
        return subscriber

    def poll(self, session_id: str) -> int:
        """Re-evaluate time-driven state (freshness) and return the cursor.

        Called by a transport on its heartbeat. Reading the clock may reveal a
        transition - a timeframe going STALE with no event at all - and that is
        recorded as observed now. Nothing about a candle changes: a heartbeat
        is not an observation.
        """
        entry = self._entry(session_id)
        self._evaluate(entry)
        return entry.seq

    # -- internals: lifecycle --------------------------------------------

    def _admit(self) -> None:
        now = self._clock.now()
        window = timedelta(minutes=1)
        while self._creations and now - self._creations[0] >= window:
            self._creations.popleft()
        if len(self._creations) >= self._limits.creations_per_minute:
            raise LiveWorkspaceError(
                WorkspaceErrorKind.RATE_LIMITED,
                "LIVE_CREATE_RATE_LIMITED",
                f"at most {self._limits.creations_per_minute} sessions per minute",
            )
        if len(self._registry) >= self._registry.capacity:
            # Only an *ended* session nobody is reading may make room. A
            # running stream, or one on somebody's screen, is never evicted.
            idle = [
                entry
                for entry in self._entries.values()
                if entry.lifecycle is Lifecycle.ENDED and not entry.subscribers
            ]
            if not idle:
                raise LiveWorkspaceError(
                    WorkspaceErrorKind.CAPACITY,
                    "LIVE_CAPACITY",
                    f"at most {self._registry.capacity} live sessions; cancel and remove one",
                )
            self._forget(min(idle, key=lambda entry: entry.ended_at or entry.created_at))
        self._creations.append(now)

    def _start(self, opened: OpenedSource, pace: PlaybackPace) -> SessionView:
        session_id = f"{_SESSION_PREFIX}{secrets.token_hex(12)}"
        freshness = playback_freshness(opened)
        holder: list[_Entry] = []
        session = LiveSession(
            provider=opened.provider,
            symbol=opened.summary.instrument_label,
            timeframes=opened.timeframes,
            clock=self._clock,
            parser=self._parser,
            freshness=freshness,
            limits=self._live_limits,
            contracts=self._contracts,
            observer=lambda record: self._on_record(holder[0], record),
        )
        entry = _Entry(
            session_id=session_id,
            session=session,
            source=opened,
            pace=pace,
            freshness=freshness,
            created_at=self._clock.now(),
            timeline=deque(maxlen=self._limits.timeline_retention),
        )
        holder.append(entry)
        try:
            self._registry.register(session_id, session)
        except LiveCapacityError:  # pragma: no cover - _admit made room first
            raise LiveWorkspaceError(
                WorkspaceErrorKind.CAPACITY, "LIVE_CAPACITY", "no live session slot is free"
            ) from None
        self._entries[session_id] = entry
        entry.status = self._status_of(entry)
        self._append(
            entry,
            TimelineEntry(
                seq=0,
                kind=TimelineKind.SESSION_STARTED,
                recorded_at=entry.created_at,
                code=opened.summary.source_id,
                after=",".join(tf.value for tf in opened.timeframes),
            ),
        )
        entry.task = asyncio.create_task(self._drive(entry), name=f"live-{session_id}")
        return self._view(entry)

    async def _drive(self, entry: _Entry) -> None:
        """Run one session's stream; record exactly how it ended."""
        try:
            await asyncio.wait_for(entry.session.run(), timeout=self._limits.max_session_seconds)
        except TimeoutError:
            entry.requested_end = entry.requested_end or EndOrigin.DEADLINE
        except asyncio.CancelledError:
            self._finish(entry)
            raise
        except Exception as error:  # noqa: BLE001 - reported safely, below
            _LOG.error(
                "live session task failed",
                extra={"session_id": entry.session_id, "error_type": type(error).__name__},
            )
            entry.session.state().terminate(TerminationReason.PROVIDER_ERROR)
        self._finish(entry)

    def _finish(self, entry: _Entry) -> None:
        if entry.ended_at is not None:
            return
        state = entry.session.state()
        if state.connection is not ConnectionState.TERMINATED:
            # Either the task was cancelled before it ran a single step, or the
            # stream stopped without saying it had ended. The first is a
            # cancellation; the second is a provider fault, not an end of data.
            state.terminate(
                TerminationReason.CANCELLED
                if entry.requested_end is not None
                else TerminationReason.PROVIDER_ERROR
            )
        entry.end_origin = entry.requested_end or EndOrigin.STREAM
        entry.ended_at = self._clock.now()
        self._after_change(entry)
        reason = state.termination_reason.value if state.termination_reason else "UNKNOWN"
        self._append(
            entry,
            TimelineEntry(
                seq=0,
                kind=TimelineKind.SESSION_ENDED,
                recorded_at=entry.ended_at,
                code=reason,
                after=entry.end_origin.value,
            ),
        )
        notice = self._end_notice(entry)
        for subscriber in list(entry.subscribers):
            subscriber.push(notice)

    def _request_end(self, entry: _Entry, origin: EndOrigin) -> None:
        if entry.ended_at is None and entry.requested_end is None:
            entry.requested_end = origin
        if entry.task is not None and not entry.task.done():
            entry.task.cancel()

    async def _stop(self, entry: _Entry, origin: EndOrigin) -> None:
        self._request_end(entry, origin)
        if entry.task is not None and not entry.task.done():
            # ``wait`` rather than ``await task``: the task's own cancellation
            # must not be mistaken for this caller being cancelled.
            await asyncio.wait({entry.task})
        self._finish(entry)

    def _forget(self, entry: _Entry) -> None:
        for subscriber in list(entry.subscribers):
            subscriber.close()
        self._registry.remove(entry.session_id)
        self._entries.pop(entry.session_id, None)

    # -- internals: records ----------------------------------------------

    def _on_record(self, entry: _Entry, record: StreamRecord) -> None:
        if record.kind is RecordKind.OBSERVATION:
            kind = _OBSERVATION_KIND.get(record.outcome, TimelineKind.CANDLE_CONFIRMED)
        elif record.kind is RecordKind.REJECTED:
            kind = TimelineKind.OBSERVATION_REJECTED
        else:
            kind = TimelineKind.PROVIDER_SIGNAL
        self._append(
            entry,
            TimelineEntry(
                seq=0,
                kind=kind,
                recorded_at=record.received_at,
                code=record.outcome,
                timeframe=record.timeframe,
                market_open_time=record.open_time,
                market_event_time=record.event_time,
                sequence=record.sequence,
                backfill=record.backfill,
            ),
        )
        self._notify_readers(entry, record)
        self._after_change(entry)

    def _notify_readers(self, entry: _Entry, record: StreamRecord) -> None:
        """Give the record to every attached reader.

        One provider stream, many readers: a shadow run attaches here rather
        than opening a second stream over the same dataset, so two observers
        of one session cannot drift apart. A reader that raises is dropped
        from the fan-out and logged by type - it is a reader, and the market
        state it was reading is still correct.
        """
        for name, reader in list(entry.readers.items()):
            try:
                reader(record)
            except Exception as error:  # noqa: BLE001 - reported safely, below
                entry.readers.pop(name, None)
                _LOG.error(
                    "live session reader failed and was detached",
                    extra={
                        "session_id": entry.session_id,
                        "reader": name,
                        "error_type": type(error).__name__,
                    },
                )

    def _after_change(self, entry: _Entry) -> None:
        state = entry.session.state()
        if state.connection is not entry.connection:
            before, entry.connection = entry.connection, state.connection
            self._append(
                entry,
                TimelineEntry(
                    seq=0,
                    kind=TimelineKind.CONNECTION_CHANGED,
                    recorded_at=self._clock.now(),
                    code=state.connection.value,
                    before=before.value,
                    after=state.connection.value,
                ),
            )
        self._evaluate(entry)

    def _evaluate(self, entry: _Entry) -> None:
        """Record every per-timeframe status change since the last look."""
        current = self._status_of(entry)
        now = self._clock.now()
        for timeframe, after in current.items():
            before = entry.status.get(timeframe)
            if before is not None and before != after:
                self._append(
                    entry,
                    TimelineEntry(
                        seq=0,
                        kind=TimelineKind.TIMEFRAME_STATUS_CHANGED,
                        recorded_at=now,
                        code=after[2],
                        timeframe=timeframe,
                        before="/".join(before),
                        after="/".join(after),
                    ),
                )
        entry.status = current

    def _status_of(self, entry: _Entry) -> dict[Timeframe, tuple[str, str, str]]:
        snapshot = entry.session.snapshot()
        return {
            item.book.timeframe: (
                item.book.integrity.value,
                item.freshness.value,
                item.availability.value,
            )
            for item in snapshot.timeframes
        }

    def _append(self, entry: _Entry, item: TimelineEntry) -> None:
        entry.seq += 1
        stamped = TimelineEntry(
            seq=entry.seq,
            kind=item.kind,
            recorded_at=item.recorded_at,
            code=item.code,
            timeframe=item.timeframe,
            market_open_time=item.market_open_time,
            market_event_time=item.market_event_time,
            sequence=item.sequence,
            before=item.before,
            after=item.after,
            backfill=item.backfill,
        )
        entry.timeline.append(stamped)
        if entry.subscribers:
            # One view per entry, shared by every reader of this session.
            view = self._view(entry)
            notice = Notification(
                kind=NotificationKind.TIMELINE,
                session_id=entry.session_id,
                cursor=stamped.seq,
                entry=stamped,
                view=view,
            )
            for subscriber in list(entry.subscribers):
                subscriber.push(notice)

    # -- internals: views ------------------------------------------------

    def _entry(self, session_id: str) -> _Entry:
        entry = self._entries.get(session_id)
        if entry is None:
            raise LiveWorkspaceError(
                WorkspaceErrorKind.NOT_FOUND,
                "LIVE_SESSION_NOT_FOUND",
                "no such live session in this server process; sessions are not kept "
                "across restarts",
            )
        return entry

    def _view(self, entry: _Entry) -> SessionView:
        session = entry.session
        state = session.state()
        snapshot = session.snapshot()
        latest: dict[Timeframe, Observation | None] = {}
        forming: dict[Timeframe, Observation | None] = {}
        for timeframe in state.timeframes:
            book = state.book(timeframe)
            confirmed = book.confirmed()
            latest[timeframe] = confirmed[-1] if confirmed else None
            forming[timeframe] = book.forming
        kept = session.last_analysis()
        analysis = AnalysisStatus(
            analyses_run=session.analyses_run,
            last_market_as_of=None if kept is None else kept[0].market_as_of,
            last_requested_at=None if kept is None else kept[0].requested_at,
            last_current=False if kept is None else kept[1],
        )
        return SessionView(
            session_id=entry.session_id,
            lifecycle=entry.lifecycle,
            end_origin=entry.end_origin,
            created_at=entry.created_at,
            ended_at=entry.ended_at,
            source=entry.source,
            pace=entry.pace,
            snapshot=snapshot,
            alerts=alert_candidates(snapshot),
            latest_confirmed=latest,
            forming=forming,
            freshness=entry.freshness,
            limits=self._live_limits,
            cursor=entry.seq,
            oldest_retained=entry.timeline[0].seq if entry.timeline else entry.seq + 1,
            subscribers=len(entry.subscribers),
            analysis=analysis,
        )

    def _state_notice(self, entry: _Entry) -> Notification:
        return Notification(
            kind=NotificationKind.STATE,
            session_id=entry.session_id,
            cursor=entry.seq,
            view=self._view(entry),
        )

    def _end_notice(self, entry: _Entry) -> Notification:
        return Notification(
            kind=NotificationKind.END,
            session_id=entry.session_id,
            cursor=entry.seq,
            view=self._view(entry),
        )

    def _resync(self, entry: _Entry, reason: ResyncReason) -> Notification:
        return Notification(
            kind=NotificationKind.RESYNC_REQUIRED,
            session_id=entry.session_id,
            cursor=entry.seq,
            reason=reason,
        )


def _discard(items: list[Subscriber], item: Subscriber) -> None:
    if item in items:
        items.remove(item)


def _from_source(error: SourceError) -> LiveWorkspaceError:
    kind = {
        SourceErrorKind.NOT_FOUND: WorkspaceErrorKind.NOT_FOUND,
        SourceErrorKind.INVALID: WorkspaceErrorKind.INVALID,
        SourceErrorKind.UNAVAILABLE: WorkspaceErrorKind.UNAVAILABLE,
    }[error.kind]
    return LiveWorkspaceError(kind, error.code, error.detail)
