"""Races around the live workspace, driven by explicit gates (Phase 13 Part 2B).

Each test holds one side of a race at a known point with an ``asyncio.Event``
and lets the other side run, so the interleaving is chosen by the test rather
than by timing. The property is always the same: no cap is bypassed, no task
outlives its owner, and no authoritative change is skipped silently.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from app.application.live.catalog import OpenedSource
from app.application.live.workspace import (
    Lifecycle,
    LiveWorkspaceError,
    NotificationKind,
    ResyncReason,
    WorkspaceErrorKind,
)
from app.domain.common.enums import Timeframe
from app.domain.live.events import StreamItem
from tests.unit.live.support import M5
from tests.unit.live.workspace_support import (
    CONNECTED,
    FakeCatalog,
    bar,
    create,
    settle,
    workspace,
)

pytestmark = pytest.mark.unit


class GatedCatalog(FakeCatalog):
    """Opening a source waits until the test opens the gate."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def open(self, source_id: str, **kwargs: Any) -> OpenedSource:
        self.entered.set()
        await self.gate.wait()
        return await super().open(source_id, **kwargs)


class ExplodingCatalog(FakeCatalog):
    async def open(self, source_id: str, **kwargs: Any) -> OpenedSource:
        raise RuntimeError(r"postgresql://u:sk-live-do-not-leak-0003@db C:\secret\path.txt")


async def drain(subscriber: Any) -> list[Any]:
    items = []
    while (item := await subscriber.next(timeout=0)) is not None:
        items.append(item)
    return items


class TestCreationRaces:
    async def test_concurrent_creations_cannot_bypass_the_cap(self) -> None:
        ws, _, _ = workspace(max_sessions=8)
        try:
            results = await asyncio.gather(*(create(ws) for _ in range(12)), return_exceptions=True)
            created = [r for r in results if isinstance(r, str)]
            refused = [r for r in results if isinstance(r, LiveWorkspaceError)]
            assert len(created) == 8
            assert len(refused) == 4
            assert all(r.kind is WorkspaceErrorKind.CAPACITY for r in refused)
            assert len(ws) == 8
        finally:
            await ws.shutdown()

    async def test_a_creation_in_flight_during_shutdown_starts_no_task(self) -> None:
        catalog = GatedCatalog()
        ws, _, _ = workspace(catalog)
        creating = asyncio.create_task(create(ws))
        await catalog.entered.wait()  # the creation is reading its source

        stopping = asyncio.create_task(ws.shutdown())
        await asyncio.sleep(0)
        catalog.gate.set()
        results = await asyncio.gather(creating, stopping, return_exceptions=True)

        assert isinstance(results[0], LiveWorkspaceError)
        assert results[0].code == "LIVE_SHUTTING_DOWN"
        assert len(ws) == 0
        assert not [t for t in asyncio.all_tasks() if t.get_name().startswith("live-")]

    async def test_a_creation_after_shutdown_is_refused(self) -> None:
        ws, _, _ = workspace()
        await ws.shutdown()
        with pytest.raises(LiveWorkspaceError) as caught:
            await create(ws)
        assert caught.value.code == "LIVE_SHUTTING_DOWN"

    async def test_create_then_cancel_immediately_leaves_no_running_provider(self) -> None:
        ws, catalog, _ = workspace()
        session_id = await create(ws)

        view = await ws.cancel(session_id)  # before the task ran a step

        assert view.lifecycle is Lifecycle.ENDED
        await settle()
        assert not [
            t for t in asyncio.all_tasks() if t.get_name() == f"live-{session_id}" and not t.done()
        ]
        assert catalog.providers[0].closed or catalog.providers[0].queue.empty()

    async def test_an_undescribed_source_failure_is_typed_and_leaks_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        ws, _, _ = workspace(ExplodingCatalog())

        with pytest.raises(LiveWorkspaceError) as caught:
            await create(ws)

        assert caught.value.kind is WorkspaceErrorKind.UNAVAILABLE
        assert caught.value.code == "SOURCE_OPEN_FAILED"
        assert "sk-live-do-not-leak-0003" not in caplog.text
        assert "secret" not in caplog.text.lower().replace("secrets", "")
        assert all(record.exc_info is None for record in caplog.records)
        assert len(ws) == 0


