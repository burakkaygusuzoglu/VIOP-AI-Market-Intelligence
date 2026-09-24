"""The Shadow API end to end, against real PostgreSQL (Phase 14 Part 2A).

The composed application, the real routes, the real replay store, the real
stored-dataset playback and the real shadow journal. A dataset is uploaded the
way a person uploads one, a live session plays it, and a shadow run watches
that session.

As in the Phase 13 tests, the production composition is where wiring and
refusal are asserted, and a *fast playback* - the same catalog class playing
the same stored candles with a short gap - is where a workflow is run to its
end. The shadow workspace is rebuilt over that faster live workspace, because a
shadow run must watch the session that actually exists.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.adapters.live.dataset_playback import ReplayDatasetCatalog
from app.adapters.market_data.csv_provider import CsvCandleTextParser
from app.adapters.persistence.database import Database
from app.adapters.persistence.shadow_store import SqlAlchemyShadowStore
from app.application.live.catalog import PlaybackPace
from app.application.live.workspace import LiveWorkspace
from app.application.shadow.ports import StoredShadowRun
from app.application.shadow.service import ShadowRunner
from app.application.shadow.workspace import ShadowWorkspace
from app.core.config import Settings
from app.domain.backtest.strategies.ema_crossover import IDENTIFIER, VERSION
from app.domain.common.enums import Timeframe
from app.domain.shadow.run import ShadowRunStatus
from tests.integration.paper_support import truncate
from tests.integration.test_live_api import HeldOpenCatalog, create, open_client, upload

pytestmark = pytest.mark.integration

SHADOW = "/api/shadow"
PLAYBACK = dict.fromkeys(PlaybackPace, 0.002)


@pytest.fixture
async def clean(migrated: Settings) -> AsyncIterator[Settings]:
    database = Database(migrated.sqlalchemy_url)
    try:
        await truncate(database)
    finally:
        await database.dispose()
    yield migrated


def on_loop(client: TestClient, function: Any, *args: Any) -> Any:
    """Run a coroutine function on the application's own event loop."""
    assert client.portal is not None
    return client.portal.call(function, *args)


def rebuild(client: TestClient, catalog_class: type[ReplayDatasetCatalog]) -> ShadowWorkspace:
    """A faster live workspace over the same store and clock, and a shadow
    workspace watching *it* - the same classes production composes."""
    app: Any = client.app
    on_loop(client, app.state.shadow_workspace.shutdown)
    on_loop(client, app.state.live_workspace.shutdown)
    live = LiveWorkspace(
        catalog=catalog_class(app.state.replay_store, spacing=PLAYBACK),
        clock=app.state.clock,
        parser=CsvCandleTextParser(),
    )
    store = SqlAlchemyShadowStore(app.state.database)
    shadow = ShadowWorkspace(
        runner=ShadowRunner(store=store, clock=app.state.clock, parser=CsvCandleTextParser()),
        store=store,
        live=live,
    )
    app.state.live_workspace = live
    app.state.shadow_workspace = shadow
    return shadow


@pytest.fixture
def api(clean: Settings) -> Iterator[TestClient]:
    client = open_client(clean)
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


@pytest.fixture
def fast(api: TestClient) -> Iterator[TestClient]:
    shadow = rebuild(api, ReplayDatasetCatalog)
    try:
        yield api
    finally:
        on_loop(api, shadow.shutdown)


@pytest.fixture
def held(api: TestClient) -> Iterator[TestClient]:
    shadow = rebuild(api, HeldOpenCatalog)
    try:
        yield api
    finally:
        on_loop(api, shadow.shutdown)


def session_for(api: TestClient, key: str) -> str:
    response = create(api, upload(api, key.ljust(16, "0")))
    assert response.status_code == 201, response.text
    session_id = response.json()["id"]
    assert isinstance(session_id, str)
    return session_id


def run_body(session_id: str, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "session_id": session_id,
        "strategy_id": IDENTIFIER,
        "strategy_version": VERSION,
        "driver": "5M",
        "timeframes": ["5M"],
        "analysis_evidence": False,
    }
    body.update(changes)
    return body


