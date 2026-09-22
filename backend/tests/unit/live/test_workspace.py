"""The Part 2A live workspace: lifecycle, timeline, subscribers, bounds.

Every test drives a ``QueueProvider`` one item at a time, so what the workspace
does is decided by the test and not by timing.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import replace
from datetime import timedelta

import pytest

from app.application.live.catalog import PlaybackPace
from app.application.live.workspace import (
    EndOrigin,
    Lifecycle,
    LiveWorkspace,
    LiveWorkspaceError,
    NotificationKind,
    ResyncReason,
    TimelineKind,
    WorkspaceErrorKind,
)
from app.domain.live.events import MarketCurrency, StreamProvenance
from app.domain.live.state import ConnectionState, Freshness, TerminationReason
from tests.unit.live.support import M5, RECEIVE_START
from tests.unit.live.workspace_support import (
    CONNECTED,
    END,
    SOURCE_ID,
    FakeCatalog,
    bar,
    create,
    fixture_bars,
    settle,
    workspace,
)

pytestmark = pytest.mark.unit


def kinds(ws: LiveWorkspace, session_id: str) -> list[TimelineKind]:
    return [entry.kind for entry in ws.timeline(session_id, after=0, limit=100).entries]


class TestCreation:
    async def test_a_session_is_simulated_historical_with_a_server_identity(self) -> None:
        ws, _, _ = workspace()
        view = await ws.create(
            source_id=SOURCE_ID, timeframes=(M5,), window_candles=100, pace=PlaybackPace.FAST
        )
        try:
            assert re.fullmatch(r"LS-[0-9a-f]{24}", view.session_id)
            assert view.lifecycle is Lifecycle.RUNNING
            assert view.snapshot.provenance is StreamProvenance.SIMULATED_HISTORICAL_STREAM
            assert view.snapshot.market_currency is MarketCurrency.HISTORICAL
            assert kinds(ws, view.session_id) == [TimelineKind.SESSION_STARTED]
        finally:
            await ws.shutdown()

    async def test_two_sessions_never_share_an_identity(self) -> None:
        ws, _, _ = workspace()
        try:
            assert await create(ws) != await create(ws)
        finally:
            await ws.shutdown()

    @pytest.mark.parametrize(
        ("changes", "code"),
        [
            ({"timeframes": ()}, "INVALID_TIMEFRAMES"),
            ({"timeframes": (M5, M5)}, "INVALID_TIMEFRAMES"),
            ({"window_candles": 49}, "INVALID_WINDOW"),
            ({"window_candles": 1001}, "INVALID_WINDOW"),
            ({"source_id": "RD-" + "b" * 32}, "SOURCE_NOT_FOUND"),
        ],
    )
    async def test_invalid_requests_are_refused_with_a_code(
        self, changes: dict[str, object], code: str
    ) -> None:
        ws, catalog, _ = workspace()
        request: dict[str, object] = {
            "source_id": SOURCE_ID,
            "timeframes": (M5,),
            "window_candles": 100,
            "pace": PlaybackPace.FAST,
        }
        request.update(changes)
        with pytest.raises(LiveWorkspaceError) as caught:
            await ws.create(**request)  # type: ignore[arg-type]
        assert caught.value.code == code
        assert len(ws) == 0

    async def test_opening_the_workspace_starts_nothing(self) -> None:
        ws, catalog, _ = workspace()

        await ws.sources(offset=0, limit=20)
        ws.sessions()

        assert catalog.opened == 0
        assert len(ws) == 0


class TestTheTimelineIsWhatHappened:
    async def test_events_are_recorded_in_order_with_three_clocks(self) -> None:
        ws, catalog, clock = workspace()
        session_id = await create(ws)
        provider = catalog.providers[0]
        try:
            provider.put(CONNECTED, bar(0), bar(1))
            await settle()

            entries = ws.timeline(session_id, after=0, limit=100).entries
            seqs = [entry.seq for entry in entries]
            assert seqs == sorted(seqs) == list(range(1, len(seqs) + 1))
            confirmed = [e for e in entries if e.kind is TimelineKind.CANDLE_CONFIRMED]
            assert [e.market_open_time for e in confirmed] == [bar(0).open_time, bar(1).open_time]
            assert all(e.recorded_at == clock.now() for e in confirmed)
            assert all(e.market_event_time != e.recorded_at for e in confirmed)
            assert TimelineKind.CONNECTION_CHANGED in [e.kind for e in entries]
        finally:
            await ws.shutdown()

    async def test_a_refused_event_records_its_code_and_no_provider_time(self) -> None:
        ws, catalog, _ = workspace()
        session_id = await create(ws)
        try:
            bad = bar(0)
            forged = replace(bad, high=bad.low, low=bad.high)
            catalog.providers[0].put(CONNECTED, forged)
            await settle()

            (entry,) = [
                e
                for e in ws.timeline(session_id, after=0, limit=100).entries
                if e.kind is TimelineKind.OBSERVATION_REJECTED
            ]
            assert entry.code == "INCONSISTENT_OHLC"
            assert entry.market_open_time is None and entry.market_event_time is None
        finally:
            await ws.shutdown()

    async def test_the_timeline_is_bounded_and_says_so(self) -> None:
        ws, catalog, _ = workspace(timeline_retention=10)
        session_id = await create(ws)
        try:
            catalog.providers[0].put(CONNECTED, *(bar(i) for i in range(30)))
            await settle()

            page = ws.timeline(session_id, after=0, limit=100)
            assert len(page.entries) == 10
            assert page.gap is True
            assert page.oldest_retained == page.cursor - 9
            # The books still hold every candle: trimming the timeline is not
            # trimming the market state.
            assert ws.get(session_id).snapshot.status(M5).book.closed_count == 30
        finally:
            await ws.shutdown()


class TestLifecycle:
    async def test_cancel_stops_the_provider_and_keeps_the_session(self) -> None:
        ws, catalog, _ = workspace()
        session_id = await create(ws)
        catalog.providers[0].put(CONNECTED, bar(0))
        await settle()

        view = await ws.cancel(session_id)

        assert view.lifecycle is Lifecycle.ENDED
        assert view.end_origin is EndOrigin.USER_CANCELLED
        assert view.snapshot.termination_reason is TerminationReason.CANCELLED
        assert catalog.providers[0].closed is True
        assert view.snapshot.status(M5).book.closed_count == 1  # kept for inspection

    async def test_cancel_is_repeatable_and_records_one_end(self) -> None:
        ws, _, _ = workspace()
        session_id = await create(ws)

        await ws.cancel(session_id)
        again = await ws.cancel(session_id)

        assert again.lifecycle is Lifecycle.ENDED
        assert kinds(ws, session_id).count(TimelineKind.SESSION_ENDED) == 1

    async def test_remove_releases_the_slot_and_the_id(self) -> None:
        ws, _, _ = workspace()
        session_id = await create(ws)

        await ws.remove(session_id)

        assert len(ws) == 0
        with pytest.raises(LiveWorkspaceError) as caught:
            ws.get(session_id)
        assert caught.value.kind is WorkspaceErrorKind.NOT_FOUND

    async def test_the_stream_ending_is_recorded_with_its_origin(self) -> None:
        ws, catalog, _ = workspace()
        session_id = await create(ws)
        catalog.providers[0].put(CONNECTED, bar(0), END)
        await settle()

        view = ws.get(session_id)
        assert view.lifecycle is Lifecycle.ENDED
        assert view.end_origin is EndOrigin.STREAM
        assert view.snapshot.termination_reason is TerminationReason.END_OF_STREAM

    async def test_a_stream_that_stops_without_saying_so_is_a_provider_fault(self) -> None:
        ws, catalog, _ = workspace()
        session_id = await create(ws)
        catalog.providers[0].put(CONNECTED, bar(0), None)
        await settle()

        view = ws.get(session_id)
        assert view.snapshot.termination_reason is TerminationReason.PROVIDER_ERROR
        assert view.end_origin is EndOrigin.STREAM

    async def test_a_provider_failure_ends_the_session_and_leaks_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        ws, _, _ = workspace(FakeCatalog(fail_on_subscribe=True))
        session_id = await create(ws)
        await settle()

        view = ws.get(session_id)
        assert view.snapshot.termination_reason is TerminationReason.PROVIDER_ERROR
        assert "sk-live-do-not-leak-0002" not in caplog.text
        assert all(r.exc_info is None for r in caplog.records)

    async def test_the_maximum_duration_ends_a_session_as_a_deadline(self) -> None:
        ws, _, _ = workspace(max_session_seconds=0.01)
        session_id = await create(ws)
        await asyncio.sleep(0.05)
        await settle()

        view = ws.get(session_id)
        assert view.end_origin is EndOrigin.DEADLINE
        assert view.snapshot.termination_reason is TerminationReason.CANCELLED

    async def test_shutdown_cancels_and_awaits_every_task(self) -> None:
        ws, catalog, _ = workspace()
        first, second = await create(ws), await create(ws)
        subscriber = ws.subscribe(first, after=None)
        await settle()  # both streams subscribed

        await ws.shutdown()

        for session_id in (first, second):
            view = ws.get(session_id)
            assert view.lifecycle is Lifecycle.ENDED
            assert view.end_origin is EndOrigin.SHUTDOWN
        assert all(provider.closed for provider in catalog.providers)
        assert subscriber.closed
        received = []
        while (item := await subscriber.next(timeout=0)) is not None:
            received.append(item.kind)
        assert received[-1] is NotificationKind.END  # queued before close, still readable
        with pytest.raises(LiveWorkspaceError):
            await create(ws)

    async def test_a_session_cancelled_before_it_ran_is_cancelled_not_failed(self) -> None:
        ws, _, _ = workspace()
        session_id = await create(ws)

        await ws.shutdown()  # before the task's first step

        view = ws.get(session_id)
        assert view.lifecycle is Lifecycle.ENDED
        assert view.end_origin is EndOrigin.SHUTDOWN
        assert view.snapshot.termination_reason is TerminationReason.CANCELLED

    async def test_no_task_outlives_shutdown(self) -> None:
        ws, _, _ = workspace()
        for _ in range(3):
            await create(ws)
        before = {t for t in asyncio.all_tasks() if t.get_name().startswith("live-")}
        assert len(before) == 3

        await ws.shutdown()

        assert all(task.done() for task in before)


class TestBounds:
    async def test_the_eighth_session_fits_and_the_ninth_is_refused(self) -> None:
        ws, _, _ = workspace()
        try:
            for _ in range(8):
                await create(ws)
            with pytest.raises(LiveWorkspaceError) as caught:
                await create(ws)
            assert caught.value.kind is WorkspaceErrorKind.CAPACITY
        finally:
            await ws.shutdown()

    async def test_an_idle_ended_session_makes_room_but_a_watched_one_does_not(self) -> None:
        ws, _, _ = workspace(max_sessions=2)
        try:
            watched, idle = await create(ws), await create(ws)
            await ws.cancel(watched)
            await ws.cancel(idle)
            subscriber = ws.subscribe(watched, after=None)

            await create(ws)  # evicts `idle`, never `watched`

            assert {view.session_id for view in ws.sessions()} >= {watched}
            with pytest.raises(LiveWorkspaceError):
                ws.get(idle)
            with pytest.raises(LiveWorkspaceError) as caught:
                await create(ws)  # the only ended one is being read
            assert caught.value.kind is WorkspaceErrorKind.CAPACITY
            subscriber.close()
        finally:
            await ws.shutdown()

    async def test_a_running_session_is_never_evicted(self) -> None:
        ws, _, _ = workspace(max_sessions=1)
        try:
            running = await create(ws)
            with pytest.raises(LiveWorkspaceError):
                await create(ws)
            assert ws.get(running).lifecycle is Lifecycle.RUNNING
        finally:
            await ws.shutdown()

    async def test_creation_is_rate_limited_on_the_server_clock(self) -> None:
        ws, _, clock = workspace(creations_per_minute=2)
        try:
            await create(ws)
            await create(ws)
            with pytest.raises(LiveWorkspaceError) as caught:
                await create(ws)
            assert caught.value.kind is WorkspaceErrorKind.RATE_LIMITED

            clock.advance(timedelta(minutes=1))
            await create(ws)
        finally:
            await ws.shutdown()

    async def test_subscribers_are_bounded_per_session_and_in_total(self) -> None:
        ws, _, _ = workspace(max_subscribers_per_session=2, max_subscribers_total=3)
        try:
            first, second = await create(ws), await create(ws)
            ws.subscribe(first, after=None)
            ws.subscribe(first, after=None)
            with pytest.raises(LiveWorkspaceError):
                ws.subscribe(first, after=None)
            ws.subscribe(second, after=None)
            with pytest.raises(LiveWorkspaceError):
                ws.subscribe(second, after=None)
        finally:
            await ws.shutdown()


class TestSubscribers:
    async def test_a_new_subscriber_starts_from_the_authoritative_state(self) -> None:
        ws, _, _ = workspace()
        try:
            session_id = await create(ws)
            subscriber = ws.subscribe(session_id, after=None)

            first = await subscriber.next(timeout=0)

            assert first is not None and first.kind is NotificationKind.STATE
            assert first.view is not None and first.view.session_id == session_id
        finally:
            await ws.shutdown()

    async def test_overflow_demands_a_resync_and_loses_no_observation(self) -> None:
        ws, catalog, _ = workspace(subscriber_queue=3)
        try:
            session_id = await create(ws)
            subscriber = ws.subscribe(session_id, after=None)
            catalog.providers[0].put(CONNECTED, *(bar(i) for i in range(20)))
            await settle()

            first = await subscriber.next(timeout=0)

            assert first is not None and first.kind is NotificationKind.RESYNC_REQUIRED
            assert first.reason is ResyncReason.SUBSCRIBER_OVERFLOW
            assert subscriber.overflows >= 1
            assert len(subscriber) <= 3
            assert ws.get(session_id).snapshot.status(M5).book.closed_count == 20
        finally:
            await ws.shutdown()

    async def test_a_retained_cursor_is_caught_up_in_order(self) -> None:
        ws, catalog, _ = workspace()
        try:
            session_id = await create(ws)
            catalog.providers[0].put(CONNECTED, bar(0), bar(1))
            await settle()
            cursor = ws.timeline(session_id, after=0, limit=100).cursor

            subscriber = ws.subscribe(session_id, after=2)
            received = []
            while (item := await subscriber.next(timeout=0)) is not None:
                received.append(item)

            timeline = [item.cursor for item in received if item.kind is NotificationKind.TIMELINE]
            assert timeline == list(range(3, cursor + 1))
            assert received[-1].kind is NotificationKind.STATE
        finally:
            await ws.shutdown()

    @pytest.mark.parametrize(
        ("after", "reason"),
        [(10_000, ResyncReason.CURSOR_UNKNOWN), (1, ResyncReason.CURSOR_NOT_RETAINED)],
    )
    async def test_an_unusable_cursor_is_told_to_resync(
        self, after: int, reason: ResyncReason
    ) -> None:
        ws, catalog, _ = workspace(timeline_retention=5)
        try:
            session_id = await create(ws)
            catalog.providers[0].put(CONNECTED, *(bar(i) for i in range(10)))
            await settle()

            subscriber = ws.subscribe(session_id, after=after)
            first = await subscriber.next(timeout=0)

            assert first is not None and first.kind is NotificationKind.RESYNC_REQUIRED
            assert first.reason is reason
        finally:
            await ws.shutdown()

    async def test_a_subscriber_leaving_does_not_end_the_session(self) -> None:
        ws, catalog, _ = workspace()
        try:
            session_id = await create(ws)
            reader, other = (
                ws.subscribe(session_id, after=None),
                ws.subscribe(session_id, after=None),
            )

            reader.close()
            catalog.providers[0].put(CONNECTED, bar(0))
            await settle()

            assert ws.get(session_id).lifecycle is Lifecycle.RUNNING
            assert ws.get(session_id).subscribers == 1
            assert not other.closed
        finally:
            await ws.shutdown()

    async def test_every_notification_belongs_to_its_session(self) -> None:
        ws, catalog, _ = workspace()
        try:
            a, b = await create(ws), await create(ws)
            reader = ws.subscribe(a, after=None)
            catalog.providers[1].put(CONNECTED, bar(0))  # only session b moves
            catalog.providers[0].put(CONNECTED)
            await settle()

            items = []
            while (item := await reader.next(timeout=0)) is not None:
                items.append(item)
            assert items and all(item.session_id == a for item in items)
            assert ws.get(b).snapshot.status(M5).book.closed_count == 1
        finally:
            await ws.shutdown()


class TestHeartbeatAndFreshness:
    async def test_polling_records_staleness_and_changes_no_candle(self) -> None:
        ws, catalog, clock = workspace()
        try:
            session_id = await create(ws)
            catalog.providers[0].put(CONNECTED, bar(0))
            await settle()
            before = ws.get(session_id).snapshot.status(M5).book
            assert ws.get(session_id).snapshot.status(M5).freshness is Freshness.FRESH

            clock.advance(timedelta(hours=1))
            ws.poll(session_id)

            after = ws.get(session_id).snapshot.status(M5)
            assert after.freshness is Freshness.STALE
            assert after.book.last_received_at == before.last_received_at
            assert after.book.closed_count == before.closed_count
            changes = [
                e
                for e in ws.timeline(session_id, after=0, limit=100).entries
                if e.kind is TimelineKind.TIMEFRAME_STATUS_CHANGED and e.after
            ]
            assert any("STALE" in (e.after or "") for e in changes)
        finally:
            await ws.shutdown()

    async def test_a_historical_candle_received_now_is_fresh_and_historical(self) -> None:
        ws, catalog, _ = workspace()
        try:
            session_id = await create(ws)
            catalog.providers[0].put(CONNECTED, bar(0))
            await settle()

            view = ws.get(session_id)
            status = view.snapshot.status(M5)
            assert status.freshness is Freshness.FRESH
            assert view.snapshot.market_currency is MarketCurrency.HISTORICAL
            assert RECEIVE_START - status.book.last_event_time > timedelta(days=100)  # type: ignore[operator]
        finally:
            await ws.shutdown()

    async def test_connected_does_not_mean_available(self) -> None:
        ws, catalog, _ = workspace()
        try:
            session_id = await create(ws)
            catalog.providers[0].put(CONNECTED)
            await settle()

            view = ws.get(session_id)
            assert view.snapshot.connection is ConnectionState.CONNECTED
            assert view.snapshot.available == ()
        finally:
            await ws.shutdown()


class TestAnalysis:
    async def test_no_event_runs_an_analysis(self) -> None:
        ws, catalog, _ = workspace()
        try:
            session_id = await create(ws)
            catalog.providers[0].put(CONNECTED, *fixture_bars(288))
            await settle()

            assert ws.get(session_id).analysis.analyses_run == 0
        finally:
            await ws.shutdown()

    async def test_concurrent_requests_compute_once(self) -> None:
        ws, catalog, _ = workspace()
        try:
            session_id = await create(ws)
            catalog.providers[0].put(CONNECTED, *fixture_bars(288))
            await settle()

            first, second = await asyncio.gather(
                ws.analyse(session_id, account=None, risk_policy=None),
                ws.analyse(session_id, account=None, risk_policy=None),
            )

            assert ws.get(session_id).analysis.analyses_run == 1
            assert sorted([first.reused, second.reused]) == [False, True]
            assert first.analysis.market_currency is MarketCurrency.HISTORICAL
        finally:
            await ws.shutdown()

    async def test_nothing_available_is_a_typed_refusal_with_reasons(self) -> None:
        ws, catalog, _ = workspace()
        try:
            session_id = await create(ws)
            catalog.providers[0].put(CONNECTED)
            await settle()

            with pytest.raises(LiveWorkspaceError) as caught:
                await ws.analyse(session_id, account=None, risk_policy=None)

            assert caught.value.kind is WorkspaceErrorKind.ANALYSIS_UNAVAILABLE
            assert M5 in caught.value.reasons
            assert TimelineKind.ANALYSIS_UNAVAILABLE in kinds(ws, session_id)
        finally:
            await ws.shutdown()

    async def test_the_workspace_has_no_way_to_trade(self) -> None:
        ws, _, _ = workspace()
        public = {name for name in dir(ws) if not name.startswith("_")}
        assert not any(
            word in name for name in public for word in ("position", "order", "trade", "paper")
        )
