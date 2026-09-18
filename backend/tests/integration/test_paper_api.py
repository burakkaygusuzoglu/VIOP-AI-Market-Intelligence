"""Paper trading end to end over HTTP, against real PostgreSQL (Phase 9).

The composed application, the real routes, the real service, the real store and
the real Phase 3 money engines. The only test-specific part is the product
resolver: the production composition has no contract metadata provider, so a
fixture contract - whose multiplier and tick size are marked verified at the
call site, and are TEST_FIXTURE numbers - is supplied here. A separate test runs
the production composition and proves it refuses.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.adapters.contract_metadata.manual_provider import ManualContractMetadataProvider
from app.adapters.persistence.database import Database
from app.adapters.persistence.paper_models import PaperEventRow, PaperPositionRow
from app.adapters.persistence.paper_store import SqlAlchemyPaperStore
from app.adapters.products.futures import FuturesProductResolver, FuturesSnapshotCodec
from app.core.config import Settings
from app.domain.paper import rebuild
from app.main import create_app
from tests.factories_futures import unverified
from tests.factories_paper import paper_contract
from tests.integration.paper_support import (
    ENTRY,
    STOP_ON_REST,
    TARGET_ONE,
    bars_csv,
    truncate,
)

pytestmark = pytest.mark.integration


def create_body(**changes: Any) -> dict[str, Any]:
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


def headers(key: str = "api-integration-key-001") -> dict[str, str]:
    return {"Idempotency-Key": key}


@pytest.fixture
async def clean(migrated: Settings) -> AsyncIterator[Settings]:
    database = Database(migrated.sqlalchemy_url)
    try:
        await truncate(database)
    finally:
        await database.dispose()
    yield migrated


def client_for(settings: Settings, contract: Any | None = None) -> TestClient:
    app = create_app(settings)
    client = TestClient(app)
    client.__enter__()
    if contract is not None:
        app.state.product_resolver = FuturesProductResolver(
            ManualContractMetadataProvider([contract])
        )
    return client


@pytest.fixture
def api(clean: Settings) -> Iterator[TestClient]:
    client = client_for(clean, paper_contract())
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


def observe(api: TestClient, position_id: str, *rows: tuple[int, str, str, str, str]) -> Any:
    return api.post(
        f"/api/paper/positions/{position_id}/observations",
        json={"content": bars_csv(*rows), "source_name": "bars.csv"},
    )


class TestLifecycleOverHttp:
    async def test_create_observe_partial_stop_and_every_view_agrees(
        self, api: TestClient, clean: Settings
    ) -> None:
        created = api.post("/api/paper/positions", json=create_body(), headers=headers())
        assert created.status_code == 201, created.text
        position = created.json()
        position_id = position["id"]
        assert position["state"] == "PENDING_ENTRY"
        assert position["simulated"] is True
        assert position["provenance"]["fills"] == "SIMULATED"
        assert position["provenance"]["market_data"] == "USER_SUPPLIED_HISTORICAL_BARS"
        assert position["origin"] == "USER_CREATED"
        assert position["risk"]["outcome"] == "ALLOWED"

        partial = observe(api, position_id, ENTRY, TARGET_ONE)
        assert partial.status_code == 200, partial.text
        assert partial.json()["state"] == "PARTIALLY_CLOSED"
        assert partial.json()["remaining"] == 2
        assert partial.json()["realized_gross"] == "80.00"
        # (104.25 - 100) x 10 x 2 = 85 on the remaining units
        assert partial.json()["unrealized_gross"] == "85.00"

        closed = observe(api, position_id, STOP_ON_REST)
        final = closed.json()
        assert final["state"] == "CLOSED"
        assert final["remaining"] == 0
        # 80 + (98 - 100) x 10 x 2 = 40
        assert final["realized_gross"] == "40.00"
        assert final["unrealized_gross"] == "0"
        assert [t["filled"] for t in final["targets"]] == [True, False]

        # The DTO, the stored row and a replay of the stored ledger all agree.
        fetched = api.get(f"/api/paper/positions/{position_id}").json()
        database = Database(clean.sqlalchemy_url)
        try:
            async with database.session() as session:
                row = await session.get(PaperPositionRow, position_id)
                event_rows = (
                    await session.scalars(
                        select(PaperEventRow)
                        .where(PaperEventRow.position_id == position_id)
                        .order_by(PaperEventRow.sequence)
                    )
                ).all()
            stored = await SqlAlchemyPaperStore(database).get(position_id)
        finally:
            await database.dispose()
        assert row is not None and stored is not None
        product = FuturesSnapshotCodec().restore(stored.product_snapshot)
        replayed = rebuild(stored.spec, stored.approval, product, stored.events)

        assert fetched["state"] == row.state == replayed.state.value == "CLOSED"
        assert Decimal(fetched["realized_gross"]) == row.realized_gross == replayed.realized_gross
        assert fetched["remaining"] == row.remaining == replayed.remaining == 0
        assert fetched["event_count"] == row.event_count == len(event_rows) == len(replayed.events)

    def test_the_ledger_explains_every_fill(self, api: TestClient) -> None:
        position_id = api.post(
            "/api/paper/positions", json=create_body(), headers=headers()
        ).json()["id"]
        final = observe(api, position_id, ENTRY, (1, "95", "96", "94", "95.50")).json()

        kinds = [event["type"] for event in final["key_events"]]
        assert kinds == ["POSITION_CREATED", "ENTRY_FILLED", "STOP_FILLED", "POSITION_CLOSED"]
        stop = final["key_events"][2]["data"]
        assert stop["trigger_price"] == "98.00"
        assert stop["fill_price"] == "95"
        assert stop["gap"] == "true"
        assert "OBSERVATION_APPLIED" not in kinds

    def test_same_bar_ambiguity_is_visible_in_the_response(self, api: TestClient) -> None:
        position_id = api.post(
            "/api/paper/positions", json=create_body(), headers=headers()
        ).json()["id"]
        final = observe(api, position_id, ENTRY, (1, "100", "104.50", "97.50", "100")).json()

        ambiguity = [e for e in final["key_events"] if e["type"] == "SAME_BAR_AMBIGUITY"]
        assert len(ambiguity) == 1
        assert ambiguity[0]["data"]["policy"] == "STOP_FIRST"
        assert final["simulation"]["same_bar"] == "STOP_FIRST"
        assert final["realized_gross"] == "-80.00"

    def test_events_are_paged(self, api: TestClient) -> None:
        position_id = api.post(
            "/api/paper/positions", json=create_body(), headers=headers()
        ).json()["id"]
        observe(api, position_id, ENTRY, TARGET_ONE, STOP_ON_REST)

        first = api.get(f"/api/paper/positions/{position_id}/events?limit=3").json()
        assert [e["sequence"] for e in first["items"]] == [1, 2, 3]
        assert first["next_after_sequence"] == 3
        rest = api.get(
            f"/api/paper/positions/{position_id}/events?after_sequence=3&limit=200"
        ).json()
        assert rest["items"][0]["sequence"] == 4
        assert rest["next_after_sequence"] is None
        assert len(first["items"]) + len(rest["items"]) == first["total"]

    def test_close_cancel_and_breakeven(self, api: TestClient) -> None:
        pending = api.post(
            "/api/paper/positions", json=create_body(), headers=headers("cancel-key-000001")
        ).json()
        cancelled = api.post(f"/api/paper/positions/{pending['id']}/cancel")
        assert cancelled.json()["state"] == "CANCELLED"

        opened = api.post(
            "/api/paper/positions", json=create_body(), headers=headers("close-key-0000001")
        ).json()
        observe(api, opened["id"], ENTRY, (1, "101", "103.50", "100.75", "103"))
        moved = api.post(f"/api/paper/positions/{opened['id']}/stop/breakeven").json()
        assert moved["stop"] == "100"
        requested = api.post(f"/api/paper/positions/{opened['id']}/close").json()
        assert requested["close_pending"] is True
        closed = observe(api, opened["id"], (2, "102", "102.50", "101", "102")).json()
        # manual exit at the open 102: (102 - 100) x 10 x 4 = 80
        assert closed["state"] == "CLOSED"
        assert closed["realized_gross"] == "80"


class TestRefusalsOverHttp:
    def test_the_production_composition_refuses_every_new_position(self, clean: Settings) -> None:
        client = client_for(clean, contract=None)
        try:
            response = client.post("/api/paper/positions", json=create_body(), headers=headers())
            listing = client.get("/api/paper/positions").json()
        finally:
            client.__exit__(None, None, None)

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "PRODUCT_METADATA_UNAVAILABLE"
        assert listing["total"] == 0

    def test_an_unverified_multiplier_is_refused(self, clean: Settings) -> None:
        client = client_for(clean, paper_contract(multiplier=unverified("10")))
        try:
            response = client.post("/api/paper/positions", json=create_body(), headers=headers())
        finally:
            client.__exit__(None, None, None)

        assert response.status_code == 422
        assert response.json()["detail"]["code"] in {
            "RISK_NOT_ALLOWED",
            "PRODUCT_NOT_CALCULABLE",
            "POINT_VALUE_UNVERIFIED",
        }

    def test_a_risk_veto_is_never_overridden(self, api: TestClient) -> None:
        response = api.post(
            "/api/paper/positions",
            json=create_body(risk={"mode": "FIXED", "fixed_risk": "10"}),
            headers=headers(),
        )

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "RISK_NOT_ALLOWED"
        assert api.get("/api/paper/positions").json()["total"] == 0

    def test_a_quantity_above_the_risk_allowance_is_refused(self, api: TestClient) -> None:
        response = api.post(
            "/api/paper/positions",
            json=create_body(risk={"mode": "FIXED", "fixed_risk": "1000", "max_contracts": 2}),
            headers=headers(),
        )

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "QUANTITY_EXCEEDS_RISK"

    def test_retry_is_idempotent_and_reuse_conflicts(self, api: TestClient) -> None:
        first = api.post("/api/paper/positions", json=create_body(), headers=headers())
        retry = api.post("/api/paper/positions", json=create_body(), headers=headers())
        reuse = api.post("/api/paper/positions", json=create_body(quantity=3), headers=headers())

        assert first.status_code == 201
        assert retry.status_code == 200
        assert retry.json()["id"] == first.json()["id"]
        assert retry.json()["idempotent_replay"] is True
        assert reuse.status_code == 409
        assert api.get("/api/paper/positions").json()["total"] == 1

    @pytest.mark.parametrize(
        ("rows", "code"),
        [
            ([(-1, "100", "101", "99.50", "100.50")], "OBSERVATION_BEFORE_DECISION"),
            ([(5000, "100", "101", "99.50", "100.50")], "OBSERVATION_NOT_CLOSED"),
        ],
        ids=["stale", "not-yet-closed"],
    )
    def test_bars_outside_the_allowed_time_are_refused(
        self, api: TestClient, rows: list[tuple[int, str, str, str, str]], code: str
    ) -> None:
        position_id = api.post(
            "/api/paper/positions", json=create_body(), headers=headers()
        ).json()["id"]
        response = observe(api, position_id, *rows)

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == code

    def test_out_of_order_and_conflicting_bars_are_refused(self, api: TestClient) -> None:
        position_id = api.post(
            "/api/paper/positions", json=create_body(), headers=headers()
        ).json()["id"]
        observe(api, position_id, ENTRY, TARGET_ONE)

        conflicting = observe(api, position_id, (0, "100", "101", "99", "100.50"))
        assert conflicting.status_code == 422
        assert conflicting.json()["detail"]["code"] == "CONFLICTING_OBSERVATION"

        unsorted = api.post(
            f"/api/paper/positions/{position_id}/observations",
            json={
                "content": "open_time,open,high,low,close,volume\n"
                "2026-03-02T15:00:00+00:00,104,104.5,103.5,104,1\n"
                "2026-03-02T14:00:00+00:00,104,104.5,103.5,104,1\n",
                "source_name": "unsorted.csv",
            },
        )
        assert unsorted.status_code == 422
        assert unsorted.json()["detail"]["code"] == "OBSERVATIONS_FAILED_DATA_QUALITY"

    def test_unknown_position_is_404(self, api: TestClient) -> None:
        assert api.get("/api/paper/positions/PP-000000000000000000000000").status_code == 404
        response = observe(api, "PP-000000000000000000000000", ENTRY)
        assert response.status_code == 404

    def test_a_decision_in_the_future_is_refused(self, api: TestClient) -> None:
        response = api.post(
            "/api/paper/positions",
            json=create_body(decision_time="2099-01-01T00:00:00+00:00"),
            headers=headers(),
        )

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "DECISION_IN_FUTURE"
