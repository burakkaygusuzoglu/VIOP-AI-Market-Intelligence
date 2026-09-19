"""Replay end to end over HTTP, against real PostgreSQL (Phase 11).

The composed application, the real routes, the real store and the real Phase
8/9/10 engines. The only test-specific part is the product resolver: production
composes none, so a replay position there is refused for want of verified
contract facts - which a test below proves, with no override in place.

The leak checks walk the *whole* response body rather than the fields a test
happens to know about. A future candle that reached the browser would have to
appear as text somewhere, whatever shape the payload takes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.contract_metadata.manual_provider import ManualContractMetadataProvider
from app.adapters.persistence.database import Database
from app.adapters.products.futures import FuturesProductResolver
from app.application.paper.service import PaperErrorKind, PaperServiceError
from app.core.config import Settings
from app.domain.common.enums import Timeframe
from app.main import create_app
from tests.factories_paper import paper_contract
from tests.factories_replay import (
    BASE,
    aggregate,
    csv_of,
    dataset,
    five_minute,
    with_absurd_future_candle,
)
from tests.integration.paper_support import truncate
from tests.integration.replay_support import all_prices, upload_payload, window_for

pytestmark = pytest.mark.integration

SESSIONS = "/api/replay/sessions"
ABSURD = "999999999"
START = BASE + timedelta(hours=8)


@pytest.fixture
async def clean(migrated: Settings) -> AsyncIterator[Settings]:
    database = Database(migrated.sqlalchemy_url)
    try:
        await truncate(database)
    finally:
        await database.dispose()
    yield migrated


def client_for(settings: Settings, *, with_products: bool = True) -> TestClient:
    app = create_app(settings)
    client = TestClient(app)
    client.__enter__()
    if with_products:
        # Explicit test composition. Production wires no metadata provider.
        app.state.product_resolver = FuturesProductResolver(
            ManualContractMetadataProvider([paper_contract()])
        )
    return client


@pytest.fixture
def api(clean: Settings) -> Iterator[TestClient]:
    client = client_for(clean)
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


def headers(key: str) -> dict[str, str]:
    return {"Idempotency-Key": key}


def create(api: TestClient, key: str, **changes: Any) -> Any:
    body = upload_payload(dataset(288), replay_start=START)
    body.update(changes)
    return api.post(SESSIONS, json=body, headers=headers(key))


def position_body(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "direction": "LONG",
        "quantity": 2,
        "intended_entry": "100.00",
        "stop": "90.00",
        "targets": [{"price": "130.00", "quantity": 2}],
        "account": {"equity": "100000"},
        "risk": {"mode": "FIXED", "fixed_risk": "1000"},
    }
    payload.update(changes)
    return payload


class TestTheWholeLifecycle:
    async def test_create_step_analyse_trade_advance_measure_and_resume(
        self, api: TestClient
    ) -> None:
        created = create(api, "api-lifecycle-000001")
        assert created.status_code == 201, created.text
        session = created.json()
        session_id = session["id"]
        assert session["simulated"] is True
        assert session["provenance"] == {
            "market_data": "USER_SUPPLIED_HISTORICAL",
            "fills": "SIMULATED",
            "origin": "REPLAY",
            "execution": "DISABLED",
        }
        assert session["cursor"]["replay_as_of"] == START.isoformat()
        assert session["cursor"]["state"] == "READY"
        assert session["cursor"]["version"] == 1
        assert session["cursor"]["revealed_driver_candles"] == 96
        assert session["cursor"]["driver_total_candles"] == 288

        stepped = api.post(f"{SESSIONS}/{session_id}/advance", json={"steps": 1})
        assert stepped.status_code == 200, stepped.text
        body = stepped.json()
        assert body["revealed_boundaries"] == [(START + timedelta(minutes=5)).isoformat()]
        assert body["session"]["cursor"]["version"] == 2
        assert body["session"]["cursor"]["state"] == "IN_PROGRESS"
        assert body["session"]["cursor"]["revealed_driver_candles"] == 97

        analysed = api.post(f"{SESSIONS}/{session_id}/analysis", json={})
        assert analysed.status_code == 200, analysed.text
        analysis = analysed.json()
        assert analysis["replay_as_of"] == body["session"]["cursor"]["replay_as_of"]
        assert analysis["analysis"]["technical_available"] is True

        opened = api.post(
            f"{SESSIONS}/{session_id}/positions",
            json=position_body(),
            headers=headers("api-lifecycle-pos-01"),
        )
        assert opened.status_code == 201, opened.text
        position = opened.json()
        assert position["simulated"] is True
        assert position["state"] == "PENDING_ENTRY"
        assert position["decision_time"] == body["session"]["cursor"]["replay_as_of"]

        advanced = api.post(f"{SESSIONS}/{session_id}/advance", json={"steps": 12})
        assert advanced.status_code == 200, advanced.text
        assert len(advanced.json()["revealed_boundaries"]) == 12
        assert advanced.json()["observed_positions"] == 1

        positions = api.get(f"{SESSIONS}/{session_id}/positions")
        assert positions.status_code == 200
        assert [item["id"] for item in positions.json()] == [position["id"]]
        assert positions.json()[0]["bars_applied"] > 0

        performance = api.get(f"{SESSIONS}/{session_id}/performance")
        assert performance.status_code == 200, performance.text
        measured = performance.json()
        assert measured["position_ids"] == [position["id"]]
        assert measured["performance"]["source"] == "PAPER_SIMULATION"
        assert measured["performance"]["counts"]["total"] == 1

        resumed = api.get(f"{SESSIONS}/{session_id}")
        assert resumed.status_code == 200
        assert resumed.json()["cursor"] == advanced.json()["session"]["cursor"]
        assert resumed.json()["linked_position_ids"] == [position["id"]]

        listed = api.get(SESSIONS)
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [session_id]
        assert listed.json()["total"] == 1

    async def test_a_second_client_resumes_the_same_cursor(
        self, api: TestClient, clean: Settings
    ) -> None:
        created = create(api, "api-lifecycle-000002")
        session_id = created.json()["id"]
        api.post(f"{SESSIONS}/{session_id}/advance", json={"steps": 5})
        expected = api.get(f"{SESSIONS}/{session_id}").json()["cursor"]

        other = client_for(clean)
        try:
            assert other.get(f"{SESSIONS}/{session_id}").json()["cursor"] == expected
        finally:
            other.__exit__(None, None, None)


class TestTheBrowserNeverReceivesTheFuture:
    async def test_no_unrevealed_price_is_anywhere_in_the_payload(self, api: TestClient) -> None:
        rows = with_absurd_future_candle(five_minute(288), 200)
        content = {
            Timeframe.M5: csv_of(rows),
            Timeframe.M15: csv_of(aggregate(rows, Timeframe.M15)),
            Timeframe.H1: csv_of(aggregate(rows, Timeframe.H1)),
        }
        body = upload_payload(content, replay_start=BASE + timedelta(hours=1))
        created = api.post(SESSIONS, json=body, headers=headers("api-leak-00000001"))
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]

        for response in (
            created,
            api.get(f"{SESSIONS}/{session_id}"),
            api.get(f"{SESSIONS}/{session_id}?chart=1H"),
            api.post(f"{SESSIONS}/{session_id}/advance", json={"steps": 3}),
            api.post(f"{SESSIONS}/{session_id}/analysis", json={}),
            api.get(SESSIONS),
        ):
            assert response.status_code == 200 or response.status_code == 201
            assert ABSURD not in response.text, response.url

    async def test_no_candle_in_the_payload_ends_after_the_replay_clock(
        self, api: TestClient
    ) -> None:
        created = create(api, "api-leak-00000002")
        session_id = created.json()["id"]
        detail = api.get(f"{SESSIONS}/{session_id}").json()
        as_of = datetime.fromisoformat(detail["cursor"]["replay_as_of"])
        window = window_for(detail, "5M")
        assert window["candles"]
        for candle in window["candles"]:
            opened = datetime.fromisoformat(candle["open_time"])
            assert opened + timedelta(minutes=5) <= as_of

    async def test_the_payload_carries_only_the_charted_timeframe(self, api: TestClient) -> None:
        """One screen, one bounded read - not every timeframe's whole history."""
        created = create(api, "api-leak-00000003")
        detail = api.get(f"{SESSIONS}/{created.json()['id']}?chart=15M").json()
        assert window_for(detail, "15M")["candles"]
        assert window_for(detail, "5M")["candles"] == []
        assert window_for(detail, "1H")["candles"] == []

    async def test_a_bounded_chart_says_how_many_it_left_out(self, api: TestClient) -> None:
        body = upload_payload(
            {Timeframe.M5: csv_of(five_minute(1000))},
            replay_start=BASE + timedelta(minutes=5 * 900),
        )
        created = api.post(SESSIONS, json=body, headers=headers("api-leak-00000004"))
        window = window_for(created.json(), "5M")
        assert window["revealed"] == 900
        assert len(window["candles"]) == window["window_limit"] == 400
        assert window["truncated"] is True

    async def test_the_analysis_payload_holds_no_future_bar(self, api: TestClient) -> None:
        created = create(api, "api-leak-00000005")
        session_id = created.json()["id"]
        as_of = datetime.fromisoformat(created.json()["cursor"]["replay_as_of"])
        analysed = api.post(f"{SESSIONS}/{session_id}/analysis", json={}).json()
        stamps = [
            value
            for value in all_prices(analysed["analysis"]["chart"])
            if _looks_like_a_timestamp(value)
        ]
        assert stamps
        for value in stamps:
            assert datetime.fromisoformat(value) <= as_of


