"""Which corrections invalidate which developments (Phase 14 Part 2B).

Part 2B's audit found two dependency categories handled unevenly. A correction
of a candle the *decision* read was handled for the driver only; a correction
of a candle the *later development* read was not handled at all, so a target
touch established by a contested candle stood as observed. These tests pin both
categories, and pin that a correction touching neither leaves history alone.

The straddling-candle and leading-gap rules are pinned here too: they decide
which candles are evidence about a decision in the first place.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app.adapters.live.mock_stream import candle_event
from app.domain.common.enums import Direction, Timeframe
from app.domain.market.candle import Candle
from app.domain.shadow.decision import JournalEntryKind, OperationalKind, ShadowOutcome
from app.domain.shadow.outcome import (
    LevelEvent,
    OutcomeState,
    ShadowOutcomeRecord,
    eligible_forward_candles,
    observe_development,
)
from app.domain.shadow.run import ShadowLimits
from tests.unit.live.support import SYMBOL, at
from tests.unit.shadow.support import (
    CONNECTED,
    END,
    M5,
    M15,
    RUN_ID,
    MemoryJournal,
    ScriptedStrategy,
    decisions_of,
    observe,
    request_for,
    runner_for,
)

pytestmark = pytest.mark.unit

# The scripted policy proposes, from a close of 100.5: stop 99.5, target 102.5.


def quiet(index: int) -> object:
    return candle_event(
        SYMBOL, M5, at(5 * index), "100.5", "100.6", "100.4", "100.5", sequence=index
    )


def spike(index: int) -> object:
    """Reaches the target (high 103 >= 102.5), never the stop."""
    return candle_event(SYMBOL, M5, at(5 * index), "100.5", "103", "100.4", "100.5", sequence=index)


def dip(index: int) -> object:
    """Reaches the stop (low 99 <= 99.5), never the target."""
    return candle_event(SYMBOL, M5, at(5 * index), "100.5", "100.6", "99", "100.5", sequence=index)


def m15(index: int) -> object:
    return candle_event(SYMBOL, M15, at(15 * index), "100", "101", "99", "100.5", sequence=index)


def corrected(event: object) -> object:
    """The same candle, arriving again with a different close: a conflict."""
    return replace(event, close=event.low)  # type: ignore[type-var, attr-defined]


def outcomes(journal: MemoryJournal) -> list[ShadowOutcomeRecord]:
    return journal.outcomes[RUN_ID]


def of_decision(journal: MemoryJournal, key: str) -> list[ShadowOutcomeRecord]:
    return [record for record in outcomes(journal) if record.decision_key == key]


def operational(journal: MemoryJournal, kind: OperationalKind) -> list[object]:
    return [e for e in journal.entries[RUN_ID] if e.operational is kind]


# ----------------------------------------------------------------------
# Which candles are evidence about a decision at all
# ----------------------------------------------------------------------


def candle_at(minute: int, high: str, low: str, *, timeframe: Timeframe = M5) -> Candle:
    return Candle(
        symbol=SYMBOL,
        timeframe=timeframe,
        open_time=at(0) + timedelta(minutes=minute),
        open=Decimal("100"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal("100"),
        volume=Decimal("1000"),
        is_closed=True,
    )


class TestStraddlingAndAdjacentCandles:
    def test_a_candle_straddling_the_decision_is_not_later_evidence(self) -> None:
        """Opens 2 minutes before T, closes 3 minutes after. Its range may have
        been made before the decision, so none of it counts."""
        straddling = candle_at(3, "150", "50")  # 09:03-09:08, decision T = 09:05

        assert eligible_forward_candles((straddling,), at(5), limit=10) == ()

    def test_the_candle_opening_exactly_at_the_decision_is_eligible(self) -> None:
        adjacent = candle_at(5, "101", "99")

        assert eligible_forward_candles((adjacent,), at(5), limit=10) == (adjacent,)

    def test_a_straddling_candle_cannot_touch_a_level(self) -> None:
        straddling = candle_at(3, "150", "50")  # would touch both levels

        development = observe_development(
            direction=Direction.LONG,
            stop=Decimal("99"),
            targets=((Decimal("102"), 1),),
            boundary=at(5),
            timeframe=M5,
            candles=(straddling,),
            window=24,
            exhausted=True,
        )

        assert development.event is not LevelEvent.BOTH_LEVELS_TOUCHED_SAME_BAR
        assert development.candles_observed == 0
        assert development.state is OutcomeState.UNAVAILABLE

    def test_a_missing_interval_right_after_the_decision_is_a_gap(self) -> None:
        """The candle opening at T is missing; the next one touches the target.
        Nothing is continuous from the decision, so the touch is not claimed."""
        later = candle_at(10, "103", "100")

        development = observe_development(
            direction=Direction.LONG,
            stop=Decimal("99"),
            targets=((Decimal("102"), 1),),
            boundary=at(5),
            timeframe=M5,
            candles=(later,),
            window=24,
            exhausted=True,
        )

        assert development.state is OutcomeState.UNAVAILABLE
        assert development.event is LevelEvent.NONE_REACHED
        assert development.candles_observed == 0
        assert "skips an interval" in (development.unresolved_reason or "")

    def test_the_default_window_is_twenty_four_candles(self) -> None:
        assert ShadowLimits().max_outcome_window == 24


# ----------------------------------------------------------------------
# Level cases through the runner
# ----------------------------------------------------------------------


class TestLevelCases:
    async def test_only_the_stop_is_touched(self) -> None:
        journal = MemoryJournal()
        await observe(
            runner_for(journal),
            request_for(ScriptedStrategy(["ENTRY_INTENT"])),
            [CONNECTED, quiet(0), dip(1), END],
        )

        (record,) = outcomes(journal)
        assert record.development.event is LevelEvent.STOP_LEVEL_TOUCHED
        assert record.development.target_ordinal is None

    async def test_the_same_candle_touching_both_levels_names_no_winner(self) -> None:
        both = candle_event(SYMBOL, M5, at(5), "100.5", "103", "99", "100.5", sequence=1)
        journal = MemoryJournal()
        await observe(
            runner_for(journal),
            request_for(ScriptedStrategy(["ENTRY_INTENT"])),
            [CONNECTED, quiet(0), both, END],
        )

        (record,) = outcomes(journal)
        assert record.development.event is LevelEvent.BOTH_LEVELS_TOUCHED_SAME_BAR
        assert record.development.ambiguous is True
        assert record.development.state is OutcomeState.OBSERVED

    async def test_a_gap_after_the_decision_makes_the_development_unavailable(self) -> None:
        journal = MemoryJournal()
        await observe(
            runner_for(journal),
            request_for(ScriptedStrategy(["ENTRY_INTENT"])),
            [CONNECTED, quiet(0), spike(2), END],  # the candle at T is missing
        )

        developments = [record.development for record in outcomes(journal)]
        assert developments[0].state is OutcomeState.UNAVAILABLE
        assert all(d.event is not LevelEvent.TARGET_LEVEL_TOUCHED for d in developments)


# ----------------------------------------------------------------------
# Corrections, by dependency
# ----------------------------------------------------------------------


class TestAHigherTimeframeDeliveredEarly:
    async def test_a_higher_candle_that_closes_later_is_not_in_an_earlier_prefix(self) -> None:
        """Found by the Part 2B mutation sweep (probe B survived): nothing tested
        timeframes arriving out of market order. The 15M candle 09:00-09:15
        arrives *before* the 5M candles inside it; the 09:05 decision must not
        see it, although it is already in the book."""
        journal = MemoryJournal()
        strategy = ScriptedStrategy()
        await observe(
            runner_for(journal),
            request_for(strategy, timeframes=(M5, M15)),
            [CONNECTED, m15(0), quiet(0), quiet(1), quiet(2), END],
            timeframes=(M5, M15),
        )

        first, second, third = strategy.seen[:3]
        assert (first.as_of, second.as_of, third.as_of) == (at(5), at(10), at(15))
        assert M15 not in first.higher and M15 not in second.higher
        assert M15 in third.higher  # 09:15: now the 15M candle has closed
        evidence = {e.timeframe: e for e in decisions_of(journal)[0].evidence.timeframes}  # type: ignore[union-attr]
        assert evidence[M15].confirmed_count == 0


class TestCorrectionsOfDecisionEvidence:
    async def test_a_corrected_higher_timeframe_candle_the_decision_read_is_followed(
        self,
    ) -> None:
        """Before Part 2B a correction of any non-driver timeframe was ignored,
        although the policy is handed higher-timeframe readings."""
        journal = MemoryJournal()
        await observe(
            runner_for(journal),
            request_for(
                ScriptedStrategy(["NO_SIGNAL", "NO_SIGNAL", "ENTRY_INTENT"]),
                timeframes=(M5, M15),
            ),
            [CONNECTED, m15(0), quiet(0), quiet(1), quiet(2), corrected(m15(0)), END],
            timeframes=(M5, M15),
        )

        intent = decisions_of(journal)[2]
        assert intent.market_boundary == at(15)  # it read the 15M candle ending 09:15
        (record,) = of_decision(journal, intent.decision_key)
        assert record.development.state is OutcomeState.INVALIDATED
        assert "the decision read the corrected 15M candle" in (
            record.development.unresolved_reason or ""
        )
        assert len(operational(journal, OperationalKind.DECISION_SUPERSEDED)) == 1
        assert intent.outcome is ShadowOutcome.ENTRY_INTENT  # published as it was

    async def test_a_higher_timeframe_candle_closing_after_the_decision_does_not_reach_it(
        self,
    ) -> None:
        journal = MemoryJournal()
        await observe(
            runner_for(journal, limits=ShadowLimits(max_outcome_window=50)),
            request_for(ScriptedStrategy(["ENTRY_INTENT"]), timeframes=(M5, M15)),
            [CONNECTED, quiet(0), quiet(1), quiet(2), m15(0), corrected(m15(0)), END],
            timeframes=(M5, M15),
        )

        intent = decisions_of(journal)[0]
        assert intent.market_boundary == at(5)  # before the 15M candle closed (09:15)
        records = of_decision(journal, intent.decision_key)
        # Its own development ended because the data did - not by invalidation.
        assert [r.development.state for r in records] == [OutcomeState.UNAVAILABLE]
        assert "never closed" in (records[0].development.unresolved_reason or "")

    async def test_a_timeframe_the_run_never_read_is_not_its_concern(self) -> None:
        journal = MemoryJournal()
        await observe(
            runner_for(journal),
            request_for(ScriptedStrategy(["NO_SIGNAL", "NO_SIGNAL", "ENTRY_INTENT"])),
            [CONNECTED, m15(0), quiet(0), quiet(1), quiet(2), corrected(m15(0)), END],
            timeframes=(M5, M15),  # the session streams 15M; the run does not use it
        )

        assert operational(journal, OperationalKind.CONFLICTING_CORRECTION) == []
        assert all(r.development.state is not OutcomeState.INVALIDATED for r in outcomes(journal))


class TestCorrectionsOfOutcomeEvidence:
    async def test_a_corrected_target_touch_is_invalidated_after_publication(self) -> None:
        journal = MemoryJournal()
        touching = spike(1)
        await observe(
            runner_for(journal),
            request_for(ScriptedStrategy(["ENTRY_INTENT"])),
            [CONNECTED, quiet(0), touching, quiet(2), corrected(touching), END],
        )

        intent = decisions_of(journal)[0]
        records = of_decision(journal, intent.decision_key)
        # Published first, then an appended invalidation - never an edit.
        assert [r.development.state for r in records] == [
            OutcomeState.OBSERVED,
            OutcomeState.INVALIDATED,
        ]
        assert records[0].development.event is LevelEvent.TARGET_LEVEL_TOUCHED
        reason = records[1].development.unresolved_reason or ""
        assert "this development read the corrected 5M candle" in reason
        assert at(5).isoformat() in reason
        assert records[1].sequence > records[0].sequence

    async def test_a_corrected_stop_touch_is_invalidated_after_publication(self) -> None:
        journal = MemoryJournal()
        touching = dip(1)
        await observe(
            runner_for(journal),
            request_for(ScriptedStrategy(["ENTRY_INTENT"])),
            [CONNECTED, quiet(0), touching, corrected(touching), END],
        )

        records = of_decision(journal, decisions_of(journal)[0].decision_key)
        assert [r.development.event for r in records] == [
            LevelEvent.STOP_LEVEL_TOUCHED,
            LevelEvent.NOT_OBSERVED,
        ]
        assert records[1].development.state is OutcomeState.INVALIDATED

    async def test_a_corrected_candle_inside_an_open_window_invalidates_it(self) -> None:
        """Continuity: nothing was touched yet, but the window already read it."""
        journal = MemoryJournal()
        between = quiet(1)
        await observe(
            runner_for(journal, limits=ShadowLimits(max_outcome_window=50)),
            request_for(ScriptedStrategy(["ENTRY_INTENT"])),
            [CONNECTED, quiet(0), between, quiet(2), corrected(between), END],
        )

        (record,) = of_decision(journal, decisions_of(journal)[0].decision_key)
        assert record.development.state is OutcomeState.INVALIDATED
        assert "this development read" in (record.development.unresolved_reason or "")

    async def test_a_correction_after_the_development_ended_changes_nothing(self) -> None:
        """The target was touched at 09:10. A correction of the 09:15 candle is
        after everything that development read, so it stands."""
        journal = MemoryJournal()
        later = quiet(3)
        await observe(
            runner_for(journal),
            request_for(ScriptedStrategy(["ENTRY_INTENT"])),
            [CONNECTED, quiet(0), spike(1), quiet(2), later, corrected(later), END],
        )

        records = of_decision(journal, decisions_of(journal)[0].decision_key)
        assert [r.development.state for r in records] == [OutcomeState.OBSERVED]
        assert records[0].development.observed_to == at(10)
        # The correction itself is still on record, for the decisions it did reach.
        assert len(operational(journal, OperationalKind.CONFLICTING_CORRECTION)) == 1

    async def test_decisions_before_a_corrected_candle_are_not_superseded(self) -> None:
        journal = MemoryJournal()
        later = quiet(3)
        await observe(
            runner_for(journal),
            request_for(ScriptedStrategy()),
            [CONNECTED, quiet(0), quiet(1), quiet(2), later, corrected(later), END],
        )

        (superseding,) = operational(journal, OperationalKind.DECISION_SUPERSEDED)
        # Only the decision at 09:20 read the 09:15-09:20 candle.
        assert superseding.market_boundary == at(20)  # type: ignore[attr-defined]
        assert "1 published decision" in superseding.reason  # type: ignore[attr-defined]

    async def test_the_journal_itself_is_never_edited(self) -> None:
        journal = MemoryJournal()
        touching = spike(1)
        await observe(
            runner_for(journal),
            request_for(ScriptedStrategy(["ENTRY_INTENT"])),
            [CONNECTED, quiet(0), touching, corrected(touching), END],
        )

        sequences = [entry.sequence for entry in journal.entries[RUN_ID]]
        assert sequences == sorted(sequences)
        assert len(set(sequences)) == len(sequences)
        kinds = {entry.kind for entry in journal.entries[RUN_ID]}
        assert kinds == {JournalEntryKind.DECISION, JournalEntryKind.OPERATIONAL}