class TestTheSnapshotSubscriptionRace:
    async def test_events_between_snapshot_and_subscribe_are_not_lost(self) -> None:
        """1. snapshot at cursor N; 2. events N+1, N+2 happen; 3. subscribe after N."""
        ws, catalog, _ = workspace()
        try:
            session_id = await create(ws)
            catalog.providers[0].put(CONNECTED)
            await settle()
            snapshot_cursor = ws.get(session_id).cursor

            catalog.providers[0].put(bar(0), bar(1))
            await settle()
            subscriber = ws.subscribe(session_id, after=snapshot_cursor)
            items = await drain(subscriber)

            timeline = [i for i in items if i.kind is NotificationKind.TIMELINE]
            assert [i.cursor for i in timeline] == list(
                range(snapshot_cursor + 1, ws.poll(session_id) + 1)
            )
            confirmed = [
                i.entry.market_open_time
                for i in timeline
                if i.entry is not None and i.entry.kind.value == "CANDLE_CONFIRMED"
            ]
            assert confirmed == [bar(0).open_time, bar(1).open_time]
            assert items[-1].kind is NotificationKind.STATE
        finally:
            await ws.shutdown()

    async def test_an_event_during_subscription_is_queued_not_skipped(self) -> None:
        ws, catalog, _ = workspace()
        try:
            session_id = await create(ws)
            catalog.providers[0].put(CONNECTED)
            await settle()
            subscriber = ws.subscribe(session_id, after=ws.poll(session_id))

            catalog.providers[0].put(bar(0))
            await settle()
            items = await drain(subscriber)

            cursors = [i.cursor for i in items if i.kind is NotificationKind.TIMELINE]
            assert cursors == sorted(cursors)
            assert len(cursors) == len(set(cursors))
            assert any(
                i.entry is not None and i.entry.kind.value == "CANDLE_CONFIRMED" for i in items
            )
        finally:
            await ws.shutdown()

    async def test_a_cursor_from_another_session_cannot_be_resumed_silently(self) -> None:
        ws, catalog, _ = workspace()
        try:
            busy, quiet = await create(ws), await create(ws)
            catalog.providers[0].put(CONNECTED, *(bar(i) for i in range(10)))
            await settle()

            foreign = ws.poll(busy)  # far beyond anything `quiet` issued
            first = await ws.subscribe(quiet, after=foreign).next(timeout=0)

            assert first is not None and first.kind is NotificationKind.RESYNC_REQUIRED
            assert first.reason is ResyncReason.CURSOR_UNKNOWN
        finally:
            await ws.shutdown()


class TestSubscribersUnderLoad:
    async def test_a_reader_leaving_mid_fan_out_does_not_disturb_the_others(self) -> None:
        ws, catalog, _ = workspace(max_subscribers_per_session=4)
        try:
            session_id = await create(ws)
            readers = [ws.subscribe(session_id, after=None) for _ in range(4)]
            for reader in readers:
                await drain(reader)
            original_push = readers[1].push

            def leave_then_push(item: Any) -> None:
                original_push(item)
                readers[2].close()  # a neighbour leaves while fan-out is running

            readers[1].push = leave_then_push  # type: ignore[method-assign]
            catalog.providers[0].put(CONNECTED, bar(0))
            await settle()

            assert len(await drain(readers[0])) > 0
            assert len(await drain(readers[3])) > 0
            assert ws.get(session_id).subscribers == 3
            assert ws.get(session_id).lifecycle is Lifecycle.RUNNING
        finally:
            await ws.shutdown()

    async def test_a_slow_reader_does_not_starve_a_fast_one(self) -> None:
        ws, catalog, _ = workspace(subscriber_queue=8)
        try:
            session_id = await create(ws)
            slow = ws.subscribe(session_id, after=None)
            fast = ws.subscribe(session_id, after=None)
            received: list[Any] = []
            catalog.providers[0].put(CONNECTED)
            for index in range(40):
                catalog.providers[0].put(bar(index))
                await settle()
                received.extend(await drain(fast))

            assert slow.overflows > 0
            assert fast.overflows == 0
            kinds = [i.kind for i in received]
            assert NotificationKind.RESYNC_REQUIRED not in kinds
            assert ws.get(session_id).snapshot.status(M5).book.closed_count == 40
        finally:
            await ws.shutdown()

    async def test_a_reader_reconnecting_after_overflow_resyncs_and_then_continues(
        self,
    ) -> None:
        ws, catalog, _ = workspace(subscriber_queue=4, timeline_retention=10)
        try:
            session_id = await create(ws)
            slow = ws.subscribe(session_id, after=None)
            catalog.providers[0].put(CONNECTED, *(bar(i) for i in range(30)))
            await settle()
            first = await slow.next(timeout=0)
            assert first is not None and first.kind is NotificationKind.RESYNC_REQUIRED
            slow.close()

            # The client re-reads the snapshot, then resumes from its cursor.
            cursor = ws.get(session_id).cursor
            again = ws.subscribe(session_id, after=cursor)
            items = await drain(again)
            assert [i.kind for i in items] == [NotificationKind.STATE]
            catalog.providers[0].put(bar(30))
            await settle()
            later = await drain(again)
            assert later and later[0].cursor == cursor + 1
        finally:
            await ws.shutdown()

    async def test_sixteen_readers_fit_and_the_seventeenth_does_not(self) -> None:
        ws, _, _ = workspace()
        try:
            sessions = [await create(ws) for _ in range(4)]
            readers = [ws.subscribe(s, after=None) for s in sessions for _ in range(4)]
            assert len(readers) == 16
            with pytest.raises(LiveWorkspaceError) as caught:
                ws.subscribe(sessions[0], after=None)
            assert caught.value.code == "LIVE_SUBSCRIBERS_FULL"
            readers[0].close()
            ws.subscribe(sessions[0], after=None)  # a slot came back
        finally:
            await ws.shutdown()

    async def test_the_last_reader_leaving_keeps_the_session_running(self) -> None:
        ws, catalog, _ = workspace()
        try:
            session_id = await create(ws)
            reader = ws.subscribe(session_id, after=None)
            reader.close()
            catalog.providers[0].put(CONNECTED, bar(0))
            await settle()

            view = ws.get(session_id)
            assert view.subscribers == 0
            assert view.lifecycle is Lifecycle.RUNNING
            assert view.snapshot.status(M5).book.closed_count == 1
        finally:
            await ws.shutdown()

    async def test_subscribing_during_shutdown_ends_cleanly(self) -> None:
        ws, _, _ = workspace()
        session_id = await create(ws)
        await ws.shutdown()

        reader = ws.subscribe(session_id, after=None)
        kinds = [i.kind for i in await drain(reader)]

        assert kinds[-1] is NotificationKind.END