def wait_ended(api: TestClient, run_id: str, timeout: float = 20.0) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        body = api.get(f"{SHADOW}/runs/{run_id}").json()
        if body["status"] == "ENDED":
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"run did not end: {body['status']}")
        time.sleep(0.05)


def every_key(node: object) -> list[str]:
    if isinstance(node, dict):
        return [key for key, value in node.items() for key in [key, *every_key(value)]]
    if isinstance(node, list):
        return [key for value in node for key in every_key(value)]
    return []


def whole_journal(api: TestClient, run_id: str) -> list[Any]:
    items: list[Any] = []
    after = 0
    while True:
        page = api.get(f"{SHADOW}/runs/{run_id}/journal", params={"after": after, "limit": 100})
        assert page.status_code == 200, page.text
        body = page.json()
        items.extend(body["items"])
        if body["next_after"] is None or len(body["items"]) < 100:
            return items
        after = body["next_after"]


# ----------------------------------------------------------------------
# Composition, capability and the deployment boundary
# ----------------------------------------------------------------------


class TestComposition:
    def test_the_capability_says_what_shadow_can_and_cannot_do(self, api: TestClient) -> None:
        body = api.get(f"{SHADOW}/capability").json()

        assert body["available"] is True
        assert body["provenance"] == "SIMULATED_HISTORICAL_STREAM"
        assert body["market_currency"] == "HISTORICAL"
        # This deployment has no verified contract metadata, and says so.
        assert body["financial_metadata_available"] is False
        assert body["execution_enabled"] is False
        assert body["strategies"] == [{"strategy_id": IDENTIFIER, "versions": [VERSION]}]

    def test_production_composes_no_shadow_workspace(self, clean: Settings) -> None:
        client = open_client(clean.model_copy(update={"app_env": "production"}))
        try:
            capability = client.get(f"{SHADOW}/capability").json()
            assert capability["available"] is False
            assert "üretimde hiçbir zaman açılmaz" in capability["reason"]
            for method, path in (
                ("GET", f"{SHADOW}/runs"),
                ("POST", f"{SHADOW}/runs"),
                ("GET", f"{SHADOW}/runs/SR-{'0' * 24}"),
            ):
                response = client.request(
                    method, path, json=run_body("LS-" + "0" * 24) if method == "POST" else None
                )
                assert response.status_code == 503, (method, path)
                assert response.json()["detail"]["code"] == "SHADOW_DISABLED"
        finally:
            client.__exit__(None, None, None)

    def test_without_the_opt_in_shadow_is_disabled(self, clean: Settings) -> None:
        client = open_client(clean, live=False)
        try:
            assert client.get(f"{SHADOW}/capability").json()["available"] is False
            assert client.get(f"{SHADOW}/runs").status_code == 503
        finally:
            client.__exit__(None, None, None)

    def test_a_foreign_host_is_refused_as_it_is_for_live(self, api: TestClient) -> None:
        response = api.get(f"{SHADOW}/runs", headers={"host": "attacker.example"})

        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "LIVE_HOST_REFUSED"

    def test_a_cross_site_write_is_refused(self, api: TestClient) -> None:
        response = api.post(
            f"{SHADOW}/runs",
            json=run_body("LS-" + "0" * 24),
            headers={"origin": "https://attacker.example"},
        )

        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "LIVE_ORIGIN_REFUSED"


# ----------------------------------------------------------------------
# Strict input: a client asks, it never answers
# ----------------------------------------------------------------------


