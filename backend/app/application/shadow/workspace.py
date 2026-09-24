"""Shadow run lifecycle over an existing live session (Phase 14 Part 2A).

Part 1 built the observer and the journal. This is the thing that owns runs:
it creates them against a live session somebody already started, keeps the
observing task, cancels on request, and reads the journal back in bounded
pages. It decides nothing about the market - every answer in it came from the
runner, which got it from the existing engines.

## One stream, many readers

A shadow run attaches to a :class:`LiveWorkspace` session as a *reader*. It
never opens a provider stream of its own, so two runs over one session cannot
see two different markets, and a browser subscribing or closing an SSE
connection has no effect on any run: subscribers and runs are different things
with different lifetimes.

## Where a run starts observing

At the **next eligible confirmed boundary** after it is created. Candles the
session already consumed are not replayed into it: a decision recorded now
about a boundary that passed before the run existed would be a retrospective
signal, and the whole point of Shadow Mode is that it only ever knew what it
could have known. A catch-up mode would be a different, explicitly requested
feature; the spec does not ask for one, so there is none.

## Creating a run twice

Creation carries an attempt key. The key is claimed in the database, not in
this process, so a retried request, a double-clicked button and two workers
racing all produce one run. The same key with a different configuration is a
conflict rather than a second run wearing the first one's name.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum, unique

from app.application.live.records import StreamRecord
from app.application.live.workspace import LiveWorkspace, LiveWorkspaceError, SessionView
from app.application.ports.system import ClockPort
from app.application.shadow.ports import (
    AttemptKeyHeldError,
    ShadowJournalStore,
    StoredShadowRun,
)
from app.application.shadow.service import ShadowRequest, ShadowRunner
from app.domain.backtest.policy import StrategyPolicy
from app.domain.backtest.registry import SUPPORTED_STRATEGIES
from app.domain.backtest.strategies.ema_crossover import (
    IDENTIFIER as EMA_IDENTIFIER,
)
from app.domain.backtest.strategies.ema_crossover import (
    EmaCrossoverStrategy,
)
from app.domain.common.enums import Timeframe
from app.domain.risk.sizing import AccountState, RiskPolicy
from app.domain.shadow.decision import JournalEntryKind, OperationalKind, ShadowDecision
from app.domain.shadow.identity import RUN_PREFIX, decision_key
from app.domain.shadow.outcome import ShadowOutcomeRecord
from app.domain.shadow.run import EndReason, ShadowError, ShadowLimits, ShadowRunStatus

__all__ = [
    "ShadowCapability",
    "recover_interrupted_runs",
    "ShadowWorkspace",
    "ShadowWorkspaceError",
    "WorkspaceErrorKind",
]

_LOG = logging.getLogger(__name__)


@unique
class WorkspaceErrorKind(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    INVALID = "INVALID"
    CONFLICT = "CONFLICT"
    CAPACITY = "CAPACITY"
    UNAVAILABLE = "UNAVAILABLE"


class ShadowWorkspaceError(RuntimeError):
    def __init__(self, kind: WorkspaceErrorKind, code: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ShadowCapability:
    """What this deployment can actually do, stated rather than implied."""

    available: bool
    reason: str
    provenance: str
    market_currency: str
    financial_metadata: bool
    """Whether a verified contract metadata provider is composed. False here
    means no run can produce an approved quantity, and every entry intent will
    say METADATA_UNAVAILABLE."""

    strategies: Mapping[str, tuple[str, ...]]
    limits: ShadowLimits


@dataclass
class _Run:
    run_id: str
    session_id: str
    task: asyncio.Task[None] | None = None
    cancelled: bool = False


class ShadowWorkspace:
    """Owns shadow runs: create, observe, inspect, cancel."""

    def __init__(
        self,
        *,
        runner: ShadowRunner,
        store: ShadowJournalStore,
        live: LiveWorkspace,
        limits: ShadowLimits | None = None,
        financial_metadata: bool = False,
    ) -> None:
        self._runner = runner
        self._store = store
        self._live = live
        self._limits = limits or ShadowLimits()
        self._financial_metadata = financial_metadata
        self._runs: dict[str, _Run] = {}
        self._create_lock = asyncio.Lock()
        self._closed = False

    # -- capability ------------------------------------------------------

    def capability(self) -> ShadowCapability:
        return ShadowCapability(
            available=not self._closed,
            reason=(
                "shutting down"
                if self._closed
                else "observing simulated historical streams; no order path exists"
            ),
            provenance="SIMULATED_HISTORICAL_STREAM",
            market_currency="HISTORICAL",
            financial_metadata=self._financial_metadata,
            strategies={
                identifier: tuple(sorted(versions))
                for identifier, versions in SUPPORTED_STRATEGIES.items()
            },
            limits=self._limits,
        )

    # -- creating --------------------------------------------------------

    async def create(
        self,
        *,
        session_id: str,
        strategy_id: str,
        strategy_version: str,
        driver: Timeframe,
        timeframes: tuple[Timeframe, ...],
        required: tuple[Timeframe, ...] = (),
        account: AccountState | None = None,
        risk: RiskPolicy | None = None,
        analysis_evidence: bool = True,
        attempt_key: str | None = None,
    ) -> StoredShadowRun:
        """Start observing an existing session under a registered policy."""
        if self._closed:
            raise ShadowWorkspaceError(
                WorkspaceErrorKind.UNAVAILABLE, "SHADOW_CLOSED", "shadow mode is shutting down"
            )
        session = self._session_view(session_id)
        strategy = self._policy(strategy_id, strategy_version)
        request = ShadowRequest(
            source_id=session.source.summary.source_id,
            instrument_label=session.source.summary.instrument_label,
            strategy=strategy,
            driver=driver,
            timeframes=timeframes,
            required=required,
            account=account,
            risk=risk,
            analysis_evidence=analysis_evidence,
        )
        async with self._create_lock:
            # Checked again under the lock: shutdown takes this lock too, so a
            # creation that waited here while shutdown ran is refused rather
            # than starting an observer nobody will stop.
            if self._closed:
                raise ShadowWorkspaceError(
                    WorkspaceErrorKind.UNAVAILABLE, "SHADOW_CLOSED", "shadow mode is shutting down"
                )
            return await self._create(session_id, request, attempt_key)

    async def _create(
        self, session_id: str, request: ShadowRequest, attempt_key: str | None
    ) -> StoredShadowRun:
        if attempt_key is not None:
            # A retry is answered before anything else - before capacity,
            # before a slot is taken - so a request that already succeeded
            # cannot be turned into a refusal by the load it itself created.
            held = await self._store.find_attempt(attempt_key)
            if held is not None:
                existing = AttemptKeyHeldError(*held)
                existing.requested_configuration = self._runner.configuration_for(
                    request, self._live.session_object(session_id)
                )
                return await self._answer_retry(attempt_key, existing)
        run_id = RUN_PREFIX + secrets.token_hex(12)
        try:
            opened = await self._runner.open(request, run_id=run_id)
        except ShadowError as error:
            raise _from_shadow(error) from None

        del opened
        session = self._live.session_object(session_id)
        self._runs[run_id] = _Run(run_id=run_id, session_id=session_id)
        try:
            # Attached before the run is written, so no boundary between
            # "created" and "observing" can pass unseen: records that arrive
            # while the journal is being opened wait in the run's queue.
            self._live.attach_reader(session_id, run_id, self._reader(run_id))
        except LiveWorkspaceError as error:
            del self._runs[run_id]
            self._runner.discard(run_id)
            raise ShadowWorkspaceError(
                WorkspaceErrorKind.CONFLICT, error.code, error.detail
            ) from None
        try:
            # The run row and the attempt key commit together or not at all,
            # and this answers only after they have: the run a caller is told
            # about always exists, and a retry can always read it back.
            created = await self._runner.begin(run_id, session, attempt_key=attempt_key)
        except AttemptKeyHeldError as held:
            self._live.detach_reader(session_id, run_id)
            del self._runs[run_id]
            return await self._answer_retry(attempt_key or "", held)
        except BaseException:
            self._live.detach_reader(session_id, run_id)
            self._runs.pop(run_id, None)
            raise
        self._runs[run_id].task = asyncio.create_task(
            self._observe(run_id, session_id), name=f"shadow-{run_id}"
        )
        return created

    async def _answer_retry(self, attempt_key: str, held: AttemptKeyHeldError) -> StoredShadowRun:
        """Somebody already used this key. Same rules: the same run. Different
        rules: a conflict, never a second run wearing the first one's name."""
        existing_id = held.run_id
        if held.configuration != held.requested_configuration:
            raise ShadowWorkspaceError(
                WorkspaceErrorKind.CONFLICT,
                "SHADOW_ATTEMPT_CONFLICT",
                (
                    f"attempt key {attempt_key} already created a run with a "
                    "different configuration; use a new key for a new run"
                ),
            )
        existing = await self._store.get_run(existing_id)
        if existing is None:  # pragma: no cover - the row cannot be deleted
            raise ShadowWorkspaceError(
                WorkspaceErrorKind.NOT_FOUND, "SHADOW_RUN_NOT_FOUND", "no such shadow run"
            )
        return existing

    def _reader(self, run_id: str) -> Callable[[StreamRecord], None]:
        return self._runner.observer(run_id)

    async def _observe(self, run_id: str, session_id: str) -> None:
        """Follow the attached session until it ends, then detach.

        The run ends when the session does or when it is cancelled. It has no
        say in either: a reader cannot keep a stream alive, and a browser
        closing an SSE connection is not one of these conditions.
        """
        held = self._runs[run_id]
        try:
            await self._runner.follow(
                run_id,
                self._live.session_object(session_id),
                lambda: self._live.session_ended(session_id) or held.cancelled,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - reported safely, below
            _LOG.error(
                "shadow run failed",
                extra={"run_id": run_id, "error_type": type(error).__name__},
            )
        finally:
            self._live.detach_reader(session_id, run_id)
            # The run is over either way; its slot and its entry here go with
            # it, so a long-lived process does not accumulate finished runs.
            self._runs.pop(run_id, None)

    # -- reading ---------------------------------------------------------

    async def get(self, run_id: str) -> StoredShadowRun:
        run = await self._store.get_run(run_id)
        if run is None:
            raise ShadowWorkspaceError(
                WorkspaceErrorKind.NOT_FOUND, "SHADOW_RUN_NOT_FOUND", "no such shadow run"
            )
        return run

    async def list(self, *, offset: int, limit: int) -> tuple[tuple[StoredShadowRun, ...], int]:
        return await self._store.list_runs(offset=offset, limit=min(limit, 100))

    async def journal(
        self, run_id: str, *, after: int, limit: int
    ) -> tuple[tuple[ShadowDecision, ...], int, Mapping[str, ShadowOutcomeRecord]]:
        """A bounded page of entries, with each decision's current development.

        The developments are fetched for the whole page in one query rather
        than one per row.
        """
        await self.get(run_id)
        entries, total = await self._store.read_entries(
            run_id, after_sequence=after, limit=min(limit, self._limits.max_journal_page)
        )
        keys = [entry.decision_key for entry in entries]
        return entries, total, await self._store.latest_outcomes(run_id, keys)

    async def outcomes(
        self, run_id: str, *, after: int, limit: int
    ) -> tuple[tuple[ShadowOutcomeRecord, ...], int]:
        await self.get(run_id)
        return await self._store.read_outcomes(
            run_id, after_sequence=after, limit=min(limit, self._limits.max_outcome_page)
        )

    # -- ending ----------------------------------------------------------

    async def cancel(self, run_id: str) -> StoredShadowRun:
        """Stop observing. The journal keeps everything already recorded.

        Cancelling an ended run is not an error and changes nothing: the answer
        is the run as it already ended, with its original reason.
        """
        return await self._end(run_id, EndReason.CANCELLED)

    async def _end(self, run_id: str, reason: EndReason) -> StoredShadowRun:
        held = self._runs.get(run_id)
        stored = await self.get(run_id)
        if held is None or stored.status is ShadowRunStatus.ENDED:
            return stored
        held.cancelled = True
        self._runner.request_end(run_id, reason)
        if held.task is not None:
            await asyncio.wait({held.task}, timeout=5)
        self._live.detach_reader(held.session_id, run_id)
        self._runs.pop(run_id, None)
        return await self.get(run_id)

    async def shutdown(self) -> None:
        """End every run as SHUTDOWN, not CANCELLED: nobody asked it to stop."""
        self._closed = True
        # Wait for a creation already in flight to finish, so its run is either
        # fully created (and ended below) or never created at all.
        async with self._create_lock:
            pass
        for run_id in list(self._runs):
            await self._end(run_id, EndReason.SHUTDOWN)

    # -- helpers ---------------------------------------------------------

    def _session_view(self, session_id: str) -> SessionView:
        try:
            return self._live.get(session_id)
        except LiveWorkspaceError as error:
            raise ShadowWorkspaceError(
                WorkspaceErrorKind.NOT_FOUND, error.code, error.detail
            ) from None

    def _policy(self, identifier: str, version: str) -> StrategyPolicy:
        """The registered implementation for the requested rules.

        The same static mapping the Phase 12 route uses, checked against the
        same registry. A strategy name from a client is a key into a table this
        build ships - never a module path, never a constructor call built from
        text, and nothing near it evaluates anything. An unregistered pair is
        refused here, and the runner checks the pair again before it observes.
        """
        known = SUPPORTED_STRATEGIES.get(identifier, frozenset())
        if identifier != EMA_IDENTIFIER or version not in known:
            raise ShadowWorkspaceError(
                WorkspaceErrorKind.INVALID,
                "STRATEGY_UNSUPPORTED",
                (
                    f"{identifier!r} version {version!r} is not a strategy this build "
                    f"implements; available: {sorted(SUPPORTED_STRATEGIES)}"
                ),
            )
        return EmaCrossoverStrategy()


async def recover_interrupted_runs(
    store: ShadowJournalStore, clock: ClockPort, *, limit: int = 1_000
) -> tuple[str, ...]:
    """Close runs a previous process left marked as observing.

    Called once, at startup, before any new run exists. A run found observing
    here cannot be resumed: the market went on while nothing was watching, and
    picking up again would stitch two observations together across a gap nobody
    recorded. So each is closed as INTERRUPTED exactly where its journal
    stops, with a final entry saying so. No decision is invented for the
    boundaries it missed, and none of its outcomes is completed after the fact.
    """
    closed: list[str] = []
    for run in await store.observing_runs(limit=limit):
        now = clock.now()
        sequence = await store.last_sequence(run.run_id) + 1
        await store.append(
            run.run_id,
            [
                ShadowDecision(
                    kind=JournalEntryKind.OPERATIONAL,
                    sequence=sequence,
                    decision_key=decision_key(
                        run_id=run.run_id,
                        configuration=run.configuration,
                        boundary=None,
                        inputs=None,
                        marker="RUN_ENDED:interrupted",
                    ),
                    market_boundary=None,
                    recorded_at=now,
                    operational=OperationalKind.RUN_ENDED,
                    reason=(
                        "the application stopped while this run was observing; it is "
                        "closed where its journal ends and nothing after that was seen"
                    ),
                )
            ],
        )
        await store.interrupt(run.run_id, ended_at=now, end_reason=EndReason.INTERRUPTED)
        closed.append(run.run_id)
    return tuple(closed)


def _from_shadow(error: ShadowError) -> ShadowWorkspaceError:
    kinds = {
        "SHADOW_CAPACITY": WorkspaceErrorKind.CAPACITY,
        "SHADOW_RUN_EXISTS": WorkspaceErrorKind.CONFLICT,
        "SHADOW_RUN_NOT_FOUND": WorkspaceErrorKind.NOT_FOUND,
    }
    return ShadowWorkspaceError(
        kinds.get(error.code, WorkspaceErrorKind.INVALID), error.code, error.detail
    )
