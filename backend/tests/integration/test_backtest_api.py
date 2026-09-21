"""Backtesting end to end over HTTP, against real PostgreSQL (Phase 12 Part 2A).

The composed application, the real routes, the real store and the real Phase
1/3/9/10/11 engines. The only test-specific part is the product resolver:
production composes none, so a run there is refused for want of verified
contract facts - which a test below proves, with no override in place.

The forgery checks matter more than the happy paths. A client may say *what to
evaluate*; it may not say what happened. Each attempt below adds one field that
would be a financial claim and expects a 422 - not because the value was
inspected and rejected, but because the field does not exist on the model.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.contract_metadata.manual_provider import ManualContractMetadataProvider
from app.adapters.persistence.database import Database
from app.adapters.products.futures import FuturesProductResolver
from app.core.config import Settings
from app.main import create_app
from tests.factories_paper import paper_contract
from tests.factories_replay import BASE
from tests.integration.backtest_support import seed_dataset, trending
from tests.integration.paper_support import truncate

pytestmark = pytest.mark.integration

BACKTEST = "/api/backtest"
RUNS = f"{BACKTEST}/runs"

ACCOUNT = {"equity": "100000", "used_margin": "0"}
RISK = {"mode": "FIXED", "fixed_risk": "5000"}


@pytest.fixture
async def seeded(migrated: Settings) -> AsyncIterator[tuple[Settings, str]]:
    """A clean database holding one immutable dataset, and its digest."""
    database = Database(migrated.sqlalchemy_url)
    try:
        await truncate(database)
        dataset = await seed_dataset(database, trending(260))
    finally:
        await database.dispose()
    yield migrated, dataset.dataset_id


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
def api(seeded: tuple[Settings, str]) -> Iterator[TestClient]:
    client = client_for(seeded[0])
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


@pytest.fixture
def dataset_id(seeded: tuple[Settings, str]) -> str:
    return seeded[1]


def body(dataset_id: str, **changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "dataset_id": dataset_id,
        "driver_timeframe": "5M",
        "start": (BASE + timedelta(minutes=5 * 60)).isoformat(),
        "end": (BASE + timedelta(minutes=5 * 200)).isoformat(),
        "strategy_id": "ema-crossover-atr",
        "strategy_version": "1.0.0",
        "account": ACCOUNT,
        "risk": RISK,
    }
    payload.update(changes)
    return payload


def headers(key: str) -> dict[str, str]:
    return {"Idempotency-Key": key}


def create(api: TestClient, dataset_id: str, key: str, **changes: Any) -> Any:
    return api.post(RUNS, json=body(dataset_id, **changes), headers=headers(key))


def code_of(response: Any) -> str:
    code = response.json()["detail"]["code"]
    assert isinstance(code, str)
    return code


# ----------------------------------------------------------------------
# Route inventory and catalogue
# ----------------------------------------------------------------------


class TestTheSurfaceIsWhatItClaims:
    def test_the_expected_routes_exist_and_no_others(self, api: TestClient) -> None:
        paths = {
            path
            for path in api.app.openapi()["paths"]  # type: ignore[attr-defined]
            if path.startswith("/api/backtest")
        }

        assert paths == {
            "/api/backtest/capability",
            "/api/backtest/strategies",
            "/api/backtest/datasets",
            "/api/backtest/runs",
            "/api/backtest/runs/{run_id}",
            "/api/backtest/runs/{run_id}/abandon",
            "/api/backtest/runs/{run_id}/trace",
            "/api/backtest/runs/{run_id}/positions",
            "/api/backtest/runs/{run_id}/positions/{position_id}/events",
            "/api/backtest/runs/{run_id}/performance",
        }

    def test_the_catalogue_publishes_only_registered_strategies(self, api: TestClient) -> None:
        payload = api.get(f"{BACKTEST}/strategies").json()

        assert [item["identifier"] for item in payload["strategies"]] == ["ema-crossover-atr"]
        assert payload["strategies"][0]["version"] == "1.0.0"

    def test_the_catalogue_says_the_parameters_are_not_configurable(self, api: TestClient) -> None:
        """They are pinned in code, so a form must not offer to change them."""
        (strategy,) = api.get(f"{BACKTEST}/strategies").json()["strategies"]

        assert strategy["parameters"]
        assert all(not item["configurable"] for item in strategy["parameters"])

    def test_the_catalogue_describes_the_actual_implementation(self, api: TestClient) -> None:
        (strategy,) = api.get(f"{BACKTEST}/strategies").json()["strategies"]
        names = {item["name"]: item["value"] for item in strategy["parameters"]}

        assert names["fast_period"] == "9"
        assert names["slow_period"] == "20"
        assert strategy["warm_up_bars"] == 21
        assert "not a recommendation" in strategy["summary"]

    def test_the_dataset_list_is_bounded_and_states_its_total(
        self, api: TestClient, dataset_id: str
    ) -> None:
        payload = api.get(f"{BACKTEST}/datasets", params={"limit": 1}).json()

        assert payload["total"] == 1
        assert payload["items"][0]["dataset_id"] == dataset_id
        assert "immutably" in payload["items"][0]["provenance"]


# ----------------------------------------------------------------------
# Metadata capability
# ----------------------------------------------------------------------


class TestMetadataCapabilityIsStatedBeforeItIsNeeded:
    def test_a_deployment_without_a_provider_says_so(self, seeded: tuple[Settings, str]) -> None:
        client = client_for(seeded[0], with_products=False)
        try:
            payload = client.get(f"{BACKTEST}/capability").json()
        finally:
            client.__exit__(None, None, None)

        assert payload["financial_execution_available"] is False
        assert payload["refusal_code"] == "PRODUCT_METADATA_UNAVAILABLE"
        assert "does not establish" in payload["reason"] or "do not establish" in payload["reason"]

    def test_a_run_without_verified_metadata_is_refused_not_faked(
        self, seeded: tuple[Settings, str]
    ) -> None:
        """No tidy zero-trade success. A refusal, with the typed code."""
        settings, dataset_id = seeded
        client = client_for(settings, with_products=False)
        try:
            response = create(client, dataset_id, "api-nometa-000000001")
        finally:
            client.__exit__(None, None, None)

        assert response.status_code == 422
        assert code_of(response) == "PRODUCT_METADATA_UNAVAILABLE"

    def test_capability_reports_the_runners_real_bounds(self, api: TestClient) -> None:
        payload = api.get(f"{BACKTEST}/capability").json()

        assert payload["max_boundaries"] == 2_500
        assert payload["max_positions"] == 200
        assert payload["max_warm_up_bars"] == 500


# ----------------------------------------------------------------------
# The trust boundary
# ----------------------------------------------------------------------


class TestTheClientCannotStateAResult:
    @pytest.mark.parametrize(
        "forged",
        [
            {"result_digest": "BC-" + "0" * 32},
            {"status": "COMPLETED"},
            {"realized_gross": "1000"},
            {"realized_net": "1000"},
            {"unrealized_gross": "1000"},
            {"fill_price": "100"},
            {"entry_fill_price": "100"},
            {"positions": []},
            {"decisions": []},
            {"trace": []},
            {"performance": {}},
            {"win_rate": "1.0"},
            {"boundaries_evaluated": 10},
            {"configuration": "BC-" + "0" * 32},
        ],
    )
    def test_a_financial_field_does_not_exist_on_the_request(
        self, api: TestClient, dataset_id: str, forged: dict[str, Any]
    ) -> None:
        response = create(api, dataset_id, "api-forge-0000000001", **forged)

        assert response.status_code == 422

    @pytest.mark.parametrize(
        "forged",
        [
            {"multiplier": "10"},
            {"tick_size": "0.25"},
            {"initial_margin": "500"},
            {"product_snapshot": {}},
            {"asset_class": "FUTURES"},
            {"contract": {}},
        ],
    )
    def test_product_metadata_cannot_be_supplied_by_the_client(
        self, api: TestClient, dataset_id: str, forged: dict[str, Any]
    ) -> None:
        response = create(api, dataset_id, "api-meta-00000000001", **forged)

        assert response.status_code == 422

    @pytest.mark.parametrize(
        "forged", [{"candles": []}, {"replay_as_of": "2026-01-01T00:00:00+00:00"}, {"as_of": "x"}]
    )
    def test_market_facts_cannot_be_supplied_by_the_client(
        self, api: TestClient, dataset_id: str, forged: dict[str, Any]
    ) -> None:
        response = create(api, dataset_id, "api-market-000000001", **forged)

        assert response.status_code == 422

    def test_an_unknown_field_is_refused_outright(self, api: TestClient, dataset_id: str) -> None:
        response = create(api, dataset_id, "api-unknown-00000001", surprise=1)

        assert response.status_code == 422


# ----------------------------------------------------------------------
# Configuration validation
# ----------------------------------------------------------------------


class TestInvalidConfigurationIsRefusedWithATypedCode:
    def test_an_unknown_strategy_is_refused(self, api: TestClient, dataset_id: str) -> None:
        response = create(api, dataset_id, "api-badstrat-0000001", strategy_id="nobody-wrote-this")

        assert response.status_code == 422
        assert code_of(response) == "STRATEGY_UNSUPPORTED"

    def test_an_unknown_version_is_refused(self, api: TestClient, dataset_id: str) -> None:
        response = create(api, dataset_id, "api-badver-000000001", strategy_version="0.9.0")

        assert response.status_code == 422
        assert code_of(response) == "STRATEGY_UNSUPPORTED"

    def test_a_backwards_interval_is_refused(self, api: TestClient, dataset_id: str) -> None:
        response = create(
            api,
            dataset_id,
            "api-backwards-000001",
            start=(BASE + timedelta(hours=4)).isoformat(),
            end=BASE.isoformat(),
        )

        assert response.status_code == 422
        assert code_of(response) == "INTERVAL_INVALID"

    def test_a_naive_timestamp_is_refused(self, api: TestClient, dataset_id: str) -> None:
        response = create(api, dataset_id, "api-naive-0000000001", start="2026-03-02T09:00:00")

        assert response.status_code == 422

    def test_an_unknown_dataset_is_a_404_style_refusal(self, api: TestClient) -> None:
        response = api.post(
            RUNS,
            json=body("RD-" + "0" * 32),
            headers=headers("api-ghost-0000000001"),
        )

        assert response.status_code == 404
        assert code_of(response) == "DATASET_NOT_FOUND"

    def test_an_invalid_risk_configuration_is_refused(
        self, api: TestClient, dataset_id: str
    ) -> None:
        response = create(
            api, dataset_id, "api-badrisk-00000001", risk={"mode": "FIXED", "fixed_risk": "-1"}
        )

        assert response.status_code == 422
        assert code_of(response) == "RISK_CONFIGURATION_INVALID"

    def test_an_unsupported_simulation_rules_version_is_refused(
        self, api: TestClient, dataset_id: str
    ) -> None:
        response = create(
            api,
            dataset_id,
            "api-badsim-000000001",
            simulation={"rules_version": "paper-sim/v99"},
        )

        assert response.status_code == 422

    def test_a_resource_limit_maps_to_413(self, api: TestClient) -> None:
        """The bound itself is enforced by the runner and proven against it in
        ``test_backtest_bounds``; what this asserts is that the API reports it
        as "too large" rather than as a malformed request or a server fault."""
        from app.api.routes.backtest import _STATUS
        from app.application.backtest.service import BacktestErrorKind

        assert _STATUS[BacktestErrorKind.TOO_LARGE] == 413
        assert _STATUS[BacktestErrorKind.CONFLICT] == 409
        assert _STATUS[BacktestErrorKind.NOT_FOUND] == 404
        assert _STATUS[BacktestErrorKind.UNAVAILABLE] == 503

    def test_a_window_with_no_candles_in_it_is_refused(
        self, api: TestClient, dataset_id: str
    ) -> None:
        """Nothing evaluated is not the same answer as nothing found."""
        response = create(
            api,
            dataset_id,
            "api-emptywin-000001",
            start=(BASE + timedelta(days=400)).isoformat(),
            end=(BASE + timedelta(days=401)).isoformat(),
        )

        assert response.status_code == 422
        assert code_of(response) == "INTERVAL_EMPTY"

    def test_a_short_idempotency_key_is_refused(self, api: TestClient, dataset_id: str) -> None:
        response = api.post(RUNS, json=body(dataset_id), headers=headers("short"))

        assert response.status_code == 422


# ----------------------------------------------------------------------
# Creation, idempotency and lifecycle
# ----------------------------------------------------------------------


class TestCreationAndIdempotency:
    def test_a_run_is_created_and_completes(self, api: TestClient, dataset_id: str) -> None:
        response = create(api, dataset_id, "api-create-000000001")

        assert response.status_code == 201
        payload = response.json()
        assert payload["status"] == "COMPLETED"
        assert payload["results_are_final"] is True
        assert payload["totals"]["result_digest"]
        assert "No order was placed" in payload["provenance"]

    def test_the_same_key_and_configuration_replays_the_same_run(
        self, api: TestClient, dataset_id: str
    ) -> None:
        first = create(api, dataset_id, "api-idem-00000000001")
        again = create(api, dataset_id, "api-idem-00000000001")

        assert first.status_code == 201
        assert again.status_code == 200
        assert again.json()["run_id"] == first.json()["run_id"]
        assert again.json()["totals"] == first.json()["totals"]

    def test_the_same_key_with_a_different_configuration_is_a_conflict(
        self, api: TestClient, dataset_id: str
    ) -> None:
        """Returning the old run would answer a question nobody asked."""
        create(api, dataset_id, "api-conflict-000001")

        response = create(
            api,
            dataset_id,
            "api-conflict-000001",
            risk={"mode": "FIXED", "fixed_risk": "1000"},
        )

        assert response.status_code == 409
        assert code_of(response) == "ATTEMPT_KEY_REUSED"

    def test_a_second_key_over_the_same_question_is_a_separate_run(
        self, api: TestClient, dataset_id: str
    ) -> None:
        first = create(api, dataset_id, "api-twice-0000000001").json()
        second = create(api, dataset_id, "api-twice-0000000002").json()

        assert first["run_id"] != second["run_id"]
        assert first["configuration_fingerprint"] == second["configuration_fingerprint"]
        assert first["totals"]["result_digest"] == second["totals"]["result_digest"]

    def test_the_run_list_is_bounded_and_states_its_total(
        self, api: TestClient, dataset_id: str
    ) -> None:
        create(api, dataset_id, "api-list-00000000001")
        create(api, dataset_id, "api-list-00000000002")

        payload = api.get(RUNS, params={"limit": 1}).json()

        assert payload["total"] == 2
        assert len(payload["items"]) == 1
        assert payload["limit"] == 1

    def test_an_oversized_page_is_refused(self, api: TestClient) -> None:
        assert api.get(RUNS, params={"limit": 1000}).status_code == 422


# ----------------------------------------------------------------------
# Reading a run
# ----------------------------------------------------------------------


class TestReadingARun:
    def test_an_unknown_run_is_a_404(self, api: TestClient) -> None:
        response = api.get(f"{RUNS}/BR-{'0' * 24}")

        assert response.status_code == 404
        assert code_of(response) == "RUN_NOT_FOUND"

    def test_a_malformed_run_id_never_reaches_the_store(self, api: TestClient) -> None:
        assert api.get(f"{RUNS}/not-a-run-id").status_code == 422

    def test_the_detail_carries_the_frozen_configuration(
        self, api: TestClient, dataset_id: str
    ) -> None:
        created = create(api, dataset_id, "api-detail-000000001").json()

        payload = api.get(f"{RUNS}/{created['run_id']}").json()

        assert payload["configuration"]["dataset_id"] == dataset_id
        assert payload["configuration"]["strategy_version"] == "1.0.0"
        assert payload["configuration"]["product_snapshot"]

    def test_the_trace_is_paginated_and_states_the_whole_total(
        self, api: TestClient, dataset_id: str
    ) -> None:
        created = create(api, dataset_id, "api-trace-0000000001").json()

        page = api.get(f"{RUNS}/{created['run_id']}/trace", params={"limit": 5}).json()

        assert len(page["items"]) == 5
        assert page["total"] == created["totals"]["decision_count"]
        assert page["total"] > 5

    def test_a_trace_page_beyond_the_bound_is_refused(
        self, api: TestClient, dataset_id: str
    ) -> None:
        created = create(api, dataset_id, "api-tracebound-0001").json()

        response = api.get(f"{RUNS}/{created['run_id']}/trace", params={"limit": 5000})

        assert response.status_code == 422

    def test_a_refusal_in_the_trace_is_not_a_trade(self, api: TestClient, dataset_id: str) -> None:
        created = create(api, dataset_id, "api-tracekind-00001").json()

        page = api.get(f"{RUNS}/{created['run_id']}/trace", params={"limit": 200}).json()
        outcomes = {item["outcome"] for item in page["items"]}

        assert outcomes <= {
            "NO_SIGNAL",
            "WAIT",
            "ENTERED",
            "REFUSED_BY_RISK",
            "REFUSED_BY_ENGINE",
            "EXIT_REQUESTED",
            "HOLDING",
        }
        assert not any(
            item["outcome"] == "NO_SIGNAL" and item["position_id"] for item in page["items"]
        )

    def test_positions_carry_their_lifecycle_and_provenance(
        self, api: TestClient, dataset_id: str
    ) -> None:
        created = create(api, dataset_id, "api-positions-00001").json()

        page = api.get(f"{RUNS}/{created['run_id']}/positions").json()

        assert page["total"] == created["totals"]["position_count"]
        item = page["items"][0]
        assert item["origin"] == "STRATEGY_BACKTEST"
        assert "Simulated fill" in item["provenance"]
        assert item["realized_net"] is None  # fees not modelled
        assert item["entry_fill_price"]

    def test_a_positions_ledger_is_paginated(self, api: TestClient, dataset_id: str) -> None:
        created = create(api, dataset_id, "api-events-000000001").json()
        page = api.get(f"{RUNS}/{created['run_id']}/positions").json()
        position_id = page["items"][0]["position_id"]

        events = api.get(
            f"{RUNS}/{created['run_id']}/positions/{position_id}/events", params={"limit": 2}
        ).json()

        assert len(events["items"]) == 2
        assert events["total"] > 2
        assert [item["sequence"] for item in events["items"]] == sorted(
            item["sequence"] for item in events["items"]
        )

    def test_one_run_cannot_read_another_runs_position(
        self, api: TestClient, dataset_id: str
    ) -> None:
        first = create(api, dataset_id, "api-cross-0000000001").json()
        second = create(api, dataset_id, "api-cross-0000000002").json()
        mine = api.get(f"{RUNS}/{first['run_id']}/positions").json()["items"][0]["position_id"]

        response = api.get(f"{RUNS}/{second['run_id']}/positions/{mine}/events")

        assert response.status_code == 404
        assert code_of(response) == "POSITION_NOT_FOUND"

    def test_performance_comes_from_phase_ten(self, api: TestClient, dataset_id: str) -> None:
        created = create(api, dataset_id, "api-perf-00000000001").json()

        payload = api.get(f"{RUNS}/{created['run_id']}/performance").json()

        assert payload["run_id"] == created["run_id"]
        assert payload["status"] == "COMPLETED"
        assert payload["performance"]["basis"]
        assert "realized_gross" in payload["performance"]


# ----------------------------------------------------------------------
# Abandon
# ----------------------------------------------------------------------


class TestAbandon:
    def test_a_completed_run_cannot_be_abandoned(self, api: TestClient, dataset_id: str) -> None:
        created = create(api, dataset_id, "api-abandon-0000001").json()

        response = api.post(f"{RUNS}/{created['run_id']}/abandon")

        assert response.status_code == 409
        assert code_of(response) == "RUN_ALREADY_COMPLETED"
        assert api.get(f"{RUNS}/{created['run_id']}").json()["status"] == "COMPLETED"

    def test_abandoning_an_unknown_run_is_a_404(self, api: TestClient) -> None:
        response = api.post(f"{RUNS}/BR-{'0' * 24}/abandon")

        assert response.status_code == 404
        assert code_of(response) == "RUN_NOT_FOUND"


# ----------------------------------------------------------------------
# Isolation
# ----------------------------------------------------------------------


class TestBacktestPositionsStayOutOfThePaperPopulation:
    def test_the_paper_list_is_empty_after_a_run(self, api: TestClient, dataset_id: str) -> None:
        create(api, dataset_id, "api-isolate-0000001")

        payload = api.get("/api/paper/positions").json()

        assert payload["total"] == 0

    def test_the_paper_performance_view_is_empty_after_a_run(
        self, api: TestClient, dataset_id: str
    ) -> None:
        create(api, dataset_id, "api-isolate-0000002")

        payload = api.get("/api/paper/performance").json()

        assert payload["counts"]["total"] == 0


# ----------------------------------------------------------------------
# An internal defect is not a user error
# ----------------------------------------------------------------------


class TestAnUnexpectedStrategyDefect:
    """A programming bug must be reported as one, and must leak nothing.

    The exception raised below carries a fake connection string and this
    file's absolute path, because those are exactly the things that travel
    when an exception's text is used as a response body.
    """

    SECRET = "sk-do-not-leak-me-0001"

    def _explode(self, api: TestClient, dataset_id: str, key: str) -> Any:
        from app.domain.backtest.strategies import ema_crossover

        original = ema_crossover.EmaCrossoverStrategy.decide

        def boom(self: Any, context: Any) -> Any:
            raise RuntimeError(
                f"defect; db=postgresql://user:{TestAnUnexpectedStrategyDefect.SECRET}@h/d "
                f"file={__file__}"
            )

        ema_crossover.EmaCrossoverStrategy.decide = boom  # type: ignore[method-assign]
        try:
            return api.post(RUNS, json=body(dataset_id), headers=headers(key))
        finally:
            ema_crossover.EmaCrossoverStrategy.decide = original  # type: ignore[method-assign]

    def test_it_is_a_500_with_a_typed_code(self, api: TestClient, dataset_id: str) -> None:
        response = self._explode(api, dataset_id, "api-defect-000000001")

        assert response.status_code == 500
        assert code_of(response) == "INTERNAL_ERROR"

    def test_it_is_not_disguised_as_a_user_or_metadata_problem(
        self, api: TestClient, dataset_id: str
    ) -> None:
        response = self._explode(api, dataset_id, "api-defect-000000002")

        assert code_of(response) != "PRODUCT_METADATA_UNAVAILABLE"
        assert response.json()["detail"]["kind"] == "INTERNAL"
        assert "nothing about the request needs changing" in response.json()["detail"]["detail"]

    @pytest.mark.parametrize(
        "leak", [SECRET, "postgresql://", "Traceback", "test_backtest_api", "app.domain"]
    )
    def test_nothing_internal_reaches_the_client(
        self, api: TestClient, dataset_id: str, leak: str
    ) -> None:
        response = self._explode(api, dataset_id, "api-defect-000000003")

        assert leak not in response.text

    def test_the_run_is_terminalised_rather_than_left_pending(
        self, api: TestClient, dataset_id: str
    ) -> None:
        """A PENDING row would read as work still in progress."""
        self._explode(api, dataset_id, "api-defect-000000004")

        (summary,) = api.get(RUNS).json()["items"]
        detail = api.get(f"{RUNS}/{summary['run_id']}").json()
        assert detail["status"] == "FAILED"
        assert detail["results_are_final"] is False
        assert detail["failure_code"] == "INTERNAL_ERROR"

    def test_no_partial_financial_result_survives(self, api: TestClient, dataset_id: str) -> None:
        self._explode(api, dataset_id, "api-defect-000000005")

        (summary,) = api.get(RUNS).json()["items"]
        detail = api.get(f"{RUNS}/{summary['run_id']}").json()
        assert detail["totals"]["position_count"] == 0
        assert detail["totals"]["decision_count"] == 0
        assert detail["totals"]["result_digest"] is None

    def test_abandoning_the_failed_run_is_harmless(self, api: TestClient, dataset_id: str) -> None:
        self._explode(api, dataset_id, "api-defect-000000006")
        (summary,) = api.get(RUNS).json()["items"]

        response = api.post(f"{RUNS}/{summary['run_id']}/abandon")

        assert response.status_code == 200
        assert response.json()["status"] == "FAILED"


# ----------------------------------------------------------------------
# Guards added after a mutation sweep found them missing
# ----------------------------------------------------------------------


class TestTheReadModelReportsWhatTheLedgerSays:
    """Three gaps a mutation sweep exposed, each closed by an assertion.

    A projection is the easiest place to hide a second formula: it looks like
    presentation, it is not where anybody goes looking for arithmetic, and a
    doubled number there is indistinguishable from a doubled number anywhere
    else by the time it reaches a screen.
    """

    def test_the_positions_realized_gross_equals_its_ledger(
        self, api: TestClient, dataset_id: str
    ) -> None:
        """Mutation R: doubling the figure in the projection went unnoticed."""
        created = create(api, dataset_id, "api-ledgertie-00001").json()
        (position,) = api.get(f"{RUNS}/{created['run_id']}/positions").json()["items"][:1]

        events = api.get(
            f"{RUNS}/{created['run_id']}/positions/{position['position_id']}/events",
            params={"limit": 200},
        ).json()
        closed = [item for item in events["items"] if item["type"] == "POSITION_CLOSED"]
        assert closed, "this fixture's first position closes"
        assert position["realized_gross"] == closed[-1]["data"]["realized_gross"]

    def test_an_unmodelled_fee_leaves_no_net_anywhere(
        self, api: TestClient, dataset_id: str
    ) -> None:
        """Mutation F: net must be absent, not equal to gross."""
        created = create(api, dataset_id, "api-nonet-000000001").json()
        page = api.get(f"{RUNS}/{created['run_id']}/positions").json()

        for item in page["items"]:
            assert item["fees_total"] is None
            assert item["realized_net"] is None
            assert item["realized_net"] != item["realized_gross"]

    def test_the_trace_is_ordered_by_market_time_ascending(
        self, api: TestClient, dataset_id: str
    ) -> None:
        """Mutation Q: reversing the order changed nothing that was checked."""
        created = create(api, dataset_id, "api-traceorder-0001").json()

        page = api.get(f"{RUNS}/{created['run_id']}/trace", params={"limit": 50}).json()
        sequences = [item["sequence"] for item in page["items"]]
        moments = [item["as_of"] for item in page["items"]]

        assert sequences == sorted(sequences)
        assert sequences[0] == 1
        assert moments == sorted(moments)

    def test_a_second_trace_page_continues_where_the_first_ended(
        self, api: TestClient, dataset_id: str
    ) -> None:
        created = create(api, dataset_id, "api-tracenext-0001").json()

        first = api.get(f"{RUNS}/{created['run_id']}/trace", params={"limit": 10}).json()
        second = api.get(
            f"{RUNS}/{created['run_id']}/trace", params={"offset": 10, "limit": 10}
        ).json()

        assert first["items"][-1]["sequence"] + 1 == second["items"][0]["sequence"]
        assert first["items"][-1]["as_of"] <= second["items"][0]["as_of"]


class TestTheServerLogIsADisclosureChannelToo:
    """The HTTP body was clean long before the log was.

    An earlier version logged this boundary with ``logger.exception``. A
    captured-log probe then found the injected connection string, its password
    and the full traceback in the server log - while every HTTP assertion
    above was passing. These tests read the log itself.
    """

    SECRET = "sk-do-not-leak-me-0002"
    DSN = f"postgresql://viop_user:{SECRET}@db.internal:5432/prod"

    def _explode_and_capture(
        self, api: TestClient, dataset_id: str, key: str, caplog: pytest.LogCaptureFixture
    ) -> str:
        from app.domain.backtest.strategies import ema_crossover

        original = ema_crossover.EmaCrossoverStrategy.decide

        def boom(self: Any, context: Any) -> Any:
            raise RuntimeError(
                f"defect; dsn={TestTheServerLogIsADisclosureChannelToo.DSN} file={__file__}"
            )

        ema_crossover.EmaCrossoverStrategy.decide = boom  # type: ignore[method-assign]
        caplog.set_level(logging.DEBUG)
        try:
            api.post(RUNS, json=body(dataset_id), headers=headers(key))
        finally:
            ema_crossover.EmaCrossoverStrategy.decide = original  # type: ignore[method-assign]

        rendered = []
        for record in caplog.records:
            rendered.append(record.getMessage())
            rendered.append(repr(record.__dict__))
            if record.exc_info:
                rendered.append(logging.Formatter().formatException(record.exc_info))
            if record.exc_text:
                rendered.append(record.exc_text)
        return "\n".join(rendered)

    @pytest.mark.parametrize(
        "leak",
        [
            SECRET,
            "postgresql://viop_user",
            "defect; dsn=",
            "Traceback",
            "test_backtest_api",
        ],
    )
    def test_no_sensitive_content_reaches_the_log(
        self,
        api: TestClient,
        dataset_id: str,
        caplog: pytest.LogCaptureFixture,
        leak: str,
    ) -> None:
        logged = self._explode_and_capture(api, dataset_id, "api-logleak-00000001", caplog)

        assert leak not in logged

    def test_no_record_carries_a_traceback(
        self, api: TestClient, dataset_id: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        self._explode_and_capture(api, dataset_id, "api-logleak-00000002", caplog)

        failures = [
            r for r in caplog.records if r.getMessage() == "backtest run failed unexpectedly"
        ]
        assert failures, "the failure must still be logged"
        assert all(record.exc_info is None for record in failures)

    def test_the_log_keeps_what_an_operator_can_act_on(
        self, api: TestClient, dataset_id: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        self._explode_and_capture(api, dataset_id, "api-logleak-00000003", caplog)

        (record,) = [
            r for r in caplog.records if r.getMessage() == "backtest run failed unexpectedly"
        ]
        assert record.code == "INTERNAL_ERROR"  # type: ignore[attr-defined]
        assert record.run_id.startswith("BR-")  # type: ignore[attr-defined]
        assert record.strategy == "ema-crossover-atr"  # type: ignore[attr-defined]
        assert record.error_type == "RuntimeError"  # type: ignore[attr-defined]
