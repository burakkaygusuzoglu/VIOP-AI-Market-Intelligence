"""Forward price development after a shadow decision (Phase 14 Part 2A).

The rules this file pins are the ones that decide whether the journal tells the
truth: no candle may judge a decision it helped make, a touched level is not a
fill, a same-bar collision is not resolved by guessing, and a gap makes the
answer unavailable rather than favourable.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.domain.common.enums import Direction, Timeframe
from app.domain.market.candle import Candle
from app.domain.shadow.outcome import (
    OUTCOME_RULES,
    LevelEvent,
    OutcomeState,
    PriceDevelopment,
    coverage_end_of,
    eligible_forward_candles,
    observe_development,
)
from tests.unit.live.support import SYMBOL, at

pytestmark = pytest.mark.unit

M5 = Timeframe.M5
ENTRY = Decimal("100")
STOP = Decimal("99")
TARGET = Decimal("102")
TARGETS = ((TARGET, 1),)


def candle(index: int, high: str, low: str, close: str = "100") -> Candle:
    """A 5M candle opening at ``at(5 * index)``."""
    return Candle(
        symbol=SYMBOL,
        timeframe=M5,
        open_time=at(5 * index),
        open=Decimal("100"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        is_closed=True,
    )


def follow(
    candles: tuple[Candle, ...],
    *,
    after_candle: int = 0,
    direction: Direction = Direction.LONG,
    stop: Decimal = STOP,
    targets: tuple[tuple[Decimal, int], ...] = TARGETS,
    window: int = 4,
    exhausted: bool = False,
) -> PriceDevelopment:
    return observe_development(
        direction=direction,
        stop=stop,
        targets=targets,
        # The decision was made at this candle's coverage end, so it and
        # every candle before it were evidence *for* the decision.
        boundary=at(5 * (after_candle + 1)),
        timeframe=M5,
        candles=candles,
        window=window,
        exhausted=exhausted,
    )


class TestForwardOnlyCausality:
    def test_the_candle_that_produced_the_decision_is_not_eligible(self) -> None:
        """Its coverage ends *at* the boundary: it is evidence for the decision,
        never evidence about it."""
        series = (candle(0, "101", "99"), candle(1, "101", "99"), candle(2, "101", "99"))

        eligible = eligible_forward_candles(series, at(5), limit=10)

        assert [item.open_time for item in eligible] == [at(5), at(10)]
        assert coverage_end_of(series[0]) == at(5)  # the decision's own candle

    def test_no_earlier_candle_can_become_later_evidence(self) -> None:
        series = (candle(0, "150", "50"), candle(1, "101", "99"))

        # The wild candle at index 0 closed before the boundary and would have
        # touched both levels. It must not appear in the development at all.
        development = follow(series, exhausted=True)

        assert development.event is not LevelEvent.BOTH_LEVELS_TOUCHED_SAME_BAR
        assert development.candles_observed == 1
        assert development.observed_from == at(10)

    def test_a_decision_at_the_end_of_the_stream_has_no_development_yet(self) -> None:
        series = (candle(0, "101", "99"),)

        assert follow(series).state is OutcomeState.PENDING

    def test_a_stream_that_ended_with_no_later_candle_is_unavailable(self) -> None:
        series = (candle(0, "101", "99"),)

        development = follow(series, exhausted=True)

        assert development.state is OutcomeState.UNAVAILABLE
        assert development.event is LevelEvent.NOT_OBSERVED
        assert "ended before" in (development.unresolved_reason or "")


class TestLevelsAreTouchedNeverFilled:
    def test_a_target_reach_is_recorded_as_a_touch(self) -> None:
        series = (candle(0, "101", "99"), candle(1, "103", "100"))

        development = follow(series, exhausted=True)

        assert development.state is OutcomeState.OBSERVED
        assert development.event is LevelEvent.TARGET_LEVEL_TOUCHED
        assert development.target_ordinal == 1
        assert development.event_at == at(10)
        assert development.rules == OUTCOME_RULES
        # Nothing here is money, a fill or a result.
        assert not hasattr(development, "pnl")
        assert not hasattr(development, "filled")
        assert "WIN" not in str(development)

    def test_a_stop_reach_is_recorded_as_a_touch(self) -> None:
        series = (candle(0, "101", "99.5"), candle(1, "100", "98.5"))

        development = follow(series, exhausted=True)

        assert development.event is LevelEvent.STOP_LEVEL_TOUCHED
        assert development.event_at == at(10)
        assert development.target_ordinal is None

    def test_a_level_exactly_reached_counts_as_touched(self) -> None:
        series = (candle(0, "101", "99.5"), candle(1, str(TARGET), "100"))

        assert follow(series).event is LevelEvent.TARGET_LEVEL_TOUCHED

    def test_the_nearest_target_is_the_one_reported(self) -> None:
        series = (candle(0, "101", "99.5"), candle(1, "106", "100"))
        targets = ((Decimal("102"), 1), (Decimal("104"), 2), (Decimal("106"), 3))

        development = follow(series, targets=targets, exhausted=True)

        assert development.target_ordinal == 1  # not the furthest one reached

    def test_a_short_decision_reads_the_levels_the_other_way(self) -> None:
        series = (candle(0, "101", "99.5"), candle(1, "100.5", "97"))

        development = follow(
            series,
            direction=Direction.SHORT,
            stop=Decimal("101"),
            targets=((Decimal("98"), 1),),
            exhausted=True,
        )

        assert development.event is LevelEvent.TARGET_LEVEL_TOUCHED
        assert development.best_price == Decimal("97")  # a short's favour is down
        assert development.worst_price == Decimal("100.5")


class TestAmbiguityIsPreservedNotResolved:
    def test_one_candle_reaching_both_levels_claims_neither(self) -> None:
        series = (candle(0, "101", "99.5"), candle(1, "103", "98"))

        development = follow(series, exhausted=True)

        assert development.event is LevelEvent.BOTH_LEVELS_TOUCHED_SAME_BAR
        assert development.ambiguous is True
        assert development.event_at == at(10)

    def test_ambiguity_cannot_be_claimed_for_a_single_level(self) -> None:
        with pytest.raises(ValueError, match="ambiguity belongs"):
            PriceDevelopment(
                state=OutcomeState.OBSERVED,
                event=LevelEvent.TARGET_LEVEL_TOUCHED,
                ambiguous=True,
            )


class TestGapsAndWindows:
    def test_a_gap_inside_the_window_makes_the_development_unavailable(self) -> None:
        series = (candle(0, "101", "99.5"), candle(1, "101", "99.5"), candle(3, "103", "98"))

        development = follow(series, window=6, exhausted=True)

        assert development.state is OutcomeState.UNAVAILABLE
        assert "skips an interval" in (development.unresolved_reason or "")
        # The candle after the gap is not read at all, so its levels are unclaimed.
        assert development.event is LevelEvent.NONE_REACHED
        assert development.candles_observed == 1

    def test_a_level_reached_before_the_gap_still_stands(self) -> None:
        series = (candle(0, "101", "99.5"), candle(1, "103", "100"), candle(4, "90", "80"))

        development = follow(series, window=6, exhausted=True)

        assert development.state is OutcomeState.OBSERVED
        assert development.event is LevelEvent.TARGET_LEVEL_TOUCHED

    def test_a_closed_window_with_nothing_reached_is_an_observed_result(self) -> None:
        series = (candle(0, "101", "99.5"), *(candle(i, "101", "99.5") for i in range(1, 4)))

        development = follow(series, window=3)

        assert development.state is OutcomeState.OBSERVED
        assert development.event is LevelEvent.NONE_REACHED
        assert development.candles_observed == 3

    def test_an_open_window_is_pending_not_a_result(self) -> None:
        series = (candle(0, "101", "99.5"), candle(1, "101", "99.5"))

        development = follow(series, window=5)

        assert development.state is OutcomeState.PENDING
        assert development.event is LevelEvent.NONE_REACHED

    def test_data_running_out_mid_window_is_unavailable_not_none_reached(self) -> None:
        series = (candle(0, "101", "99.5"), candle(1, "101", "99.5"))

        development = follow(series, window=5, exhausted=True)

        assert development.state is OutcomeState.UNAVAILABLE
        assert "never closed" in (development.unresolved_reason or "")

    def test_the_window_bounds_how_many_candles_are_read(self) -> None:
        series = tuple(candle(i, "101", "99.5") for i in range(10))

        development = follow(series, window=3)

        assert development.candles_observed == 3
        assert development.observed_to == at(20)


class TestExcursionsArePricesNotMoney:
    def test_the_best_and_worst_prices_are_the_range_actually_traded(self) -> None:
        series = (
            candle(0, "101", "99.5"),
            candle(1, "101.5", "99.2"),
            candle(2, "101.8", "99.6"),
        )

        development = follow(series, window=2)

        assert development.best_price == Decimal("101.8")
        assert development.worst_price == Decimal("99.2")
        assert development.last_close == Decimal("100")

    def test_an_unavailable_development_must_say_why(self) -> None:
        with pytest.raises(ValueError, match="says why"):
            PriceDevelopment(state=OutcomeState.UNAVAILABLE, event=LevelEvent.NONE_REACHED)

    def test_an_observed_development_names_what_price_did(self) -> None:
        with pytest.raises(ValueError, match="names what price did"):
            PriceDevelopment(state=OutcomeState.OBSERVED, event=LevelEvent.NOT_OBSERVED)


class TestTheVocabularyRefusesFinancialClaims:
    def test_no_outcome_word_asserts_money_or_a_fill(self) -> None:
        banned = ("WIN", "LOSS", "PROFIT", "FILLED", "REALIZED", "PNL")
        words = {*(state.value for state in OutcomeState), *(e.value for e in LevelEvent)}

        assert not [word for word in words for ban in banned if ban in word]

    def test_every_level_word_says_touched_rather_than_filled(self) -> None:
        reached = {LevelEvent.STOP_LEVEL_TOUCHED, LevelEvent.TARGET_LEVEL_TOUCHED}

        assert all("TOUCHED" in event.value for event in reached)
        assert all("LEVEL" in event.value for event in reached)


def test_coverage_end_is_the_interval_end_not_the_open() -> None:
    assert coverage_end_of(candle(3, "101", "99")) == at(15) + timedelta(minutes=5)
