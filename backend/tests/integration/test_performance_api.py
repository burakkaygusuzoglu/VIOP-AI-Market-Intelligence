"""Performance and journal over HTTP, against real PostgreSQL (Phase 10).

The composed application, the real routes, the real ledger. The only
test-specific part is the Phase 9 product resolver, exactly as in the Phase 9
API tests: production composes none, so a fixture contract is supplied here at
the call site.

What these check is the contract a screen depends on: the same filters reach the
summary, the breakdowns and the journal; a metric never arrives as a bare
number; and a note can be written without any financial value moving.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.contract_metadata.manual_provider import ManualContractMetadataProvider
from app.adapters.persistence.database import Database
from app.adapters.products.futures import FuturesProductResolver
from app.core.config import Settings
from app.main import create_app
from tests.factories_paper import paper_contract
from tests.integration.paper_support import bars_csv, truncate
from tests.integration.performance_support import (
    ENTRY,
    SHORT_STOP_ON_REST,
    SHORT_TARGET_ONE,
    STOP_ON_REST,
    TARGET_ONE,
)

pytestmark = pytest.mark.integration

LONG_PLAN: dict[str, Any] = {
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
SHORT_PLAN: dict[str, Any] = {
    **LONG_PLAN,
    "direction": "SHORT",
    "stop": "102.00",
    "targets": [{"price": "96.00", "quantity": 2}, {"price": "94.00", "quantity": 2}],
}
FEES = {
    "same_bar": "STOP_FIRST",
    "slippage_mode": "ZERO",
    "fee_mode": "USER_DEFINED_PER_UNIT",
    "fee_per_unit": "2",
}


@pytest.fixture
async def clean(migrated: Settings) -> AsyncIterator[Settings]:
    database = Database(migrated.sqlalchemy_url)
    try:
        await truncate(database)
    finally:
        await database.dispose()
    yield migrated


@pytest.fixture
def api(clean: Settings) -> Iterator[TestClient]:
    app = create_app(clean)
    client = TestClient(app)
    client.__enter__()
    app.state.product_resolver = FuturesProductResolver(
        ManualContractMetadataProvider([paper_contract()])
    )
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


def make(
    api: TestClient,
    key: str,
    plan: dict[str, Any],
    *rows: tuple[int, str, str, str, str],
    **extra: Any,
) -> str:
    body = {**plan, **extra}
    created = api.post("/api/paper/positions", json=body, headers={"Idempotency-Key": key})
    assert created.status_code == 201, created.text
    position_id: str = created.json()["id"]
    if rows:
        response = api.post(
            f"/api/paper/positions/{position_id}/observations",
            json={"content": bars_csv(*rows), "source_name": "bars.csv"},
        )
        assert response.status_code == 200, response.text
    return position_id


class TestPerformanceOverHttp:
    async def test_the_summary_matches_the_ledger_it_came_from(self, api: TestClient) -> None:
        make(api, "perf-api-key-00000001", LONG_PLAN, ENTRY, TARGET_ONE, STOP_ON_REST)

        response = api.get("/api/paper/performance")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["simulated"] is True
        assert body["source"] == "PAPER_SIMULATION"
        assert body["sample_size"] == 1
        assert body["wins"] == 1
        assert body["realized_gross"] == {
            "status": "AVAILABLE",
            "value": "40.00",
            "basis": "REALIZED_GROSS",
            "sample_size": 1,
            "coverage": None,
            "reason": None,
            "numerator": None,
            "denominator": None,
        }
        assert body["win_rate"]["value"] == "100.0000"
        assert body["win_rate"]["numerator"] == 1
        assert body["win_rate"]["denominator"] == 1
        assert body["counts"]["closed"] == 1
        assert body["timeline"] == [
            {
                "position_id": body["timeline"][0]["position_id"],
                "terminal_time": "2026-03-02T12:00:00+00:00",
                "amount": "40.00",
                "cumulative": "40.00",
            }
        ]

    async def test_an_unavailable_metric_is_never_a_zero(self, api: TestClient) -> None:
        make(api, "perf-api-key-00000002", LONG_PLAN, ENTRY, TARGET_ONE, STOP_ON_REST)

        body = api.get("/api/paper/performance").json()

        assert body["profit_factor"]["status"] == "UNAVAILABLE"
        assert body["profit_factor"]["value"] is None
        assert "no losing completed position" in body["profit_factor"]["reason"]
        assert body["realized_net"]["status"] == "UNAVAILABLE"
        assert body["average_loss"]["value"] is None
        for name in ("mae", "mfe", "sharpe_ratio", "sortino_ratio", "annualised_return"):
            assert body[name]["status"] == "NOT_IMPLEMENTED"
            assert body[name]["value"] is None
            assert body[name]["reason"]
        assert body["drawdown_percentage"]["status"] == "UNAVAILABLE"

    async def test_an_empty_database_reports_nothing_rather_than_zero_percent(
        self, api: TestClient
    ) -> None:
        body = api.get("/api/paper/performance").json()

        assert body["sample_size"] == 0
        assert body["counts"]["total"] == 0
        assert body["win_rate"]["status"] == "UNAVAILABLE"
        assert body["win_rate"]["value"] is None
        assert body["expectancy"]["value"] is None
        assert body["timeline"] == []

    async def test_mixed_fee_coverage_is_visible_in_the_response(self, api: TestClient) -> None:
        make(api, "perf-api-key-00000003", LONG_PLAN, ENTRY, TARGET_ONE, STOP_ON_REST)
        make(
            api,
            "perf-api-key-00000004",
            LONG_PLAN,
            ENTRY,
            TARGET_ONE,
            STOP_ON_REST,
            simulation=FEES,
        )

        body = api.get("/api/paper/performance").json()

        assert body["basis"] == "REALIZED_GROSS"
        assert "1 of 2" in body["basis_reason"]
        assert body["fee_coverage"] == {"covered": 1, "total": 2}
        assert body["realized_net"]["status"] == "PARTIAL_COVERAGE"
        assert body["realized_net"]["value"] is None
        assert body["fees_known"]["value"] == "16"
        assert body["realized_gross"]["value"] == "80.00"

    async def test_filters_move_the_summary_and_the_breakdowns_together(
        self, api: TestClient
    ) -> None:
        make(api, "perf-api-key-00000005", LONG_PLAN, ENTRY, TARGET_ONE, STOP_ON_REST)
        make(
            api,
            "perf-api-key-00000006",
            SHORT_PLAN,
            ENTRY,
            SHORT_TARGET_ONE,
            SHORT_STOP_ON_REST,
        )

        everything = api.get("/api/paper/performance").json()
        longs = api.get("/api/paper/performance?direction=LONG").json()
        long_breakdowns = api.get("/api/paper/performance/breakdowns?direction=LONG").json()
        long_journal = api.get("/api/paper/journal?direction=LONG").json()

        assert everything["sample_size"] == 2
        assert longs["sample_size"] == 1
        assert longs["filters"]["direction"] == "LONG"
        assert {row["key"] for row in long_breakdowns["by_direction"]["rows"]} == {"LONG"}
        assert long_breakdowns["by_direction"]["rows"][0]["sample_size"] == 1
        assert long_breakdowns["by_direction"]["is_complete"] is True
        assert long_journal["total"] == 1
        assert long_journal["items"][0]["direction"] == "LONG"
        # The headline and the chart describe the same population.
        assert (
            longs["realized_gross"]["value"]
            == (long_breakdowns["by_direction"]["rows"][0]["realized_gross"]["value"])
        )
        assert len(longs["timeline"]) == longs["sample_size"]

    async def test_the_breakdowns_name_what_cannot_be_grouped(self, api: TestClient) -> None:
        make(api, "perf-api-key-00000007", LONG_PLAN, ENTRY, TARGET_ONE, STOP_ON_REST)

        body = api.get("/api/paper/performance/breakdowns").json()

        assert any("setup" in reason for reason in body["unavailable_breakdowns"])
        assert any("regime" in reason for reason in body["unavailable_breakdowns"])
        assert {row["key"] for row in body["by_timeframe"]["rows"]} == {"1H"}
        assert body["by_instrument"]["rows"][0]["key"] == "FUTURES:TEST_FIXTURE_FUT"

    async def test_the_response_says_why_setup_performance_is_absent(self, api: TestClient) -> None:
        body = api.get("/api/paper/performance").json()

        assert "USER_CREATED" in body["analysis_linkage"]
        assert "no analysis snapshot is persisted" in body["analysis_linkage"]


class TestJournalOverHttp:
    async def test_write_read_and_conflict(self, api: TestClient) -> None:
        position_id = make(api, "perf-api-key-00000010", LONG_PLAN, ENTRY, TARGET_ONE, STOP_ON_REST)

        empty = api.get(f"/api/paper/positions/{position_id}/journal")
        assert empty.status_code == 200
        assert empty.json() == {
            "position_id": position_id,
            "note": None,
            "tags": [],
            "version": 0,
            "created_at": None,
            "updated_at": None,
            "provenance": "USER_AUTHORED",
        }

        saved = api.put(
            f"/api/paper/positions/{position_id}/journal",
            json={"note": "Girişi plana göre aldım.", "tags": ["Breakout"], "expected_version": 0},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["version"] == 1
        assert saved.json()["tags"] == ["breakout"]

        stale = api.put(
            f"/api/paper/positions/{position_id}/journal",
            json={"note": "üzerine yazmayı denedim", "tags": [], "expected_version": 0},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "JOURNAL_VERSION_CONFLICT"
        assert api.get(f"/api/paper/positions/{position_id}/journal").json()["note"] == (
            "Girişi plana göre aldım."
        )

    async def test_a_note_cannot_move_a_number(self, api: TestClient) -> None:
        position_id = make(api, "perf-api-key-00000011", LONG_PLAN, ENTRY, TARGET_ONE, STOP_ON_REST)
        before = api.get("/api/paper/performance").json()
        position_before = api.get(f"/api/paper/positions/{position_id}").json()

        api.put(
            f"/api/paper/positions/{position_id}/journal",
            json={"note": "hiçbir rakam değişmemeli", "tags": ["x"], "expected_version": 0},
        )

        after = api.get("/api/paper/performance").json()
        position_after = api.get(f"/api/paper/positions/{position_id}").json()
        assert after["realized_gross"] == before["realized_gross"]
        assert after["win_rate"] == before["win_rate"]
        assert position_after == position_before

    async def test_hostile_text_survives_as_text_and_is_refused_where_it_must_be(
        self, api: TestClient
    ) -> None:
        position_id = make(api, "perf-api-key-00000012", LONG_PLAN, ENTRY, TARGET_ONE, STOP_ON_REST)

        note = api.put(
            f"/api/paper/positions/{position_id}/journal",
            json={
                "note": "<script>alert(1)</script>",
                "tags": [],
                "expected_version": 0,
            },
        )
        tag = api.put(
            f"/api/paper/positions/{position_id}/journal",
            json={"note": None, "tags": ["<script>"], "expected_version": 1},
        )

        assert note.status_code == 200
        assert note.json()["note"] == "<script>alert(1)</script>"
        assert tag.status_code == 422
        assert tag.json()["detail"]["code"] == "JOURNAL_CONTENT_INVALID"

    async def test_the_journal_row_shows_facts_beside_the_writing(self, api: TestClient) -> None:
        position_id = make(
            api,
            "perf-api-key-00000013",
            LONG_PLAN,
            ENTRY,
            TARGET_ONE,
            STOP_ON_REST,
            simulation=FEES,
        )
        api.put(
            f"/api/paper/positions/{position_id}/journal",
            json={"note": "kayıt", "tags": ["disiplin"], "expected_version": 0},
        )

        row = api.get("/api/paper/journal").json()["items"][0]

        assert row["position_id"] == position_id
        assert row["realized_gross"] == "40.00"
        assert row["realized_net"] == "24.00"
        assert row["outcome"] == "WIN"
        assert row["outcome_basis"] == "REALIZED_NET"
        assert row["annotation"]["note"] == "kayıt"
        assert row["annotation"]["provenance"] == "USER_AUTHORED"

    async def test_tags_in_use_are_listed_for_filtering(self, api: TestClient) -> None:
        first = make(api, "perf-api-key-00000014", LONG_PLAN, ENTRY, TARGET_ONE, STOP_ON_REST)
        second = make(api, "perf-api-key-00000015", LONG_PLAN, ENTRY, TARGET_ONE, STOP_ON_REST)
        api.put(
            f"/api/paper/positions/{first}/journal",
            json={"note": None, "tags": ["ortak", "tek"], "expected_version": 0},
        )
        api.put(
            f"/api/paper/positions/{second}/journal",
            json={"note": None, "tags": ["ortak"], "expected_version": 0},
        )

        body = api.get("/api/paper/journal/tags").json()

        assert body["items"] == [
            {"tag": "ortak", "positions": 2},
            {"tag": "tek", "positions": 1},
        ]

    async def test_a_journal_for_an_unknown_position_is_404(self, api: TestClient) -> None:
        response = api.get("/api/paper/positions/PP-000000000000000000000000/journal")

        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "POSITION_NOT_FOUND"
