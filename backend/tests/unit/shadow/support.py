"""Shared support for the Phase 14 shadow tests.

A shadow run is driven exactly as it is in production: a real
:class:`LiveSession` over a controllable provider, with the real runner and a
journal that keeps what was written. The only doubles are the provider (so a
test decides what arrives and when) and the store (so a unit test needs no
database - the integration tests use the real one).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from app.adapters.live.mock_stream import ManualClock
from app.adapters.market_data.csv_provider import CsvCandleTextParser
from app.application.live.session import LiveSession
from app.application.shadow.ports import AttemptKeyHeldError, StoredShadowRun
from app.application.shadow.service import ShadowRequest, ShadowRunner
from app.domain.backtest.policy import (
    DecisionKind,
    EntryIntent,
    StrategyContext,
    StrategyDecision,
    TargetLevel,
)
from app.domain.common.enums import Direction, Timeframe
from app.domain.shadow.decision import JournalEntryKind, ShadowDecision, ShadowOutcome
from app.domain.shadow.outcome import ShadowOutcomeRecord
from app.domain.shadow.run import EndReason, ShadowLimits, ShadowRunStatus
from tests.unit.live.support import FRESHNESS, H1, M5, M15, RECEIVE_START, SYMBOL
from tests.unit.live.workspace_support import CONNECTED, END, QueueProvider, bar, fixture_bars

SOURCE_ID = "RD-" + "e" * 32
RUN_ID = "SR-" + "1" * 24
TEST_STRATEGY = {"test-shadow": frozenset({"1.0.0"})}

__all__ = [
    "CONNECTED",
    "END",
    "FRESHNESS",
    "H1",
    "M5",
    "M15",
    "RECEIVE_START",
    "RUN_ID",
    "SOURCE_ID",
    "SYMBOL",
    "TEST_STRATEGY",
    "MemoryJournal",
    "QueueProvider",
    "ScriptedStrategy",
    "bar",
    "decisions_of",
    "fixture_bars",
    "observe",
    "request_for",
    "runner_for",
    "session_for",
]


class MemoryJournal:
    """An in-memory :class:`ShadowJournalStore` with the same two rules as the
    real one: append-only, and one entry per decision key.

    Outcomes obey the same rules, keyed on the outcome key. The integration
    tests use the real store; this one exists so a unit test needs no database,
    not so it can be more forgiving.
    """

    def __init__(self) -> None:
        self.runs: dict[str, StoredShadowRun] = {}
        self.entries: dict[str, list[ShadowDecision]] = {}
        self.keys: dict[str, set[str]] = {}
        self.outcomes: dict[str, list[ShadowOutcomeRecord]] = {}
        self.outcome_keys: dict[str, set[str]] = {}
        self.attempts: dict[str, tuple[str, str]] = {}
        self.append_calls = 0
        self.outcome_calls = 0

    async def create_run(
        self, run: StoredShadowRun, *, attempt_key: str | None = None
    ) -> StoredShadowRun:
        if run.run_id in self.runs:
            raise AssertionError("a run id is never reused")
        if attempt_key is not None:
            held = self.attempts.get(attempt_key)
            if held is not None:
                raise AttemptKeyHeldError(*held)
            self.attempts[attempt_key] = (run.run_id, run.configuration)
        self.runs[run.run_id] = run
        self.entries[run.run_id] = []
        self.keys[run.run_id] = set()
        self.outcomes[run.run_id] = []
        self.outcome_keys[run.run_id] = set()
        return run

    async def append(self, run_id: str, entries: Sequence[ShadowDecision]) -> int:
        self.append_calls += 1
        written = 0
        for entry in entries:
            if entry.decision_key in self.keys[run_id]:
                continue  # the same observation, written once
            self.keys[run_id].add(entry.decision_key)
            self.entries[run_id].append(entry)
            written += 1
        return written

    async def finish_run(
        self,
        run_id: str,
        *,
        status: ShadowRunStatus,
        end_reason: EndReason,
        ended_at: datetime,
        observations: int,
        decisions: int,
        entries: int,
        first_boundary: datetime | None,
        last_boundary: datetime | None,
        failure_code: str | None = None,
    ) -> StoredShadowRun:
        run = self.runs[run_id]
        updated = StoredShadowRun(
            run_id=run.run_id,
            configuration=run.configuration,
            source_id=run.source_id,
            instrument_label=run.instrument_label,
            provenance=run.provenance,
            market_currency=run.market_currency,
            strategy_id=run.strategy_id,
            strategy_version=run.strategy_version,
            strategy_parameters=run.strategy_parameters,
            driver=run.driver,
            timeframes=run.timeframes,
            required_timeframes=run.required_timeframes,
            risk=run.risk,
            account=run.account,
            status=status,
            started_at=run.started_at,
            ended_at=ended_at,
            end_reason=end_reason,
            observations=observations,
            decisions=decisions,
            entries=entries,
            first_boundary=first_boundary,
            last_boundary=last_boundary,
            failure_code=failure_code,
        )
        self.runs[run_id] = updated
        return updated

    async def get_run(self, run_id: str) -> StoredShadowRun | None:
        return self.runs.get(run_id)

    async def list_runs(
        self, *, offset: int, limit: int
    ) -> tuple[tuple[StoredShadowRun, ...], int]:
        ordered = sorted(self.runs.values(), key=lambda run: run.started_at, reverse=True)
        return tuple(ordered[offset : offset + limit]), len(ordered)

    async def read_entries(
        self, run_id: str, *, after_sequence: int, limit: int
    ) -> tuple[tuple[ShadowDecision, ...], int]:
        held = self.entries.get(run_id, [])
        chosen = [item for item in held if item.sequence > after_sequence][:limit]
        return tuple(chosen), len(held)

    async def last_sequence(self, run_id: str) -> int:
        held = self.entries.get(run_id, [])
        return max((item.sequence for item in held), default=0)

    async def append_outcomes(self, run_id: str, records: Sequence[ShadowOutcomeRecord]) -> int:
        self.outcome_calls += 1
        written = 0
        for record in records:
            if record.outcome_key in self.outcome_keys[run_id]:
                continue  # the same development, published once
            self.outcome_keys[run_id].add(record.outcome_key)
            self.outcomes[run_id].append(record)
            written += 1
        return written

    async def read_outcomes(
        self, run_id: str, *, after_sequence: int, limit: int
    ) -> tuple[tuple[ShadowOutcomeRecord, ...], int]:
        held = self.outcomes.get(run_id, [])
        chosen = [item for item in held if item.sequence > after_sequence][:limit]
        return tuple(chosen), len(held)

    async def latest_outcomes(
        self, run_id: str, decision_keys: Sequence[str]
    ) -> dict[str, ShadowOutcomeRecord]:
        wanted = set(decision_keys)
        latest: dict[str, ShadowOutcomeRecord] = {}
        for record in sorted(self.outcomes.get(run_id, []), key=lambda item: item.sequence):
            if record.decision_key in wanted:
                latest[record.decision_key] = record
        return latest

    async def last_outcome_sequence(self, run_id: str) -> int:
        held = self.outcomes.get(run_id, [])
        return max((item.sequence for item in held), default=0)

    async def find_attempt(self, attempt_key: str) -> tuple[str, str] | None:
        return self.attempts.get(attempt_key)

    async def observing_runs(self, *, limit: int) -> tuple[StoredShadowRun, ...]:
        found = [run for run in self.runs.values() if run.status is ShadowRunStatus.OBSERVING]
        return tuple(sorted(found, key=lambda run: run.started_at)[:limit])

    async def interrupt(
        self, run_id: str, *, ended_at: datetime, end_reason: EndReason
    ) -> StoredShadowRun:
        run = self.runs[run_id]
        if run.status is not ShadowRunStatus.OBSERVING:
            return run
        decisions = [e for e in self.entries[run_id] if e.kind is JournalEntryKind.DECISION]
        boundaries = [e.market_boundary for e in decisions if e.market_boundary is not None]
        return await self.finish_run(
            run_id,
            status=ShadowRunStatus.ENDED,
            end_reason=end_reason,
            ended_at=ended_at,
            observations=len(decisions),
            decisions=sum(1 for e in decisions if e.outcome is not ShadowOutcome.UNAVAILABLE),
            entries=len(self.entries[run_id]),
            first_boundary=min(boundaries, default=None),
            last_boundary=max(boundaries, default=None),
        )


class ScriptedStrategy:
    """A deterministic policy whose answers a test chooses, by boundary index.

    Registered only through the runner's injectable table, exactly as the
    Phase 12 runner allows a scripted policy - the shipped registry is never
    widened.
    """

    def __init__(
        self,
        answers: Sequence[str] = (),
        *,
        warm_up_bars: int = 1,
        identifier: str = "test-shadow",
        version: str = "1.0.0",
        fail_at: int | None = None,
    ) -> None:
        self._answers = list(answers)
        self._warm_up = warm_up_bars
        self._identifier = identifier
        self._version = version
        self._fail_at = fail_at
        self.seen: list[StrategyContext] = []

    @property
    def identifier(self) -> str:
        return self._identifier

    @property
    def version(self) -> str:
        return self._version

    @property
    def warm_up_bars(self) -> int:
        return self._warm_up

    def parameters(self) -> dict[str, str]:
        return {"answers": ",".join(self._answers), "warm_up": str(self._warm_up)}

    def decide(self, context: StrategyContext) -> StrategyDecision:
        index = len(self.seen)
        self.seen.append(context)
        if self._fail_at is not None and index == self._fail_at:
            raise RuntimeError("scripted failure C:\\secrets\\shadow.txt sk-shadow-do-not-leak")
        answer = self._answers[index] if index < len(self._answers) else "NO_SIGNAL"
        if answer == DecisionKind.ENTRY_INTENT.value:
            close = context.bar.close
            return StrategyDecision.enter(
                EntryIntent(
                    direction=Direction.LONG,
                    intended_entry=close,
                    stop=close - Decimal("1"),
                    targets=(TargetLevel(close + Decimal("2"), 1),),
                    quantity=1,
                ),
                reason=f"scripted entry at {context.as_of.isoformat()}",
            )
        if answer == DecisionKind.EXIT_INTENT.value:
            return StrategyDecision.exit_now("scripted exit")
        if answer == DecisionKind.WAIT.value:
            return StrategyDecision.wait("scripted wait")
        return StrategyDecision.no_signal("scripted no signal")


def session_for(
    provider: QueueProvider,
    *,
    timeframes: tuple[Timeframe, ...] = (M5,),
    clock: ManualClock | None = None,
    observer: object | None = None,
) -> LiveSession:
    return LiveSession(
        provider=provider,
        symbol=SYMBOL,
        timeframes=timeframes,
        clock=clock or ManualClock(RECEIVE_START),
        parser=CsvCandleTextParser(),
        freshness=FRESHNESS,
        observer=observer,  # type: ignore[arg-type]
    )


def request_for(
    strategy: ScriptedStrategy,
    *,
    timeframes: tuple[Timeframe, ...] = (M5,),
    driver: Timeframe = M5,
    required: tuple[Timeframe, ...] = (),
    account: object | None = None,
    risk: object | None = None,
    analysis_evidence: bool = False,
) -> ShadowRequest:
    return ShadowRequest(
        source_id=SOURCE_ID,
        instrument_label=SYMBOL,
        strategy=strategy,
        driver=driver,
        timeframes=timeframes,
        required=required,
        account=account,  # type: ignore[arg-type]
        risk=risk,  # type: ignore[arg-type]
        analysis_evidence=analysis_evidence,
    )


async def observe(
    runner: ShadowRunner,
    request: ShadowRequest,
    items: Sequence[object],
    *,
    run_id: str = RUN_ID,
    timeframes: tuple[Timeframe, ...] = (M5,),
    clock: ManualClock | None = None,
) -> tuple[StoredShadowRun, QueueProvider]:
    """Open a run, play ``items`` through a real session, and wait for the end."""
    provider = QueueProvider()
    await runner.open(request, run_id=run_id)
    session = session_for(
        provider, timeframes=timeframes, clock=clock, observer=runner.observer(run_id)
    )
    provider.put(*items)  # type: ignore[arg-type]
    run = await runner.drive(run_id, session)
    return run, provider


def decisions_of(journal: MemoryJournal, run_id: str = RUN_ID) -> list[ShadowDecision]:
    return [entry for entry in journal.entries[run_id] if entry.kind is JournalEntryKind.DECISION]


def runner_for(
    journal: MemoryJournal,
    *,
    limits: ShadowLimits | None = None,
    resolver: object | None = None,
    clock: ManualClock | None = None,
    contracts: object | None = None,
    supported: object | None = None,
) -> ShadowRunner:
    return ShadowRunner(
        store=journal,
        clock=clock or ManualClock(RECEIVE_START),
        parser=CsvCandleTextParser(),
        limits=limits,
        resolver=resolver,  # type: ignore[arg-type]
        contracts=contracts,  # type: ignore[arg-type]
        supported=TEST_STRATEGY if supported is None else supported,  # type: ignore[arg-type]
    )
