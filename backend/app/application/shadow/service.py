"""Shadow Mode: watch confirmed evidence, record what the rules decided.

Phase 14 Part 1. Master spec section 78: the system analyses market data and
"opens no paper or real position automatically". This module is the whole of
that, and it computes nothing itself:

* the market state, its availability and its continuity are Phase 13's;
* the readings are Phase 1's, through the shared strategy-context builder the
  Phase 12 runner also uses;
* the decision is a registered Phase 12 :class:`StrategyPolicy`;
* the sizing verdict is Phase 3's;
* the regime, suitability and setup quality recorded beside an intent are the
  existing Phase 8 analysis, run over the same confirmed prefix.

## What it may look at

Only candles whose coverage ended at or before the boundary being evaluated.
The prefix is rebuilt per boundary by filtering the session's confirmed books,
so a consumer that falls behind decides exactly what it would have decided on
time: lag changes when a decision is written, never what it says. A forming
candle is never in a book, and a candle that arrives later cannot reach back.

## When it evaluates

On a genuinely new confirmed driver boundary, and nothing else. A duplicate,
a forming update, a heartbeat, a replayed notification and a reconnect are not
new market facts, and a late fill behind the head does not create a boundary -
it is recorded as an operational fact instead.

## What it never does

It creates no paper position, no backtest run and no order; it has no fill
model, so an entry it records has no result. The financial half of a decision
is the existing risk engine's answer, or the honest statement that no verified
product metadata exists - which is this deployment's default.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from app.application.analysis.orchestrator import AnalysisOutcome, run_analysis
from app.application.analysis.request import AnalysisRequest, TimeframeDataset
from app.application.analysis.serialisation import candles_to_csv
from app.application.live.records import RecordKind, StreamRecord
from app.application.live.session import LiveSession
from app.application.ports.contract_metadata import ContractMetadataProvider
from app.application.ports.market_data import CandleTextParser
from app.application.ports.paper import ProductResolver
from app.application.ports.system import ClockPort
from app.application.shadow.ports import (
    AttemptKeyHeldError,
    ShadowJournalStore,
    StoredShadowRun,
)
from app.application.strategy import confirmed_higher, readings_series
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.analysis.scenarios import ScenarioCase
from app.domain.backtest.policy import DecisionKind, Readings, StrategyContext, StrategyPolicy
from app.domain.backtest.registry import SUPPORTED_STRATEGIES, require_supported_rules
from app.domain.backtest.run import BacktestError
from app.domain.common.enums import Direction, Timeframe
from app.domain.live.book import ApplyOutcome
from app.domain.market.candle import Candle
from app.domain.replay import coverage_end
from app.domain.risk.sizing import AccountState, RiskPolicy, SizingOutcome, size_for_product
from app.domain.shadow.decision import (
    EntrySketch,
    FinancialState,
    JournalEntryKind,
    OperationalKind,
    ShadowDecision,
    ShadowEvidence,
    ShadowOutcome,
    TimeframeEvidence,
    TimeframeReadings,
)
from app.domain.shadow.eligibility import eligible_for_evaluation
from app.domain.shadow.identity import (
    RUN_PREFIX,
    configuration_fingerprint,
    decision_key,
    inputs_fingerprint,
    outcome_key,
)
from app.domain.shadow.outcome import (
    LevelEvent,
    OutcomeState,
    PriceDevelopment,
    ShadowOutcomeRecord,
    observe_development,
)
from app.domain.shadow.run import EndReason, ShadowError, ShadowLimits, ShadowRunStatus

__all__ = ["ShadowRequest", "ShadowRunner"]

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _AsOf:
    """A clock that reports the market moment being analysed, nothing else."""

    moment: datetime

    def now(self) -> datetime:
        return self.moment


@dataclass(frozen=True, slots=True)
class ShadowRequest:
    """What to observe, and under which frozen rules."""

    source_id: str
    instrument_label: str
    strategy: StrategyPolicy
    driver: Timeframe
    timeframes: tuple[Timeframe, ...]
    required: tuple[Timeframe, ...] = ()
    """Timeframes the policy genuinely needs. Empty means the driver alone -
    a dependency this build does not invent for a rule that does not have it."""

    account: AccountState | None = None
    risk: RiskPolicy | None = None
    analysis_evidence: bool = True
    """Run the existing analysis beside a proposed entry, for the regime,
    suitability and setup-quality the master spec asks a shadow record to
    carry. Never for every quiet boundary: that would be a full analysis per
    candle with nothing to attach it to."""


@dataclass(frozen=True, slots=True)
class _Capture:
    """Everything a boundary's decision may see, taken *at* that boundary.

    Built inside the stream callback, synchronously, so a consumer that falls
    behind cannot decide on evidence that arrived afterwards: the availability
    is the availability then, and the prefixes are the candles confirmed then.
    """

    boundary: datetime
    connection: str
    provenance: str
    market_currency: str
    evidence: tuple[TimeframeEvidence, ...]
    prefixes: Mapping[Timeframe, tuple[Candle, ...]]


@dataclass(frozen=True, slots=True)
class _Watch:
    """A decision whose price development is still being followed.

    It holds the levels and the boundary, and nothing else: the answer is
    recomputed from the confirmed series each time, so a watch cannot
    accumulate a private opinion about what happened.
    """

    decision_key: str
    boundary: datetime
    direction: Direction
    stop: Decimal
    targets: tuple[tuple[Decimal, int], ...]


@dataclass(frozen=True, slots=True)
class _Published:
    """A settled development that still stands, and the candles it read.

    Kept so a correction arriving *after* publication can still find every
    development whose evidence it contests. Only the window is held: the
    development itself is in the journal and is never edited.
    """

    watch: _Watch
    observed_to: datetime


@dataclass
class _RunState:
    run: StoredShadowRun
    request: ShadowRequest
    pending: deque[StreamRecord | _Capture] = field(default_factory=deque)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    sequence: int = 0
    observations: int = 0
    decisions: int = 0
    entries: int = 0
    first_boundary: datetime | None = None
    last_boundary: datetime | None = None
    evaluated: set[datetime] = field(default_factory=set)
    watches: dict[str, _Watch] = field(default_factory=dict)
    published: dict[str, _Published] = field(default_factory=dict)
    opened: bool = False
    outcome_sequence: int = 0
    outcomes_published: int = 0
    watches_refused: int = 0
    session: LiveSession | None = None
    overflowed: bool = False
    finished: bool = False
    end_reason: EndReason | None = None
    failure_code: str | None = None


class ShadowRunner:
    """Opens shadow runs, drives them from a live session, writes the journal."""

    def __init__(
        self,
        *,
        store: ShadowJournalStore,
        clock: ClockPort,
        parser: CandleTextParser,
        limits: ShadowLimits | None = None,
        resolver: ProductResolver | None = None,
        contracts: ContractMetadataProvider | None = None,
        supported: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._parser = parser
        self._limits = limits or ShadowLimits()
        self._resolver = resolver
        self._contracts = contracts
        self._supported = SUPPORTED_STRATEGIES if supported is None else supported
        self._runs: dict[str, _RunState] = {}

    @property
    def limits(self) -> ShadowLimits:
        return self._limits

    def __len__(self) -> int:
        return len(self._runs)

    # -- opening ---------------------------------------------------------

    async def open(self, request: ShadowRequest, *, run_id: str) -> StoredShadowRun:
        """Validate the rules, freeze the configuration, create the run."""
        if len(self._runs) >= self._limits.max_runs:
            raise ShadowError(
                "SHADOW_CAPACITY",
                f"at most {self._limits.max_runs} shadow runs observe at once",
            )
        if not run_id.startswith(RUN_PREFIX):
            raise ShadowError("INVALID_RUN_ID", "a shadow run id starts with " + RUN_PREFIX)
        if request.driver not in request.timeframes:
            raise ShadowError(
                "DRIVER_NOT_SUBSCRIBED",
                "the driver timeframe must be one the run subscribes to",
            )
        for timeframe in request.required:
            if timeframe not in request.timeframes:
                raise ShadowError(
                    "REQUIRED_NOT_SUBSCRIBED",
                    f"{timeframe.value} is required by the rules but not subscribed",
                )
        if (request.account is None) != (request.risk is None):
            raise ShadowError(
                "INCOMPLETE_RISK_CONFIGURATION",
                "give both an account and a risk policy, or neither",
            )
        strategy = request.strategy
        try:
            require_supported_rules(
                strategy.identifier, strategy.version, supported=self._supported
            )
        except BacktestError as error:
            raise ShadowError(error.code, error.reason) from None

        parameters = {key: str(value) for key, value in strategy.parameters().items()}
        risk_facts = _risk_facts(request.risk)
        account_facts = _account_facts(request.account)
        configuration = configuration_fingerprint(
            source_id=request.source_id,
            instrument_label=request.instrument_label,
            provenance="",  # filled once the session states it; see `drive`
            strategy_id=strategy.identifier,
            strategy_version=strategy.version,
            strategy_parameters=parameters,
            driver=request.driver.value,
            timeframes=[tf.value for tf in request.timeframes],
            risk=risk_facts,
            account=account_facts,
            evidence_rules=_evidence_rules(request.analysis_evidence),
        )
        now = self._clock.now()
        run = StoredShadowRun(
            run_id=run_id,
            configuration=configuration,
            source_id=request.source_id,
            instrument_label=request.instrument_label,
            provenance="",
            market_currency="",
            strategy_id=strategy.identifier,
            strategy_version=strategy.version,
            strategy_parameters=parameters,
            driver=request.driver,
            timeframes=request.timeframes,
            required_timeframes=request.required or (request.driver,),
            risk=risk_facts,
            account=account_facts,
            status=ShadowRunStatus.OBSERVING,
            started_at=now,
        )
        self._runs[run_id] = _RunState(run=run, request=request)
        return run

    def configuration_for(self, request: ShadowRequest, session: LiveSession) -> str:
        """The frozen configuration a request *would* have over this session.

        Computed without opening a run or taking a slot, so a retried creation
        can be recognised - and answered with the run it already made - even
        when every slot is taken. It is the same fingerprint :meth:`begin`
        stamps, from the same inputs, so the comparison is exact.
        """
        snapshot = session.snapshot()
        strategy = request.strategy
        return configuration_fingerprint(
            source_id=request.source_id,
            instrument_label=request.instrument_label,
            provenance=snapshot.provenance.value,
            strategy_id=strategy.identifier,
            strategy_version=strategy.version,
            strategy_parameters={k: str(v) for k, v in strategy.parameters().items()},
            driver=request.driver.value,
            timeframes=[tf.value for tf in request.timeframes],
            risk=_risk_facts(request.risk),
            account=_account_facts(request.account),
            evidence_rules=_evidence_rules(request.analysis_evidence),
        )

    def discard(self, run_id: str) -> None:
        """Forget a run that was opened but never observed anything.

        Used when a creation attempt loses a race for its idempotency key: the
        loser must not keep a slot, and nothing was written for it.
        """
        self._runs.pop(run_id, None)

    def request_end(self, run_id: str, reason: EndReason) -> None:
        """Ask a run to stop at the next record it processes.

        It is a request, not a kill: the consumer finishes the entry it is
        writing, so a cancelled run has a complete journal up to a stated
        point rather than a torn one.
        """
        state = self._runs.get(run_id)
        if state is not None and state.end_reason is None:
            state.end_reason = reason
            state.ready.set()

    def observer(self, run_id: str) -> Callable[[StreamRecord], None]:
        """The callback to hand :class:`LiveSession`. Never blocks the stream."""
        state = self._state(run_id)

        def record(item: StreamRecord) -> None:
            if state.finished:
                return
            if len(state.pending) >= self._limits.max_pending_records:
                # A journal with an unrecorded hole is worse than a run that
                # says it could not keep up.
                state.overflowed = True
                state.ready.set()
                return
            captured = self._capture(state, item)
            state.pending.append(item if captured is None else captured)
            state.ready.set()

        return record

    def _capture(self, state: _RunState, record: StreamRecord) -> _Capture | None:
        """Freeze a new confirmed boundary's evidence, or return None.

        Runs inside the stream callback: the cost is the price of keeping the
        observation causal, and a shadow run that cannot keep up must slow the
        stream rather than decide on later information.
        """
        session = state.session
        if session is None or record.kind is not RecordKind.OBSERVATION:
            return None
        if record.timeframe is not state.request.driver:
            return None
        if record.outcome != ApplyOutcome.ACCEPTED.value or record.closed is not True:
            return None
        boundary = _boundary_of(record)
        if boundary is None or boundary in state.evaluated:
            return None
        market = session.state()
        snapshot = session.snapshot()
        prefixes = {
            timeframe: _confirmed_at(market, timeframe, boundary)
            for timeframe in state.run.timeframes
        }
        evidence = []
        for item in snapshot.timeframes[: self._limits.max_evidence_timeframes]:
            timeframe = item.book.timeframe
            candles = prefixes.get(timeframe, ())
            evidence.append(
                TimeframeEvidence(
                    timeframe=timeframe,
                    available=item.availability.value == "AVAILABLE",
                    freshness=item.freshness.value,
                    integrity=item.book.integrity.value,
                    confirmed_count=len(candles),
                    reasons=tuple(item.reasons[: self._limits.max_excluded_reasons]),
                    last_coverage_end=coverage_end(candles[-1]) if candles else None,
                )
            )
        return _Capture(
            boundary=boundary,
            connection=snapshot.connection.value,
            provenance=snapshot.provenance.value,
            market_currency=snapshot.market_currency.value,
            evidence=tuple(evidence),
            prefixes=prefixes,
        )

    # -- driving ---------------------------------------------------------

    async def begin(
        self, run_id: str, session: LiveSession, *, attempt_key: str | None = None
    ) -> StoredShadowRun:
        """Stamp the stream's provenance on the run and open its journal.

        Separate from observing, and awaited before creation answers: a run the
        caller has been told about exists in the journal, so a retry that finds
        the attempt key already claimed can always read the run it names.
        """
        state = self._state(run_id)
        state.session = session
        try:
            await self._begin(state, session, attempt_key=attempt_key)
        except AttemptKeyHeldError as held:
            # Nothing was written for this run, so it holds no slot either.
            held.requested_configuration = state.run.configuration
            self._runs.pop(run_id, None)
            raise
        except BaseException:
            self._runs.pop(run_id, None)
            raise
        return state.run

    async def _begin(
        self, state: _RunState, session: LiveSession, *, attempt_key: str | None = None
    ) -> None:
        """Stamp the stream's own provenance on the run, and open the journal."""
        if state.opened:
            return
        state.opened = True
        snapshot = session.snapshot()
        state.run = _with_provenance(
            state.run,
            snapshot.provenance.value,
            snapshot.market_currency.value,
            _evidence_rules(state.request.analysis_evidence),
        )
        await self._store.create_run(state.run, attempt_key=attempt_key)
        await self._write(
            state,
            self._operational(
                state,
                OperationalKind.RUN_OPENED,
                reason=(
                    f"observing {state.run.instrument_label} over "
                    f"{state.run.provenance} with {state.run.strategy_id} "
                    f"{state.run.strategy_version}"
                ),
                marker="opened",
            ),
        )

    async def drive(self, run_id: str, session: LiveSession) -> StoredShadowRun:
        """Run the session and record what its evidence implies, until it ends."""
        state = self._state(run_id)
        state.session = session
        await self._begin(state, session)
        stream = asyncio.create_task(session.run(), name=f"shadow-stream-{run_id}")
        try:
            await self._consume(state, session, stream.done)
        except asyncio.CancelledError:
            state.end_reason = state.end_reason or EndReason.CANCELLED
            raise
        finally:
            if not stream.done():
                stream.cancel()
            await asyncio.wait({stream})
            return_run = await self._finish(state, session)
        return return_run

    async def follow(
        self, run_id: str, session: LiveSession, finished: Callable[[], bool]
    ) -> StoredShadowRun:
        """Observe a session somebody else is driving, until it ends.

        Part 2A attaches a shadow run to an existing live session rather than
        opening a second stream over the same data: one provider stream, many
        readers, so two observers of one session cannot see different markets.
        The run does not own the session, cannot pace or stop it, and ends when
        ``finished()`` says the records have run out.
        """
        state = self._state(run_id)
        state.session = session
        await self._begin(state, session)
        try:
            await self._consume(state, session, finished)
        except asyncio.CancelledError:
            state.end_reason = state.end_reason or EndReason.CANCELLED
            raise
        finally:
            followed = await self._finish(state, session)
        return followed

    async def _consume(
        self, state: _RunState, session: LiveSession, finished: Callable[[], bool]
    ) -> None:
        while True:
            while state.pending:
                item = state.pending.popleft()
                if isinstance(item, _Capture):
                    await self._evaluate(state, item)
                else:
                    await self._on_record(state, item)
                if state.end_reason is not None:
                    return
            if state.overflowed:
                state.end_reason = EndReason.PROVIDER_ERROR
                state.failure_code = "PENDING_OVERFLOW"
                await self._write(
                    state,
                    self._operational(
                        state,
                        OperationalKind.EVALUATION_FAILED,
                        reason=(
                            "the shadow consumer fell more than "
                            f"{self._limits.max_pending_records} records behind; the run "
                            "stops rather than leave an unrecorded hole"
                        ),
                        marker="overflow",
                    ),
                )
                return
            if finished() and not state.pending:
                state.end_reason = state.end_reason or _end_reason_for(session)
                return
            state.ready.clear()
            with suppress(TimeoutError):
                # A short wait rather than an indefinite one: the stream task
                # may finish without producing another record.
                await asyncio.wait_for(state.ready.wait(), timeout=0.05)

    async def _on_record(self, state: _RunState, record: StreamRecord) -> None:
        if record.kind is RecordKind.SIGNAL:
            await self._on_signal(state, record)
            return
        if record.kind is RecordKind.REJECTED:
            await self._write(
                state,
                self._operational(
                    state,
                    OperationalKind.OBSERVATION_REFUSED,
                    reason=f"the stream refused an observation: {record.outcome}",
                    marker=f"rejected:{record.outcome}",
                ),
            )
            return
        if record.outcome == ApplyOutcome.CONFLICT.value:
            await self._on_conflict(state, record)
            return
        if record.timeframe is not state.request.driver:
            return  # a higher timeframe closing is evidence, not a boundary
        if record.outcome == ApplyOutcome.LATE_FILL.value:
            await self._write(
                state,
                self._operational(
                    state,
                    OperationalKind.LATE_FILL,
                    reason=(
                        "a late candle filled a recorded gap; decisions already "
                        "published are left as they were"
                    ),
                    boundary=_boundary_of(record),
                    marker=f"late:{record.open_time}",
                ),
            )
            return
        # An accepted new boundary was captured in the callback; a duplicate
        # or a forming update is not a new market fact and reaches nothing.
        return

    async def _on_signal(self, state: _RunState, record: StreamRecord) -> None:
        kinds = {
            "DISCONNECTED": OperationalKind.PROVIDER_DISCONNECTED,
            "CONNECTED": OperationalKind.PROVIDER_RECOVERING,
        }
        operational = kinds.get(record.outcome)
        if operational is None:
            return  # END_OF_STREAM and OVERFLOW are recorded when the run ends
        await self._write(
            state,
            self._operational(
                state,
                operational,
                reason=f"the provider signalled {record.outcome}",
                marker=f"signal:{record.outcome}:{record.received_at.isoformat()}",
            ),
        )

    # -- evaluating ------------------------------------------------------

    async def _evaluate(self, state: _RunState, capture: _Capture) -> None:
        boundary = capture.boundary
        driver = state.request.driver
        if boundary in state.evaluated:
            return
        if state.observations >= self._limits.max_observations:
            state.end_reason = EndReason.OBSERVATION_LIMIT
            await self._write(
                state,
                self._operational(
                    state,
                    OperationalKind.RUN_ENDED,
                    reason=(
                        f"the run reached its bound of {self._limits.max_observations} "
                        "observations and stopped observing"
                    ),
                    boundary=boundary,
                    marker="limit",
                ),
            )
            return

        state.evaluated.add(boundary)
        state.observations += 1
        state.first_boundary = state.first_boundary or boundary
        state.last_boundary = boundary

        evidence = list(capture.evidence)
        eligibility = eligible_for_evaluation(
            driver=driver,
            required=state.run.required_timeframes,
            evidence=evidence,
            connection=capture.connection,
            warm_up_bars=state.request.strategy.warm_up_bars,
        )
        base = ShadowEvidence(
            provenance=capture.provenance,
            market_currency=capture.market_currency,
            connection=capture.connection,
            bars_available=len(capture.prefixes.get(driver, ())),
            included=tuple(item.timeframe for item in evidence if item.available),
            excluded=tuple(item for item in evidence if not item.available),
            timeframes=tuple(evidence),
        )
        if eligibility.refused:
            await self._write(
                state,
                ShadowDecision(
                    kind=JournalEntryKind.DECISION,
                    sequence=self._next(state),
                    decision_key=decision_key(
                        run_id=state.run.run_id,
                        configuration=state.run.configuration,
                        boundary=boundary,
                        inputs=None,
                        marker=eligibility.code,
                    ),
                    market_boundary=boundary,
                    recorded_at=self._clock.now(),
                    outcome=ShadowOutcome.UNAVAILABLE,
                    reason=_bounded(
                        "; ".join(eligibility.reasons) or eligibility.code, self._limits
                    ),
                    evidence=base,
                    fields={"code": eligibility.code},
                ),
            )
            # A boundary nobody could decide at is still a boundary whose
            # candles may settle an earlier decision, so the follow-up runs.
            await self._advance_watches(state, capture.prefixes.get(driver, ()), exhausted=False)
            return

        context, inputs, readings = _context_for(state, capture)
        try:
            decision = state.request.strategy.decide(context)
        except Exception as error:  # noqa: BLE001 - reported safely, below
            _LOG.error(
                "shadow strategy evaluation failed",
                extra={
                    "run_id": state.run.run_id,
                    "strategy": state.run.strategy_id,
                    "strategy_version": state.run.strategy_version,
                    "error_type": type(error).__name__,
                },
            )
            state.end_reason = EndReason.EVALUATION_ERROR
            state.failure_code = "STRATEGY_FAILED"
            await self._write(
                state,
                self._operational(
                    state,
                    OperationalKind.EVALUATION_FAILED,
                    reason="the strategy failed to evaluate this boundary",
                    boundary=boundary,
                    marker="strategy-error",
                ),
            )
            return

        evidence_with_readings = ShadowEvidence(
            provenance=base.provenance,
            market_currency=base.market_currency,
            connection=base.connection,
            bars_available=context.bars_available,
            included=base.included,
            excluded=base.excluded,
            readings=readings,
            timeframes=base.timeframes,
        )
        outcome = _outcome_for(decision.kind)
        entry = None
        financial = FinancialState.NOT_APPLICABLE
        risk_outcome: str | None = None
        risk_reason: str | None = None

        if decision.entry is not None:
            intent = decision.entry
            entry = EntrySketch(
                direction=intent.direction,
                intended_entry=intent.intended_entry,
                stop=intent.stop,
                targets=tuple((level.price, level.quantity) for level in intent.targets),
                requested_quantity=intent.quantity,
            )
            financial, risk_outcome, risk_reason, approved = await self._financial(state, intent)
            if approved is not None:
                entry = EntrySketch(
                    direction=entry.direction,
                    intended_entry=entry.intended_entry,
                    stop=entry.stop,
                    targets=entry.targets,
                    requested_quantity=entry.requested_quantity,
                    approved_quantity=approved,
                )
            if financial is FinancialState.REFUSED:
                # An intent never overrides a refusal: the journal records the
                # refusal as the outcome, with no approved quantity.
                outcome = ShadowOutcome.REFUSED
            if state.request.analysis_evidence:
                evidence_with_readings = await self._with_analysis(
                    state, capture, evidence_with_readings, intent.direction
                )

        key = decision_key(
            run_id=state.run.run_id,
            configuration=state.run.configuration,
            boundary=boundary,
            inputs=inputs,
        )
        await self._write(
            state,
            ShadowDecision(
                kind=JournalEntryKind.DECISION,
                sequence=self._next(state),
                decision_key=key,
                market_boundary=boundary,
                recorded_at=self._clock.now(),
                outcome=outcome,
                reason=_bounded(decision.reason, self._limits),
                strategy_kind=decision.kind.value,
                direction=entry.direction if entry is not None else None,
                entry=entry,
                financial_state=financial,
                risk_outcome=risk_outcome,
                risk_reason=None if risk_reason is None else _bounded(risk_reason, self._limits),
                evidence=evidence_with_readings,
                input_fingerprint=inputs,
            ),
        )
        state.decisions += 1
        self._watch(state, key, boundary, entry, outcome)
        # What this boundary's own candles say about *earlier* decisions. The
        # prefix is the one captured at this boundary, so following a decision
        # can never see further than observing it could.
        await self._advance_watches(state, capture.prefixes.get(driver, ()), exhausted=False)

    async def _financial(
        self, state: _RunState, intent: object
    ) -> tuple[FinancialState, str | None, str | None, int | None]:
        """The existing risk engine's answer, or the honest absence of one."""
        request = state.request
        if request.account is None or request.risk is None:
            return FinancialState.NOT_CONFIGURED, None, None, None
        if self._resolver is None:
            return (
                FinancialState.METADATA_UNAVAILABLE,
                None,
                "no verified contract metadata provider is configured",
                None,
            )
        try:
            product = await self._resolver.resolve(state.run.instrument_label)
        except Exception as error:  # noqa: BLE001 - reported safely, below
            _LOG.error(
                "shadow product resolution failed",
                extra={"run_id": state.run.run_id, "error_type": type(error).__name__},
            )
            return (
                FinancialState.METADATA_UNAVAILABLE,
                None,
                "contract metadata could not be read",
                None,
            )
        if product is None:
            return (
                FinancialState.METADATA_UNAVAILABLE,
                None,
                f"no verified contract metadata for {state.run.instrument_label}",
                None,
            )
        sizing = size_for_product(
            direction=intent.direction,  # type: ignore[attr-defined]
            entry_price=intent.intended_entry,  # type: ignore[attr-defined]
            stop_price=intent.stop,  # type: ignore[attr-defined]
            product=product,
            account=request.account,
            policy=request.risk,
        )
        if sizing.outcome is SizingOutcome.ALLOWED and sizing.allowed_contracts:
            approved = min(int(intent.quantity), int(sizing.allowed_contracts))  # type: ignore[attr-defined]
            return FinancialState.APPROVED, sizing.outcome.value, sizing.reason, approved
        if sizing.outcome is SizingOutcome.UNDETERMINED:
            return FinancialState.UNDETERMINED, sizing.outcome.value, sizing.reason, None
        return FinancialState.REFUSED, sizing.outcome.value, sizing.reason, None

    async def _with_analysis(
        self,
        state: _RunState,
        capture: _Capture,
        evidence: ShadowEvidence,
        direction: Direction,
    ) -> ShadowEvidence:
        """The existing analysis over the same confirmed prefix, for the record."""
        boundary = capture.boundary
        datasets: list[TimeframeDataset] = []
        for timeframe in state.run.timeframes:
            candles = capture.prefixes.get(timeframe, ())
            if candles:
                datasets.append(
                    TimeframeDataset(
                        timeframe=timeframe,
                        content=candles_to_csv(candles),
                        source_name=f"shadow {timeframe.value}",
                    )
                )
        if not datasets:
            return evidence
        try:
            outcome = await run_analysis(
                AnalysisRequest(
                    symbol=state.run.instrument_label,
                    datasets=tuple(datasets),
                    account=state.request.account,
                    risk_policy=state.request.risk,
                ),
                parser=self._parser,
                clock=_AsOf(boundary),
                contracts=self._contracts,
            )
        except Exception as error:  # noqa: BLE001 - evidence is optional, the decision is not
            _LOG.warning(
                "shadow analysis evidence unavailable",
                extra={"run_id": state.run.run_id, "error_type": type(error).__name__},
            )
            return evidence
        regime, suitability, quality = _analysis_facts(outcome, direction)
        return ShadowEvidence(
            provenance=evidence.provenance,
            market_currency=evidence.market_currency,
            connection=evidence.connection,
            bars_available=evidence.bars_available,
            included=evidence.included,
            excluded=evidence.excluded,
            readings=evidence.readings,
            timeframes=evidence.timeframes,
            regime=regime,
            suitability=suitability,
            setup_quality=quality,
            analysis_market_as_of=boundary,
        )

    # -- journal ---------------------------------------------------------

    async def _write(self, state: _RunState, entry: ShadowDecision) -> None:
        written = await self._store.append(state.run.run_id, [entry])
        state.entries += written

    # -- following what happened next ------------------------------------

    def _watch(
        self,
        state: _RunState,
        key: str,
        boundary: datetime,
        entry: EntrySketch | None,
        outcome: ShadowOutcome,
    ) -> None:
        """Start following a proposed entry, if this decision proposed one.

        Only an ENTRY_INTENT is followed. A refused signal has levels too, but
        nothing was proposed to the market, and following it would invite the
        reading that a position existed and then lost.
        """
        if entry is None or outcome is not ShadowOutcome.ENTRY_INTENT:
            return
        if len(state.watches) >= self._limits.max_open_watches:
            state.watches_refused += 1
            return
        state.watches[key] = _Watch(
            decision_key=key,
            boundary=boundary,
            direction=entry.direction,
            stop=entry.stop,
            targets=entry.targets,
        )

    async def _advance_watches(
        self, state: _RunState, candles: tuple[Candle, ...], *, exhausted: bool
    ) -> None:
        """Ask each open watch what price has done, and publish the settled ones.

        ``candles`` is the driver's confirmed series as of the boundary just
        processed. Forward-only causality is not enforced here by convention:
        :func:`observe_development` takes the decision's own boundary and
        discards everything at or before it.
        """
        if not state.watches:
            return
        settled: list[ShadowOutcomeRecord] = []
        for watch in list(state.watches.values()):
            development = observe_development(
                direction=watch.direction,
                stop=watch.stop,
                targets=watch.targets,
                boundary=watch.boundary,
                timeframe=state.request.driver,
                candles=candles,
                window=self._limits.max_outcome_window,
                exhausted=exhausted,
            )
            if development.state is OutcomeState.PENDING:
                continue  # still an open question, and recorded as none
            settled.append(self._record(state, watch, development))
            del state.watches[watch.decision_key]
            if development.observed_to is not None:
                state.published[watch.decision_key] = _Published(watch, development.observed_to)
        await self._publish(state, settled)

    async def _on_conflict(self, state: _RunState, record: StreamRecord) -> None:
        """A correction contradicted a candle already held. Say what it touches.

        The stored candle is not rewritten (Phase 13 quarantines the
        correction), so whatever read it now rests on contested evidence. Two
        dependencies are followed, and nothing is touched for being near in
        time:

        * a **decision** read the candle when the candle closed at or before its
          boundary - for the driver and for every other timeframe this run
          subscribes to, since both reach the policy;
        * a **development** read the candle when it is a driver candle inside
          that development's observed window - after the decision, and no later
          than the last candle the development took into account.

        A correction of a timeframe this run never subscribed to reached none
        of its evidence and is not recorded here.
        """
        timeframe = record.timeframe
        opened = record.open_time
        contested = _boundary_of(record)
        if timeframe is None or opened is None or contested is None:
            return
        if timeframe not in state.run.timeframes:
            return
        marker = f"conflict:{timeframe.value}:{opened.isoformat()}"
        await self._write(
            state,
            self._operational(
                state,
                OperationalKind.CONFLICTING_CORRECTION,
                reason=(
                    f"a conflicting correction of the {timeframe.value} candle opening "
                    f"{opened.isoformat()} was quarantined; the stored candle was not "
                    "rewritten and no published decision was changed"
                ),
                boundary=contested,
                marker=marker,
            ),
        )
        await self._supersede(state, contested, marker=marker)
        await self._invalidate(
            state,
            opened=opened,
            contested=contested,
            label=f"{timeframe.value} candle opening {opened.isoformat()}",
            outcome_evidence=timeframe is state.request.driver,
        )

    async def _invalidate(
        self,
        state: _RunState,
        *,
        opened: datetime,
        contested: datetime,
        label: str,
        outcome_evidence: bool,
    ) -> None:
        """Append an INVALIDATED development for every follow-up that read it."""

        def why(boundary: datetime, observed_to: datetime | None) -> str | None:
            if boundary >= contested:
                return (
                    f"the decision read the corrected {label}, so what price did "
                    "afterwards cannot be attributed to it"
                )
            if (
                outcome_evidence
                and opened >= boundary
                and (observed_to is None or contested <= observed_to)
            ):
                return (
                    f"this development read the corrected {label}; the level it "
                    "reports cannot be stated from contested evidence"
                )
            return None

        settled: list[ShadowOutcomeRecord] = []
        for watch in list(state.watches.values()):
            # An open window has read every driver candle since its decision.
            reason = why(watch.boundary, None)
            if reason is not None:
                settled.append(self._record(state, watch, _invalidated(reason)))
                del state.watches[watch.decision_key]
        for key, published in list(state.published.items()):
            reason = why(published.watch.boundary, published.observed_to)
            if reason is not None:
                settled.append(self._record(state, published.watch, _invalidated(reason)))
                del state.published[key]
        await self._publish(state, settled)

    def _record(
        self, state: _RunState, watch: _Watch, development: PriceDevelopment
    ) -> ShadowOutcomeRecord:
        state.outcome_sequence += 1
        return ShadowOutcomeRecord(
            run_id=state.run.run_id,
            decision_key=watch.decision_key,
            sequence=state.outcome_sequence,
            outcome_key=outcome_key(
                run_id=state.run.run_id,
                decision_key_value=watch.decision_key,
                state=development.state.value,
                event=development.event.value,
                observed_to=development.observed_to,
                event_at=development.event_at,
            ),
            recorded_at=self._clock.now(),
            decision_boundary=watch.boundary,
            direction=watch.direction,
            development=development,
        )

    async def _publish(self, state: _RunState, records: list[ShadowOutcomeRecord]) -> None:
        if not records:
            return
        state.outcomes_published += await self._store.append_outcomes(state.run.run_id, records)

    async def _supersede(
        self, state: _RunState, contested: datetime | None, *, marker: str
    ) -> None:
        """Say which published decisions a correction puts in doubt.

        A decision that was published is never edited, and a correction does not
        make it untrue that the rules decided what they decided. What a reader
        needs is the fact that the evidence underneath it is now contested - so
        the invalidation is a new entry naming the earliest affected boundary,
        not a rewrite of the entries it concerns. Every decision from that
        boundary onward read the contested candle, so all of them are named.
        """
        if contested is None:
            return
        affected = sorted(boundary for boundary in state.evaluated if boundary >= contested)
        if not affected:
            return
        await self._write(
            state,
            self._operational(
                state,
                OperationalKind.DECISION_SUPERSEDED,
                reason=(
                    f"{len(affected)} published decision(s) from this boundary onward read "
                    "evidence that a correction contests; they stand as published and are "
                    "marked superseded rather than rewritten"
                ),
                boundary=affected[0],
                marker=f"superseded:{marker}",
            ),
        )

    def _operational(
        self,
        state: _RunState,
        kind: OperationalKind,
        *,
        reason: str,
        boundary: datetime | None = None,
        marker: str,
    ) -> ShadowDecision:
        return ShadowDecision(
            kind=JournalEntryKind.OPERATIONAL,
            sequence=self._next(state),
            decision_key=decision_key(
                run_id=state.run.run_id,
                configuration=state.run.configuration,
                boundary=boundary,
                inputs=None,
                marker=f"{kind.value}:{marker}",
            ),
            market_boundary=boundary,
            recorded_at=self._clock.now(),
            operational=kind,
            reason=_bounded(reason, self._limits),
        )

    def _next(self, state: _RunState) -> int:
        state.sequence += 1
        return state.sequence

    async def _finish(self, state: _RunState, session: LiveSession) -> StoredShadowRun:
        if state.finished:
            return state.run
        state.finished = True
        reason = state.end_reason or _end_reason_for(session)
        ended = self._clock.now()
        # The stream is over, so every question still open has its final
        # answer now: a window that never closed is unavailable, not a result.
        await self._advance_watches(state, _final_series(session, state), exhausted=True)
        await self._write(
            state,
            self._operational(
                state,
                OperationalKind.RUN_ENDED,
                reason=(
                    f"observation ended: {reason.value}. This is the end of an "
                    "observation, never a claim about the market."
                ),
                marker=f"ended:{reason.value}",
            ),
        )
        run = await self._store.finish_run(
            state.run.run_id,
            status=ShadowRunStatus.ENDED,
            end_reason=reason,
            ended_at=ended,
            observations=state.observations,
            decisions=state.decisions,
            entries=state.entries,
            first_boundary=state.first_boundary,
            last_boundary=state.last_boundary,
            failure_code=state.failure_code,
        )
        state.run = run
        self._runs.pop(state.run.run_id, None)
        return run

    def _state(self, run_id: str) -> _RunState:
        state = self._runs.get(run_id)
        if state is None:
            raise ShadowError("SHADOW_RUN_NOT_FOUND", "no such shadow run in this process")
        return state


