"""A live session end to end, against the real analysis pipeline (Phase 13).

Golden cases A, J, K, P, Q, R, S and T, plus the failure behaviour. The
analysis called here is the unmodified Phase 8 ``run_analysis`` over the
confirmed candles - there is no live indicator, and a test below checks the
session never reaches one.

No real time passes in any of these tests. The receive clock is a
``ManualClock`` that the mock script advances.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytest

from app.adapters.live.mock_stream import (
    Advance,
    Emit,
    Fail,
    ManualClock,
    MockBackfillProvider,
    MockPushProvider,
    MockStreamProvider,
    Signal,
    Step,
    candle_event,
    historical_script,
)
from app.adapters.market_data.csv_provider import CsvCandleTextParser
from app.application.live.registry import LiveCapacityError, LiveSessionRegistry
from app.application.live.session import LiveAnalysisUnavailableError, LiveSession
from app.domain.common.enums import Timeframe
from app.domain.live.alerts import AlertKind
from app.domain.live.book import Integrity
from app.domain.live.events import (
    ProviderSignal,
    RawCandleEvent,
    SignalKind,
    StreamKey,
    StreamProvenance,
)
from app.domain.live.limits import LiveLimits
from app.domain.live.state import Availability, ConnectionState, TerminationReason
from tests.unit.live.support import (
    FRESHNESS,
    H1,
    M5,
    M15,
    RECEIVE_START,
    SYMBOL,
    at,
    fixture_market,
    session_for,
)

pytestmark = pytest.mark.unit


def m5_event(index: int, *, sequence: int | None = None) -> RawCandleEvent:
    return candle_event(
        SYMBOL,
        M5,
        at(5 * index),
        "100",
        "101",
        "99",
        "100.5",
        sequence=index if sequence is None else sequence,
    )


# ----------------------------------------------------------------------
# A. A clean chronological stream, through the existing analysis
# ----------------------------------------------------------------------


class TestAACleanStream:
    async def test_every_candle_is_confirmed_and_the_stream_ends_honestly(self) -> None:
        market = fixture_market()
        session, _ = session_for(historical_script(market))

        await session.run()

        snapshot = session.snapshot()
        assert snapshot.connection is ConnectionState.TERMINATED
        assert snapshot.termination_reason is TerminationReason.END_OF_STREAM
        for timeframe, series in market.items():
            status = snapshot.status(timeframe)
            assert status.book.closed_count == len(series)
            assert status.book.integrity is Integrity.COMPLETE

    async def test_the_provenance_is_simulated_all_the_way_through(self) -> None:
        session, _ = session_for(historical_script(fixture_market(), end=False))

        await session.run()
        analysis = await session.confirmed_analysis()

        assert session.provenance is StreamProvenance.SIMULATED_HISTORICAL_STREAM
        assert session.snapshot().provenance is StreamProvenance.SIMULATED_HISTORICAL_STREAM
        assert analysis.provenance is StreamProvenance.SIMULATED_HISTORICAL_STREAM

    async def test_the_analysis_describes_the_market_moment_not_the_receive_moment(
        self,
    ) -> None:
        market = fixture_market()
        session, clock = session_for(historical_script(market, end=False))

        await session.run()
        analysis = await session.confirmed_analysis()

        last_5m = market[M5][-1].open_time + timedelta(minutes=5)
        assert analysis.market_as_of == last_5m
        assert analysis.requested_at == clock.now()
        assert analysis.market_as_of < analysis.requested_at  # historical, and says so
        assert analysis.outcome.identity.generated_at == last_5m


# ----------------------------------------------------------------------
# P. MTF availability
# ----------------------------------------------------------------------


class TestPMultiTimeframeAvailability:
    async def test_a_missing_timeframe_is_reported_not_fabricated(self) -> None:
        """No 1D was ever streamed. The analysis must not invent one."""
        session, _ = session_for(
            historical_script(fixture_market(), end=False), timeframes=(M5, M15, H1)
        )
        await session.run()

        analysis = await session.confirmed_analysis()

        assert set(analysis.included) == {M5, M15, H1}
        assert analysis.excluded == {}
        # The existing pipeline names the missing regime timeframe itself.
        assert Timeframe.D1 in analysis.outcome.missing_timeframes
        assert {item.timeframe for item in analysis.outcome.timeframes} == {M5, M15, H1}

    async def test_streamed_ohlcv_never_verifies_contract_metadata(self) -> None:
        """No provider is composed, and a stream cannot stand in for one."""
        session, _ = session_for(historical_script(fixture_market(), end=False))
        await session.run()

        analysis = await session.confirmed_analysis()

        assert analysis.outcome.identity.contract_metadata_verified is False

    async def test_a_stale_timeframe_is_left_out_with_its_reason(self) -> None:
        market = fixture_market()
        script = historical_script({M5: market[M5], H1: market[H1]}, end=False)
        session, clock = session_for(script, timeframes=(M5, H1))
        await session.run()

        clock.advance(timedelta(minutes=30))  # 5M threshold 10 min, 1H threshold 2 h
        analysis = await session.confirmed_analysis()

        assert analysis.included == (H1,)
        assert any("no valid observation" in reason for reason in analysis.excluded[M5])

    async def test_nothing_available_is_a_refusal_not_an_empty_analysis(self) -> None:
        session, clock = session_for(historical_script(fixture_market(), end=False))
        await session.run()
        clock.advance(timedelta(days=3))

        with pytest.raises(LiveAnalysisUnavailableError) as raised:
            await session.confirmed_analysis()

        assert raised.value.code == "NO_TIMEFRAME_AVAILABLE"
        assert set(raised.value.reasons) == {M5, M15, H1}


# ----------------------------------------------------------------------
# J and K. Reconnect with and without backfill
# ----------------------------------------------------------------------


class TestJReconnectWithoutBackfill:
    async def test_missed_candles_are_exposed_not_stitched(self) -> None:
        script: list[Step] = [
            Signal(SignalKind.CONNECTED),
            Emit(m5_event(0)),
            Emit(m5_event(1)),
            Signal(SignalKind.DISCONNECTED),
            Advance(timedelta(minutes=20)),
            Signal(SignalKind.CONNECTED),
            Emit(m5_event(5)),  # 2, 3 and 4 happened while disconnected
        ]
        session, _ = session_for(script, timeframes=(M5,))

        await session.run()

        status = session.snapshot().status(M5)
        assert status.book.integrity is Integrity.GAPPED
        assert status.book.missing_sequences == 3
        assert status.availability is Availability.UNAVAILABLE
        assert [o.candle.open_time for o in session.state().book(M5).confirmed()] == [
            at(0),
            at(5),
            at(25),
        ]

    async def test_until_proven_the_timeframe_stays_unverified(self) -> None:
        script: list[Step] = [
            Signal(SignalKind.CONNECTED),
            Emit(m5_event(0)),
            Signal(SignalKind.DISCONNECTED),
            Signal(SignalKind.CONNECTED),
        ]
        session, _ = session_for(script, timeframes=(M5,))

        await session.run()

        snapshot = session.snapshot()
        assert snapshot.connection is ConnectionState.RECOVERING
        assert snapshot.status(M5).book.integrity is Integrity.UNVERIFIED
        assert AlertKind.RECOVERY_PENDING in {a.kind for a in session.alerts()}


class TestKReconnectWithCompleteBackfill:
    async def test_backfill_restores_continuity_by_sequence(self) -> None:
        clock = ManualClock(RECEIVE_START)
        provider = MockBackfillProvider(
            script=[
                Signal(SignalKind.CONNECTED),
                Emit(m5_event(0)),
                Emit(m5_event(1)),
                Signal(SignalKind.DISCONNECTED),
                Signal(SignalKind.CONNECTED),
                Emit(m5_event(5)),
            ],
            clock=clock,
            archive={StreamKey(SYMBOL, M5): [m5_event(i) for i in range(6)]},
        )
        session, _ = session_for([], timeframes=(M5,), clock=clock, provider=provider)

        await session.run()

        assert provider.backfill_calls == [(StreamKey(SYMBOL, M5), 1)]
        status = session.snapshot().status(M5)
        assert status.book.integrity is Integrity.COMPLETE
        assert status.book.closed_count == 6
        assert session.snapshot().connection is ConnectionState.CONNECTED

    async def test_an_incomplete_backfill_leaves_the_gap_visible(self) -> None:
        clock = ManualClock(RECEIVE_START)
        provider = MockBackfillProvider(
            script=[
                Signal(SignalKind.CONNECTED),
                Emit(m5_event(0)),
                Signal(SignalKind.DISCONNECTED),
                Signal(SignalKind.CONNECTED),
                Emit(m5_event(5)),
            ],
            clock=clock,
            # The archive is missing sequence 3.
            archive={StreamKey(SYMBOL, M5): [m5_event(i) for i in (1, 2, 4)]},
        )
        session, _ = session_for([], timeframes=(M5,), clock=clock, provider=provider)

        await session.run()

        status = session.snapshot().status(M5)
        assert status.book.integrity is Integrity.GAPPED
        assert status.book.missing_sequences == 1


# ----------------------------------------------------------------------
# Q. Bounded buffer overflow
# ----------------------------------------------------------------------


class TestQBoundedQueueOverflow:
    async def test_overflow_terminates_the_stream_rather_than_thinning_it(self) -> None:
        provider = MockPushProvider(capacity=3)
        assert provider.push(ProviderSignal(SignalKind.CONNECTED))
        assert provider.push(m5_event(0))
        assert provider.push(m5_event(1))
        assert provider.push(m5_event(2)) is False  # full: refused, and latched
        assert provider.push(m5_event(3)) is False

        session, _ = session_for([], timeframes=(M5,), provider=provider)
        await session.run()

        snapshot = session.snapshot()
        assert snapshot.connection is ConnectionState.TERMINATED
        assert snapshot.termination_reason is TerminationReason.OVERLOADED
        assert snapshot.status(M5).availability is Availability.UNAVAILABLE
        assert AlertKind.STREAM_OVERLOADED in {a.kind for a in session.alerts()}

    async def test_the_buffer_never_grows_past_its_capacity(self) -> None:
        provider = MockPushProvider(capacity=10)
        for index in range(1000):
            provider.push(m5_event(index))

        assert len(provider.buffer) == 10
        assert provider.buffer.refused == 990


# ----------------------------------------------------------------------
# R and S. Cancellation, end of stream
# ----------------------------------------------------------------------


class TestRCancellation:
    async def test_a_cancelled_session_says_so_and_closes_its_subscription(self) -> None:
        provider = MockPushProvider(capacity=10)
        provider.push(ProviderSignal(SignalKind.CONNECTED))
        session, _ = session_for([], timeframes=(M5,), provider=provider)

        task = asyncio.create_task(session.run())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert session.snapshot().termination_reason is TerminationReason.CANCELLED
        assert provider.buffer.offer(m5_event(0)) is False  # closed by the session


class TestSEndOfMockStream:
    async def test_after_the_end_the_last_analysis_is_kept_but_not_current(self) -> None:
        market = fixture_market()
        steps = historical_script(market, end=False)
        session, _ = session_for(steps)
        await session.run()
        await session.confirmed_analysis()

        session.state().apply_signal(ProviderSignal(SignalKind.END_OF_STREAM))

        kept = session.last_analysis()
        assert kept is not None
        analysis, current = kept
        assert current is False
        assert analysis.market_as_of == market[M5][-1].open_time + timedelta(minutes=5)
        with pytest.raises(LiveAnalysisUnavailableError):
            await session.confirmed_analysis()


# ----------------------------------------------------------------------
# T. Determinism
# ----------------------------------------------------------------------


class TestTDeterministicReplay:
    async def test_the_same_events_produce_the_same_market_state_and_analysis(self) -> None:
        market = fixture_market()
        first, _ = session_for(historical_script(market, end=False))
        second, _ = session_for(historical_script(market, end=False))

        await first.run()
        await second.run()
        a = await first.confirmed_analysis()
        b = await second.confirmed_analysis()

        for timeframe in (M5, M15, H1):
            left = [o.candle for o in first.state().book(timeframe).confirmed()]
            right = [o.candle for o in second.state().book(timeframe).confirmed()]
            assert left == right
        assert a.market_as_of == b.market_as_of
        assert a.outcome == b.outcome


# ----------------------------------------------------------------------
# Trigger policy
# ----------------------------------------------------------------------


class TestAnalysisRunsOnlyWhenAskedAndOnlyOnce:
    async def test_ingesting_events_never_runs_an_analysis(self) -> None:
        session, _ = session_for(historical_script(fixture_market(), end=False))

        await session.run()

        assert session.analyses_run == 0

    async def test_an_unchanged_prefix_is_served_from_the_cache(self) -> None:
        session, _ = session_for(historical_script(fixture_market(), end=False))
        await session.run()

        first = await session.confirmed_analysis()
        second = await session.confirmed_analysis()

        assert session.analyses_run == 1
        assert second.outcome is first.outcome

    async def test_a_new_closed_candle_invalidates_the_cache(self) -> None:
        market = fixture_market(count=60)
        steps = historical_script({M5: market[M5][:-1]}, end=False)
        clock = ManualClock(RECEIVE_START)
        session, _ = session_for(steps, timeframes=(M5,), clock=clock)
        await session.run()
        await session.confirmed_analysis()

        last = market[M5][-1]
        session.state().apply_event(
            candle_event(
                SYMBOL,
                M5,
                last.open_time,
                str(last.open),
                str(last.high),
                str(last.low),
                str(last.close),
                str(last.volume),
                sequence=len(market[M5]) - 1,
            ),
            received_at=clock.now(),
        )
        await session.confirmed_analysis()

        assert session.analyses_run == 2


# ----------------------------------------------------------------------
# Failure behaviour
# ----------------------------------------------------------------------


SECRET = "sk-live-do-not-leak-0001"


class TestFailuresAreContainedAndLeakNothing:
    async def test_a_provider_that_fails_to_subscribe_terminates_cleanly(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock = ManualClock(RECEIVE_START)
        provider = MockStreamProvider(
            script=[], clock=clock, fail_on_subscribe=f"wss://feed?key={SECRET}"
        )
        session, _ = session_for([], clock=clock, provider=provider)
        caplog.set_level(logging.DEBUG)

        await session.run()

        assert session.snapshot().termination_reason is TerminationReason.PROVIDER_ERROR
        assert SECRET not in caplog.text
        assert "wss://" not in caplog.text

    async def test_a_mid_stream_exception_terminates_and_leaks_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        script: list[Step] = [
            Signal(SignalKind.CONNECTED),
            Emit(m5_event(0)),
            Fail(f"postgresql://user:{SECRET}@db/prod payload=<raw>"),
            Emit(m5_event(1)),
        ]
        session, _ = session_for(script, timeframes=(M5,))
        caplog.set_level(logging.DEBUG)

        await session.run()

        assert session.snapshot().termination_reason is TerminationReason.PROVIDER_ERROR
        assert session.state().book(M5).status().closed_count == 1  # kept, not extended
        for record in caplog.records:
            rendered = record.getMessage() + repr(record.__dict__)
            assert SECRET not in rendered
            assert "postgresql://" not in rendered
            assert record.exc_info is None

    async def test_a_downstream_analysis_failure_is_typed_and_safe(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session, _ = session_for(historical_script(fixture_market(), end=False))
        await session.run()

        async def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError(f"analysis blew up with {SECRET}")

        monkeypatch.setattr("app.application.live.session.run_analysis", boom)
        caplog.set_level(logging.DEBUG)

        with pytest.raises(LiveAnalysisUnavailableError) as raised:
            await session.confirmed_analysis()

        assert raised.value.code == "ANALYSIS_FAILED"
        assert raised.value.__cause__ is None
        assert SECRET not in caplog.text

    async def test_a_disconnect_before_the_first_connect_is_still_recoverable(self) -> None:
        """A provider that drops before ever connecting, then connects.

        The continuity rule applies to the very first candle too: it arrives
        during recovery, and it is that candle - not the CONNECTED notice -
        that ends recovery.
        """
        script: list[Step] = [
            Signal(SignalKind.DISCONNECTED),
            Signal(SignalKind.CONNECTED),
            Emit(m5_event(0)),
        ]
        session, _ = session_for(script, timeframes=(M5,))

        await session.run()

        assert session.snapshot().connection is ConnectionState.CONNECTED
        assert session.state().book(M5).integrity() is Integrity.COMPLETE

    def test_every_signal_combination_is_a_legal_transition_or_a_no_op(self) -> None:
        """So a misbehaving provider cannot drive the state machine into an
        illegal state; `IllegalTransitionError` remains as a guard for code,
        not as something a provider can trigger."""
        from app.domain.live.state import LiveMarketState

        for first in SignalKind:
            for second in SignalKind:
                market = LiveMarketState(
                    symbol=SYMBOL,
                    timeframes=(M5,),
                    provenance=StreamProvenance.SIMULATED_HISTORICAL_STREAM,
                    freshness=FRESHNESS,
                )
                market.apply_signal(ProviderSignal(first))
                market.apply_signal(ProviderSignal(second))  # must not raise


# ----------------------------------------------------------------------
# Bounded sessions
# ----------------------------------------------------------------------


class TestTheRegistryIsBounded:
    def test_registration_beyond_capacity_is_refused(self) -> None:
        registry = LiveSessionRegistry(max_sessions=2)
        clock = ManualClock(RECEIVE_START)

        def make() -> LiveSession:
            return LiveSession(
                provider=MockStreamProvider(script=[], clock=clock),
                symbol=SYMBOL,
                timeframes=(M5,),
                clock=clock,
                parser=CsvCandleTextParser(),
                freshness=FRESHNESS,
                limits=LiveLimits(),
            )

        registry.register("a", make())
        registry.register("b", make())
        with pytest.raises(LiveCapacityError):
            registry.register("c", make())
        assert len(registry) == 2
