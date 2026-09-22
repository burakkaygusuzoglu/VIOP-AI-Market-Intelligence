"""Candle books and market state, golden cases B-I and N-O (Phase 13).

Each expectation is written out from the event sequence above it - which
candles were sent, in what order, with what sequence numbers - rather than read
back from the implementation.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.adapters.live.mock_stream import candle_event
from app.domain.common.enums import Timeframe
from app.domain.live.alerts import AlertKind, alert_candidates
from app.domain.live.book import ApplyOutcome, Integrity
from app.domain.live.events import ProviderSignal, RawCandleEvent, SignalKind, StreamProvenance
from app.domain.live.limits import LiveLimits
from app.domain.live.state import (
    Availability,
    ConnectionState,
    Freshness,
    FreshnessPolicy,
    LiveMarketState,
    TerminationReason,
)
from app.domain.live.validation import Rejection, RejectionCode
from tests.unit.live.support import FRESHNESS, H1, M5, RECEIVE_START, SYMBOL, at

pytestmark = pytest.mark.unit


def state(
    timeframes: tuple[Timeframe, ...] = (M5, H1),
    limits: LiveLimits | None = None,
) -> LiveMarketState:
    market = LiveMarketState(
        symbol=SYMBOL,
        timeframes=timeframes,
        provenance=StreamProvenance.SIMULATED_HISTORICAL_STREAM,
        freshness=FRESHNESS,
        limits=limits or LiveLimits(),
    )
    market.apply_signal(ProviderSignal(SignalKind.CONNECTED))
    return market


def m5(
    index: int,
    *,
    sequence: int | None = None,
    sequenced: bool = True,
    close: str = "100.5",
    closed: bool = True,
) -> RawCandleEvent:
    """A 5M candle at ``index``. Sequenced by its index unless told otherwise;
    ``sequenced=False`` is an unsequenced stream, which ``sequence=None`` alone
    could not express."""
    number = (sequence if sequence is not None else index) if sequenced and closed else None
    return candle_event(
        SYMBOL,
        M5,
        at(5 * index),
        "100",
        "101",
        "99",
        close,
        closed=closed,
        sequence=number,
        event_time=None if closed else at(5 * index + 2),
    )


def send(
    market: LiveMarketState, event: RawCandleEvent, *, after: timedelta = timedelta(0)
) -> ApplyOutcome | Rejection:
    return market.apply_event(event, received_at=RECEIVE_START + after)


class TestAAndOAClosedCandleIsConfirmed:
    def test_a_chronological_stream_is_complete(self) -> None:
        market = state((M5,))
        for index in range(5):
            assert send(market, m5(index)) is ApplyOutcome.ACCEPTED

        book = market.book(M5)
        assert [o.candle.open_time for o in book.confirmed()] == [at(5 * i) for i in range(5)]
        assert book.integrity() is Integrity.COMPLETE
        assert market.snapshot(RECEIVE_START).status(M5).availability is Availability.AVAILABLE


class TestNAFormingCandleIsNeverConfirmed:
    def test_a_forming_update_is_held_apart(self) -> None:
        market = state((M5,))
        send(market, m5(0))
        assert send(market, m5(1, closed=False)) is ApplyOutcome.FORMING_UPDATED

        book = market.book(M5)
        assert [o.candle.open_time for o in book.confirmed()] == [at(0)]
        assert book.forming is not None
        assert book.forming.candle.is_closed is False

    def test_the_forming_candle_is_replaced_when_its_interval_closes(self) -> None:
        market = state((M5,))
        send(market, m5(0))
        send(market, m5(1, closed=False, close="100.25"))
        send(market, m5(1, close="100.75"))

        book = market.book(M5)
        assert book.forming is None
        assert book.confirmed()[-1].candle.close == Decimal("100.75")

    def test_a_forming_update_for_an_already_closed_interval_is_refused(self) -> None:
        market = state((M5,))
        send(market, m5(0))
        send(market, m5(1))

        result = send(market, m5(1, closed=False))

        assert isinstance(result, Rejection)
        assert result.code is RejectionCode.STALE_FORMING


class TestBIdenticalDuplicate:
    def test_it_is_not_a_new_observation(self) -> None:
        market = state((M5,))
        send(market, m5(0))

        assert send(market, m5(0), after=timedelta(minutes=1)) is ApplyOutcome.DUPLICATE
        assert len(market.book(M5).confirmed()) == 1
        assert market.book(M5).status().duplicates == 1

    def test_a_duplicate_flood_does_not_keep_the_stream_fresh(self) -> None:
        """Repeating the same candle is not evidence the market is still moving."""
        market = state((M5,))
        send(market, m5(0))
        for minute in range(1, 60):
            send(market, m5(0), after=timedelta(minutes=minute))

        later = RECEIVE_START + timedelta(minutes=60)
        assert market.snapshot(later).status(M5).freshness is Freshness.STALE


class TestCConflictingDuplicate:
    def test_the_stored_candle_is_not_rewritten(self) -> None:
        market = state((M5,))
        send(market, m5(0, close="100.5"))

        assert send(market, m5(0, close="100.9")) is ApplyOutcome.CONFLICT
        assert market.book(M5).confirmed()[0].candle.close == Decimal("100.5")

    def test_the_timeframe_is_quarantined(self) -> None:
        market = state((M5,))
        send(market, m5(0, close="100.5"))
        send(market, m5(0, close="100.9"))

        status = market.snapshot(RECEIVE_START).status(M5)
        assert status.book.integrity is Integrity.CONFLICTED
        assert status.availability is Availability.UNAVAILABLE
        assert any("conflicting" in reason for reason in status.reasons)
        assert AlertKind.DATA_CONFLICT in {
            a.kind for a in alert_candidates(market.snapshot(RECEIVE_START))
        }


class TestDAMissingCandle:
    def test_a_sequence_jump_is_a_known_gap(self) -> None:
        market = state((M5,))
        send(market, m5(0))
        send(market, m5(1))
        send(market, m5(3))  # sequence 2 never arrives

        status = market.snapshot(RECEIVE_START).status(M5)
        assert status.book.integrity is Integrity.GAPPED
        assert status.book.missing_sequences == 1
        assert status.availability is Availability.UNAVAILABLE

    def test_no_candle_is_invented_to_fill_it(self) -> None:
        market = state((M5,))
        send(market, m5(0))
        send(market, m5(3))

        assert [o.candle.open_time for o in market.book(M5).confirmed()] == [at(0), at(15)]

    def test_on_an_unsequenced_stream_a_time_jump_is_an_unproven_discontinuity(self) -> None:
        """A session break or a lost candle - which one cannot be known here."""
        market = state((M5,))
        send(market, m5(0, sequenced=False))
        send(market, m5(3, sequenced=False))

        status = market.snapshot(RECEIVE_START).status(M5)
        assert status.book.integrity is Integrity.DISCONTINUOUS
        assert any("verified exchange calendar" in reason for reason in status.reasons)

    def test_contiguous_sequences_across_a_time_jump_are_not_a_session_break(self) -> None:
        """Corrected in the Part 1 closeout. Adjacent provider numbers say the
        provider skipped nothing it numbered; they do not say the market was
        closed for the 199 intervals between. Without verified session
        evidence that stays an unexplained temporal gap, and blocks."""
        market = state((M5,))
        send(market, m5(0, sequence=0))
        send(market, m5(200, sequence=1))

        status = market.snapshot(RECEIVE_START).status(M5)
        assert status.book.integrity is Integrity.DISCONTINUOUS
        assert status.book.temporal_gaps == 1
        assert status.book.missing_sequences == 0
        assert status.availability is Availability.UNAVAILABLE


class TestEAndFLateAndOutOfOrder:
    def test_a_late_candle_that_fills_a_recorded_gap_restores_completeness(self) -> None:
        market = state((M5,))
        send(market, m5(0))
        send(market, m5(2))
        assert market.book(M5).integrity() is Integrity.GAPPED

        assert send(market, m5(1)) is ApplyOutcome.LATE_FILL
        assert market.book(M5).integrity() is Integrity.COMPLETE
        assert [o.candle.open_time for o in market.book(M5).confirmed()] == [at(0), at(5), at(10)]

    def test_a_delayed_arrival_is_ordered_by_market_time_not_receive_time(self) -> None:
        market = state((M5,))
        send(market, m5(0), after=timedelta(minutes=0))
        send(market, m5(2), after=timedelta(minutes=1))
        send(market, m5(1), after=timedelta(minutes=9))

        confirmed = market.book(M5).confirmed()
        assert [o.candle.open_time for o in confirmed] == [at(0), at(5), at(10)]
        assert confirmed[1].received_at > confirmed[2].received_at

    def test_a_sequence_that_disagrees_with_its_time_is_refused(self) -> None:
        market = state((M5,))
        send(market, m5(0, sequence=0))
        send(market, m5(2, sequence=2))

        # sequence 1, but claiming to be after the candle numbered 2
        result = send(market, m5(3, sequence=1))

        assert isinstance(result, Rejection)
        assert result.code is RejectionCode.ORDER_VIOLATION

    def test_a_late_candle_on_an_unsequenced_stream_is_refused(self) -> None:
        market = state((M5,))
        send(market, m5(0, sequenced=False))
        send(market, m5(2, sequenced=False))

        result = send(market, m5(1, sequenced=False))

        assert isinstance(result, Rejection)
        assert result.code is RejectionCode.LATE_UNSEQUENCED

    def test_mixing_sequenced_and_unsequenced_candles_is_refused(self) -> None:
        market = state((M5,))
        send(market, m5(0, sequence=0))

        result = send(market, m5(1, sequenced=False))

        assert isinstance(result, Rejection)
        assert result.code is RejectionCode.MIXED_SEQUENCING


class TestGStaleTimeframeWhileAnotherStaysAvailable:
    def test_5m_goes_stale_and_1h_does_not(self) -> None:
        market = state((M5, H1))
        send(market, m5(0))
        send(
            market,
            candle_event(SYMBOL, H1, at(0), "100", "101", "99", "100.5", sequence=0),
        )

        later = RECEIVE_START + timedelta(minutes=30)
        snapshot = market.snapshot(later)
        assert snapshot.status(M5).freshness is Freshness.STALE
        assert snapshot.status(H1).freshness is Freshness.FRESH
        assert snapshot.available == (H1,)

    def test_stale_data_keeps_its_original_timestamps(self) -> None:
        """Nothing is refreshed to make an old observation look current."""
        market = state((M5,))
        send(market, m5(0))
        before = market.book(M5).status()

        market.snapshot(RECEIVE_START + timedelta(hours=5))

        after = market.book(M5).status()
        assert after.last_received_at == before.last_received_at == RECEIVE_START
        assert after.last_event_time == at(5)
        assert after.closed_count == 1

    def test_absence_of_data_is_never_a_candle(self) -> None:
        market = state((M5,))
        send(market, m5(0))

        market.snapshot(RECEIVE_START + timedelta(hours=5))

        assert len(market.book(M5).confirmed()) == 1

    def test_thresholds_are_per_timeframe_and_explicit(self) -> None:
        with pytest.raises(ValueError, match="no freshness threshold"):
            LiveMarketState(
                symbol=SYMBOL,
                timeframes=(M5,),
                provenance=StreamProvenance.SIMULATED_HISTORICAL_STREAM,
                freshness=FreshnessPolicy({H1: timedelta(hours=1)}),
            )


class TestHConnectedButNoValidData:
    def test_invalid_events_do_not_make_a_stream_fresh(self) -> None:
        market = state((M5,))
        for minute in range(5):
            bad = candle_event(SYMBOL, M5, at(0), "100", "99", "101", "100", sequence=minute)
            send(market, bad, after=timedelta(minutes=minute))

        snapshot = market.snapshot(RECEIVE_START + timedelta(minutes=5))
        assert snapshot.connection is ConnectionState.CONNECTED
        assert snapshot.status(M5).freshness is Freshness.NO_DATA
        assert snapshot.status(M5).availability is Availability.UNAVAILABLE
        assert snapshot.rejection_counts["INCONSISTENT_OHLC"] == 5


class TestIDisconnect:
    def test_confirmed_candles_stay_and_the_forming_one_goes(self) -> None:
        market = state((M5,))
        send(market, m5(0))
        send(market, m5(1, closed=False))

        market.apply_signal(ProviderSignal(SignalKind.DISCONNECTED))

        book = market.book(M5)
        assert len(book.confirmed()) == 1
        assert book.forming is None
        assert market.connection is ConnectionState.DISCONNECTED

    def test_a_disconnected_timeframe_is_unavailable_and_says_why(self) -> None:
        market = state((M5,))
        send(market, m5(0))
        market.apply_signal(ProviderSignal(SignalKind.DISCONNECTED))

        status = market.snapshot(RECEIVE_START).status(M5)
        assert status.availability is Availability.UNAVAILABLE
        assert "the provider is disconnected" in status.reasons
        kinds = {a.kind for a in alert_candidates(market.snapshot(RECEIVE_START))}
        assert AlertKind.PROVIDER_DISCONNECTED in kinds

    def test_a_candle_during_a_disconnect_is_refused(self) -> None:
        market = state((M5,))
        market.apply_signal(ProviderSignal(SignalKind.DISCONNECTED))

        result = send(market, m5(0))

        assert isinstance(result, Rejection)
        assert result.code is RejectionCode.NOT_CONNECTED

    def test_no_state_ever_claims_the_market_is_closed(self) -> None:
        values = {state.value for state in ConnectionState} | {f.value for f in Freshness}
        assert not any("CLOSED" in value or "MARKET" in value for value in values)


class TestReconnectContinuity:
    def test_reconnect_is_recovery_not_restoration(self) -> None:
        market = state((M5,))
        send(market, m5(0))
        market.apply_signal(ProviderSignal(SignalKind.DISCONNECTED))
        market.apply_signal(ProviderSignal(SignalKind.CONNECTED))

        assert market.connection is ConnectionState.RECOVERING
        assert market.book(M5).integrity() is Integrity.UNVERIFIED

    def test_the_next_contiguous_candle_proves_continuity(self) -> None:
        market = state((M5,))
        send(market, m5(0))
        market.apply_signal(ProviderSignal(SignalKind.DISCONNECTED))
        market.apply_signal(ProviderSignal(SignalKind.CONNECTED))

        send(market, m5(1))

        assert market.book(M5).integrity() is Integrity.COMPLETE
        assert market.connection is ConnectionState.CONNECTED

    def test_a_later_candle_after_reconnect_exposes_the_gap(self) -> None:
        market = state((M5,))
        send(market, m5(0))
        market.apply_signal(ProviderSignal(SignalKind.DISCONNECTED))
        market.apply_signal(ProviderSignal(SignalKind.CONNECTED))

        send(market, m5(4))

        assert market.book(M5).integrity() is Integrity.GAPPED
        assert market.book(M5).status().missing_sequences == 3

    def test_a_repeated_connected_notice_cannot_end_recovery(self) -> None:
        market = state((M5,))
        send(market, m5(0))
        market.apply_signal(ProviderSignal(SignalKind.DISCONNECTED))
        market.apply_signal(ProviderSignal(SignalKind.CONNECTED))

        market.apply_signal(ProviderSignal(SignalKind.CONNECTED))

        assert market.connection is ConnectionState.RECOVERING

    def test_too_many_reconnects_terminate_the_stream(self) -> None:
        market = state((M5,), limits=LiveLimits(max_reconnects=2))
        for _ in range(3):
            market.apply_signal(ProviderSignal(SignalKind.DISCONNECTED))
            market.apply_signal(ProviderSignal(SignalKind.CONNECTED))

        assert market.connection is ConnectionState.TERMINATED
        assert market.termination_reason is TerminationReason.RECONNECT_LIMIT


class TestBoundedState:
    def test_the_confirmed_history_is_trimmed_and_the_trim_counted(self) -> None:
        market = state((M5,), limits=LiveLimits(max_closed_candles=10))
        for index in range(25):
            send(market, m5(index))

        status = market.book(M5).status()
        assert status.closed_count == 10
        assert status.trimmed == 15
        assert status.first_closed_open_time == at(5 * 15)

    def test_a_gap_that_scrolls_out_of_the_window_stops_counting(self) -> None:
        market = state((M5,), limits=LiveLimits(max_closed_candles=5))
        send(market, m5(0))
        send(market, m5(2))  # sequence 1 lost
        for index in range(3, 12):
            send(market, m5(index))

        assert market.book(M5).integrity() is Integrity.COMPLETE

    def test_rejection_detail_is_bounded_and_counts_are_exact(self) -> None:
        market = state((M5,), limits=LiveLimits(max_recorded_issues=3))
        for minute in range(10):
            send(
                market,
                candle_event(SYMBOL, M5, at(0), "100", "99", "101", "100"),
                after=timedelta(minutes=minute),
            )

        snapshot = market.snapshot(RECEIVE_START)
        assert len(snapshot.recent_rejections) == 3
        assert snapshot.rejection_counts["INCONSISTENT_OHLC"] == 10

    def test_a_huge_sequence_jump_is_reported_without_enumerating_it(self) -> None:
        market = state((M5,), limits=LiveLimits(max_missing_sequences=10))
        send(market, m5(0, sequence=0))
        send(market, m5(600, sequence=600))  # 599 lost, consistent with time

        status = market.book(M5).status()
        assert status.missing_sequences == 10
        assert status.missing_overflowed is True
        assert status.integrity is Integrity.GAPPED


class TestTerminalStates:
    def test_nothing_revives_an_ended_stream(self) -> None:
        market = state((M5,))
        market.apply_signal(ProviderSignal(SignalKind.END_OF_STREAM))
        market.apply_signal(ProviderSignal(SignalKind.CONNECTED))

        assert market.connection is ConnectionState.TERMINATED
        assert market.termination_reason is TerminationReason.END_OF_STREAM