# ----------------------------------------------------------------------


def _evidence_rules(analysis_evidence: bool) -> str:
    """Which evidence a run keeps, as a versioned name in its identity."""
    return "shadow-evidence/v1+analysis" if analysis_evidence else "shadow-evidence/v1"


def _with_provenance(
    run: StoredShadowRun, provenance: str, currency: str, evidence_rules: str
) -> StoredShadowRun:
    configuration = configuration_fingerprint(
        source_id=run.source_id,
        instrument_label=run.instrument_label,
        provenance=provenance,
        strategy_id=run.strategy_id,
        strategy_version=run.strategy_version,
        strategy_parameters=run.strategy_parameters,
        driver=run.driver.value,
        timeframes=[tf.value for tf in run.timeframes],
        risk=run.risk,
        account=run.account,
        evidence_rules=evidence_rules,
    )
    return StoredShadowRun(
        run_id=run.run_id,
        configuration=configuration,
        source_id=run.source_id,
        instrument_label=run.instrument_label,
        provenance=provenance,
        market_currency=currency,
        strategy_id=run.strategy_id,
        strategy_version=run.strategy_version,
        strategy_parameters=run.strategy_parameters,
        driver=run.driver,
        timeframes=run.timeframes,
        required_timeframes=run.required_timeframes,
        risk=run.risk,
        account=run.account,
        status=run.status,
        started_at=run.started_at,
    )


