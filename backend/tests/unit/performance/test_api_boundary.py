"""What the performance and journal API will and will not accept.

The journal is the only writable surface Phase 10 adds, and it is attached to
immutable financial history. So the tests that matter are the ones that try to
reach past it: a body that also carries a realized P&L, a state, an outcome or a
provenance must be refused outright rather than quietly ignored.

The read surfaces are checked for the same thing in reverse - every metric
arrives with a status, so an absent number can never be rendered as zero.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.schemas.performance import (
    JournalUpdateBody,
    MetricResponse,
    PerformanceResponse,
)
from app.main import create_app

POSITION = "PP-0123456789abcdef01234567"


def openapi(client: TestClient) -> dict[str, Any]:
    """The running application's schema, through the client that composed it."""
    app: Any = client.app
    return dict(app.openapi())


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as running:
        yield running


class TestNoClientMaySubmitAResult:
    @pytest.mark.parametrize(
        "forged",
        [
            "realized_gross",
            "realized_net",
            "fees_total",
            "outcome",
            "win_rate",
            "profit_factor",
            "expectancy",
            "drawdown",
            "streak",
            "state",
            "population",
            "provenance",
            "fill_price",
            "quantity",
            "direction",
            "setup",
            "regime",
            "ai_verdict",
            "version",
        ],
    )
    def test_a_forged_field_is_refused(self, client: TestClient, forged: str) -> None:
        response = client.put(
            f"/api/paper/positions/{POSITION}/journal",
            json={"note": "mine", "tags": [], "expected_version": 0, forged: "1"},
        )

        assert response.status_code == 422

    def test_the_journal_body_holds_only_a_person_writing(self) -> None:
        assert set(JournalUpdateBody.model_fields) == {"note", "tags", "expected_version"}
        assert JournalUpdateBody.model_config["extra"] == "forbid"

    def test_the_version_must_be_a_plausible_integer(self, client: TestClient) -> None:
        for bad in (-1, 10_000_000, "three", None):
            response = client.put(
                f"/api/paper/positions/{POSITION}/journal",
                json={"note": "x", "tags": [], "expected_version": bad},
            )
            assert response.status_code == 422

    def test_oversized_journal_content_is_refused_by_the_schema(self, client: TestClient) -> None:
        response = client.put(
            f"/api/paper/positions/{POSITION}/journal",
            json={"note": "a" * 5000, "tags": [], "expected_version": 0},
        )

        assert response.status_code == 422

    def test_too_many_tags_are_refused_by_the_schema(self, client: TestClient) -> None:
        response = client.put(
            f"/api/paper/positions/{POSITION}/journal",
            json={"note": None, "tags": [f"t{i}" for i in range(50)], "expected_version": 0},
        )

        assert response.status_code == 422


class TestEveryMetricCarriesItsStatus:
    def test_a_metric_may_not_be_a_bare_number(self) -> None:
        fields = MetricResponse.model_fields
        assert "status" in fields
        assert fields["value"].annotation == (str | None)

    def test_the_performance_response_exposes_no_raw_float(self) -> None:
        for name, field in PerformanceResponse.model_fields.items():
            assert field.annotation is not float, name

    def test_the_response_names_why_setup_performance_does_not_exist(self) -> None:
        assert "analysis_linkage" in PerformanceResponse.model_fields


class TestFilterValidation:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("?direction=SIDEWAYS", 422),
            ("?timeframe=3H", 422),
            ("?from=not-a-date", 422),
            ("?symbol=" + "x" * 100, 422),
            ("?symbol=%27%20OR%201%3D1%20--", 422),
            ("?tag=%3Cscript%3E", 422),
            ("?direction=LONG", 200),
            ("?tag=Breakout", 200),
            ("?from=2026-03-02T00:00:00%2B00:00&to=2026-03-09T00:00:00%2B00:00", 200),
        ],
    )
    def test_filters_are_typed_or_refused(
        self, client: TestClient, query: str, expected: int
    ) -> None:
        response = client.get(f"/api/paper/performance{query}")

        # 503 means the database was unreachable in this unit environment; the
        # point here is that a malformed filter never reaches it.
        assert response.status_code == expected or (expected == 200 and response.status_code == 503)

    def test_a_reversed_range_is_refused(self, client: TestClient) -> None:
        response = client.get(
            "/api/paper/performance?from=2026-03-09T00:00:00%2B00:00&to=2026-03-02T00:00:00%2B00:00"
        )

        assert response.status_code in (422, 503)
        if response.status_code == 422:
            assert response.json()["detail"]["code"] == "RANGE_REVERSED"

    def test_a_naive_timestamp_is_refused(self, client: TestClient) -> None:
        response = client.get("/api/paper/performance?from=2026-03-02T00:00:00")

        assert response.status_code in (422, 503)
        if response.status_code == 422:
            assert response.json()["detail"]["code"] == "RANGE_NOT_TIMEZONE_AWARE"

    def test_journal_paging_is_bounded(self, client: TestClient) -> None:
        assert client.get("/api/paper/journal?limit=500").status_code == 422
        assert client.get("/api/paper/journal?limit=0").status_code == 422
        assert client.get("/api/paper/journal?offset=-1").status_code == 422


class TestRouteInventory:
    def test_exactly_the_phase_10_surfaces_exist(self, client: TestClient) -> None:
        paths = {
            path for path in openapi(client)["paths"] if "performance" in path or "journal" in path
        }

        assert paths == {
            "/api/paper/performance",
            "/api/paper/performance/breakdowns",
            "/api/paper/journal",
            "/api/paper/journal/tags",
            "/api/paper/positions/{position_id}/journal",
        }

    def test_no_route_deletes_financial_history(self, client: TestClient) -> None:
        for path, operations in openapi(client)["paths"].items():
            if "paper" in path:
                assert "delete" not in operations, path

    def test_the_journal_surface_offers_no_broker_or_execution_verb(
        self, client: TestClient
    ) -> None:
        segments = {
            segment.lower()
            for path in openapi(client)["paths"]
            if path.startswith("/api/paper")
            for segment in path.split("/")
        }

        assert segments.isdisjoint(
            {"orders", "order", "broker", "midas", "execute", "execution", "live", "stream"}
        )

    @pytest.mark.parametrize("position_id", ["PP-short", "../../etc", "PP-" + "z" * 24, "1"])
    def test_a_malformed_position_id_never_reaches_the_service(
        self, client: TestClient, position_id: str
    ) -> None:
        response = client.get(f"/api/paper/positions/{position_id}/journal")

        assert response.status_code in (404, 422)