class TestAnalysisConcurrency:
    async def test_at_most_two_analyses_run_at_once_and_one_per_session(self) -> None:
        ws, _, _ = workspace(max_concurrent_analyses=2)
        try:
            sessions = [await create(ws) for _ in range(3)]
            running = 0
            peak = 0
            per_session: dict[str, int] = {}
            release = asyncio.Event()

            def gated(session_id: str) -> Any:
                async def analysis(**_: object) -> Any:
                    nonlocal running, peak
                    running += 1
                    per_session[session_id] = per_session.get(session_id, 0) + 1
                    peak = max(peak, running)
                    assert per_session[session_id] == 1, "two analyses in one session"
                    await release.wait()
                    running -= 1
                    per_session[session_id] -= 1
                    raise_unavailable()

                return analysis

            for session_id in sessions:
                ws._entries[session_id].session.confirmed_analysis = gated(session_id)  # type: ignore[method-assign]  # noqa: SLF001

            calls = [
                asyncio.create_task(ws.analyse(s, account=None, risk_policy=None))
                for s in [*sessions, sessions[0]]
            ]
            for _ in range(10):
                await asyncio.sleep(0)
            assert peak == 2
            release.set()
            await asyncio.gather(*calls, return_exceptions=True)
            assert peak == 2
        finally:
            await ws.shutdown()


def raise_unavailable() -> None:
    from app.application.live.session import LiveAnalysisUnavailableError

    raise LiveAnalysisUnavailableError("NO_TIMEFRAME_AVAILABLE", {Timeframe.M5: ("gated",)})


class TestOverflowEndsTheReader:
    async def test_an_overflowing_reader_gets_one_resync_and_is_closed(self) -> None:
        ws, catalog, _ = workspace(subscriber_queue=4)
        try:
            session_id = await create(ws)
            slow = ws.subscribe(session_id, after=None)
            other = ws.subscribe(session_id, after=None)
            await drain(other)
            steps: list[StreamItem] = [CONNECTED, *(bar(i) for i in range(3))]
            for step in steps:
                catalog.providers[0].put(step)
                await settle()
                await drain(other)  # a reader that keeps up

            items = await drain(slow)

            assert [i.kind for i in items] == [NotificationKind.RESYNC_REQUIRED]
            assert items[0].reason is ResyncReason.SUBSCRIBER_OVERFLOW
            assert slow.closed
            assert ws.get(session_id).subscribers == 1  # deregistered; the other stays
            catalog.providers[0].put(bar(3))
            await settle()
            assert await drain(slow) == []  # nothing that would skip ahead
            assert await drain(other)  # the reader that kept up still receives
            assert ws.get(session_id).snapshot.status(M5).book.closed_count == 4
        finally:
            await ws.shutdown()

    async def test_a_catch_up_larger_than_the_queue_is_refused_up_front(self) -> None:
        ws, catalog, _ = workspace(subscriber_queue=8)
        try:
            session_id = await create(ws)
            catalog.providers[0].put(CONNECTED, *(bar(i) for i in range(20)))
            await settle()

            reader = ws.subscribe(session_id, after=1)
            items = await drain(reader)

            assert [i.kind for i in items] == [
                NotificationKind.RESYNC_REQUIRED,
                NotificationKind.STATE,
            ]
            assert items[0].reason is ResyncReason.CATCH_UP_TOO_LARGE
            assert not reader.closed
            assert items[1].cursor == ws.poll(session_id)
        finally:
            await ws.shutdown()