def _end_reason_for(session: LiveSession) -> EndReason:
    reason = session.snapshot().termination_reason
    mapping = {
        "END_OF_STREAM": EndReason.STREAM_ENDED,
        "CANCELLED": EndReason.CANCELLED,
        "OVERLOADED": EndReason.PROVIDER_ERROR,
        "PROVIDER_ERROR": EndReason.PROVIDER_ERROR,
        "RECONNECT_LIMIT": EndReason.PROVIDER_ERROR,
    }
    return mapping.get(reason.value if reason else "", EndReason.CANCELLED)


def _invalidated(reason: str) -> PriceDevelopment:
    return PriceDevelopment(
        state=OutcomeState.INVALIDATED, event=LevelEvent.NOT_OBSERVED, unresolved_reason=reason
    )


def _boundary_of(record: StreamRecord) -> datetime | None:
    if record.open_time is None or record.timeframe is None:
        return None
    return record.open_time + timedelta(minutes=record.timeframe.minutes)


def _final_series(session: LiveSession, state: _RunState) -> tuple[Candle, ...]:
    """The driver's confirmed candles at the end of the run.

    Read once, when the stream is over, to settle the questions still open.
    Using it earlier would be the lookahead the rest of this module exists to
    prevent - which is why every other read goes through a boundary's capture.
    """
    book = session.state().book(state.request.driver)
    return tuple(observation.candle for observation in book.confirmed())