class TestForgedInputIsRefused:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("decision", "ENTRY_INTENT"),
            ("outcome", "OBSERVED"),
            ("development", {"event": "TARGET_LEVEL_TOUCHED"}),
            ("pnl", "1000"),
            ("realized_profit", "1000"),
            ("fill_price", "100"),
            ("approved_quantity", 5),
            ("financial_state", "APPROVED"),
            ("risk_verdict", "ALLOWED"),
            ("contract_metadata", {"multiplier": "10", "tick_size": "0.05"}),
            ("provenance", "EXCHANGE"),
            ("market_currency", "LIVE"),
            ("status", "ENDED"),
            ("completeness", "COMPLETE"),
            ("journal_entry", {"sequence": 1}),
            ("candles", [{"open": "1"}]),
            ("strategy_code", "import os"),
        ],
    )
    def test_a_forged_field_is_a_422(self, api: TestClient, field: str, value: object) -> None:
        body = run_body("LS-" + "0" * 24)
        body[field] = value

        response = api.post(f"{SHADOW}/runs", json=body)

        assert response.status_code == 422, field

    def test_an_unregistered_version_is_refused(self, api: TestClient) -> None:
        session_id = session_for(api, "shadow-unknown-v1")

        response = api.post(f"{SHADOW}/runs", json=run_body(session_id, strategy_version="9.9.9"))

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "STRATEGY_UNSUPPORTED"

    @pytest.mark.parametrize(
        "strategy_id", ["os.system", "../../etc/passwd", "__import__('os')", "EmaCrossover"]
    )
    def test_a_strategy_name_is_never_a_module_path(
        self, api: TestClient, strategy_id: str
    ) -> None:
        session_id = session_for(api, f"shadow-name-{abs(hash(strategy_id)) % 10**8:08d}")

        response = api.post(f"{SHADOW}/runs", json=run_body(session_id, strategy_id=strategy_id))

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "STRATEGY_UNSUPPORTED"

    def test_half_a_risk_configuration_is_refused(self, api: TestClient) -> None:
        body = run_body("LS-" + "0" * 24, account={"equity": "100000", "used_margin": "0"})

        response = api.post(f"{SHADOW}/runs", json=body)

        assert response.status_code == 422

    def test_an_unknown_session_is_not_found(self, api: TestClient) -> None:
        response = api.post(f"{SHADOW}/runs", json=run_body("LS-" + "f" * 24))

        assert response.status_code == 404

    def test_a_malformed_run_id_never_reaches_storage(self, api: TestClient) -> None:
        assert api.get(f"{SHADOW}/runs/not-a-run").status_code == 422
        assert api.get(f"{SHADOW}/runs/SR-{'0' * 24}").status_code == 404


# ----------------------------------------------------------------------
# The local simulated workflow, to its end
# ----------------------------------------------------------------------


class TestTheLocalWorkflow:
    def test_a_run_watches_a_session_to_the_end_of_its_data(self, fast: TestClient) -> None:
        session_id = session_for(fast, "shadow-flow-0001")
        created = fast.post(f"{SHADOW}/runs", json=run_body(session_id))
        assert created.status_code == 201, created.text
        run = created.json()

        assert run["status"] == "OBSERVING"
        assert run["completeness"] == "OBSERVING"
        assert run["provenance"] == "SIMULATED_HISTORICAL_STREAM"

        ended = wait_ended(fast, run["run_id"])
        journal = whole_journal(fast, run["run_id"])

        assert ended["end_reason"] == "STREAM_ENDED"
        assert ended["completeness"] == "COMPLETE"
        assert journal[0]["operational"] == "RUN_OPENED"
        assert journal[-1]["operational"] == "RUN_ENDED"
        assert [item["sequence"] for item in journal] == sorted(
            item["sequence"] for item in journal
        )

    def test_nothing_financial_is_claimed_without_verified_metadata(self, fast: TestClient) -> None:
        session_id = session_for(fast, "shadow-flow-0002")
        body = run_body(
            session_id,
            account={"equity": "500000", "used_margin": "0"},
            risk={"mode": "PERCENTAGE", "risk_ratio": "0.01"},
        )
        run = fast.post(f"{SHADOW}/runs", json=body).json()
        wait_ended(fast, run["run_id"])
        journal = whole_journal(fast, run["run_id"])

        decisions = [item for item in journal if item["kind"] == "DECISION"]
        assert all(item["financial_state"] != "APPROVED" for item in decisions)
        for item in decisions:
            if item["entry"] is not None:
                assert item["entry"]["approved_quantity"] is None
                assert item["financial_state"] == "METADATA_UNAVAILABLE"
        keys = {key.lower() for key in every_key(journal)}
        assert not keys & {"pnl", "profit", "win", "loss", "filled", "fill_price", "net", "gross"}

    def test_every_development_cites_only_what_came_after(self, fast: TestClient) -> None:
        session_id = session_for(fast, "shadow-flow-0003")
        run = fast.post(f"{SHADOW}/runs", json=run_body(session_id)).json()
        wait_ended(fast, run["run_id"])

        page = fast.get(f"{SHADOW}/runs/{run['run_id']}/outcomes", params={"limit": 100})
        assert page.status_code == 200
        for record in page.json()["items"]:
            development = record["development"]
            if development["observed_from"] is not None:
                assert development["observed_from"] > record["decision_boundary"]
            assert development["event"] in {
                "NONE_REACHED",
                "STOP_LEVEL_TOUCHED",
                "TARGET_LEVEL_TOUCHED",
                "BOTH_LEVELS_TOUCHED_SAME_BAR",
                "NOT_OBSERVED",
            }
            if development["state"] == "UNAVAILABLE":
                assert development["unresolved_reason"]

    def test_shadow_opens_no_position_and_no_backtest_run(
        self, fast: TestClient, clean: Settings
    ) -> None:
        session_id = session_for(fast, "shadow-flow-0004")
        run = fast.post(f"{SHADOW}/runs", json=run_body(session_id)).json()
        wait_ended(fast, run["run_id"])

        counts = on_loop(fast, _counts, clean)

        assert counts == (0, 0, 0)

    def test_a_run_never_records_a_boundary_from_before_it_existed(self, fast: TestClient) -> None:
        session_id = session_for(fast, "shadow-flow-0005")
        started = time.monotonic()
        while True:
            body = fast.get(f"/api/live/sessions/{session_id}").json()
            if body["lifecycle"] == "ENDED" or time.monotonic() - started > 20:
                break
            time.sleep(0.05)

        # The session already played everything, so a run created now is
        # refused rather than handed history it never watched.
        response = fast.post(f"{SHADOW}/runs", json=run_body(session_id))

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "LIVE_SESSION_ENDED"