class TestRetriesAndConcurrency:
    async def test_the_same_create_key_returns_the_same_session_with_200(
        self, api: TestClient
    ) -> None:
        first = create(api, "api-retry-00000001")
        second = create(api, "api-retry-00000001")
        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["idempotent_replay"] is True

    async def test_the_same_key_for_a_different_dataset_is_a_409(self, api: TestClient) -> None:
        create(api, "api-retry-00000002")
        conflict = create(
            api, "api-retry-00000002", replay_start=(BASE + timedelta(hours=9)).isoformat()
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"

    async def test_a_retried_advance_does_not_step_twice(self, api: TestClient) -> None:
        session_id = create(api, "api-retry-00000003").json()["id"]
        key = headers("api-advance-key-0001")
        first = api.post(f"{SESSIONS}/{session_id}/advance", json={"steps": 1}, headers=key)
        again = api.post(f"{SESSIONS}/{session_id}/advance", json={"steps": 1}, headers=key)
        assert first.json()["session"]["cursor"] == again.json()["session"]["cursor"]
        assert again.json()["idempotent_replay"] is True
        assert again.json()["revealed_boundaries"] == []

    async def test_a_stale_version_is_a_409(self, api: TestClient) -> None:
        session_id = create(api, "api-retry-00000004").json()["id"]
        api.post(f"{SESSIONS}/{session_id}/advance", json={"steps": 1})
        stale = api.post(
            f"{SESSIONS}/{session_id}/advance", json={"steps": 1, "expected_version": 1}
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "VERSION_CONFLICT"

    async def test_the_current_version_is_accepted(self, api: TestClient) -> None:
        session_id = create(api, "api-retry-00000005").json()["id"]
        current = api.get(f"{SESSIONS}/{session_id}").json()["cursor"]["version"]
        moved = api.post(
            f"{SESSIONS}/{session_id}/advance",
            json={"steps": 1, "expected_version": current},
        )
        assert moved.status_code == 200
        assert moved.json()["session"]["cursor"]["version"] == current + 1


class TestTypedRefusals:
    async def test_an_unknown_session_is_a_404(self, api: TestClient) -> None:
        response = api.get(f"{SESSIONS}/RS-000000000000000000000000")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "SESSION_NOT_FOUND"

    async def test_stepping_past_the_end_is_a_typed_refusal(self, api: TestClient) -> None:
        body = upload_payload(
            {Timeframe.M5: csv_of(five_minute(14))},
            replay_start=BASE + timedelta(minutes=60),
        )
        created = api.post(SESSIONS, json=body, headers=headers("api-end-000000001"))
        session_id = created.json()["id"]
        api.post(f"{SESSIONS}/{session_id}/advance", json={"steps": 2})
        detail = api.get(f"{SESSIONS}/{session_id}").json()
        assert detail["cursor"]["state"] == "END_OF_DATASET"

        refused = api.post(f"{SESSIONS}/{session_id}/advance", json={"steps": 1})
        assert refused.status_code == 422
        assert refused.json()["detail"]["code"] == "REPLAY_END"

        after = api.get(f"{SESSIONS}/{session_id}").json()
        assert after["cursor"] == detail["cursor"]

    async def test_a_start_before_the_data_is_a_typed_refusal(self, api: TestClient) -> None:
        response = create(api, "api-refuse-0000001", replay_start="2020-01-01T00:00:00+00:00")
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "REPLAY_START_BEFORE_DATA"

    async def test_a_start_after_the_data_is_a_typed_refusal(self, api: TestClient) -> None:
        response = create(api, "api-refuse-0000002", replay_start="2030-01-01T00:00:00+00:00")
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "REPLAY_START_AFTER_DATA"

    async def test_a_driver_with_no_data_is_a_typed_refusal(self, api: TestClient) -> None:
        body = upload_payload(dataset(288, timeframes=(Timeframe.M5,)), driver="1H")
        response = api.post(SESSIONS, json=body, headers=headers("api-refuse-0000003"))
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "DRIVER_TIMEFRAME_MISSING"

    @pytest.mark.parametrize(
        ("content", "name"),
        [
            ("open_time,open,high,low,close,volume\nnot-a-date,1,2,0,1,5\n", "bad-date"),
            (
                "open_time,open,high,low,close,volume\n2026-03-02T09:00:00+00:00,1,0,2,1,5\n",
                "high<low",
            ),
            ("open_time,open,high,low,close,volume\n2026-03-02T09:00:00,1,2,0,1,5\n", "naive"),
            (
                "open_time,open,high,low,close,volume\n2026-03-02T09:00:00+00:00,1,2,0,1,5\n"
                "2026-03-02T09:00:00+00:00,1,2,0,1,5\n",
                "duplicate",
            ),
            (
                "open_time,open,high,low,close,volume\n2026-03-02T09:05:00+00:00,1,2,0,1,5\n"
                "2026-03-02T09:00:00+00:00,1,2,0,1,5\n",
                "out-of-order",
            ),
            (
                "open_time,open,high,low,close,volume\n2026-03-02T09:00:00+00:00,1,2,0,1e999,5\n",
                "huge",
            ),
            ("nonsense\n", "no-header"),
            ("", "empty"),
        ],
    )
    async def test_unusable_data_is_a_typed_refusal_not_a_500(
        self, api: TestClient, content: str, name: str
    ) -> None:
        body = upload_payload({Timeframe.M5: content or "x"})
        response = api.post(SESSIONS, json=body, headers=headers(f"api-bad-{name:.>13}"[:40]))
        assert response.status_code in (413, 422), f"{name}: {response.status_code}"
        assert response.status_code != 500

    async def test_a_cross_session_position_is_a_404(self, api: TestClient) -> None:
        mine = create(api, "api-cross-00000001").json()["id"]
        theirs = create(api, "api-cross-00000002", symbol="TEST_FIXTURE_FUT").json()["id"]
        assert mine != theirs
        opened = api.post(
            f"{SESSIONS}/{mine}/positions",
            json=position_body(),
            headers=headers("api-cross-pos-0001"),
        )
        assert opened.status_code == 201, opened.text
        position_id = opened.json()["id"]

        stolen = api.post(f"{SESSIONS}/{theirs}/positions/{position_id}/close")
        assert stolen.status_code == 404
        assert stolen.json()["detail"]["code"] == "POSITION_NOT_IN_SESSION"

        their_performance = api.get(f"{SESSIONS}/{theirs}/performance").json()
        assert their_performance["position_ids"] == []


class TestProductMetadataTrustBoundary:
    async def test_production_composition_refuses_to_open_a_position(self, clean: Settings) -> None:
        """No override here. A CSV symbol establishes nothing."""
        client = client_for(clean, with_products=False)
        try:
            created = create(client, "api-noproduct-00001")
            assert created.status_code == 201
            opened = client.post(
                f"{SESSIONS}/{created.json()['id']}/positions",
                json=position_body(),
                headers=headers("api-noproduct-pos-1"),
            )
            assert opened.status_code == 422
            assert opened.json()["detail"]["code"] == "PRODUCT_METADATA_UNAVAILABLE"
        finally:
            client.__exit__(None, None, None)

    async def test_the_session_itself_claims_no_product_facts(self, clean: Settings) -> None:
        client = client_for(clean, with_products=False)
        try:
            created = create(client, "api-noproduct-00002")
            payload = created.json()
            text = created.text
            assert payload["plan"]["symbol"] == "TEST_FIXTURE_FUT"
            for claim in ("multiplier", "tick_size", "point_value", "margin", "expiry"):
                assert claim not in text
            assert "VERIFIED_CURRENT_FACT" not in text
        finally:
            client.__exit__(None, None, None)


def _looks_like_a_timestamp(value: str) -> bool:
    return len(value) >= 19 and value[4] == "-" and value[10] in ("T", " ")


class TestTestComposedPaperLifecycleReachesRealFills:
    """The half the production stack cannot reach, run over the same HTTP.

    Production composes no contract metadata provider, so a replay position
    there is refused — proven in `TestProductMetadataTrustBoundary` and in the
    container. This class is the *other* case, and is deliberately kept
    separate from it: with an explicit test-only provider, a position opens,
    fills, partially exits, and the Phase 10 engine measures it. The two must
    never be described as one test.
    """

    async def test_entry_fill_partial_exit_ledger_and_session_performance(
        self, api: TestClient
    ) -> None:
        created = create(api, "api-fills-00000001")
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]

        # Levels chosen from the fixture's own range over the bars this test
        # advances through: entry above the market so the next bar's open
        # fills it, a target inside the range so one leg closes, and a stop
        # far below so the rest stays open.
        opened = api.post(
            f"{SESSIONS}/{session_id}/positions",
            json=position_body(
                quantity=4,
                intended_entry="98.50",
                stop="95.00",
                targets=[{"price": "99.50", "quantity": 2}, {"price": "140.00", "quantity": 2}],
            ),
            headers=headers("api-fills-position-1"),
        )
        assert opened.status_code == 201, opened.text
        position = opened.json()
        position_id = position["id"]
        assert position["state"] == "PENDING_ENTRY"
        assert position["risk"]["outcome"] == "ALLOWED"
        assert position["provenance"]["fills"] == "SIMULATED"

        advanced = api.post(f"{SESSIONS}/{session_id}/advance", json={"steps": 20})
        assert advanced.status_code == 200, advanced.text
        assert advanced.json()["observed_positions"] == 1

        detail = api.get(f"{SESSIONS}/{session_id}/positions").json()[0]
        assert detail["entry_fill_price"] is not None, "the entry never filled"
        assert detail["bars_applied"] >= 20
        assert detail["state"] in {"OPEN", "PARTIALLY_CLOSED", "CLOSED"}
        assert [target["filled"] for target in detail["targets"]][0] is True, (
            "the first target should have been reached"
        )
        assert detail["state"] == "PARTIALLY_CLOSED"
        assert detail["remaining"] == 2
        assert Decimal(detail["realized_gross"]) > 0

        # The ledger is the authority; the projection must agree with it.
        events = api.get(f"/api/paper/positions/{position_id}/events?limit=200").json()
        assert events["total"] >= 20
        kinds = {item["type"] for item in detail["key_events"]}
        assert "ENTRY_FILLED" in kinds
        assert "TARGET_FILLED" in kinds

        measured = api.get(f"{SESSIONS}/{session_id}/performance").json()
        assert measured["position_ids"] == [position_id]
        assert measured["performance"]["counts"]["total"] == 1
        assert measured["performance"]["counts"]["open_exposure"] == 1
        # Partial realized accounting is stated separately, as Phase 10 requires.
        accounting = measured["performance"]["realized_accounting"]
        assert accounting["from_open_positions"] >= 1
        assert accounting["gross"]["status"] == "AVAILABLE"

    async def test_a_manual_close_exits_the_rest_at_replay_time(self, api: TestClient) -> None:
        created = create(api, "api-fills-00000002")
        session_id = created.json()["id"]
        opened = api.post(
            f"{SESSIONS}/{session_id}/positions",
            json=position_body(
                quantity=4,
                intended_entry="98.50",
                stop="95.00",
                targets=[{"price": "140.00", "quantity": 4}],
            ),
            headers=headers("api-fills-position-2"),
        )
        position_id = opened.json()["id"]
        api.post(f"{SESSIONS}/{session_id}/advance", json={"steps": 10})

        asked = api.post(f"{SESSIONS}/{session_id}/positions/{position_id}/close")
        assert asked.status_code == 200, asked.text
        assert asked.json()["close_pending"] is True

        api.post(f"{SESSIONS}/{session_id}/advance", json={"steps": 2})
        closed = api.get(f"{SESSIONS}/{session_id}/positions").json()[0]
        assert closed["state"] == "CLOSED"
        assert closed["remaining"] == 0
        assert closed["unrealized_gross"] == "0"

        measured = api.get(f"{SESSIONS}/{session_id}/performance").json()
        assert measured["performance"]["counts"]["closed"] == 1


class TestAPartialAdvanceOverHttp:
    """The same no-overshoot guarantee, seen from the wire.

    The fault is injected by patching the class the route builds per request,
    which is the only test-specific part: the routes, the store, the ledger and
    PostgreSQL are the real ones.
    """

    async def test_a_retried_advance_finishes_its_command_and_stops(
        self, api: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.application.replay.service import ReplayService
        from tests.integration.test_replay_recovery import CountingPaper

        created = create(api, "api-overshoot-00001")
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]
        opened = api.post(
            f"{SESSIONS}/{session_id}/positions",
            json=position_body(),
            headers=headers("api-overshoot-pos-1"),
        )
        assert opened.status_code == 201, opened.text
        start = api.get(f"{SESSIONS}/{session_id}").json()["cursor"]
        intended = start["revealed_driver_candles"] + 10

        # One budget shared by every service instance the route builds.
        budget: dict[str, Any] = {
            "calls": 0,
            "after": 4,
            "armed": 1,
            "fault": lambda position_id: PaperServiceError(
                PaperErrorKind.UNAVAILABLE,
                "PAPER_STORE_UNAVAILABLE",
                f"the ledger could not be written for {position_id}",
            ),
        }
        original = ReplayService._paper_for

        def failing(self: ReplayService, as_of: Any) -> Any:
            return CountingPaper(original(self, as_of), budget)

        monkeypatch.setattr(ReplayService, "_paper_for", failing)

        failed = api.post(
            f"{SESSIONS}/{session_id}/advance",
            json={"steps": 10},
            headers=headers("api-overshoot-cmd-1"),
        )
        assert failed.status_code == 503, failed.text
        assert "completed 4 of 10 steps" in failed.json()["detail"]["detail"]

        partial = api.get(f"{SESSIONS}/{session_id}").json()["cursor"]
        assert partial["revealed_driver_candles"] == start["revealed_driver_candles"] + 4

        resumed = api.post(
            f"{SESSIONS}/{session_id}/advance",
            json={"steps": 10},
            headers=headers("api-overshoot-cmd-1"),
        )
        assert resumed.status_code == 200, resumed.text
        body = resumed.json()
        assert body["session"]["cursor"]["revealed_driver_candles"] == intended, (
            "the retry overshot the cursor the original command aimed at"
        )
        assert len(body["revealed_boundaries"]) == 6

        again = api.post(
            f"{SESSIONS}/{session_id}/advance",
            json={"steps": 10},
            headers=headers("api-overshoot-cmd-1"),
        )
        assert again.json()["idempotent_replay"] is True
        assert again.json()["session"]["cursor"]["revealed_driver_candles"] == intended

        # The ledger agrees with the cursor: ten bars, none applied twice.
        events = api.get(f"/api/paper/positions/{opened.json()['id']}/events?limit=200").json()
        applied = [
            item["market_time"] for item in events["items"] if item["type"] == "OBSERVATION_APPLIED"
        ]
        assert len(applied) == len(set(applied))
        assert len(applied) == 10
