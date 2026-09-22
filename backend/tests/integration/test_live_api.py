"""The Live Intelligence API end to end, against real PostgreSQL (Phase 13 Part 2A).

The composed application, the real routes, the real replay store and the real
stored-dataset playback. Datasets are created the way a person creates them -
through the replay upload - and then streamed.

Two test compositions exist, both stated here:

* the **production composition**, untouched, where every assertion about
  wiring, provenance and refusal is made;
* a **fast playback**, where the same catalog class plays the same stored
  candles with a one-millisecond gap - and, for the analysis tests, holds the
  stream open after the last candle instead of ending it, so there is a
  connected, fresh stream to analyse. Market data is unchanged in both.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.live.dataset_playback import PacedPlaybackProvider, ReplayDatasetCatalog
from app.adapters.live.mock_stream import Advance, Signal
from app.adapters.market_data.csv_provider import CsvCandleTextParser
from app.adapters.persistence.database import Database
from app.application.live.catalog import OpenedSource, PlaybackPace
from app.application.live.workspace import LiveWorkspace
from app.core.config import Settings
from app.domain.common.enums import Timeframe
from app.domain.live.events import SignalKind
from app.main import create_app
from tests.factories_replay import BASE, csv_of, dataset, five_minute
from tests.integration.paper_support import truncate
from tests.integration.replay_support import upload_payload

pytestmark = pytest.mark.integration

LIVE = "/api/live"
FAST = dict.fromkeys(PlaybackPace, 0.001)


class HeldOpenCatalog(ReplayDatasetCatalog):
    """The real catalog, whose stream stays connected after its last candle
    instead of ending - so a test has a live, fresh stream to analyse."""

    async def open(self, source_id: str, **kwargs: Any) -> OpenedSource:
        opened = await super().open(source_id, **kwargs)
        steps = [
            step
            for step in opened.provider._steps  # type: ignore[attr-defined]  # noqa: SLF001
            if not (isinstance(step, Signal) and step.kind is SignalKind.END_OF_STREAM)
        ]
        steps.append(Advance(timedelta(hours=1)))
        return replace(opened, provider=PacedPlaybackProvider(steps))


@pytest.fixture
async def clean(migrated: Settings) -> AsyncIterator[Settings]:
    database = Database(migrated.sqlalchemy_url)
    try:
        await truncate(database)
    finally:
        await database.dispose()
    yield migrated


def open_client(settings: Settings, *, live: bool = True) -> TestClient:
    """The composed app, opted into the live workspace (the documented local
    configuration), addressed by a loopback host name as a browser would be."""
    configured = settings.model_copy(update={"live_simulation_enabled": live})
    client = TestClient(create_app(configured), base_url="http://localhost")
    client.__enter__()
    return client


def use_catalog(client: TestClient, catalog_class: type[ReplayDatasetCatalog]) -> LiveWorkspace:
    """Swap in a fast-playback workspace over the *same* store and clock."""
    app: Any = client.app
    original: LiveWorkspace = app.state.live_workspace
    client.portal.call(original.shutdown)  # type: ignore[union-attr]
    workspace = LiveWorkspace(
        catalog=catalog_class(app.state.replay_store, spacing=FAST),
        clock=app.state.clock,
        parser=CsvCandleTextParser(),
    )
    app.state.live_workspace = workspace
    return workspace


@pytest.fixture
def api(clean: Settings) -> Iterator[TestClient]:
    client = open_client(clean)
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


@pytest.fixture
def fast(api: TestClient) -> Iterator[TestClient]:
    workspace = use_catalog(api, ReplayDatasetCatalog)
    try:
        yield api
    finally:
        api.portal.call(workspace.shutdown)  # type: ignore[union-attr]


@pytest.fixture
def held(api: TestClient) -> Iterator[TestClient]:
    workspace = use_catalog(api, HeldOpenCatalog)
    try:
        yield api
    finally:
        api.portal.call(workspace.shutdown)  # type: ignore[union-attr]


def upload(api: TestClient, key: str, **changes: Any) -> str:
    body = upload_payload(dataset(288), replay_start=BASE + timedelta(hours=1))
    body.update(changes)
    response = api.post("/api/replay/sessions", json=body, headers={"Idempotency-Key": key})
    assert response.status_code == 201, response.text
    dataset_id = response.json()["dataset"]["dataset_id"]
    assert isinstance(dataset_id, str)
    return dataset_id


def create(api: TestClient, source_id: str, **changes: Any) -> Any:
    body: dict[str, Any] = {
        "source_id": source_id,
        "timeframes": ["5M", "15M", "1H"],
        "window_candles": 288,
        "pace": "FAST",
    }
    body.update(changes)
    return api.post(f"{LIVE}/sessions", json=body)


def wait_for(api: TestClient, session_id: str, predicate: Any, timeout: float = 10.0) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        body = api.get(f"{LIVE}/sessions/{session_id}").json()
        if predicate(body):
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not reached: {body['connection']}")
        time.sleep(0.02)


def every_string(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [text for value in node.values() for text in every_string(value)]
    if isinstance(node, list):
        return [text for value in node for text in every_string(value)]
    return []


# ----------------------------------------------------------------------
# Composition and capability
# ----------------------------------------------------------------------


class TestComposition:
    def test_the_capability_states_what_this_is_and_is_not(self, api: TestClient) -> None:
        body = api.get(f"{LIVE}/capability").json()

        assert body["state"] == "AVAILABLE"
        assert body["provenance"] == "SIMULATED_HISTORICAL_STREAM"
        assert body["market_currency"] == "HISTORICAL"
        assert body["real_exchange_connected"] is False
        assert body["execution"] == "DISABLED"
        assert body["transport"] == "SSE"
        assert body["scope"] == "LOCAL_DEVELOPMENT"
        assert body["limits"]["max_sessions"] == 8
        assert body["limits"]["max_closed_candles"] == 2500
        assert "Borsaya bağlı değildir" in body["detail"]

    def test_production_composes_no_live_workspace(self, clean: Settings) -> None:
        # Opted in, and still refused: production ignores the opt-in.
        client = open_client(clean.model_copy(update={"app_env": "production"}))
        try:
            capability = client.get(f"{LIVE}/capability").json()
            assert capability["state"] == "DISABLED"
            assert capability["limits"] is None
            for method, path in (
                ("GET", f"{LIVE}/sources"),
                ("GET", f"{LIVE}/sessions"),
                ("POST", f"{LIVE}/sessions"),
            ):
                response = client.request(
                    method,
                    path,
                    json=None
                    if method == "GET"
                    else {"source_id": "RD-" + "a" * 32, "timeframes": ["5M"]},
                )
                assert response.status_code == 503, path
                assert response.json()["detail"]["code"] == "LIVE_DISABLED"
        finally:
            client.__exit__(None, None, None)

    def test_opening_the_workspace_creates_no_session(self, api: TestClient) -> None:
        upload(api, "live-open-0000000001")

        api.get(f"{LIVE}/capability")
        api.get(f"{LIVE}/sources")

        assert api.get(f"{LIVE}/sessions").json()["items"] == []

    def test_the_production_metadata_boundary_is_unchanged(self, api: TestClient) -> None:
        source = upload(api, "live-meta-0000000001")
        created = create(api, source, pace="SLOW")
        assert created.status_code == 201

        backtest = api.get("/api/backtest/capability").json()
        assert backtest["financial_execution_available"] is False
        assert backtest["refusal_code"] == "PRODUCT_METADATA_UNAVAILABLE"
        assert api.app.state.product_resolver is None  # type: ignore[attr-defined]
        assert created.json()["identity"]["contract_identity"] == "NOT_ESTABLISHED"
        api.post(f"{LIVE}/sessions/{created.json()['id']}/cancel")


# ----------------------------------------------------------------------
# Sources
# ----------------------------------------------------------------------


class TestSources:
    def test_a_stored_dataset_is_a_labelled_source_without_contract_facts(
        self, api: TestClient
    ) -> None:
        source = upload(api, "live-src-00000000001")

        (item,) = api.get(f"{LIVE}/sources").json()["items"]

        assert item["source_id"] == source
        assert item["origin"] == "USER_SUPPLIED_HISTORICAL"
        assert item["contract_identity"] == "NOT_ESTABLISHED"
        assert item["streamable"] is True
        assert {tf["timeframe"] for tf in item["timeframes"]} == {"5M", "15M", "1H"}
        for banned in ("multiplier", "tick", "margin", "expiry"):
            assert banned not in str(item).lower()

    def test_a_hostile_symbol_is_listed_as_not_streamable_and_refused(
        self, fast: TestClient
    ) -> None:
        source = upload(fast, "live-hostile-000001", symbol="<script>X</script>")

        (item,) = fast.get(f"{LIVE}/sources").json()["items"]
        assert item["streamable"] is False
        assert item["refusal"]

        response = create(fast, source)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "SOURCE_NOT_STREAMABLE"


# ----------------------------------------------------------------------
# Strict input
# ----------------------------------------------------------------------


class TestStrictInput:
    @pytest.mark.parametrize(
        "forged",
        [
            {"provenance": "LIVE_EXCHANGE_FEED"},
            {"market_currency": "CURRENT"},
            {"multiplier": "10"},
            {"tick_size": "0.025"},
            {"symbol": "F_XU0301"},
            {"status": "CONNECTED"},
            {"connection": "CONNECTED"},
            {"candles": [{"open": "1"}]},
            {"verified": True},
            {"received_at": "2026-09-21T00:00:00Z"},
            {"analysis": {}},
            {"risk_approved": True},
            {"position": {}},
            {"order": {"side": "BUY"}},
        ],
    )
    def test_a_forged_fact_is_a_422(self, api: TestClient, forged: dict[str, Any]) -> None:
        body = {"source_id": "RD-" + "a" * 32, "timeframes": ["5M"], **forged}

        response = api.post(f"{LIVE}/sessions", json=body)

        assert response.status_code == 422
        assert api.get(f"{LIVE}/sessions").json()["items"] == []

    @pytest.mark.parametrize(
        "body",
        [
            {"source_id": "'; DROP TABLE replay_candles;--", "timeframes": ["5M"]},
            {"source_id": "RD-" + "a" * 32, "timeframes": []},
            {"source_id": "RD-" + "a" * 32, "timeframes": ["5M"] * 5},
            {"source_id": "RD-" + "a" * 32, "timeframes": ["4H"]},
            {"source_id": "RD-" + "a" * 32, "timeframes": ["5M"], "window_candles": 5000},
            {"source_id": "RD-" + "a" * 32, "timeframes": ["5M"], "window_candles": "300"},
            {"source_id": "RD-" + "a" * 32, "timeframes": ["5M"], "pace": "LUDICROUS"},
        ],
    )
    def test_malformed_input_is_a_422(self, api: TestClient, body: dict[str, Any]) -> None:
        assert api.post(f"{LIVE}/sessions", json=body).status_code == 422

    def test_an_oversized_body_is_refused_before_it_is_read(self, api: TestClient) -> None:
        huge = "x" * (49 * 1024 * 1024)
        response = api.post(
            f"{LIVE}/sessions",
            content=huge.encode(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413

    def test_an_unknown_source_is_a_404(self, api: TestClient) -> None:
        response = create(api, "RD-" + "0" * 32)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "SOURCE_NOT_FOUND"

    @pytest.mark.parametrize("session_id", ["LS-nothex", "../../etc", "LS-" + "0" * 25])
    def test_a_malformed_session_id_is_a_422(self, api: TestClient, session_id: str) -> None:
        assert api.get(f"{LIVE}/sessions/{session_id}").status_code in (404, 422)

    def test_an_unknown_session_is_a_404_that_explains_restarts(self, api: TestClient) -> None:
        response = api.get(f"{LIVE}/sessions/LS-{'0' * 24}")
        assert response.status_code == 404
        assert "restart" in response.json()["detail"]["detail"]


# ----------------------------------------------------------------------
# A whole playback
# ----------------------------------------------------------------------


class TestPlayback:
    def test_a_stored_dataset_plays_to_its_end_honestly(self, fast: TestClient) -> None:
        source = upload(fast, "live-play-000000001")
        created = create(fast, source)
        assert created.status_code == 201
        session_id = created.json()["id"]

        body = wait_for(fast, session_id, lambda b: b["lifecycle"] == "ENDED")

        assert body["identity"]["provenance"] == "SIMULATED_HISTORICAL_STREAM"
        assert body["identity"]["market_currency"] == "HISTORICAL"
        assert body["termination_reason"] == "END_OF_STREAM"
        assert body["end_origin"] == "STREAM"
        counts = body["playback"]["candles_per_timeframe"]
        assert counts == {"5M": 288, "15M": 96, "1H": 24}
        for timeframe in body["timeframes"]:
            assert timeframe["closed_count"] == counts[timeframe["timeframe"]]
            assert timeframe["integrity"] == "COMPLETE"
            assert timeframe["availability"] == "UNAVAILABLE"  # the stream has ended
            assert timeframe["forming"] is None
        assert "STREAM_ENDED" in {alert["kind"] for alert in body["alerts"]}
        assert body["analysis"]["analyses_run"] == 0  # nothing ran by itself
        assert body["playback"]["forming_candles_published"] is False

    def test_market_time_and_receive_time_stay_apart(self, fast: TestClient) -> None:
        source = upload(fast, "live-clock-00000001")
        session_id = create(fast, source).json()["id"]

        body = wait_for(fast, session_id, lambda b: b["lifecycle"] == "ENDED")

        latest = next(t for t in body["timeframes"] if t["timeframe"] == "5M")["latest_confirmed"]
        assert latest["state"] == "CLOSED"
        assert latest["market_open_time"].startswith("2026-03-")
        assert latest["received_at"] > latest["market_event_time"]
        assert body["playback"]["market_window_start"].startswith("2026-03-02")

    def test_an_incomplete_history_is_reported_not_repaired(self, fast: TestClient) -> None:
        rows = five_minute(288)
        holed = rows[:100] + rows[103:]
        source = upload(
            fast,
            "live-hole-000000001",
            datasets=[{"timeframe": "5M", "content": csv_of(holed), "source_name": "5M.csv"}],
            driver_timeframe="5M",
        )
        session_id = create(fast, source, timeframes=["5M"]).json()["id"]

        body = wait_for(fast, session_id, lambda b: b["lifecycle"] == "ENDED")

        (tf,) = body["timeframes"]
        assert tf["integrity"] == "DISCONTINUOUS"
        assert tf["temporal_gaps"] == 1
        assert tf["closed_count"] == 285  # nothing invented for the hole
        assert "DATA_DISCONTINUITY" in {alert["kind"] for alert in body["alerts"]}

    def test_the_event_stream_of_an_ended_session_replays_and_ends(self, fast: TestClient) -> None:
        source = upload(fast, "live-sse-0000000001")
        session_id = create(fast, source, timeframes=["1H"], window_candles=50).json()["id"]
        wait_for(fast, session_id, lambda b: b["lifecycle"] == "ENDED")

        response = fast.get(f"{LIVE}/sessions/{session_id}/events")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        frames = [f for f in response.text.split("\n\n") if f.strip()]
        assert frames[0].startswith("retry: ")
        assert '"kind":"STATE"' in frames[1]
        assert '"kind":"END"' in frames[-1]
        assert all('"session_id":"' + session_id + '"' in f for f in frames[1:])


# ----------------------------------------------------------------------
# Lifecycle over HTTP
# ----------------------------------------------------------------------


class TestLifecycle:
    def test_cancel_is_repeatable_and_delete_releases(self, held: TestClient) -> None:
        source = upload(held, "live-cancel-0000001")
        session_id = create(held, source).json()["id"]

        first = held.post(f"{LIVE}/sessions/{session_id}/cancel")
        second = held.post(f"{LIVE}/sessions/{session_id}/cancel")

        assert first.status_code == second.status_code == 200
        assert second.json()["lifecycle"] == "ENDED"
        assert second.json()["end_origin"] == "USER_CANCELLED"
        assert held.delete(f"{LIVE}/sessions/{session_id}").status_code == 204
        assert held.get(f"{LIVE}/sessions/{session_id}").status_code == 404
        assert held.delete(f"{LIVE}/sessions/{session_id}").status_code == 404

    def test_the_ninth_session_is_refused(self, held: TestClient) -> None:
        source = upload(held, "live-cap-0000000001")
        for _ in range(8):
            assert create(held, source).status_code == 201

        response = create(held, source)

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "LIVE_CAPACITY"
        assert len(held.get(f"{LIVE}/sessions").json()["items"]) == 8

    def test_creation_is_rate_limited(self, held: TestClient) -> None:
        source = upload(held, "live-rate-000000001")
        statuses = []
        for index in range(14):
            response = create(held, source)
            statuses.append(response.status_code)
            if response.status_code == 201:
                held.delete(f"{LIVE}/sessions/{response.json()['id']}")
            del index

        assert statuses.count(201) == 12
        assert statuses[-1] == 429

    def test_the_timeline_is_ordered_and_bounded(self, fast: TestClient) -> None:
        source = upload(fast, "live-timeline-00001")
        session_id = create(fast, source, timeframes=["1H"], window_candles=50).json()["id"]
        wait_for(fast, session_id, lambda b: b["lifecycle"] == "ENDED")

        page = fast.get(f"{LIVE}/sessions/{session_id}/timeline?after=0&limit=100").json()

        seqs = [entry["seq"] for entry in page["entries"]]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        kinds = [entry["kind"] for entry in page["entries"]]
        assert kinds[0] == "SESSION_STARTED"
        assert "CANDLE_CONFIRMED" in kinds
        assert fast.get(f"{LIVE}/sessions/{session_id}/timeline?limit=101").status_code == 422


# ----------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------


class TestAnalysis:
    def test_confirmed_analysis_is_historical_reused_and_trades_nothing(
        self, held: TestClient
    ) -> None:
        source = upload(held, "live-analysis-00001")
        session_id = create(held, source).json()["id"]
        wait_for(
            held,
            session_id,
            lambda b: (
                all(t["closed_count"] == 288 for t in b["timeframes"] if t["timeframe"] == "5M")
                and len(b["analysis"]["available_timeframes"]) == 3
            ),
        )

        first = held.post(f"{LIVE}/sessions/{session_id}/analysis", json={})
        second = held.post(f"{LIVE}/sessions/{session_id}/analysis", json={})

        assert first.status_code == second.status_code == 200
        one, two = first.json(), second.json()
        assert one["provenance"] == "SIMULATED_HISTORICAL_STREAM"
        assert one["market_currency"] == "HISTORICAL"
        assert one["market_as_of"] < one["requested_at"]
        assert one["market_as_of"].startswith("2026-03-03")
        assert set(one["included"]) == {"5M", "15M", "1H"}
        assert one["reused"] is False and two["reused"] is True
        assert one["current"] is True
        assert one["analysis"]["synthesis"]["status"] == "NOT_APPLICABLE"
        assert one["session"]["analysis"]["analyses_run"] == 1
        assert held.get("/api/paper/positions").json()["total"] == 0
        held.post(f"{LIVE}/sessions/{session_id}/cancel")

    def test_no_available_timeframe_is_a_typed_409_with_reasons(self, fast: TestClient) -> None:
        source = upload(fast, "live-noanalysis-001")
        session_id = create(fast, source, timeframes=["1H"], window_candles=50).json()["id"]
        wait_for(fast, session_id, lambda b: b["lifecycle"] == "ENDED")

        response = fast.post(f"{LIVE}/sessions/{session_id}/analysis", json={})

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "NO_TIMEFRAME_AVAILABLE"
        assert "1H" in detail["reasons"]

    def test_a_forged_analysis_body_is_a_422(self, held: TestClient) -> None:
        source = upload(held, "live-forged-000001")
        session_id = create(held, source).json()["id"]

        forged_bodies: list[dict[str, Any]] = [
            {"candles": []},
            {"market_currency": "CURRENT"},
            {"entry_price": "1"},
        ]
        for forged in forged_bodies:
            response = held.post(f"{LIVE}/sessions/{session_id}/analysis", json=forged)
            assert response.status_code == 422
        held.post(f"{LIVE}/sessions/{session_id}/cancel")


# ----------------------------------------------------------------------
# Shutdown
# ----------------------------------------------------------------------


def test_server_shutdown_cancels_and_awaits_every_live_task(clean: Settings) -> None:
    client = open_client(clean)
    source = upload(client, "live-shutdown-00001")
    for _ in range(3):
        assert create(client, source, pace="SLOW").status_code == 201
    workspace: LiveWorkspace = client.app.state.live_workspace  # type: ignore[attr-defined]
    tasks = [entry.task for entry in workspace._entries.values()]  # noqa: SLF001

    client.__exit__(None, None, None)

    assert tasks and all(task is not None and task.done() for task in tasks)
    assert all(view.end_origin is not None for view in workspace.sessions())
    assert {view.end_origin.value for view in workspace.sessions()} == {"SHUTDOWN"}  # type: ignore[union-attr]


def test_a_restarted_server_does_not_resurrect_a_session(clean: Settings) -> None:
    first = open_client(clean)
    source = upload(first, "live-restart-000001")
    session_id = create(first, source, pace="SLOW").json()["id"]
    first.__exit__(None, None, None)

    second = open_client(clean)
    try:
        response = second.get(f"{LIVE}/sessions/{session_id}")
        assert response.status_code == 404
        assert second.get(f"{LIVE}/sessions").json()["items"] == []
        # The dataset survives - it is stored - but the stream never did.
        assert second.get(f"{LIVE}/sources").json()["total"] == 1
    finally:
        second.__exit__(None, None, None)


def test_every_response_string_is_free_of_exchange_claims(fast: TestClient) -> None:
    source = upload(fast, "live-claims-0000001")
    session_id = create(fast, source).json()["id"]
    body = wait_for(fast, session_id, lambda b: b["lifecycle"] == "ENDED")

    texts = " ".join(every_string(body)) + " ".join(
        every_string(fast.get(f"{LIVE}/capability").json())
    )
    for claim in ("EXCHANGE_VERIFIED", "LIVE_EXCHANGE_FEED", "BROKER_VERIFIED", "REAL_TIME"):
        assert claim not in texts
    assert Timeframe.M5.value in texts