async def _counts(settings: Settings) -> tuple[int, int, int]:
    database = Database(settings.sqlalchemy_url)
    try:
        async with database.engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM paper_positions),"
                        " (SELECT count(*) FROM backtest_runs),"
                        " (SELECT count(*) FROM replay_position_links)"
                    )
                )
            ).one()
        return int(row[0]), int(row[1]), int(row[2])
    finally:
        await database.dispose()


# ----------------------------------------------------------------------
# Idempotency, isolation, pagination and lifecycle
# ----------------------------------------------------------------------


class TestIdempotency:
    def test_a_retried_request_returns_the_same_run(self, held: TestClient) -> None:
        session_id = session_for(held, "shadow-idem-0001")
        body = run_body(session_id, attempt_key="attempt-key-0001")

        first = held.post(f"{SHADOW}/runs", json=body)
        second = held.post(f"{SHADOW}/runs", json=body)

        assert first.status_code == second.status_code == 201
        assert first.json()["run_id"] == second.json()["run_id"]
        assert held.get(f"{SHADOW}/runs").json()["total"] == 1

    def test_the_same_key_with_other_rules_is_a_conflict(self, held: TestClient) -> None:
        session_id = session_for(held, "shadow-idem-0002")
        held.post(f"{SHADOW}/runs", json=run_body(session_id, attempt_key="attempt-key-0002"))

        response = held.post(
            f"{SHADOW}/runs",
            json=run_body(session_id, attempt_key="attempt-key-0002", analysis_evidence=True),
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "SHADOW_ATTEMPT_CONFLICT"
        assert held.get(f"{SHADOW}/runs").json()["total"] == 1


class TestIsolationAndPages:
    def test_one_run_never_reads_another_runs_journal(self, held: TestClient) -> None:
        session_id = session_for(held, "shadow-iso-0001")
        first = held.post(f"{SHADOW}/runs", json=run_body(session_id)).json()
        second = held.post(f"{SHADOW}/runs", json=run_body(session_id)).json()

        one = held.get(f"{SHADOW}/runs/{first['run_id']}/journal").json()
        two = held.get(f"{SHADOW}/runs/{second['run_id']}/journal").json()

        assert one["run_id"] == first["run_id"]
        assert two["run_id"] == second["run_id"]
        assert {item["decision_key"] for item in one["items"]}.isdisjoint(
            item["decision_key"] for item in two["items"]
        )

    def test_a_page_is_bounded_and_says_where_to_continue(self, fast: TestClient) -> None:
        session_id = session_for(fast, "shadow-page-0001")
        run = fast.post(f"{SHADOW}/runs", json=run_body(session_id)).json()
        wait_ended(fast, run["run_id"])

        page = fast.get(f"{SHADOW}/runs/{run['run_id']}/journal", params={"limit": 3}).json()

        assert len(page["items"]) <= 3
        assert page["total"] >= len(page["items"])
        assert page["next_after"] == page["items"][-1]["sequence"]
        assert (
            fast.get(f"{SHADOW}/runs/{run['run_id']}/journal", params={"limit": 101}).status_code
            == 422
        )
        assert fast.get(f"{SHADOW}/runs", params={"limit": 101}).status_code == 422
        # A cursor past what the column can hold is malformed input, not an
        # unreachable journal (Part 2B: it used to come back as a 503).
        for kind in ("journal", "outcomes"):
            beyond = fast.get(
                f"{SHADOW}/runs/{run['run_id']}/{kind}", params={"after": 99999999999999999999}
            )
            assert beyond.status_code == 422, kind


class TestLifecycle:
    def test_cancelling_keeps_the_journal_and_says_partial(self, held: TestClient) -> None:
        session_id = session_for(held, "shadow-cancel-0001")
        run = held.post(f"{SHADOW}/runs", json=run_body(session_id)).json()

        cancelled = held.post(f"{SHADOW}/runs/{run['run_id']}/cancel")
        again = held.post(f"{SHADOW}/runs/{run['run_id']}/cancel")

        assert cancelled.status_code == again.status_code == 200
        assert cancelled.json()["end_reason"] == "CANCELLED"
        assert cancelled.json()["completeness"] == "PARTIAL"
        assert again.json()["ended_at"] == cancelled.json()["ended_at"]
        journal = whole_journal(held, run["run_id"])
        assert journal[0]["operational"] == "RUN_OPENED"
        assert journal[-1]["operational"] == "RUN_ENDED"


class TestRestart:
    def test_a_run_left_observing_is_closed_as_interrupted_not_resumed(
        self, clean: Settings
    ) -> None:
        """A process that died mid-run left it marked OBSERVING. The next start
        closes it where its journal ends and invents nothing it missed."""
        run_id = "SR-" + "a" * 24
        from tests.integration.test_shadow_persistence import a_decision, a_run

        async def orphan() -> None:
            database = Database(clean.sqlalchemy_url)
            try:
                store = SqlAlchemyShadowStore(database)
                await store.create_run(
                    _replace_run(a_run(run_id), status=ShadowRunStatus.OBSERVING)
                )
                await store.append(run_id, [a_decision(1, "k1"), a_decision(2, "k2")])
            finally:
                await database.dispose()

        client = open_client(clean, live=False)
        on_loop(client, orphan)
        client.__exit__(None, None, None)

        restarted = open_client(clean)
        try:
            body = restarted.get(f"{SHADOW}/runs/{run_id}").json()
            journal = restarted.get(f"{SHADOW}/runs/{run_id}/journal").json()["items"]
        finally:
            restarted.__exit__(None, None, None)

        assert body["status"] == "ENDED"
        assert body["end_reason"] == "INTERRUPTED"
        assert body["completeness"] == "INTERRUPTED"
        assert body["observations"] == 2  # from its journal, not guessed
        decisions = [item for item in journal if item["kind"] == "DECISION"]
        assert len(decisions) == 2  # nothing it missed was invented
        assert journal[-1]["operational"] == "RUN_ENDED"


def _replace_run(run: StoredShadowRun, **changes: Any) -> StoredShadowRun:
    from dataclasses import replace

    return replace(run, **changes)


def test_the_timeframe_table_matches_the_schema() -> None:
    """Guards the route's mapping against a timeframe the schema allows."""
    from app.api.routes.shadow import _TIMEFRAMES  # noqa: PLC2701

    assert set(_TIMEFRAMES) == {"5M", "15M", "1H", "1D"}
    assert set(_TIMEFRAMES.values()) <= set(Timeframe)