def _confirmed_at(market: object, timeframe: Timeframe, boundary: datetime) -> tuple[Candle, ...]:
    """The confirmed candles whose coverage ended at or before the boundary.

    The filter is what makes a late consumer decide what it would have decided
    on time, and what keeps a candle that arrived afterwards out of an earlier
    decision.
    """
    book = market.book(timeframe)  # type: ignore[attr-defined]
    return tuple(
        observation.candle
        for observation in book.confirmed()
        if coverage_end(observation.candle) <= boundary
    )


def _context_for(
    state: _RunState, capture: _Capture
) -> tuple[StrategyContext, str, tuple[TimeframeReadings, ...]]:
    driver = state.request.driver
    boundary = capture.boundary
    driver_candles = capture.prefixes.get(driver, ())
    driver_readings = readings_series(driver_candles)
    higher_candles = {
        timeframe: capture.prefixes.get(timeframe, ())
        for timeframe in state.run.timeframes
        if timeframe is not driver
    }
    higher_readings = {
        timeframe: readings_series(candles) for timeframe, candles in higher_candles.items()
    }
    higher = confirmed_higher(higher_candles, higher_readings, boundary)
    index = len(driver_candles) - 1
    current = driver_readings[index] if driver_readings else Readings()
    previous = driver_readings[index - 1] if index > 0 else Readings()
    bar = driver_candles[-1]
    context = StrategyContext(
        as_of=boundary,
        symbol=bar.symbol,
        driver=driver,
        bar=bar,
        current=current,
        previous=previous,
        higher=higher,
        bars_available=len(driver_candles),
        has_open_position=False,
    )
    inputs = inputs_fingerprint(
        boundary=boundary,
        bar={
            "open_time": bar.open_time.isoformat(),
            "open": str(bar.open),
            "high": str(bar.high),
            "low": str(bar.low),
            "close": str(bar.close),
            "volume": str(bar.volume),
        },
        current=_readings_map(current),
        previous=_readings_map(previous),
        higher={tf.value: _readings_map(item) for tf, item in higher.items()},
        bars_available=len(driver_candles),
    )
    readings = (
        _readings_record(driver, current),
        *(_readings_record(tf, item) for tf, item in sorted(higher.items(), key=_by_minutes)),
    )
    return context, inputs, readings


