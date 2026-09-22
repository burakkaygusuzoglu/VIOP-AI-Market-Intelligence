"""The Part 1 data-integrity closeout, as tests (Phase 13).

Four claims were audited and corrected:

* provider sequence continuity is not interval coverage, and neither is an
  exchange session boundary;
* receiving an observation recently does not make it a current market price;
* trimming the bounded window must not launder an unresolved gap;
* a cached analysis cannot become "current" again unless the stream really
  is back in the state it analysed.

Each class states the fact it defends. Nothing here weakens a golden case:
the one golden assertion that changed - contiguous numbers across a time jump
used to be called a session break - is corrected in test_market_state.py and
reported as such.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from app.adapters.live.mock_stream import ManualClock, candle_event, historical_script
from app.application.live.session import LiveSession
from app.domain.common.enums import Timeframe
from app.domain.live.alerts import AlertKind, alert_candidates
from app.domain.live.book import ApplyOutcome, DiscontinuityKind, Integrity
from app.domain.live.events import (
    MarketCurrency,
    ProviderSignal,
    RawCandleEvent,
    SignalKind,
    StreamProvenance,
    market_currency_of,
)
from app.domain.live.limits import LiveLimits
from app.domain.live.state import (
    Availability,
    ConnectionState,
    Freshness,
    LiveMarketState,
    TerminationReason,
    TimeframeStatus,
)
from app.domain.live.validation import Rejection, RejectionCode
from tests.unit.live.support import (
    FRESHNESS,
    M5,
    RECEIVE_START,
    SYMBOL,
    at,
    fixture_market,
    session_for,
)

pytestmark = pytest.mark.unit


def state(limits: LiveLimits | None = None) -> LiveMarketState:
    market = LiveMarketState(
        symbol=SYMBOL,
        timeframes=(M5,),
        provenance=StreamProvenance.SIMULATED_HISTORICAL_STREAM,
        freshness=FRESHNESS,
        limits=limits or LiveLimits(),
    )
    market.apply_signal(ProviderSignal(SignalKind.CONNECTED))
    return market


def bar(slot: int, sequence: int | None, *, closed: bool = True) -> RawCandleEvent:
    """A 5M candle in grid slot ``slot``, carrying provider number ``sequence``."""
    return candle_event(
        SYMBOL,
        M5,
        at(5 * slot),
        "100",
        "101",
        "99",
        "100.5",
        closed=closed,
        sequence=sequence if closed else None,
        event_time=None if closed else at(5 * slot + 1),
    )


def feed(market: LiveMarketState, *events: RawCandleEvent) -> list[object]:
    return [market.apply_event(event, received_at=RECEIVE_START) for event in events]


def status_of(market: LiveMarketState) -> TimeframeStatus:
    return market.snapshot(RECEIVE_START).status(M5)


def reconnect(market: LiveMarketState) -> None:
    market.apply_signal(ProviderSignal(SignalKind.DISCONNECTED))
    market.apply_signal(ProviderSignal(SignalKind.CONNECTED))


def window_is_contiguous(market: LiveMarketState) -> bool:
    times = [o.candle.open_time for o in market.book(M5).confirmed()]
    return all(b - a == timedelta(minutes=5) for a, b in zip(times, times[1:], strict=False))


# ----------------------------------------------------------------------
# 2. Sequence continuity, interval coverage and session boundaries
# ----------------------------------------------------------------------


class TestSequenceContinuityIsNotCoverage:
    def test_contiguous_sequences_over_contiguous_intervals_are_complete(self) -> None:
        market = state()
        feed(market, *(bar(slot, slot) for slot in range(6)))

        status = status_of(market)
        assert status.book.integrity is Integrity.COMPLETE
        assert status.availability is Availability.AVAILABLE

    def test_contiguous_sequences_cannot_hide_a_missing_5m_interval(self) -> None:
        market = state()
        feed(market, bar(0, 0), bar(1, 1), bar(3, 2))  # slot 2 has no candle

        status = status_of(market)
        assert status.book.integrity is Integrity.DISCONTINUOUS
        assert status.book.temporal_gaps == 1
        assert status.book.missing_sequences == 0  # nothing numbered was lost
        assert status.availability is Availability.UNAVAILABLE
        assert [o.candle.open_time for o in market.book(M5).confirmed()] == [
            at(0),
            at(5),
            at(15),
        ]  # and nothing was invented for slot 2

    def test_a_sequence_jump_over_adjacent_intervals_is_a_contract_breach(self) -> None:
        """The number says a candle was lost; the grid says there was no room
        for one. Not recorded as a candle to wait for - no interval exists."""
        market = state()
        feed(market, bar(0, 0), bar(1, 2))

        status = status_of(market)
        assert status.book.integrity is Integrity.DISCONTINUOUS
        assert status.book.sequence_mismatches == 1
        assert status.book.missing_sequences == 0
        assert any("disagree" in reason for reason in status.reasons)

    def test_the_breach_cannot_be_patched_with_a_late_candle(self) -> None:
        market = state()
        feed(market, bar(0, 0), bar(1, 2))

        (result,) = feed(market, bar(1, 1))  # slot 1 already holds number 2

        assert result is ApplyOutcome.CONFLICT  # quarantined, not inserted
        assert len(market.book(M5).confirmed()) == 2
        assert status_of(market).book.sequence_mismatches == 1
        assert status_of(market).availability is Availability.UNAVAILABLE

    def test_a_time_jump_longer_than_the_sequence_jump_is_not_a_fillable_gap(self) -> None:
        market = state()
        feed(market, bar(0, 0), bar(5, 2))  # four slots, one number

        status = status_of(market)
        assert status.book.integrity is Integrity.DISCONTINUOUS
        assert status.book.temporal_gaps == 1
        assert status.book.missing_sequences == 0

    def test_a_genuine_known_gap_is_recorded_and_filled_exactly(self) -> None:
        market = state()
        feed(market, bar(0, 0), bar(3, 3))
        assert status_of(market).book.missing_sequences == 2
        assert market.book(M5).integrity() is Integrity.GAPPED

        assert feed(market, bar(1, 1), bar(2, 2)) == [ApplyOutcome.LATE_FILL] * 2

        assert status_of(market).availability is Availability.AVAILABLE
        assert window_is_contiguous(market)

    def test_a_late_candle_must_fill_the_interval_its_number_names(self) -> None:
        market = state()
        feed(market, bar(0, 0), bar(3, 3))

        (result,) = feed(market, bar(2, 1))  # number 1 belongs in slot 1

        assert isinstance(result, Rejection)
        assert result.code is RejectionCode.ORDER_VIOLATION
        assert status_of(market).book.missing_sequences == 2

    def test_nothing_in_the_model_can_name_a_session_break(self) -> None:
        names = {
            *(member.value for member in Integrity),
            *(member.value for member in DiscontinuityKind),
            *(member.value for member in ConnectionState),
            *(member.value for member in AlertKind),
        }
        assert not any("SESSION" in name or "MARKET_CLOSED" in name for name in names)

    def test_the_discontinuity_alert_does_not_assume_a_session_break(self) -> None:
        market = state()
        feed(market, bar(0, 0), bar(200, 1))

        (alert,) = [
            a
            for a in alert_candidates(market.snapshot(RECEIVE_START))
            if a.kind is AlertKind.DATA_DISCONTINUITY
        ]
        assert "no jump is assumed to be a session break" in alert.detail


class TestReconnectCoverage:
    def test_a_reconnect_with_an_unresolved_temporal_gap_stays_unavailable(self) -> None:
        market = state()
        feed(market, bar(0, 0))
        reconnect(market)

        feed(market, bar(12, 1))  # the next number, an hour later

        status = status_of(market)
        assert market.connection is ConnectionState.CONNECTED  # the question is answered...
        assert status.book.integrity is Integrity.DISCONTINUOUS  # ...and the answer is "no"
        assert status.availability is Availability.UNAVAILABLE

    def test_recovery_completes_only_when_the_missing_candles_are_supplied(self) -> None:
        market = state()
        feed(market, bar(0, 0))
        reconnect(market)
        feed(market, bar(4, 4))
        assert market.book(M5).integrity() is Integrity.GAPPED

        feed(market, bar(1, 1), bar(2, 2))
        assert market.book(M5).integrity() is Integrity.GAPPED  # one still missing

        feed(market, bar(3, 3))
        assert status_of(market).availability is Availability.AVAILABLE
        assert window_is_contiguous(market)

    def test_a_late_fill_after_a_reconnect_does_not_prove_the_present(self) -> None:
        """Corrected in the closeout: any closed candle used to end the wait,
        including one filling an old hole - which says nothing about what
        happened since the disconnect."""
        market = state()
        feed(market, bar(0, 0), bar(2, 2))  # number 1 lost
        reconnect(market)

        (outcome,) = feed(market, bar(1, 1))

        assert outcome is ApplyOutcome.LATE_FILL
        assert market.connection is ConnectionState.RECOVERING
        assert market.book(M5).integrity() is Integrity.UNVERIFIED
        assert status_of(market).availability is Availability.UNAVAILABLE


class TestAFormingCandleAheadExposesUnreceivedIntervals:
    def test_a_forming_candle_for_the_next_interval_changes_nothing(self) -> None:
        market = state()
        feed(market, bar(0, 0), bar(1, closed=False, sequence=None))

        assert status_of(market).availability is Availability.AVAILABLE

    def test_a_forming_candle_three_intervals_ahead_is_unverified(self) -> None:
        market = state()
        feed(market, bar(0, 0), bar(3, closed=False, sequence=None))

        status = status_of(market)
        assert status.book.forming_ahead is True
        assert status.book.integrity is Integrity.UNVERIFIED
        assert status.availability is Availability.UNAVAILABLE
        kinds = {a.kind for a in alert_candidates(market.snapshot(RECEIVE_START))}
        assert AlertKind.CONTINUITY_UNPROVEN in kinds

    def test_it_is_resolved_only_when_confirmed_history_catches_up(self) -> None:
        market = state()
        feed(market, bar(0, 0), bar(3, closed=False, sequence=None))

        feed(market, bar(1, 1))
        assert market.book(M5).integrity() is Integrity.UNVERIFIED
        feed(market, bar(2, 2))
        assert market.book(M5).integrity() is Integrity.COMPLETE


# ----------------------------------------------------------------------
# 3. Receive freshness is not market currency
# ----------------------------------------------------------------------


class TestHistoricalMockFreshnessIsNotCurrency:
    def test_a_march_candle_received_in_september_is_fresh_and_historical(self) -> None:
        market = state()
        feed(market, bar(0, 0))

        snapshot = market.snapshot(RECEIVE_START)
        status = snapshot.status(M5)
        assert status.freshness is Freshness.FRESH  # the transport is alive...
        assert snapshot.market_currency is MarketCurrency.HISTORICAL  # ...the data is old
        assert snapshot.provenance is StreamProvenance.SIMULATED_HISTORICAL_STREAM
        assert status.book.last_event_time == at(5)
        assert status.book.last_received_at == RECEIVE_START
        assert RECEIVE_START - at(5) > timedelta(days=180)

    def test_the_mock_is_not_made_unusable_by_comparing_with_a_wall_clock(self) -> None:
        market = state()
        feed(market, bar(0, 0))

        assert status_of(market).availability is Availability.AVAILABLE

    def test_every_provenance_has_a_stated_currency_and_none_is_current(self) -> None:
        for provenance in StreamProvenance:
            assert market_currency_of(provenance) is MarketCurrency.HISTORICAL
        assert [member.value for member in MarketCurrency] == ["HISTORICAL"]

    def test_a_connected_fresh_stream_is_still_not_a_current_quote(self) -> None:
        market = state()
        feed(market, bar(0, 0))

        snapshot = market.snapshot(RECEIVE_START)
        assert snapshot.connection is ConnectionState.CONNECTED
        assert snapshot.market_currency is not None
        assert "CURRENT" not in snapshot.market_currency.value
        assert "LIVE" not in snapshot.market_currency.value

    @pytest.mark.parametrize("label", ["CURRENT", "LIVE_EXCHANGE_FEED", "EXCHANGE_VERIFIED"])
    def test_a_snapshot_cannot_be_relabelled_as_current(self, label: str) -> None:
        market = state()
        feed(market, bar(0, 0))
        snapshot = market.snapshot(RECEIVE_START)

        with pytest.raises(ValueError, match="must follow from provenance"):
            replace(snapshot, market_currency=label)  # type: ignore[arg-type]

    async def test_an_analysis_cannot_be_relabelled_as_current(self) -> None:
        session, _ = m5_session()
        await session.run()
        analysis = await session.confirmed_analysis()

        with pytest.raises(ValueError, match="must follow from provenance"):
            replace(analysis, market_currency="CURRENT")  # type: ignore[arg-type]

    async def test_the_analysis_carries_provenance_and_historical_currency(self) -> None:
        session, clock = session_for(historical_script(fixture_market(), end=False))
        await session.run()

        analysis = await session.confirmed_analysis()

        assert analysis.provenance is StreamProvenance.SIMULATED_HISTORICAL_STREAM
        assert analysis.market_currency is MarketCurrency.HISTORICAL
        assert analysis.market_as_of < analysis.requested_at == clock.now()

    async def test_no_future_or_forming_candle_reaches_confirmed_analysis(self) -> None:
        session, clock = m5_session()
        await session.run()
        market = session.state()
        last = market.book(M5).confirmed()[-1].candle.open_time
        market.apply_event(
            candle_event(
                SYMBOL,
                M5,
                last + timedelta(minutes=5),
                "100",
                "101",
                "99",
                "100.5",
                closed=False,
                event_time=last + timedelta(minutes=6),
            ),
            received_at=clock.now(),
        )

        analysis = await session.confirmed_analysis()

        assert analysis.market_as_of == last + timedelta(minutes=5)
        assert market.book(M5).forming is not None  # held, and not analysed
        for observation in market.book(M5).confirmed():
            end = observation.candle.open_time + timedelta(minutes=5)
            assert observation.candle.is_closed
            assert end <= analysis.market_as_of <= observation.received_at


# ----------------------------------------------------------------------
# 4. Trimming cannot launder an unresolved gap
# ----------------------------------------------------------------------


class TestTrimmingAndGapHistory:
    def test_a_gap_scrolls_out_only_with_the_window_proven_contiguous(self) -> None:
        market = state(LiveLimits(max_closed_candles=5))
        feed(market, bar(0, 0), bar(2, 2))  # number 1 lost
        feed(market, *(bar(slot, slot) for slot in range(3, 12)))

        status = status_of(market)
        assert status.book.integrity is Integrity.COMPLETE
        assert window_is_contiguous(market)
        assert status.book.unresolved_trimmed == 1  # disclosed, not forgotten

    def test_an_overflowed_gap_is_not_retired_while_any_of_it_is_in_the_window(
        self,
    ) -> None:
        """The recorded numbers leave the window first; the ones that could
        not be recorded are still inside it. Corrected: the overflow used to be
        sticky forever; it now retires exactly when all of it is outside."""
        market = state(LiveLimits(max_closed_candles=2, max_missing_sequences=2))
        feed(market, bar(0, 0), bar(3, 3))  # 1 and 2 recorded: the record is full
        feed(market, bar(6, 6))  # 4 and 5 lost, not recordable; slot 0 trims

        status = status_of(market)
        assert status.book.first_closed_open_time == at(15)  # window = [3, 6]
        assert status.book.missing_sequences == 0  # every *recorded* number is gone
        assert status.book.missing_overflowed is True  # but 4 and 5 are inside
        assert status.book.integrity is Integrity.GAPPED
        assert status.availability is Availability.UNAVAILABLE

        feed(market, bar(7, 7))  # slot 3 trims: the whole gap is now outside
        status = status_of(market)
        assert status.book.missing_overflowed is False
        assert status.book.integrity is Integrity.COMPLETE
        assert window_is_contiguous(market)

    def test_more_than_500_missing_numbers_stay_bounded_and_block(self) -> None:
        market = state()
        feed(market, bar(0, 0), bar(40_000, 40_000))

        status = status_of(market)
        assert status.book.missing_sequences == LiveLimits().max_missing_sequences
        assert status.book.missing_overflowed is True
        assert status.availability is Availability.UNAVAILABLE

    def test_a_huge_contract_breach_allocates_nothing_per_number(self) -> None:
        market = state()
        feed(market, bar(0, 0), bar(1, 10**15))

        assert status_of(market).book.missing_sequences == 0
        assert market.book(M5).integrity() is Integrity.DISCONTINUOUS

    def test_a_discontinuity_leaves_the_record_only_when_it_leaves_the_window(
        self,
    ) -> None:
        market = state(LiveLimits(max_closed_candles=3))
        feed(market, bar(0, None), bar(2, None))  # unsequenced jump over slot 1
        feed(market, bar(3, None))
        assert market.book(M5).integrity() is Integrity.DISCONTINUOUS  # [0, 2, 3]

        feed(market, bar(4, None))  # [2, 3, 4]: the jump is before the window
        status = status_of(market)
        assert status.book.integrity is Integrity.COMPLETE
        assert status.book.unresolved_trimmed == 1
        assert window_is_contiguous(market)

    def test_a_conflict_that_scrolls_out_is_counted(self) -> None:
        market = state(LiveLimits(max_closed_candles=2))
        feed(market, bar(0, 0))
        conflicting = candle_event(SYMBOL, M5, at(0), "100", "101", "99", "100.9", sequence=0)
        assert feed(market, conflicting) == [ApplyOutcome.CONFLICT]

        feed(market, bar(1, 1), bar(2, 2))

        status = status_of(market)
        assert status.book.integrity is Integrity.COMPLETE
        assert status.book.unresolved_trimmed == 1

    def test_a_long_stream_at_the_cap_is_bounded_and_honest(self) -> None:
        market = state()
        cap = LiveLimits().max_closed_candles
        feed(market, bar(0, 0), bar(2, 2))  # one lost near the start
        feed(market, *(bar(slot, slot) for slot in range(3, cap + 600)))

        status = status_of(market)
        assert status.book.closed_count == cap
        assert status.book.integrity is Integrity.COMPLETE
        assert status.book.unresolved_trimmed == 1
        assert window_is_contiguous(market)


# ----------------------------------------------------------------------
# 5. The analysis cache and the "current" label
# ----------------------------------------------------------------------


def m5_session() -> tuple[LiveSession, ManualClock]:
    """A 5M-only session that has played a day of fixture history and is
    still connected, so tests can continue it one event at a time."""
    history = {M5: fixture_market()[M5]}
    return session_for(historical_script(history, end=False), timeframes=(M5,))


def next_bar(session: LiveSession, *, offset: int = 1, closed: bool = True) -> RawCandleEvent:
    book = session.state().book(M5)
    last = book.confirmed()[-1]
    open_time = last.candle.open_time + timedelta(minutes=5 * offset)
    assert last.sequence is not None
    return candle_event(
        SYMBOL,
        M5,
        open_time,
        "100",
        "101",
        "99",
        "100.5",
        closed=closed,
        sequence=last.sequence + offset if closed else None,
        event_time=None if closed else open_time + timedelta(minutes=1),
    )


class TestTheCacheCannotRelabelItselfCurrent:
    async def _analysed(self) -> tuple[LiveSession, ManualClock]:
        session, clock = m5_session()
        await session.run()
        await session.confirmed_analysis()
        assert session.last_analysis() is not None
        return session, clock

    async def test_staleness_ends_currency_and_a_connected_notice_cannot_restore_it(
        self,
    ) -> None:
        session, clock = await self._analysed()
        clock.advance(timedelta(minutes=30))

        session.state().apply_signal(ProviderSignal(SignalKind.CONNECTED))
        kept = session.last_analysis()

        assert kept is not None
        assert kept[1] is False
        assert session.snapshot().status(M5).freshness is Freshness.STALE

    async def test_a_duplicate_cannot_refresh_a_stale_analysis(self) -> None:
        session, clock = await self._analysed()
        clock.advance(timedelta(minutes=30))
        last = session.state().book(M5).confirmed()[-1]
        duplicate = candle_event(
            SYMBOL,
            M5,
            last.candle.open_time,
            str(last.candle.open),
            str(last.candle.high),
            str(last.candle.low),
            str(last.candle.close),
            str(last.candle.volume),
            sequence=last.sequence,
        )

        outcome = session.state().apply_event(duplicate, received_at=clock.now())

        assert outcome is ApplyOutcome.DUPLICATE
        kept = session.last_analysis()
        assert kept is not None
        assert kept[1] is False

    async def test_a_disconnect_and_reconnect_with_unchanged_books_is_not_current(
        self,
    ) -> None:
        session, _ = await self._analysed()
        version = session.state().book(M5).status().version

        session.state().apply_signal(ProviderSignal(SignalKind.DISCONNECTED))
        session.state().apply_signal(ProviderSignal(SignalKind.CONNECTED))

        assert session.state().book(M5).status().version == version
        kept = session.last_analysis()
        assert kept is not None
        assert kept[1] is False
        assert session.snapshot().connection is ConnectionState.RECOVERING

    async def test_a_conflict_ends_currency_even_though_no_version_changed(self) -> None:
        session, clock = await self._analysed()
        book = session.state().book(M5)
        version = book.status().version
        last = book.confirmed()[-1]
        conflicting = candle_event(
            SYMBOL,
            M5,
            last.candle.open_time,
            str(last.candle.open),
            str(last.candle.high),
            str(last.candle.low),
            str(last.candle.low),  # a different close
            sequence=last.sequence,
        )

        session.state().apply_event(conflicting, received_at=clock.now())

        assert book.status().version == version
        kept = session.last_analysis()
        assert kept is not None
        assert kept[1] is False

    async def test_a_forming_candle_ahead_ends_currency_without_a_new_close(self) -> None:
        session, clock = await self._analysed()

        session.state().apply_event(
            next_bar(session, offset=3, closed=False), received_at=clock.now()
        )

        kept = session.last_analysis()
        assert kept is not None
        assert kept[1] is False
        assert session.analyses_run == 1

    async def test_a_new_candle_and_the_trim_it_causes_end_currency(self) -> None:
        session, clock = await self._analysed()

        session.state().apply_event(next_bar(session), received_at=clock.now())

        kept = session.last_analysis()
        assert kept is not None
        assert kept[1] is False
        await session.confirmed_analysis()
        assert session.analyses_run == 2  # a real change is really re-analysed

    async def test_identical_inputs_reuse_the_calculation_with_a_fresh_envelope(
        self,
    ) -> None:
        """Stale, then fresh again through a valid forming update for the next
        interval: the confirmed candles are exactly those analysed, so the
        calculation is reused - and its envelope says when it was asked."""
        session, clock = await self._analysed()
        first = session.last_analysis()
        assert first is not None
        clock.advance(timedelta(minutes=30))
        assert session.last_analysis() == (first[0], False)

        session.state().apply_event(next_bar(session, closed=False), received_at=clock.now())
        again = await session.confirmed_analysis()

        assert session.analyses_run == 1
        assert again.outcome is first[0].outcome
        assert again.requested_at == clock.now() > first[0].requested_at
        assert session.last_analysis() is not None

    async def test_an_ended_stream_is_never_current(self) -> None:
        session, _ = await self._analysed()

        session.state().terminate(TerminationReason.CANCELLED)

        kept = session.last_analysis()
        assert kept is not None
        assert kept[1] is False


# ----------------------------------------------------------------------
# 6. Still no position, of any kind
# ----------------------------------------------------------------------


class TestReceivingDataOpensNothing:
    async def test_a_full_stream_and_an_analysis_create_no_position(self) -> None:
        session, _ = m5_session()
        await session.run()
        await session.confirmed_analysis()

        public = {name for name in dir(session) if not name.startswith("_")}
        assert not any(
            word in name for name in public for word in ("position", "order", "trade", "paper")
        )
        assert session.analyses_run == 1

    def test_timeframes_other_than_those_subscribed_are_never_derived(self) -> None:
        market = state()
        with pytest.raises(KeyError):
            market.book(Timeframe.H1)
