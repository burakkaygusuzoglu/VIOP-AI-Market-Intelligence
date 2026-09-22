"""The SSE route's frames, read one at a time (Phase 13 Part 2A).

The route function is called directly and its body iterator read frame by
frame, because an HTTP test client buffers a response to its end - and a live
stream's defining property is that it does not end until the session does.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.requests import Request

from app.api.routes.live import stream_events
from app.application.live.workspace import LiveWorkspace
from tests.unit.live.support import M5, at
from tests.unit.live.workspace_support import CONNECTED, END, bar, create, settle, workspace

pytestmark = pytest.mark.unit


def request(heartbeat: float = 0.01) -> Request:
    app = SimpleNamespace(state=SimpleNamespace(live_heartbeat_seconds=heartbeat))
    return Request(
        {
            "type": "http",
            "app": app,
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
        }
    )


async def open_stream(
    ws: LiveWorkspace,
    session_id: str,
    *,
    after: int | None = None,
    last_event_id: str | None = None,
    heartbeat: float = 0.01,
) -> StreamingResponse:
    return await stream_events(
        session_id=session_id,
        request=request(heartbeat),
        workspace=ws,
        clock=ws._clock,  # noqa: SLF001 - the workspace's own receive clock
        after=after,
        last_event_id=last_event_id,
    )


async def frame(response: StreamingResponse) -> str:
    iterator = cast("AsyncGenerator[str | bytes, None]", response.body_iterator)
    chunk = await asyncio.wait_for(anext(iterator), timeout=2)
    return chunk if isinstance(chunk, str) else bytes(chunk).decode()


def data_of(text: str) -> dict[str, Any]:
    line = next(item for item in text.splitlines() if item.startswith("data: "))
    parsed = json.loads(line.removeprefix("data: "))
    assert isinstance(parsed, dict)
    return parsed


def id_of(text: str) -> str | None:
    line = next((item for item in text.splitlines() if item.startswith("id: ")), None)
    return None if line is None else line.removeprefix("id: ")


async def close(response: StreamingResponse) -> None:
    await cast("AsyncGenerator[str | bytes, None]", response.body_iterator).aclose()


class TestTheStream:
    async def test_it_opens_with_a_retry_hint_and_the_authoritative_state(self) -> None:
        ws, _, _ = workspace()
        try:
            session_id = await create(ws)
            response = await open_stream(ws, session_id)

            assert (await frame(response)).startswith("retry: ")
            state = data_of(await frame(response))
            assert state["kind"] == "STATE"
            assert state["session_id"] == session_id
            assert state["session"]["identity"]["provenance"] == "SIMULATED_HISTORICAL_STREAM"
            assert state["session"]["identity"]["market_currency"] == "HISTORICAL"
            assert response.headers["x-accel-buffering"] == "no"
            assert response.media_type == "text/event-stream"
            await close(response)
        finally:
            await ws.shutdown()

    async def test_a_candle_arrives_as_a_timeline_event_with_its_id(self) -> None:
        ws, catalog, _ = workspace()
        try:
            session_id = await create(ws)
            response = await open_stream(ws, session_id, heartbeat=5)
            await frame(response)
            await frame(response)  # STATE

            catalog.providers[0].put(CONNECTED, bar(0))
            await settle()
            seen = []
            for _ in range(8):
                text = await frame(response)
                payload = data_of(text)
                seen.append(payload)
                assert id_of(text) == str(payload["cursor"])
                if payload["entry"]["kind"] == "CANDLE_CONFIRMED":
                    break
            confirmed = seen[-1]
            assert confirmed["entry"]["timeframe"] == "5M"
            assert confirmed["entry"]["market_open_time"] == at(0).isoformat()
            assert confirmed["session"]["timeframes"][0]["closed_count"] == 1
            await close(response)
        finally:
            await ws.shutdown()


class TestHeartbeat:
    async def test_a_heartbeat_is_transport_only(self) -> None:
        ws, catalog, _ = workspace()
        try:
            session_id = await create(ws)
            catalog.providers[0].put(CONNECTED, bar(0))
            await settle()
            before = ws.get(session_id).snapshot.status(M5).book
            response = await open_stream(ws, session_id, after=ws.poll(session_id))
            await frame(response)
            await frame(response)  # STATE

            beats = [await frame(response) for _ in range(3)]

            for text in beats:
                payload = data_of(text)
                assert payload["kind"] == "HEARTBEAT"
                assert payload["event_id"] is None
                assert id_of(text) is None  # cannot move Last-Event-ID
                assert payload["session"] is None and payload["entry"] is None
            after = ws.get(session_id).snapshot.status(M5).book
            assert after.last_received_at == before.last_received_at
            assert after.closed_count == before.closed_count
            assert after.version == before.version
            await close(response)
        finally:
            await ws.shutdown()


class TestEndAndDisconnect:
    async def test_the_stream_closes_after_the_session_ends(self) -> None:
        ws, catalog, _ = workspace()
        try:
            session_id = await create(ws)
            response = await open_stream(ws, session_id, heartbeat=5)
            catalog.providers[0].put(CONNECTED, bar(0), END)
            await settle()

            kinds = []
            while True:
                try:
                    text = await frame(response)
                except StopAsyncIteration:
                    break
                if text.startswith("data: ") or "\ndata: " in text:
                    kinds.append(data_of(text)["kind"])
            assert kinds[-1] == "END"
        finally:
            await ws.shutdown()

    async def test_a_client_disconnecting_ends_only_its_own_subscription(self) -> None:
        ws, _, _ = workspace()
        try:
            session_id = await create(ws)
            response = await open_stream(ws, session_id)
            await frame(response)
            await frame(response)
            assert ws.get(session_id).subscribers == 1

            await close(response)

            view = ws.get(session_id)
            assert view.subscribers == 0
            assert view.lifecycle.value == "RUNNING"
        finally:
            await ws.shutdown()


class TestResume:
    async def test_last_event_id_resumes_from_the_retained_timeline(self) -> None:
        ws, catalog, _ = workspace()
        try:
            session_id = await create(ws)
            catalog.providers[0].put(CONNECTED, bar(0), bar(1))
            await settle()

            response = await open_stream(ws, session_id, last_event_id="1", heartbeat=5)
            await frame(response)
            ids = []
            while True:
                payload = data_of(await frame(response))
                if payload["kind"] == "STATE":
                    break
                ids.append(payload["event_id"])
            assert ids == list(range(2, ws.poll(session_id) + 1))
            await close(response)
        finally:
            await ws.shutdown()

    async def test_a_cursor_from_before_a_restart_is_told_to_resync(self) -> None:
        ws, _, _ = workspace()
        try:
            session_id = await create(ws)
            response = await open_stream(ws, session_id, after=999_999)
            await frame(response)

            payload = data_of(await frame(response))
            assert payload["kind"] == "RESYNC_REQUIRED"
            assert payload["reason"] == "CURSOR_UNKNOWN"
            await close(response)
        finally:
            await ws.shutdown()

    async def test_an_unknown_session_is_a_404_before_any_stream(self) -> None:
        ws, _, _ = workspace()
        with pytest.raises(HTTPException) as caught:
            await open_stream(ws, "LS-" + "0" * 24)
        assert caught.value.status_code == 404


class TestOverflowOverTheWire:
    async def test_the_stream_ends_after_an_overflow_resync(self) -> None:
        ws, catalog, _ = workspace(subscriber_queue=3)
        try:
            session_id = await create(ws)
            response = await open_stream(ws, session_id, heartbeat=5)
            await frame(response)  # retry hint; STATE is still queued
            catalog.providers[0].put(CONNECTED, *(bar(i) for i in range(10)))
            await settle()

            kinds = []
            while True:
                try:
                    text = await frame(response)
                except StopAsyncIteration:
                    break
                kinds.append(data_of(text)["kind"])
            assert kinds == ["RESYNC_REQUIRED"]
            assert ws.get(session_id).lifecycle.value == "RUNNING"
        finally:
            await ws.shutdown()