def _by_minutes(item: tuple[Timeframe, Readings]) -> int:
    return item[0].minutes


def _readings_map(readings: Readings) -> dict[str, str | None]:
    return {
        "ema_fast": _text(readings.ema_fast),
        "ema_slow": _text(readings.ema_slow),
        "rsi": _text(readings.rsi),
        "atr": _text(readings.atr),
        "adx": _text(readings.adx),
    }


def _readings_record(timeframe: Timeframe, readings: Readings) -> TimeframeReadings:
    return TimeframeReadings(
        timeframe=timeframe,
        ema_fast=_text(readings.ema_fast),
        ema_slow=_text(readings.ema_slow),
        rsi=_text(readings.rsi),
        atr=_text(readings.atr),
        adx=_text(readings.adx),
    )


def _text(value: float | None) -> str | None:
    """One spelling per reading, so a fingerprint is stable across runs."""
    return None if value is None else repr(float(value))


def _outcome_for(kind: DecisionKind) -> ShadowOutcome:
    return {
        DecisionKind.NO_SIGNAL: ShadowOutcome.NO_SIGNAL,
        DecisionKind.WAIT: ShadowOutcome.WAIT,
        DecisionKind.ENTRY_INTENT: ShadowOutcome.ENTRY_INTENT,
        DecisionKind.EXIT_INTENT: ShadowOutcome.EXIT_INTENT,
    }[kind]


