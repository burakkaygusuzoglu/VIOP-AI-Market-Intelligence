"""Replay use cases.

This module is an orchestrator, not an engine. It decides *which market facts
exist yet* and then hands them, unchanged, to the code that already knows what
to do with them:

* analysis goes to Phase 8's ``run_analysis`` - the same function the analysis
  endpoint calls, given the revealed prefix serialised back to CSV, so a replay
  analysis and a direct analysis of the same prefix are literally one code path;
* paper trading goes to Phase 9's ``PaperTradingService``, constructed with a
  **replay clock** that reports the session's market time rather than the wall
  clock, which is the seam Phase 9 documented for exactly this;
* performance goes to Phase 10's engine through its existing source, narrowed
  to the positions this session opened.

There is no replay indicator, no replay fill, no replay P&L and no replay win
rate. If a number appears in a replay screen, some earlier phase computed it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum, unique

from app.application.analysis.orchestrator import (
    AnalysisInputError,
    AnalysisOutcome,
    run_analysis,
)
from app.application.analysis.request import AnalysisRequest, TimeframeDataset
from app.application.analysis.serialisation import candles_to_csv
from app.application.paper.service import (
    CreatePaperPosition,
    PaperErrorKind,
    PaperServiceError,
    PaperTradingService,
    PositionView,
)
from app.application.performance.ports import OutcomeFilters
from app.application.performance.service import (
    PerformanceService,
    PerformanceServiceError,
    PerformanceView,
)
from app.application.ports.contract_metadata import ContractMetadataProvider
from app.application.ports.market_data import CandleTextParser
from app.application.ports.paper import (
    PaperStore,
    ProductResolver,
    ProductSnapshotCodec,
    StoredPosition,
)
from app.application.ports.system import ClockPort
from app.application.replay.ports import (
    DatasetIdentityConflictError,
    DuplicateSessionError,
    ReplayStore,
    ReplayStoreUnavailableError,
    SessionConflictError,
    SessionSummary,
    StoredDataset,
    StoredSession,
    TimeframeSummary,
    UnknownSessionError,
)
from app.domain.common.enums import Direction, Timeframe
from app.domain.market.candle import Candle
from app.domain.paper import SimulationPolicy, TargetSpec
from app.domain.replay import (
    MAX_ADVANCE_STEPS,
    ReplayError,
    ReplayPlan,
    advance,
    coverage_end,
    start_cursor,
    step,
)
from app.domain.risk.sizing import AccountState, RiskPolicy

SUPPORTED_TIMEFRAMES: tuple[Timeframe, ...] = (
    Timeframe.D1,
    Timeframe.H1,
    Timeframe.M15,
    Timeframe.M5,
)
"""The timeframes the analysis stack has roles for. A dataset may supply any
subset; a missing one stays missing rather than being built from a finer one."""

TERMINAL_POSITION = "INVALID_TRANSITION"
"""The one Phase 9 refusal a replay may skip: a closed position takes no
more bars. Every other refusal means the bar was not delivered."""

PROVENANCE_MARKET_DATA = "USER_SUPPLIED_HISTORICAL"
PROVENANCE_EXECUTION = "SIMULATED"
PROVENANCE_SESSION = "REPLAY"


@dataclass(frozen=True, slots=True)
class ReplayLimits:
    """Resource bounds. Project decisions, informed by the Phase 8 analysis cost."""

    max_rows_per_timeframe: int = 2_500
    """The Phase 8 analytical ceiling: replay analyses the same way, so it
    cannot usefully hold more history per timeframe than analysis can read."""

    max_total_rows: int = 6_000
    max_chart_candles: int = 400
    """What a chart returns at once, newest first - the Phase 8 display policy."""

    max_sessions_page: int = 25
    max_advance_steps: int = MAX_ADVANCE_STEPS

    max_session_positions: int = 50
    """How many paper positions one replay session may open.

    Every open position is given every revealed bar, so this is the one
    collection whose size multiplies the work a step does. Fifty is far more
    than a person walks through a chart with, and it keeps a single advance
    bounded rather than proportional to a number nobody set."""


@unique
class ReplayErrorKind(StrEnum):
    INVALID = "INVALID"
    REFUSED = "REFUSED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    TOO_LARGE = "TOO_LARGE"
    UNAVAILABLE = "UNAVAILABLE"


class ReplayServiceError(Exception):
    """A typed failure the API maps to a status code."""

    def __init__(self, kind: ReplayErrorKind, code: str, detail: str) -> None:
        self.kind = kind
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class TimeframeUpload:
    """One timeframe of historical data, as supplied."""

    timeframe: Timeframe
    content: str
    source_name: str


@dataclass(frozen=True, slots=True)
class CreateReplaySession:
    """Everything one replay session is created from."""

    idempotency_key: str
    symbol: str
    driver: Timeframe
    replay_start: datetime
    datasets: tuple[TimeframeUpload, ...]


@dataclass(frozen=True, slots=True)
class RevealedWindow:
    """A bounded slice of what is currently visible on one timeframe."""

    timeframe: Timeframe
    candles: tuple[Candle, ...]
    revealed_total: int
    dataset_total: int
    windowed: bool
    """True when candles were asked for at all. A timeframe the chart did not
    request has none, which is not the same as having been cut short."""

    @property
    def truncated(self) -> bool:
        return self.windowed and len(self.candles) < self.revealed_total


@dataclass(frozen=True, slots=True)
class SessionView:
    """A session, its cursor, and what each timeframe currently shows."""

    session: StoredSession
    availability: tuple[RevealedWindow, ...]
    linked_positions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StepResult:
    view: SessionView
    revealed: tuple[datetime, ...]
    """The market moments crossed, in order. One per candle revealed."""

    observed_positions: int
    replayed: bool = False
    """True when a retried command returned the cursor it had already produced."""


class ReplayService:
    def __init__(
        self,
        *,
        store: ReplayStore,
        paper_store: PaperStore,
        codec: ProductSnapshotCodec,
        resolver: ProductResolver | None,
        parser: CandleTextParser,
        performance: PerformanceService,
        clock: ClockPort,
        limits: ReplayLimits | None = None,
    ) -> None:
        self._store = store
        self._paper_store = paper_store
        self._codec = codec
        self._resolver = resolver
        self._parser = parser
        self._performance = performance
        self._clock = clock
        self._limits = limits or ReplayLimits()

    @property
    def limits(self) -> ReplayLimits:
        return self._limits

    # ------------------------------------------------------------------
    # Creating a session
    # ------------------------------------------------------------------

    async def create(self, command: CreateReplaySession) -> tuple[StoredSession, bool]:
        """Ingest the datasets and open a session over them.

        Returns the session and whether this was a replayed retry of an earlier
        identical request.
        """
        key = _validated_key(command.idempotency_key)
        fingerprint = _fingerprint(command)
        try:
            existing = await self._store.find_session_by_key(key)
            if existing is not None:
                stored, stored_fingerprint = existing
                if stored_fingerprint != fingerprint:
                    raise ReplayServiceError(
                        ReplayErrorKind.CONFLICT,
                        "IDEMPOTENCY_CONFLICT",
                        "this idempotency key was used for a different replay request",
                    )
                return stored, True

            candles = self._parse_datasets(command)
            try:
                dataset = await self._store.save_dataset(
                    _describe(command.symbol, candles), candles
                )
            except DatasetIdentityConflictError as error:
                raise ReplayServiceError(
                    ReplayErrorKind.CONFLICT, "DATASET_IDENTITY_CONFLICT", str(error)
                ) from error
            driver_candles = candles.get(command.driver, ())
            plan = ReplayPlan(
                dataset_id=dataset.dataset_id,
                symbol=command.symbol.strip(),
                driver=command.driver,
                replay_start=command.replay_start,
            )
            try:
                cursor = start_cursor(plan, list(driver_candles))
            except ReplayError as error:
                raise ReplayServiceError(
                    ReplayErrorKind.INVALID, error.code, error.reason
                ) from error
            now = self._clock.now()
            session = StoredSession(
                session_id=_session_id(key),
                plan=plan,
                cursor=cursor,
                dataset=dataset,
                last_command_key=None,
                command_target_revealed=None,
                created_at=now,
                updated_at=now,
            )
            try:
                saved = await self._store.create_session(
                    session, idempotency_key=key, fingerprint=fingerprint, now=now
                )
            except DuplicateSessionError:
                # Two identical creates raced; answer from the one that won.
                found = await self._store.find_session_by_key(key)
                if found is None:  # pragma: no cover - the winner exists by definition
                    raise
                return found[0], True
            return saved, False
        except ReplayStoreUnavailableError as error:
            raise ReplayServiceError(
                ReplayErrorKind.UNAVAILABLE, "REPLAY_STORE_UNAVAILABLE", str(error)
            ) from error

    def _parse_datasets(self, command: CreateReplaySession) -> dict[Timeframe, tuple[Candle, ...]]:
        """Parse and validate every supplied timeframe, or refuse the request.

        The Phase 1 parser and its validation do the work; replay adds only its
        own bounds and the rule that the driver timeframe must actually be
        present in what was supplied.
        """
        if not command.datasets:
            raise ReplayServiceError(
                ReplayErrorKind.INVALID, "DATASET_INVALID", "no market data was supplied"
            )
        symbol = command.symbol.strip()
        if not symbol:
            raise ReplayServiceError(
                ReplayErrorKind.INVALID, "DATASET_INVALID", "a symbol is required"
            )
        seen: dict[Timeframe, tuple[Candle, ...]] = {}
        total = 0
        for upload in command.datasets:
            if upload.timeframe not in SUPPORTED_TIMEFRAMES:
                raise ReplayServiceError(
                    ReplayErrorKind.INVALID,
                    "UNSUPPORTED_TIMEFRAME",
                    f"{upload.timeframe.value} is not a replay timeframe",
                )
            if upload.timeframe in seen:
                raise ReplayServiceError(
                    ReplayErrorKind.INVALID,
                    "DATASET_INVALID",
                    f"{upload.timeframe.value} was supplied twice",
                )
            try:
                fetch = self._parser.parse(
                    upload.content,
                    symbol=symbol,
                    timeframe=upload.timeframe,
                    source_name=upload.source_name,
                    max_rows=self._limits.max_rows_per_timeframe,
                )
            except Exception as error:  # noqa: BLE001 - parser raises its own typed errors
                raise ReplayServiceError(
                    ReplayErrorKind.INVALID,
                    "DATASET_INVALID",
                    f"{upload.timeframe.value}: {error}",
                ) from error
            if not fetch.candles:
                raise ReplayServiceError(
                    ReplayErrorKind.INVALID,
                    "DATASET_INVALID",
                    f"{upload.timeframe.value} contains no candles",
                )
            total += len(fetch.candles)
            if total > self._limits.max_total_rows:
                raise ReplayServiceError(
                    ReplayErrorKind.TOO_LARGE,
                    "RESOURCE_LIMIT",
                    (
                        f"a replay dataset holds at most {self._limits.max_total_rows} rows in "
                        f"total; this one has at least {total}"
                    ),
                )
            seen[upload.timeframe] = tuple(fetch.candles)
        if command.driver not in seen:
            raise ReplayServiceError(
                ReplayErrorKind.INVALID,
                "DRIVER_TIMEFRAME_MISSING",
                (
                    f"the driver timeframe {command.driver.value} is not among the supplied "
                    "datasets; one step would have nothing to reveal"
                ),
            )
        return seen

    # ------------------------------------------------------------------
    # Reading a session
    # ------------------------------------------------------------------

    async def get(self, session_id: str, *, chart: Timeframe | None = None) -> SessionView:
        return await self._view(await self._load(session_id), chart)

    async def _view(self, session: StoredSession, chart: Timeframe | None) -> SessionView:
        """The session as it stands, with candles for one timeframe only.

        One builder for reading a session and for answering a step, so the two
        can never describe the same cursor differently. ``chart`` defaults to
        the driver timeframe: a view always carries exactly one bounded window,
        never every timeframe's candles at once.
        """
        target = chart if chart is not None else session.plan.driver
        windows = []
        for summary in session.dataset.timeframes:
            window = await self._window(
                session, summary, include_candles=summary.timeframe is target
            )
            windows.append(window)
        links = await self._store.linked_positions(session.session_id)
        return SessionView(session=session, availability=tuple(windows), linked_positions=links)

    async def list(self, *, offset: int, limit: int) -> tuple[tuple[SessionSummary, ...], int]:
        bounded = max(1, min(limit, self._limits.max_sessions_page))
        try:
            return await self._store.list_sessions(offset=max(0, offset), limit=bounded)
        except ReplayStoreUnavailableError as error:
            raise ReplayServiceError(
                ReplayErrorKind.UNAVAILABLE, "REPLAY_STORE_UNAVAILABLE", str(error)
            ) from error

    async def _window(
        self, session: StoredSession, summary: TimeframeSummary, *, include_candles: bool
    ) -> RevealedWindow:
        """One timeframe's currently visible candles, newest-bounded.

        The bound is applied *after* availability, so the chart shows the latest
        part of what is revealed and never a candle that has not finished.
        """
        revealed_total = await self._revealed_total(session, summary)
        candles: tuple[Candle, ...] = ()
        if include_candles and revealed_total:
            candles = await self._store.candles(
                session.plan.dataset_id,
                summary.timeframe,
                until=session.cursor.as_of,
                limit=self._limits.max_chart_candles,
                newest_first=True,
            )
        return RevealedWindow(
            timeframe=summary.timeframe,
            candles=candles,
            revealed_total=revealed_total,
            dataset_total=summary.rows,
            windowed=include_candles,
        )

    async def _revealed_total(self, session: StoredSession, summary: TimeframeSummary) -> int:
        if summary.last_coverage_end <= session.cursor.as_of:
            return summary.rows
        candles = await self._store.candles(
            session.plan.dataset_id, summary.timeframe, until=session.cursor.as_of
        )
        return len(candles)

    # ------------------------------------------------------------------
    # Stepping
    # ------------------------------------------------------------------

    async def step(
        self,
        session_id: str,
        *,
        steps: int = 1,
        command_key: str | None,
        expected_version: int | None,
        chart: Timeframe | None = None,
    ) -> StepResult:
        """Reveal one or more driver candles, and let paper positions see them.

        A multi-step is exactly its single steps: every boundary crossed is fed
        to the session's open paper positions in order, so nothing is skipped
        and nothing arrives twice.
        """
        if steps < 1:
            raise ReplayServiceError(
                ReplayErrorKind.INVALID, "INVALID_ADVANCE", "an advance must be at least one step"
            )
        if steps > self._limits.max_advance_steps:
            raise ReplayServiceError(
                ReplayErrorKind.TOO_LARGE,
                "RESOURCE_LIMIT",
                f"an advance may cover at most {self._limits.max_advance_steps} steps",
            )
        session = await self._load(session_id)

        remaining = steps
        if command_key is not None and session.last_command_key == command_key:
            target = session.command_target_revealed
            if target is None or session.cursor.revealed_driver_candles >= target:
                # The same command, sent twice, and it finished. The cursor it
                # produced is the answer.
                return StepResult(
                    view=await self._view(session, chart),
                    revealed=(),
                    observed_positions=0,
                    replayed=True,
                )
            # It stopped part way. Finish it - the *remainder*, not the whole
            # request again. Advancing `steps` more from here would leave the
            # session past the cursor the original command was going to reach.
            remaining = target - session.cursor.revealed_driver_candles
        if expected_version is not None and expected_version != session.cursor.version:
            raise ReplayServiceError(
                ReplayErrorKind.CONFLICT,
                "VERSION_CONFLICT",
                (
                    f"this session is at version {session.cursor.version}, not "
                    f"{expected_version}; re-read it before stepping again"
                ),
            )

        driver = await self._store.candles(session.plan.dataset_id, session.plan.driver)
        try:
            # One code path for one step and for many: a bounded advance is
            # defined as its single steps, so it cannot drift from them. This
            # call is pure and validates the whole request up front - an advance
            # is refused as a whole, or it begins.
            _final, boundaries = advance(session.cursor, driver, remaining)
        except ReplayError as error:
            kind = (
                ReplayErrorKind.REFUSED if error.code == "REPLAY_END" else ReplayErrorKind.INVALID
            )
            raise ReplayServiceError(kind, error.code, error.reason) from error

        # Where this command is going. Recorded with its key on every boundary
        # it commits, so a retry finishes it rather than repeating it.
        target = session.cursor.revealed_driver_candles + len(boundaries)
        links = await self._store.linked_positions(session_id)
        current = session
        # Distinct positions, not deliveries: an advance of twelve that fed one
        # position twelve bars has observed one position, and saying twelve
        # would read as twelve trades.
        observed: set[str] = set()
        for index, boundary in enumerate(boundaries):
            reached = step(current.cursor, driver)
            # Deliver first, then commit. The order is the recovery design: a
            # cursor that moved before delivery would leave a bar undeliverable
            # for ever, while a cursor that lags a delivered bar is corrected by
            # the retry - an identical bar is a no-op in the Phase 9 engine.
            # Each boundary commits on its own, so a failure leaves the session
            # at the last *completed* step rather than somewhere inside it.
            try:
                observed |= await self._feed_positions(current, links, boundary)
            except ReplayServiceError as error:
                # Say where the advance got to. Without it a client that asked
                # for ten steps and received a failure cannot tell whether it
                # moved none of them or nine, and would have to guess before
                # retrying.
                raise ReplayServiceError(
                    error.kind,
                    error.code,
                    (f"{error.detail}; this advance completed {index} of {len(boundaries)} steps"),
                ) from error
            try:
                current = await self._store.advance_session(
                    session_id,
                    reached,
                    expected_version=current.cursor.version,
                    # Key and target on every boundary: the key identifies the
                    # command, and the target says where it was going, so a
                    # retry can tell a finished command from one that stopped
                    # part way and can finish exactly the remainder.
                    command_key=command_key,
                    command_target=target if command_key is not None else None,
                    now=self._clock.now(),
                )
            except SessionConflictError as error:
                raise ReplayServiceError(
                    ReplayErrorKind.CONFLICT,
                    "VERSION_CONFLICT",
                    (
                        "this session advanced while the step was being applied; re-read it and "
                        f"step again (it is now at version {error.actual})"
                    ),
                ) from error
        return StepResult(
            view=await self._view(current, chart),
            revealed=boundaries,
            observed_positions=len(observed),
        )

    async def _feed_positions(
        self, session: StoredSession, links: Sequence[str], boundary: datetime
    ) -> set[str]:
        """Give one newly revealed driver candle to this session's positions.

        One boundary, one bar, in the order the market printed them. The bar
        goes through the ordinary Phase 9 observation path - its duplicate,
        ordering, timeframe and symbol rules all apply unchanged - with a clock
        that reports replay time.
        """
        if not links:
            return set()
        # Exactly the bar this boundary revealed: bounded above by the
        # availability rule, and below so the query does not carry the whole
        # prefix that ends with it.
        opened = boundary - timedelta(minutes=session.plan.driver.minutes)
        candles = await self._store.candles(
            session.plan.dataset_id, session.plan.driver, since=opened, until=boundary
        )
        if not candles:
            return set()
        # The query is bounded at both ends, so it should have returned exactly
        # the bar this boundary revealed. Checking rather than filtering: a
        # silent filter would hide a widened query, and a widened query is how
        # a bar the step was not authorised to deliver would reach a position.
        unexpected = [c for c in candles if coverage_end(c) != boundary]
        if unexpected:
            raise ReplayServiceError(
                ReplayErrorKind.UNAVAILABLE,
                "UNBOUNDED_OBSERVATION_WINDOW",
                (
                    f"the store returned {len(candles)} candles for the boundary at "
                    f"{boundary.isoformat()}; refusing to feed a window this step did "
                    "not authorise"
                ),
            )
        fresh = list(candles)
        content = candles_to_csv(fresh)
        paper = self._paper_for(boundary)
        touched: set[str] = set()
        for position_id in links:
            try:
                await paper.observe(position_id, content, "replay")
                touched.add(position_id)
            except PaperServiceError as error:
                # Exactly one refusal means "nothing to learn": a position that
                # has reached a terminal state takes no further bars. Every
                # other refusal - out of order, conflicting prices, a bar that
                # has not closed, the wrong timeframe - means this bar was *not*
                # delivered, and the cursor must not move past it.
                if error.code == TERMINAL_POSITION:
                    continue
                raise ReplayServiceError(
                    ReplayErrorKind.UNAVAILABLE,
                    "OBSERVATION_NOT_DELIVERED",
                    (
                        f"{position_id} did not accept the bar closing at "
                        f"{boundary.isoformat()} ({error.code}: {error.detail}); the replay "
                        "clock was not advanced past it"
                    ),
                ) from error
        return touched

    # ------------------------------------------------------------------
    # Analysis, paper positions, performance - all existing engines
    # ------------------------------------------------------------------

    async def analyse(
        self,
        session_id: str,
        *,
        account: AccountState | None = None,
        risk_policy: RiskPolicy | None = None,
        entry_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        contracts: ContractMetadataProvider | None = None,
    ) -> tuple[StoredSession, AnalysisOutcome]:
        """Run the existing analysis over exactly what is revealed right now.

        The revealed prefix is serialised back to the canonical CSV the Phase 8
        pipeline reads, so this is not "replay's analysis" - it is the analysis,
        given a market that stops at ``as_of``.
        """
        session = await self._load(session_id)
        datasets: list[TimeframeDataset] = []
        for summary in session.dataset.timeframes:
            candles = await self._store.candles(
                session.plan.dataset_id, summary.timeframe, until=session.cursor.as_of
            )
            if not candles:
                continue
            datasets.append(
                TimeframeDataset(
                    timeframe=summary.timeframe,
                    content=candles_to_csv(candles),
                    source_name=f"replay {summary.timeframe.value}",
                )
            )
        if not datasets:
            raise ReplayServiceError(
                ReplayErrorKind.REFUSED,
                "NOTHING_REVEALED",
                "no candle has finished at this replay time yet, so there is nothing to analyse",
            )
        request = AnalysisRequest(
            symbol=session.plan.symbol,
            datasets=tuple(datasets),
            account=account,
            risk_policy=risk_policy,
            entry_price=entry_price,
            stop_price=stop_price,
        )
        try:
            outcome = await run_analysis(
                request,
                parser=self._parser,
                clock=ReplayClock(session.cursor.as_of),
                contracts=contracts,
            )
        except AnalysisInputError as error:
            raise ReplayServiceError(ReplayErrorKind.INVALID, error.code, str(error)) from error
        return session, outcome

    async def open_paper_position(
        self,
        session_id: str,
        *,
        idempotency_key: str,
        direction: Direction,
        quantity: int,
        intended_entry: Decimal,
        stop: Decimal,
        targets: tuple[TargetSpec, ...],
        account: AccountState,
        risk: RiskPolicy,
        policy: SimulationPolicy,
        note: str | None = None,
    ) -> tuple[StoredSession, StoredPosition]:
        """Open a Phase 9 paper position whose decision time is *replay* time.

        Nothing here decides to trade: a person asked. The position is created
        through the existing service, so every Phase 9 rule - risk veto,
        verified product metadata, entry causality - applies unchanged, and the
        decision time is the session's market time rather than the wall clock.
        """
        session = await self._load(session_id)
        existing = await self._store.linked_positions(session_id)
        if len(existing) >= self._limits.max_session_positions:
            raise ReplayServiceError(
                ReplayErrorKind.TOO_LARGE,
                "RESOURCE_LIMIT",
                (
                    f"a replay session holds at most "
                    f"{self._limits.max_session_positions} simulated positions; "
                    "start another session to keep going"
                ),
            )
        paper = self._paper_for(session.cursor.as_of)
        command = CreatePaperPosition(
            idempotency_key=idempotency_key,
            symbol=session.plan.symbol,
            direction=direction,
            quantity=quantity,
            intended_entry=intended_entry,
            stop=stop,
            targets=targets,
            timeframe=session.plan.driver,
            decision_time=session.cursor.as_of,
            account=account,
            risk=risk,
            policy=policy,
            note=note,
        )
        try:
            view = await paper.create(command)
        except PaperServiceError as error:
            raise ReplayServiceError(_paper_kind(error.kind), error.code, error.detail) from error
        await self._store.link_position(session_id, view.stored.position_id, self._clock.now())
        return session, view.stored

    async def positions(self, session_id: str) -> tuple[StoredPosition, ...]:
        """Every paper position this session opened, read at replay time."""
        session = await self._load(session_id)
        paper = self._paper_for(session.cursor.as_of)
        links = await self._store.linked_positions(session.session_id)
        found = []
        for position_id in links:
            try:
                found.append((await paper.get(position_id)).stored)
            except PaperServiceError as error:
                raise ReplayServiceError(
                    _paper_kind(error.kind), error.code, error.detail
                ) from error
        return tuple(found)

    async def close_position(self, session_id: str, position_id: str) -> StoredPosition:
        """Ask a replay position to exit at the next revealed bar's open."""
        return await self._position_command(
            session_id, position_id, lambda paper, pid: paper.request_close(pid)
        )

    async def move_stop_to_breakeven(self, session_id: str, position_id: str) -> StoredPosition:
        return await self._position_command(
            session_id, position_id, lambda paper, pid: paper.move_stop_to_breakeven(pid)
        )

    async def cancel_position(self, session_id: str, position_id: str) -> StoredPosition:
        return await self._position_command(
            session_id, position_id, lambda paper, pid: paper.cancel(pid)
        )

    async def _position_command(
        self,
        session_id: str,
        position_id: str,
        operation: Callable[[PaperTradingService, str], Awaitable[PositionView]],
    ) -> StoredPosition:
        """Run one existing Phase 9 command on a position this session owns.

        The ownership check comes first and is a link lookup, not a symbol or
        timestamp guess: a replay may only act on the positions it opened.
        """
        session = await self._load(session_id)
        owner = await self._store.session_of_position(position_id)
        if owner != session.session_id:
            raise ReplayServiceError(
                ReplayErrorKind.NOT_FOUND,
                "POSITION_NOT_IN_SESSION",
                f"{position_id} was not opened by replay session {session_id}",
            )
        paper = self._paper_for(session.cursor.as_of)
        try:
            view = await operation(paper, position_id)
        except PaperServiceError as error:
            raise ReplayServiceError(_paper_kind(error.kind), error.code, error.detail) from error
        return view.stored

    async def performance(
        self, session_id: str
    ) -> tuple[StoredSession, tuple[str, ...], PerformanceView]:
        """Phase 10's engine, over exactly this session's positions.

        The population is returned with the metrics: a session's performance is
        a named set of positions, and saying which ones is part of the answer.
        """
        session = await self._load(session_id)
        links = await self._store.linked_positions(session.session_id)
        try:
            view = await self._performance.summary(OutcomeFilters(position_ids=links))
        except PerformanceServiceError as error:
            raise ReplayServiceError(
                ReplayErrorKind.UNAVAILABLE, error.code, error.detail
            ) from error
        return session, links, view

    # ------------------------------------------------------------------

    def _paper_for(self, as_of: datetime) -> PaperTradingService:
        """The Phase 9 service, told that "now" is this replay moment.

        Phase 9 injects its clock precisely so a replay can supply historical
        time; this is that seam, and it is the only thing replay changes about
        paper trading.
        """
        return PaperTradingService(
            store=self._paper_store,
            codec=self._codec,
            resolver=self._resolver,
            parser=self._parser,
            clock=ReplayClock(as_of),
        )

    async def _load(self, session_id: str) -> StoredSession:
        try:
            session = await self._store.get_session(session_id)
        except ReplayStoreUnavailableError as error:
            raise ReplayServiceError(
                ReplayErrorKind.UNAVAILABLE, "REPLAY_STORE_UNAVAILABLE", str(error)
            ) from error
        except UnknownSessionError as error:
            raise _not_found(session_id) from error
        if session is None:
            raise _not_found(session_id)
        return session


