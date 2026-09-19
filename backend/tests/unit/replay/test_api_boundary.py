"""The replay HTTP boundary refuses what a client may not say (Phase 11).

These run without a database: every request here fails validation before any
store is touched. The point is not that forged state is *rejected* - it is that
there is nowhere to put it. A field that does not exist cannot be trusted by a
later refactor, whereas a field that is validated away can.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, SecretStr

from app.api.schemas.replay import (
    AdvanceReplayBody,
    CreateReplaySessionBody,
    OpenReplayPositionBody,
    ReplayAnalysisBody,
    ReplayDatasetBody,
)
from app.core.config import Settings
from app.main import create_app

KEY = {"Idempotency-Key": "unit-replay-key-000001"}
SESSION = "/api/replay/sessions"
ONE = f"{SESSION}/RS-000000000000000000000001"

CSV = (
    "open_time,open,high,low,close,volume\n"
    "2026-03-02T09:00:00+00:00,100,101,99,100.5,1000\n"
    "2026-03-02T09:05:00+00:00,100.5,101.5,99.5,101,1000\n"
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(
        app_env="test",
        app_version="0.0.0-test",
        postgres_host="localhost",
        postgres_port=5432,
        postgres_user="viop",
        postgres_password=SecretStr("fixture-password"),  # TEST_FIXTURE value
        postgres_db="viop_test",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def create_body(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": "TEST_FIXTURE_FUT",
        "driver_timeframe": "5M",
        "replay_start": "2026-03-02T09:10:00+00:00",
        "datasets": [{"timeframe": "5M", "content": CSV, "source_name": "5m.csv"}],
    }
    payload.update(changes)
    return payload


def position_body(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "direction": "LONG",
        "quantity": 4,
        "intended_entry": "100.00",
        "stop": "98.00",
        "targets": [{"price": "104.00", "quantity": 4}],
        "account": {"equity": "100000"},
        "risk": {"mode": "FIXED", "fixed_risk": "1000"},
    }
    payload.update(changes)
    return payload


@pytest.mark.unit
class TestNoServerOwnedStateHasAHome:
    """The replay clock is the server's. There is no field to submit it in."""

    @pytest.mark.parametrize(
        "forged",
        [
            {"replay_as_of": "2026-03-02T23:00:00+00:00"},
            {"as_of": "2026-03-02T23:00:00+00:00"},
            {"cursor": {"as_of": "2026-03-02T23:00:00+00:00", "version": 99}},
            {"revealed_driver_candles": 999},
            {"revealed": 999},
            {"state": "END_OF_DATASET"},
            {"version": 42},
            {"dataset_id": "RD-00000000000000000000000000000001"},
            {"session_id": "RS-000000000000000000000002"},
            {"linked_position_ids": ["PP-000000000000000000000001"]},
            {"candles": [{"open_time": "2026-03-02T23:00:00+00:00", "close": "999"}]},
            {"analysis": {"rsi": 99}},
            {"performance": {"win_rate": "1.0"}},
            {"provenance": "VERIFIED_CURRENT_FACT"},
            {"asset_class": "FUTURES"},
            {"point_value": "10"},
            {"multiplier": "10"},
            {"tick_size": "0.25"},
            {"margin": "1000"},
            {"expiry": "2026-06-30"},
            {"simulated": False},
        ],
    )
    def test_a_forged_field_is_a_422(self, client: TestClient, forged: dict[str, Any]) -> None:
        response = client.post(SESSION, json=create_body(**forged), headers=KEY)
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "forged",
        [
            {"decision_time": "2020-01-01T00:00:00+00:00"},
            {"symbol": "OTHER_FUT"},
            {"timeframe": "1D"},
            {"replay_as_of": "2026-03-02T23:00:00+00:00"},
            {"entry_fill_price": "1"},
            {"realized_gross": "1000000"},
            {"state": "CLOSED"},
            {"session_id": "RS-000000000000000000000002"},
        ],
    )
    def test_a_replay_position_cannot_carry_session_state(
        self, client: TestClient, forged: dict[str, Any]
    ) -> None:
        """Symbol, timeframe and decision time belong to the session.

        A client that could send ``decision_time`` could backdate a simulated
        decision to a moment it already knows the outcome of.
        """
        response = client.post(f"{ONE}/positions", json=position_body(**forged), headers=KEY)
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "forged",
        [
            {"as_of": "2026-03-02T23:00:00+00:00"},
            {"replay_as_of": "2026-03-02T23:00:00+00:00"},
            {"to": "2026-03-02T23:00:00+00:00"},
            {"cursor": "RS-000000000000000000000002"},
            {"candles": []},
            {"version": 5},
            {"set_version": 5},
        ],
    )
    def test_an_advance_says_how_far_not_where_to(
        self, client: TestClient, forged: dict[str, Any]
    ) -> None:
        response = client.post(f"{ONE}/advance", json={"steps": 1, **forged})
        assert response.status_code == 422

    def test_an_analysis_cannot_bring_its_own_market_data(self, client: TestClient) -> None:
        """The revealed prefix is the data, and it is not the client's."""
        response = client.post(
            f"{ONE}/analysis", json={"datasets": [{"timeframe": "5M", "content": CSV}]}
        )
        assert response.status_code == 422