def _analysis_facts(
    outcome: AnalysisOutcome, direction: Direction
) -> tuple[str | None, str | None, str | None]:
    """Regime, suitability and setup quality, read from the existing analysis."""
    analysis = outcome.analysis
    if analysis is None:
        return None, None, None
    case = ScenarioCase.BULL if direction is Direction.LONG else ScenarioCase.BEAR
    quality = analysis.scenarios.case(case).quality
    evidence_direction = (
        EvidenceDirection.BULLISH if direction is Direction.LONG else EvidenceDirection.BEARISH
    )
    assessment = next(
        (verdict for candidate, verdict in outcome.suitability if candidate is evidence_direction),
        None,
    )
    suitability = (
        None
        if assessment is None
        else ("UNKNOWN" if assessment.no_trade is None else str(assessment.no_trade).upper())
    )
    regime_view = next(
        (view for view in analysis.views.views if view.role.value == "REGIME"),
        None,
    )
    regime = None if regime_view is None else regime_view.structure.regime.regime.value
    return regime, suitability, None if quality is None else str(quality.score)


def _risk_facts(policy: RiskPolicy | None) -> dict[str, str]:
    if policy is None:
        return {}
    return {
        "mode": policy.mode.value,
        "fixed_risk": _decimal(policy.fixed_risk),
        "risk_ratio": _decimal(policy.risk_ratio),
        "max_contracts": "" if policy.max_contracts is None else str(policy.max_contracts),
    }


def _account_facts(account: AccountState | None) -> dict[str, str]:
    if account is None:
        return {}
    return {"equity": _decimal(account.equity), "used_margin": _decimal(account.used_margin)}


def _decimal(value: Decimal | None) -> str:
    return "" if value is None else format(value.normalize(), "f")


def _bounded(text: str, limits: ShadowLimits) -> str:
    return text if len(text) <= limits.max_reason_length else text[: limits.max_reason_length]