@dataclass(frozen=True, slots=True)
class ReplayClock:
    """A clock that reports replay market time. Never the wall clock.

    Public because everything that runs inside a replay must be able to take
    time from the session rather than from the process: the paper engine, the
    analysis pipeline and anything the API layer runs alongside them.
    """

    as_of: datetime

    def now(self) -> datetime:
        return self.as_of


def _paper_kind(kind: PaperErrorKind) -> ReplayErrorKind:
    return {
        PaperErrorKind.INVALID: ReplayErrorKind.INVALID,
        PaperErrorKind.REFUSED: ReplayErrorKind.REFUSED,
        PaperErrorKind.NOT_FOUND: ReplayErrorKind.NOT_FOUND,
        PaperErrorKind.CONFLICT: ReplayErrorKind.CONFLICT,
        PaperErrorKind.TOO_LARGE: ReplayErrorKind.TOO_LARGE,
        PaperErrorKind.UNAVAILABLE: ReplayErrorKind.UNAVAILABLE,
    }[kind]


def _not_found(session_id: str) -> ReplayServiceError:
    return ReplayServiceError(
        ReplayErrorKind.NOT_FOUND, "SESSION_NOT_FOUND", f"no replay session {session_id}"
    )


def _canonical(value: Decimal | None) -> str:
    """One spelling per number, for identity only.

    ``100`` and ``100.00`` are the same price, so they must give the same
    dataset id; ``normalize`` removes the trailing zeros a person's export
    happened to include, and ``format(_, "f")`` keeps the result out of
    exponent notation. Storage keeps the value exactly as supplied - this
    spelling never reaches a candle, a chart or a calculation.
    """
    return "" if value is None else format(value.normalize(), "f")