@pytest.mark.unit
class TestBounds:
    @pytest.mark.parametrize("steps", [0, -1, -1000, 51, 10_000, 2**40])
    def test_an_advance_outside_its_bounds_is_refused(self, client: TestClient, steps: int) -> None:
        response = client.post(f"{ONE}/advance", json={"steps": steps})
        assert response.status_code == 422

    @pytest.mark.parametrize("steps", ["1", 1.0, True, None, [1]])
    def test_a_step_count_that_is_not_an_integer_is_refused(
        self, client: TestClient, steps: object
    ) -> None:
        response = client.post(f"{ONE}/advance", json={"steps": steps})
        assert response.status_code == 422

    def test_an_empty_dataset_list_is_refused(self, client: TestClient) -> None:
        response = client.post(SESSION, json=create_body(datasets=[]), headers=KEY)
        assert response.status_code == 422

    def test_more_timeframes_than_exist_is_refused(self, client: TestClient) -> None:
        datasets = [
            {"timeframe": tf, "content": CSV, "source_name": f"{tf}.csv"}
            for tf in ("5M", "15M", "1H", "1D", "5M")
        ]
        response = client.post(SESSION, json=create_body(datasets=datasets), headers=KEY)
        assert response.status_code == 422

    def test_an_oversized_upload_is_refused(self, client: TestClient) -> None:
        huge = CSV + ("2026-03-02T09:05:00+00:00,1,1,1,1,1\n" * 40_000)
        datasets = [{"timeframe": "5M", "content": huge, "source_name": "5m.csv"}]
        response = client.post(SESSION, json=create_body(datasets=datasets), headers=KEY)
        assert response.status_code in (413, 422)

    def test_a_session_list_page_is_bounded(self, client: TestClient) -> None:
        assert client.get(f"{SESSION}?limit=1000").status_code == 422


@pytest.mark.unit
class TestMalformedInput:
    def test_a_naive_replay_start_is_refused(self, client: TestClient) -> None:
        """No timezone, no replay: a market moment without an offset is a guess."""
        response = client.post(
            SESSION, json=create_body(replay_start="2026-03-02T09:10:00"), headers=KEY
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("timeframe", ["1M", "4H", "1W", "", "5m", "SECOND"])
    def test_an_unsupported_timeframe_is_refused(self, client: TestClient, timeframe: str) -> None:
        datasets = [{"timeframe": timeframe, "content": CSV, "source_name": "x.csv"}]
        response = client.post(SESSION, json=create_body(datasets=datasets), headers=KEY)
        assert response.status_code == 422

    def test_an_empty_symbol_is_refused(self, client: TestClient) -> None:
        response = client.post(SESSION, json=create_body(symbol=""), headers=KEY)
        assert response.status_code == 422

    def test_a_create_without_an_idempotency_key_is_refused(self, client: TestClient) -> None:
        assert client.post(SESSION, json=create_body()).status_code == 422

    def test_a_short_idempotency_key_is_refused(self, client: TestClient) -> None:
        response = client.post(SESSION, json=create_body(), headers={"Idempotency-Key": "short"})
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "session_id",
        ["RS-not-hex", "RS-0000", "PP-000000000000000000000001", "../../etc/passwd", "RS-"],
    )
    def test_a_malformed_session_id_is_refused(self, client: TestClient, session_id: str) -> None:
        assert client.get(f"{SESSION}/{session_id}").status_code in (404, 422)

    def test_an_unknown_chart_timeframe_is_refused(self, client: TestClient) -> None:
        assert client.get(f"{ONE}?chart=4H").status_code == 422

    @pytest.mark.parametrize("value", ["1e999999", "NaN", "Infinity", "-Infinity", "abc", ""])
    def test_an_impossible_price_is_refused(self, client: TestClient, value: str) -> None:
        response = client.post(
            f"{ONE}/positions", json=position_body(intended_entry=value), headers=KEY
        )
        assert response.status_code == 422


@pytest.mark.unit
class TestRouteInventory:
    def test_exactly_the_phase_11_surfaces_exist(self, client: TestClient) -> None:
        paths = {path for path in client.get("/openapi.json").json()["paths"] if "replay" in path}
        assert paths == {
            "/api/replay/sessions",
            "/api/replay/sessions/{session_id}",
            "/api/replay/sessions/{session_id}/advance",
            "/api/replay/sessions/{session_id}/analysis",
            "/api/replay/sessions/{session_id}/performance",
            "/api/replay/sessions/{session_id}/positions",
            "/api/replay/sessions/{session_id}/positions/{position_id}/cancel",
            "/api/replay/sessions/{session_id}/positions/{position_id}/close",
            "/api/replay/sessions/{session_id}/positions/{position_id}/stop/breakeven",
        }

    def test_no_replay_route_rewinds_or_deletes(self, client: TestClient) -> None:
        """Forward only, and no route removes financial history."""
        for path, operations in client.get("/openapi.json").json()["paths"].items():
            if "replay" not in path:
                continue
            assert "delete" not in operations, path
            assert "put" not in operations, path
            assert not any(
                verb in path.lower() for verb in ("rewind", "back", "seek", "reset", "undo")
            ), path

    def test_the_replay_surface_offers_no_broker_or_live_verb(self, client: TestClient) -> None:
        segments = {
            segment.lower()
            for path in client.get("/openapi.json").json()["paths"]
            if path.startswith("/api/replay")
            for segment in path.split("/")
        }
        assert segments.isdisjoint(
            {
                "orders",
                "order",
                "broker",
                "midas",
                "execute",
                "execution",
                "live",
                "stream",
                "ws",
                "sse",
                "backtest",
                "optimize",
                "shadow",
            }
        )


