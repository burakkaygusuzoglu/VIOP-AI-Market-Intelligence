"""The paper-trading HTTP boundary refuses what a client may not say (Phase 9).

These run without a database: every request here fails validation before any
store is touched. A 422 for a forged field is proof the field has no home in the
schema - not that it was inspected and discarded.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.schemas.paper import CreatePaperPositionBody, ObservationsBody
from app.core.config import Settings
from app.main import create_app

KEY = {"Idempotency-Key": "unit-boundary-key-0001"}


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


def body(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": "TEST_FIXTURE_FUT",
        "direction": "LONG",
        "quantity": 4,
        "intended_entry": "100.00",
        "stop": "98.00",
        "targets": [{"price": "104.00", "quantity": 2}, {"price": "106.00", "quantity": 2}],
        "timeframe": "1H",
        "decision_time": "2026-03-02T10:00:00+00:00",
        "account": {"equity": "100000"},
        "risk": {"mode": "FIXED", "fixed_risk": "1000"},
    }
    payload.update(changes)
    return payload


class TestNoServerDerivedFieldHasAHome:
    @pytest.mark.parametrize(
        "forged",
        [
            {"state": "CLOSED"},
            {"realized_gross": "1000000"},
            {"realized_pnl": "1000000"},
            {"unrealized_gross": "5"},
            {"entry_fill_price": "1"},
            {"fill_price": "1"},
            {"fills": [{"price": "1", "quantity": 4}]},
            {"events": [{"type": "TARGET_FILLED"}]},
            {"event_type": "POSITION_CLOSED"},
            {"remaining": 0},
            {"asset_class": "FUTURES"},
            {"asset_class_status": "VERIFIED_CURRENT_FACT"},
            {"provenance": "VERIFIED_CURRENT_FACT"},
            {"point_value": "10"},
            {"multiplier": "10"},
            {"risk_approval": {"outcome": "ALLOWED", "allowed_units": 1000}},
            {"allowed_units": 1000},
            {"origin": "ANALYSIS"},
            {"simulated": False},
            {"product_policy": "FuturesProductPolicy"},
            {"position_id": "PP-000000000000000000000000"},
            {"version": 7},
        ],
    )
    def test_a_forged_field_is_refused(self, client: TestClient, forged: dict[str, Any]) -> None:
        response = client.post("/api/paper/positions", json=body(**forged), headers=KEY)

        assert response.status_code == 422
        assert "extra_forbidden" in response.text

    @pytest.mark.parametrize(
        "forged",
        [
            {"risk": {"mode": "FIXED", "fixed_risk": "1000", "allowed": True}},
            {"simulation": {"same_bar": "TARGET_FIRST"}},
            {"simulation": {"rules_version": "paper-sim/v0"}},
            {"simulation": {"rules_version": "paper-sim/v2"}},
            {"simulation": {"fill_model": "OPTIMISTIC"}},
            {"targets": [{"price": "104", "quantity": 4, "filled": True}]},
        ],
    )
    def test_nested_forgery_is_refused(self, client: TestClient, forged: dict[str, Any]) -> None:
        response = client.post("/api/paper/positions", json=body(**forged), headers=KEY)

        assert response.status_code == 422

    def test_the_request_schema_has_only_intention_fields(self) -> None:
        assert set(CreatePaperPositionBody.model_fields) == {
            "symbol",
            "direction",
            "quantity",
            "intended_entry",
            "stop",
            "targets",
            "timeframe",
            "decision_time",
            "account",
            "risk",
            "simulation",
            "note",
        }
        assert set(ObservationsBody.model_fields) == {"content", "source_name"}


class TestAdversarialValues:
    @pytest.mark.parametrize(
        "changes",
        [
            {"quantity": 0},
            {"quantity": -4},
            {"quantity": 1_000_000},
            {"quantity": "4"},
            {"intended_entry": "NaN"},
            {"intended_entry": "Infinity"},
            {"intended_entry": "-Infinity"},
            {"stop": "sNaN"},
            {"intended_entry": "1E+100000"},
            {"intended_entry": "9" * 41},
            {"intended_entry": "abc"},
            {"intended_entry": 100.0},
            {"direction": "NEUTRAL"},
            {"direction": "long"},
            {"timeframe": "3M"},
            {"decision_time": "2026-03-02T10:00:00"},
            {"decision_time": "not a time"},
            {"targets": []},
            {"targets": [{"price": "104", "quantity": 1}] * 6},
            {"targets": [{"price": "104", "quantity": 0}]},
            {"note": "x" * 281},
            {"symbol": ""},
            {"symbol": "X" * 65},
        ],
    )
    def test_invalid_input_is_a_typed_422_never_a_500(
        self, client: TestClient, changes: dict[str, Any]
    ) -> None:
        response = client.post("/api/paper/positions", json=body(**changes), headers=KEY)

        assert response.status_code == 422

    @pytest.mark.parametrize("key", ["", "short", "x" * 129])
    def test_an_idempotency_key_is_required_and_bounded(self, client: TestClient, key: str) -> None:
        headers = {"Idempotency-Key": key} if key else {}
        response = client.post("/api/paper/positions", json=body(), headers=headers)

        assert response.status_code == 422

    @pytest.mark.parametrize(
        "path",
        [
            "/api/paper/positions/not-an-id",
            "/api/paper/positions/PP-XYZ",
            "/api/paper/positions/PP-00000000000000000000000G",
            "/api/paper/positions/..%2F..%2Fetc",
        ],
    )
    def test_malformed_ids_never_reach_the_store(self, client: TestClient, path: str) -> None:
        assert client.get(path).status_code in (404, 422)

    def test_list_limits_are_bounded(self, client: TestClient) -> None:
        assert client.get("/api/paper/positions?limit=51").status_code == 422
        assert client.get("/api/paper/positions?limit=0").status_code == 422
        assert client.get("/api/paper/positions?offset=-1").status_code == 422

    def test_event_page_limits_are_bounded(self, client: TestClient) -> None:
        path = "/api/paper/positions/PP-000000000000000000000000/events"
        assert client.get(f"{path}?limit=201").status_code == 422
        assert client.get(f"{path}?after_sequence=-1").status_code == 422

    def test_an_oversized_observation_upload_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/api/paper/positions/PP-000000000000000000000000/observations",
            json={"content": "x" * (512 * 1024 + 1), "source_name": "big.csv"},
        )
        assert response.status_code == 422


def _settings(**changes: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "test",
        "app_version": "0.0.0-test",
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_user": "viop",
        "postgres_password": SecretStr("fixture-password"),  # TEST_FIXTURE value
        "postgres_db": "viop_test",
    }
    base.update(changes)
    return Settings(**base)


def _paths() -> set[str]:
    return set(create_app(_settings()).openapi()["paths"])


class TestNoBrokerSurface:
    def test_no_route_places_routes_or_transmits_an_order(self) -> None:
        forbidden = {"order", "orders", "broker", "execute", "execution", "midas", "stream", "ws"}
        # Whole path segments: `/api/health/live` is the Phase 0 liveness probe, not
        # a live-trading surface, and a substring match would confuse the two.
        segments = {segment.lower() for path in _paths() for segment in path.split("/")}

        assert not segments & forbidden, segments & forbidden
        assert not any("order" in segment or "broker" in segment for segment in segments)

    def test_every_paper_route_is_under_the_paper_prefix(self) -> None:
        paper = {p for p in _paths() if "paper" in p}

        assert paper == {
            "/api/paper/positions",
            "/api/paper/positions/{position_id}",
            "/api/paper/positions/{position_id}/events",
            "/api/paper/positions/{position_id}/observations",
            "/api/paper/positions/{position_id}/close",
            "/api/paper/positions/{position_id}/stop/breakeven",
            "/api/paper/positions/{position_id}/cancel",
            # Phase 10 reads the same simulated history and adds one writable
            # surface for a person's own notes. Nothing here places an order.
            "/api/paper/performance",
            "/api/paper/performance/breakdowns",
            "/api/paper/journal",
            "/api/paper/journal/tags",
            "/api/paper/positions/{position_id}/journal",
        }


class TestStoreOutage:
    def test_an_unreachable_database_is_a_typed_503_not_a_500(self) -> None:
        """Nothing listens on port 1, so the connection is refused immediately."""
        app = create_app(_settings(postgres_host="127.0.0.1", postgres_port=1))
        with TestClient(app) as client:
            response = client.post("/api/paper/positions", json=body(), headers=KEY)
            listing = client.get("/api/paper/positions")

        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "PAPER_STORE_UNAVAILABLE"
        assert listing.status_code == 503
        assert "Traceback" not in response.text
        assert "password" not in response.text.lower()
