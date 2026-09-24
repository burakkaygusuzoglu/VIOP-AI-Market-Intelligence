"""Shadow observation: what produces a decision, and what never does.

Golden cases A-I and Q, U, V of the Phase 14 Part 1 plan. Each drives a real
:class:`LiveSession` over a controllable provider, so what the runner sees is
exactly what a stream would have delivered.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.adapters.live.mock_stream import ManualClock, candle_event
from app.application.live.records import RecordKind
from app.domain.backtest.policy import DecisionKind
from app.domain.live.events import ProviderSignal, RawCandleEvent, SignalKind
from app.domain.shadow.decision import (
    FinancialState,
    JournalEntryKind,
    OperationalKind,
    ShadowDecision,
    ShadowOutcome,
)
from app.domain.shadow.run import EndReason, ShadowRunStatus
from tests.unit.live.support import RECEIVE_START, SYMBOL, at
from tests.unit.shadow.support import (
    CONNECTED,
    END,
    M5,
    M15,
    RUN_ID,
    MemoryJournal,
    QueueProvider,
    ScriptedStrategy,
    bar,
    decisions_of,
    observe,
    request_for,
    runner_for,
    session_for,
)


def m15(index: int) -> RawCandleEvent:
    """A confirmed 15M candle, so a higher timeframe has evidence too."""
    return candle_event(SYMBOL, M15, at(15 * index), "100", "101", "99", "100.5", sequence=index)


pytestmark = pytest.mark.unit


def boundaries(entries: Sequence[ShadowDecision]) -> list[datetime | None]:
    return [entry.market_boundary for entry in entries]


class TestAConfirmedCandleProducesOneEvaluation:
    async def test_each_confirmed_boundary_is_evaluated_exactly_once(self) -> None:
        journal = MemoryJournal()
        strategy = ScriptedStrategy()
        runner = runner_for(journal)

        run, _ = await observe(
            runner, request_for(strategy), [CONNECTED, bar(0), bar(1), bar(2), END]
        )

        decisions = decisions_of(journal)
        assert len(strategy.seen) == 3
        assert boundaries(decisions) == [at(5), at(10), at(15)]
        assert run.observations == 3
        assert run.decisions == 3
        assert run.status is ShadowRunStatus.ENDED
        assert run.end_reason is EndReason.STREAM_ENDED

    async def test_the_decision_is_attributed_to_the_market_boundary(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal)

        await observe(runner, request_for(ScriptedStrategy()), [CONNECTED, bar(0), END])

        (decision,) = decisions_of(journal)
        assert decision.market_boundary == at(5)  # the candle's coverage end
        assert decision.recorded_at != decision.market_boundary  # audit time is separate
        assert decision.outcome is ShadowOutcome.NO_SIGNAL
        assert decision.strategy_kind == DecisionKind.NO_SIGNAL.value


class TestBDuplicatesAndCReplays:
    async def test_an_identical_duplicate_produces_no_second_evaluation(self) -> None:
        journal = MemoryJournal()
        strategy = ScriptedStrategy()
        runner = runner_for(journal)

        await observe(runner, request_for(strategy), [CONNECTED, bar(0), bar(0), bar(0), END])

        assert len(strategy.seen) == 1
        assert len(decisions_of(journal)) == 1

    async def test_a_forming_candle_produces_no_evaluation(self) -> None:
        journal = MemoryJournal()
        strategy = ScriptedStrategy()
        runner = runner_for(journal)
        forming = bar(1)
        forming = replace(forming, closed=False, sequence=None, event_time=at(7))

        await observe(runner, request_for(strategy), [CONNECTED, bar(0), forming, END])

        assert len(strategy.seen) == 1
        assert boundaries(decisions_of(journal)) == [at(5)]

    async def test_a_reconnect_without_new_evidence_produces_no_evaluation(self) -> None:
        journal = MemoryJournal()
        strategy = ScriptedStrategy()
        runner = runner_for(journal)

        await observe(
            runner,
            request_for(strategy),
            [
                CONNECTED,
                bar(0),
                ProviderSignal(SignalKind.DISCONNECTED),
                CONNECTED,
                bar(0),  # the same candle again after the reconnect
                END,
            ],
        )

        assert len(strategy.seen) == 1
        assert len(decisions_of(journal)) == 1
        operational = [
            entry.operational
            for entry in journal.entries[RUN_ID]
            if entry.kind is JournalEntryKind.OPERATIONAL
        ]
        assert OperationalKind.PROVIDER_DISCONNECTED in operational
        assert OperationalKind.PROVIDER_RECOVERING in operational


class TestDNoKeepaliveIsAMarketFact:
    def test_the_observer_has_no_heartbeat_to_react_to(self) -> None:
        """Case D, structurally.

        A heartbeat is an SSE transport concern (Phase 13 part 2A) and never
        becomes a stream record: the observer's whole input vocabulary is
        `RecordKind`, and there is no heartbeat in it. So the strongest form of
        "a heartbeat produces no evaluation" is that one cannot arrive at all.
        """
        assert {kind.value for kind in RecordKind} == {"OBSERVATION", "REJECTED", "SIGNAL"}

    async def test_repeated_stream_signals_produce_no_decision(self) -> None:
        """The nearest thing that does arrive: a provider repeating itself."""
        journal = MemoryJournal()
        runner = runner_for(journal)

        await observe(
            runner,
            request_for(ScriptedStrategy()),
            [CONNECTED, CONNECTED, bar(0), CONNECTED, CONNECTED, bar(1), CONNECTED, END],
        )

        # Two candles, two boundaries. The five signals between them add none.
        assert boundaries(decisions_of(journal)) == [at(5), at(10)]


class TestFNoHindsight:
    async def test_a_decision_sees_only_candles_closed_at_its_boundary(self) -> None:
        journal = MemoryJournal()
        strategy = ScriptedStrategy()
        runner = runner_for(journal)

        await observe(runner, request_for(strategy), [CONNECTED, bar(0), bar(1), bar(2), END])

        for context in strategy.seen:
            latest = context.bar.open_time + timedelta(minutes=5)
            assert latest == context.as_of
            assert context.bars_available == len(
                [c for c in strategy.seen if c.as_of <= context.as_of]
            )

    async def test_a_late_arriving_candle_cannot_reach_an_earlier_decision(self) -> None:
        journal = MemoryJournal()
        strategy = ScriptedStrategy()
        runner = runner_for(journal)

        # 0 and 2 arrive; 1 arrives afterwards and fills the recorded gap.
        await observe(
            runner,
            request_for(strategy),
            [CONNECTED, bar(0), bar(2), bar(1), bar(3), END],
        )

        first = strategy.seen[0]
        assert first.as_of == at(5)
        assert first.bars_available == 1  # never 2, whatever arrived later
        kinds = [
            entry.operational
            for entry in journal.entries[RUN_ID]
            if entry.kind is JournalEntryKind.OPERATIONAL
        ]
        assert OperationalKind.LATE_FILL in kinds


class TestGGapsAndHConflicts:
    async def test_a_temporal_gap_blocks_evaluation_and_says_why(self) -> None:
        journal = MemoryJournal()
        strategy = ScriptedStrategy()
        runner = runner_for(journal)

        # A jump with contiguous sequence numbers: Phase 13 calls this an
        # unexplained temporal gap, and shadow may not evaluate through it.
        await observe(
            runner,
            request_for(strategy),
            [CONNECTED, bar(0), bar(10, sequence=1), END],
        )

        decisions = decisions_of(journal)
        assert [entry.outcome for entry in decisions] == [
            ShadowOutcome.NO_SIGNAL,
            ShadowOutcome.UNAVAILABLE,
        ]
        assert len(strategy.seen) == 1
        unavailable = decisions[-1]
        assert unavailable.fields["code"] == "EVIDENCE_UNAVAILABLE"
        assert any("jump in time" in reason for reason in [unavailable.reason])

    async def test_a_conflicting_correction_is_recorded_and_changes_nothing(self) -> None:
        journal = MemoryJournal()
        strategy = ScriptedStrategy()
        runner = runner_for(journal)
        conflicting = replace(bar(0), close=bar(0).low)

        await observe(runner, request_for(strategy), [CONNECTED, bar(0), conflicting, bar(1), END])

        published = decisions_of(journal)[0]
        assert published.market_boundary == at(5)
        assert published.outcome is ShadowOutcome.NO_SIGNAL
        operational = [
            entry
            for entry in journal.entries[RUN_ID]
            if entry.operational is OperationalKind.CONFLICTING_CORRECTION
        ]
        assert len(operational) == 1
        assert "not rewritten" in operational[0].reason
        # The published decision read the contested candle, so it is explicitly
        # named as superseded - and still stands exactly as it was published.
        superseded = [
            entry
            for entry in journal.entries[RUN_ID]
            if entry.operational is OperationalKind.DECISION_SUPERSEDED
        ]
        assert len(superseded) == 1
        assert superseded[0].market_boundary == at(5)
        assert "1 published decision" in superseded[0].reason
        assert published.outcome is ShadowOutcome.NO_SIGNAL
        # The timeframe is CONFLICTED afterwards, so the next boundary is not
        # evaluated - it is recorded as unavailable instead.
        assert decisions_of(journal)[-1].outcome is ShadowOutcome.UNAVAILABLE


class TestIStaleAndUnavailableEvidence:
    async def test_a_stale_required_timeframe_blocks_the_decision(self) -> None:
        """A driver candle arriving refreshes the driver, and nothing else. A
        required timeframe that stopped arriving is stale, and a decision that
        needed it is recorded unavailable rather than made without it."""
        journal = MemoryJournal()
        strategy = ScriptedStrategy()
        clock = ManualClock(RECEIVE_START)
        runner = runner_for(journal, clock=clock)
        request = request_for(strategy, timeframes=(M5, M15), required=(M15,))
        provider = QueueProvider()
        await runner.open(request, run_id=RUN_ID)
        session = session_for(
            provider, timeframes=(M5, M15), clock=clock, observer=runner.observer(RUN_ID)
        )
        provider.put(CONNECTED, m15(0), bar(0))
        driving = asyncio.create_task(runner.drive(RUN_ID, session))
        for _ in range(50):
            await asyncio.sleep(0)

        clock.advance(timedelta(hours=1))  # no 15M candle for an hour
        provider.put(bar(12), END)
        await driving

        outcomes = [entry.outcome for entry in decisions_of(journal)]
        assert outcomes == [ShadowOutcome.NO_SIGNAL, ShadowOutcome.UNAVAILABLE]
        blocked = decisions_of(journal)[-1]
        assert "15M" in blocked.reason
        assert blocked.fields["code"] == "EVIDENCE_UNAVAILABLE"
        assert len(strategy.seen) == 1  # the rule was not run without its evidence

    async def test_a_correction_names_every_decision_that_read_the_candle(self) -> None:
        """Not only the boundary the candle closed: every later decision had it
        in its confirmed prefix, so all of them rest on contested evidence."""
        journal = MemoryJournal()
        runner = runner_for(journal)
        conflicting = replace(bar(0), close=bar(0).low)

        await observe(
            runner,
            request_for(ScriptedStrategy()),
            [CONNECTED, bar(0), bar(1), bar(2), conflicting, END],
        )

        superseded = [
            entry
            for entry in journal.entries[RUN_ID]
            if entry.operational is OperationalKind.DECISION_SUPERSEDED
        ]
        assert len(superseded) == 1
        assert superseded[0].market_boundary == at(5)  # the earliest affected
        assert "3 published decision" in superseded[0].reason
        # And the three decisions are exactly as they were published.
        assert boundaries(decisions_of(journal)) == [at(5), at(10), at(15)]

    async def test_an_unavailable_timeframe_is_recorded_apart_from_no_signal(self) -> None:
        journal = MemoryJournal()
        strategy = ScriptedStrategy(warm_up_bars=3)
        runner = runner_for(journal)

        await observe(runner, request_for(strategy), [CONNECTED, bar(0), bar(1), END])

        outcomes = [entry.outcome for entry in decisions_of(journal)]
        assert outcomes == [ShadowOutcome.UNAVAILABLE, ShadowOutcome.UNAVAILABLE]
        assert all(entry.fields["code"] == "WARM_UP_INCOMPLETE" for entry in decisions_of(journal))
        assert strategy.seen == []


class TestQDeterminismAndUProvenance:
    async def test_identical_observations_produce_identical_decisions(self) -> None:
        items = [CONNECTED, bar(0), bar(1), bar(2), END]
        runs = []
        for index in range(2):
            journal = MemoryJournal()
            runner = runner_for(journal)
            run_id = f"SR-{index}" + "0" * 22
            await observe(
                runner,
                request_for(ScriptedStrategy(["NO_SIGNAL", "ENTRY_INTENT", "WAIT"])),
                items,
                run_id=run_id,
            )
            runs.append(
                [
                    (
                        entry.market_boundary,
                        entry.outcome,
                        entry.reason,
                        entry.input_fingerprint,
                        entry.financial_state,
                    )
                    for entry in decisions_of(journal, run_id)
                ]
            )

        assert runs[0] == runs[1]

    async def test_the_run_and_every_decision_keep_the_simulated_provenance(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal)

        run, _ = await observe(runner, request_for(ScriptedStrategy()), [CONNECTED, bar(0), END])

        assert run.provenance == "SIMULATED_HISTORICAL_STREAM"
        assert run.market_currency == "HISTORICAL"
        evidence = decisions_of(journal)[0].evidence
        assert evidence is not None
        assert evidence.provenance == "SIMULATED_HISTORICAL_STREAM"
        assert evidence.market_currency == "HISTORICAL"
        assert evidence.connection == "CONNECTED"


class TestVNoFabricatedResult:
    async def test_an_entry_intent_carries_no_result_and_no_approval(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal)

        await observe(
            runner,
            request_for(ScriptedStrategy(["ENTRY_INTENT"])),
            [CONNECTED, bar(0), END],
        )

        decision = decisions_of(journal)[0]
        assert decision.outcome is ShadowOutcome.ENTRY_INTENT
        assert decision.entry is not None
        assert decision.entry.approved_quantity is None
        assert decision.financial_state is FinancialState.NOT_CONFIGURED
        recorded = decision.__dict__ if hasattr(decision, "__dict__") else {}
        for banned in ("pnl", "profit", "realized", "result"):
            assert banned not in str(recorded).lower()

    async def test_the_journal_has_no_field_for_a_fill_or_a_profit(self) -> None:
        from app.domain.shadow.decision import ShadowDecision

        fields = set(ShadowDecision.__dataclass_fields__)
        for banned in ("pnl", "profit", "realized", "fill", "filled", "exit_price"):
            assert not any(banned in name for name in fields)


class TestEvidenceRecorded:
    async def test_every_decision_explains_itself_from_its_inputs(self) -> None:
        journal = MemoryJournal()
        runner = runner_for(journal)

        await observe(
            runner,
            request_for(ScriptedStrategy(), timeframes=(M5, M15)),
            [CONNECTED, bar(0), bar(1), bar(2), END],
            timeframes=(M5, M15),
        )

        decision = decisions_of(journal)[-1]
        evidence = decision.evidence
        assert evidence is not None
        assert M5 in evidence.included
        assert M15 in {item.timeframe for item in evidence.excluded}  # never streamed
        assert evidence.bars_available == 3
        assert {item.timeframe for item in evidence.readings} == {M5}
        assert decision.input_fingerprint
        assert decision.reason