def _describe(symbol: str, candles: dict[Timeframe, tuple[Candle, ...]]) -> StoredDataset:
    """The dataset identity and its factual summary.

    The identity is a digest of the normalised candles themselves - timeframe,
    open time and the five amounts - so changing one price changes the dataset,
    while re-uploading the same market twice does not. No audit timestamp and no
    formatting choice takes part.
    """
    summaries: list[TimeframeSummary] = []
    partitions: list[dict[str, object]] = []
    total = 0
    for timeframe in sorted(candles, key=lambda item: item.value):
        series = candles[timeframe]
        partitions.append(
            {
                "timeframe": timeframe.value,
                "rows": [
                    [
                        # The same instant written in two offsets is one market
                        # moment; PostgreSQL stores it as one, so identity must
                        # treat it as one too.
                        candle.open_time.astimezone(UTC).isoformat(),
                        _canonical(candle.open),
                        _canonical(candle.high),
                        _canonical(candle.low),
                        _canonical(candle.close),
                        _canonical(candle.volume),
                    ]
                    for candle in series
                ],
            }
        )
        total += len(series)
        summaries.append(
            TimeframeSummary(
                timeframe=timeframe,
                rows=len(series),
                first_open_time=series[0].open_time,
                last_open_time=series[-1].open_time,
                last_coverage_end=coverage_end(series[-1]),
            )
        )
    # One structured document rather than concatenated fields. Feeding a hash
    # "symbol, then labels, then rows" makes the boundaries between them
    # invisible to the digest: a crafted symbol could carry the bytes of a
    # timeframe label and a row, and two different markets would collide. JSON
    # quotes and delimits every value, so each part can only be read one way.
    identity = json.dumps(
        {"symbol": symbol.strip(), "timeframes": partitions},
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(identity.encode("utf-8"))
    return StoredDataset(
        dataset_id=f"RD-{digest.hexdigest()[:32]}",
        symbol=symbol.strip(),
        total_rows=total,
        timeframes=tuple(summaries),
        created_at=datetime.min,  # replaced by the store on write
    )


def _session_id(key: str) -> str:
    """Deterministic from the idempotency key, as Phase 9 does for positions."""
    return "RS-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def _validated_key(key: str) -> str:
    stripped = key.strip()
    if not 16 <= len(stripped) <= 128 or not all(c.isalnum() or c in "-_" for c in stripped):
        raise ReplayServiceError(
            ReplayErrorKind.INVALID,
            "IDEMPOTENCY_KEY_INVALID",
            "an idempotency key is 16-128 characters of letters, digits, '-' or '_'",
        )
    return stripped


def _fingerprint(command: CreateReplaySession) -> str:
    """What "the same request" means for a retry: the plan and the data."""
    payload = json.dumps(
        {
            "symbol": command.symbol.strip(),
            "driver": command.driver.value,
            "replay_start": command.replay_start.isoformat(),
            "datasets": sorted(
                (item.timeframe.value, hashlib.sha256(item.content.encode()).hexdigest())
                for item in command.datasets
            ),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


__all__ = [
    "CreateReplaySession",
    "ReplayClock",
    "ReplayErrorKind",
    "ReplayLimits",
    "ReplayService",
    "ReplayServiceError",
    "RevealedWindow",
    "SessionView",
    "StepResult",
    "TimeframeUpload",
]