@pytest.mark.unit
class TestRequestModelInventory:
    """The request models are pinned field by field.

    A parametrised "forged field is a 422" check can pass for the wrong reason:
    a mutation that added `cursor: str | None` was still refused, because the
    probe happened to send an integer. Pinning the field set catches the field
    itself, whatever type it was given - which is what the rule is actually
    about.
    """

    @pytest.mark.parametrize(
        ("model", "fields"),
        [
            (
                CreateReplaySessionBody,
                {"symbol", "driver_timeframe", "replay_start", "datasets"},
            ),
            (AdvanceReplayBody, {"steps", "expected_version"}),
            (ReplayDatasetBody, {"timeframe", "content", "source_name"}),
            (ReplayAnalysisBody, {"account", "risk", "entry_price", "stop_price"}),
            (
                OpenReplayPositionBody,
                {
                    "direction",
                    "quantity",
                    "intended_entry",
                    "stop",
                    "targets",
                    "account",
                    "risk",
                    "simulation",
                    "note",
                },
            ),
        ],
    )
    def test_a_request_model_has_exactly_these_fields(
        self, model: type[BaseModel], fields: set[str]
    ) -> None:
        assert set(model.model_fields) == fields

    @pytest.mark.parametrize(
        "model",
        [
            CreateReplaySessionBody,
            AdvanceReplayBody,
            ReplayDatasetBody,
            ReplayAnalysisBody,
            OpenReplayPositionBody,
        ],
    )
    def test_no_request_model_names_server_owned_state(self, model: type[BaseModel]) -> None:
        forbidden = {
            "replay_as_of",
            "as_of",
            "cursor",
            "revealed",
            "revealed_driver_candles",
            "version",
            "state",
            "candles",
            "session_id",
            "dataset_id",
            "decision_time",
            "point_value",
            "multiplier",
            "tick_size",
            "speed",
        }
        assert set(model.model_fields).isdisjoint(forbidden)

    @pytest.mark.parametrize(
        "model",
        [
            CreateReplaySessionBody,
            AdvanceReplayBody,
            ReplayDatasetBody,
            ReplayAnalysisBody,
            OpenReplayPositionBody,
        ],
    )
    def test_every_request_model_forbids_unknown_fields(self, model: type[BaseModel]) -> None:
        assert model.model_config.get("extra") == "forbid"


@pytest.mark.unit
class TestALinkCannotBeForged:
    """Membership of a replay session is established by creating a position in
    it, and by nothing else. There is no surface that attaches an existing one.
    """

    @pytest.mark.parametrize(
        "forged",
        [
            {"position_id": "PP-000000000000000000000001"},
            {"positions": ["PP-000000000000000000000001"]},
            {"link_position_id": "PP-000000000000000000000001"},
            {"attach": "PP-000000000000000000000001"},
            {"paper_position_id": "PP-000000000000000000000001"},
        ],
    )
    def test_no_create_body_can_name_an_existing_position(
        self, client: TestClient, forged: dict[str, Any]
    ) -> None:
        response = client.post(f"{ONE}/positions", json=position_body(**forged), headers=KEY)
        assert response.status_code == 422

    def test_no_request_model_has_a_position_field(self) -> None:
        for model in (
            CreateReplaySessionBody,
            AdvanceReplayBody,
            ReplayDatasetBody,
            ReplayAnalysisBody,
            OpenReplayPositionBody,
        ):
            names = set(model.model_fields)
            assert not any("position" in name for name in names), model.__name__

    def test_the_only_routes_taking_a_position_id_act_on_one_already_owned(
        self, client: TestClient
    ) -> None:
        """A position id appears in a path, never in a body - and every such
        path is a command on a position the session already owns."""
        paths = [
            path
            for path in client.get("/openapi.json").json()["paths"]
            if path.startswith("/api/replay") and "{position_id}" in path
        ]
        assert sorted(paths) == [
            "/api/replay/sessions/{session_id}/positions/{position_id}/cancel",
            "/api/replay/sessions/{session_id}/positions/{position_id}/close",
            "/api/replay/sessions/{session_id}/positions/{position_id}/stop/breakeven",
        ]
        for path in paths:
            operations = client.get("/openapi.json").json()["paths"][path]
            assert set(operations) == {"post"}
            assert "requestBody" not in operations["post"]
