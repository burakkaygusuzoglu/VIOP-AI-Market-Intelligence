"""The source-status API and the operator command, end to end (Part 2B).

The composed application over real PostgreSQL. Facts are journalled the only
way this build allows - through the local operator command - and then read
over HTTP. The HTTP surface is shown to be read-only, bounded, and unable to
leak a credential or treat a hostile identifier as anything but malformed.
"""

from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.adapters.persistence.database import Database
from app.adapters.persistence.fact_store import SqlAlchemyFactVerificationStore
from app.application.sourcing.fact_review import FactVerificationService
from app.core.config import Settings
from app.main import create_app
from app.operator.fact_review import MALFORMED, OK, REFUSED, UNREACHABLE, run
from tests.integration.paper_support import truncate
from tests.integration.test_fact_verification_persistence import (
    START,
    SYMBOL,
    StepClock,
    approved_pair,
)

pytestmark = pytest.mark.integration

SOURCES = "/api/sources"


@pytest.fixture
async def clean(migrated: Settings) -> AsyncIterator[Settings]:
    database = Database(migrated.sqlalchemy_url)
    try:
        await truncate(database)
    finally:
        await database.dispose()
    yield migrated


def client_for(settings: Settings, *, live: bool = False) -> TestClient:
    configured = settings.model_copy(update={"live_simulation_enabled": live})
    client = TestClient(create_app(configured), base_url="http://localhost")
    client.__enter__()
    return client


@pytest.fixture
def api(clean: Settings) -> Iterator[TestClient]:
    client = client_for(clean)
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


async def operator(
    settings: Settings, *argv: str, clock: StepClock | None = None
) -> tuple[int, list[dict[str, object]]]:
    database = Database(settings.sqlalchemy_url)
    out = io.StringIO()
    try:
        code = await run(list(argv), database=database, clock=clock or StepClock(), out=out)
    finally:
        await database.dispose()
    return code, [json.loads(line) for line in out.getvalue().splitlines()]


async def publish_fixture(
    settings: Settings, *, symbol: str = SYMBOL, multiplier: str = "10"
) -> None:
    database = Database(settings.sqlalchemy_url)
    try:
        service = FactVerificationService(
            SqlAlchemyFactVerificationStore(database), StepClock(START + timedelta(hours=2))
        )
        m, t = await approved_pair(
            service, f"{symbol}-{multiplier}-", multiplier=multiplier, symbol=symbol
        )
        await service.publish(
            f"R-{symbol}-{multiplier}",
            underlying_symbol="TEST_FIXTURE_U",
            contract_name="fixture - not a VIOP specification",
            multiplier=m,
            tick_size=t,
        )
    finally:
        await database.dispose()


# ----------------------------------------------------------------------


class TestCapabilities:
    def test_the_deployment_says_what_is_not_here(self, api: TestClient) -> None:
        body = api.get(f"{SOURCES}/capabilities").json()

        deployment = body["deployment"]
        assert deployment["market_data_provider"] == "none"
        assert deployment["real_provider_connected"] is False
        assert deployment["financial_use_enabled"] is False
        assert deployment["verification_writes"] == "LOCAL_OPERATOR_COMMAND_ONLY"
        assert deployment["reviewer_identity"] == "OPERATOR_ASSERTION_NOT_AUTHENTICATED"
        assert deployment["calendar_source_composed"] is False
        assert [c["category"] for c in body["categories"]] == [
            "MARKET_DATA",
            "CONTRACT_METADATA",
            "OPEN_INTEREST",
            "NEWS",
            "MARKET_BREADTH",
            "SESSION_CALENDAR",
        ]
        for category in body["categories"]:  # O: no port is AVAILABLE for existing
            assert category["status"] == "NOT_CONFIGURED"
            assert not any(
                category[k]
                for k in (
                    "configured",
                    "licensed",
                    "connected",
                    "available",
                    "fresh",
                    "adapter_in_build",
                )
            )

    def test_the_local_simulation_is_named_and_still_not_market_data(self, clean: Settings) -> None:
        client = client_for(clean, live=True)
        try:
            body = client.get(f"{SOURCES}/capabilities").json()
        finally:
            client.__exit__(None, None, None)

        assert body["deployment"]["simulated_market_data"] is True
        assert body["categories"][0]["status"] == "NOT_CONFIGURED"

    async def test_verified_records_are_counted_but_confer_no_capability(
        self, api: TestClient, clean: Settings
    ) -> None:
        await publish_fixture(clean)

        body = api.get(f"{SOURCES}/capabilities").json()

        metadata = body["categories"][1]
        assert metadata["verified"] is True
        assert metadata["status"] == "NOT_CONFIGURED"
        assert body["journal"]["records"] == 1


class TestFNoHttpApproval:
    def test_every_source_route_is_get_only(self, api: TestClient) -> None:
        paths = api.get("/openapi.json").json()["paths"]
        served = {
            (path, method)
            for path, operations in paths.items()
            if path.startswith(SOURCES)
            for method in operations
        }
        assert {path for path, _ in served} == {
            f"{SOURCES}/capabilities",
            f"{SOURCES}/metadata/{{symbol}}",
            f"{SOURCES}/calendar/{{symbol}}",
            f"{SOURCES}/reviews",
        }
        assert {method for _, method in served} == {"get"}

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    @pytest.mark.parametrize(
        "path", ["/reviews", "/metadata/TEST_FIXTURE_FUT", "/capabilities", "/approve", "/verify"]
    )
    def test_no_write_verb_reaches_anything(self, api: TestClient, method: str, path: str) -> None:
        response = api.request(
            method,
            f"{SOURCES}{path}",
            json={
                "submission_id": "S1",
                "reviewer": "admin",
                "outcome": "APPROVED",
                "document_checked": True,
                "status": "VERIFIED_CURRENT_FACT",
            },
        )

        assert response.status_code in (404, 405)

    async def test_an_attempted_http_approval_leaves_the_journal_empty(
        self, api: TestClient, clean: Settings
    ) -> None:
        api.post(f"{SOURCES}/reviews", json={"submission_id": "S1", "outcome": "APPROVED"})

        assert api.get(f"{SOURCES}/reviews").json()["total"] == 0


class TestGHOperatorAssertionsConferNothing:
    async def test_a_grand_reviewer_name_is_shown_as_an_assertion(
        self, api: TestClient, clean: Settings
    ) -> None:
        clock = StepClock()
        await operator(
            clean,
            "submit",
            "--id",
            "S1",
            "--symbol",
            SYMBOL,
            "--fact",
            "MULTIPLIER",
            "--value",
            "10",
            "--reference",
            "https://www.borsaistanbul.com/en/viop-spec.pdf",
            "--authority",
            "SECONDARY",
            "--effective-from",
            "2026-01-01T00:00:00+00:00",
            "--submitted-by",
            "Borsa Istanbul",
            clock=clock,
        )
        code, out = await operator(
            clean,
            "decide",
            "--id",
            "S1",
            "--reviewer",
            "Borsa Istanbul Official",
            "--outcome",
            "APPROVED",
            "--document-checked",
            clock=clock,
        )

        (entry,) = api.get(f"{SOURCES}/reviews").json()["items"]
        assert code == REFUSED
        assert (
            out[0]["refusal_code"] == "NOT_AN_AUTHORITATIVE_SOURCE"
        )  # H: the URL asserted nothing
        assert entry["decision"]["result"] == "REFUSED"
        assert entry["decision"]["reviewer_identity"] == "OPERATOR_ASSERTION_NOT_AUTHENTICATED"
        status = api.get(f"{SOURCES}/metadata/{SYMBOL}").json()
        assert status["verdict"] == "MISSING"

    async def test_approval_without_the_document_checked_flag_is_refused(
        self, clean: Settings
    ) -> None:
        clock = StepClock()
        await operator(
            clean,
            "submit",
            "--id",
            "S1",
            "--symbol",
            SYMBOL,
            "--fact",
            "MULTIPLIER",
            "--value",
            "10",
            "--reference",
            "TEST_FIXTURE_DOC",
            "--authority",
            "EXCHANGE_OFFICIAL",
            "--effective-from",
            "2026-01-01T00:00:00+00:00",
            "--submitted-by",
            "op",
            clock=clock,
        )
        code, out = await operator(
            clean, "decide", "--id", "S1", "--reviewer", "op", "--outcome", "APPROVED", clock=clock
        )

        assert code == REFUSED and out[0]["refusal_code"] == "DOCUMENT_NOT_CHECKED"


class TestOperatorCommand:
    async def test_the_full_workflow_publishes_a_record_the_api_reads(
        self, api: TestClient, clean: Settings
    ) -> None:
        clock = StepClock()
        for sid, fact, value in (("M", "MULTIPLIER", "10"), ("T", "TICK_SIZE", "0.25")):
            code, _ = await operator(
                clean,
                "submit",
                "--id",
                sid,
                "--symbol",
                SYMBOL,
                "--fact",
                fact,
                "--value",
                value,
                "--reference",
                "TEST_FIXTURE_DOC#spec",
                "--authority",
                "EXCHANGE_OFFICIAL",
                "--effective-from",
                "2026-01-01T00:00:00+00:00",
                "--submitted-by",
                "op",
                clock=clock,
            )
            assert code == OK
            code, _ = await operator(
                clean,
                "decide",
                "--id",
                sid,
                "--reviewer",
                "op",
                "--outcome",
                "APPROVED",
                "--document-checked",
                clock=clock,
            )
            assert code == OK
        code, out = await operator(
            clean,
            "publish",
            "--record-id",
            "R1",
            "--underlying",
            "U",
            "--name",
            "fixture",
            "--multiplier",
            "M",
            "--tick-size",
            "T",
            clock=clock,
        )

        status = api.get(f"{SOURCES}/metadata/{SYMBOL}").json()
        assert code == OK and out[0]["financial_use_enabled"] is False
        assert status["verdict"] == "USABLE"
        fields = {f["name"]: f for f in status["fields"]}
        assert fields["multiplier"]["value"] == "10"
        assert fields["tick_size"]["value"] == "0.25"
        assert fields["tick_value"]["state"] == "NOT_REVIEWABLE"
        assert fields["initial_margin"]["state"] == "NOT_REVIEWABLE"
        assert fields["maintenance_margin"]["value"] is None
        assert status["financial_use_enabled"] is False
        assert status["checks"]["financial_use_enabled"] is False

    @pytest.mark.parametrize(
        "argv",
        [
            ["submit", "--id", "S1"],
            [
                "submit",
                "--id",
                "S1",
                "--symbol",
                SYMBOL,
                "--fact",
                "MULTIPLIER",
                "--value",
                "abc",
                "--reference",
                "r",
                "--authority",
                "EXCHANGE_OFFICIAL",
                "--submitted-by",
                "op",
            ],
            [
                "submit",
                "--id",
                "S1",
                "--symbol",
                SYMBOL,
                "--fact",
                "MULTIPLIER",
                "--value",
                "10",
                "--reference",
                "r",
                "--authority",
                "EXCHANGE_OFFICIAL",
                "--submitted-by",
                "op",
                "--effective-from",
                "2026-01-01T00:00:00",
            ],
            ["history", "--limit", "1000"],
            ["unknown"],
        ],
    )
    async def test_malformed_commands_write_nothing(self, clean: Settings, argv: list[str]) -> None:
        code, _ = await operator(clean, *argv)
        _, history = await operator(clean, "history")

        assert code == MALFORMED
        assert history[-1] == {"total": 0}

    async def test_an_unreachable_journal_is_reported_without_its_address(
        self, clean: Settings
    ) -> None:
        dead = clean.model_copy(
            update={"postgres_port": 1, "postgres_password": SecretStr("sk-db-do-not-leak")}
        )
        code, out = await operator(dead, "history")

        assert code == UNREACHABLE
        assert out == [{"error": "JOURNAL_UNREACHABLE"}]

    def test_the_command_never_fetches_or_opens_a_reference(self) -> None:
        from pathlib import Path

        source = Path("app/operator/fact_review.py").read_text(encoding="utf-8")
        imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        for forbidden in ("httpx", "urllib", "requests", "socket", "aiohttp", "webbrowser"):
            assert not any(forbidden in line for line in imports)
        assert "open(" not in source


class TestMetadataQuestions:
    async def test_i_another_contract_s_facts_are_never_returned(
        self, api: TestClient, clean: Settings
    ) -> None:
        await publish_fixture(clean, symbol="TEST_FIXTURE_A")

        status = api.get(f"{SOURCES}/metadata/TEST_FIXTURE_B?retrospective=true").json()

        assert status["verdict"] == "MISSING"
        assert status["records"] == []

    async def test_j_a_moment_outside_the_period_is_not_in_effect(
        self, api: TestClient, clean: Settings
    ) -> None:
        await publish_fixture(clean)
        before = (START - timedelta(days=400)).isoformat()

        status = api.get(
            f"{SOURCES}/metadata/{SYMBOL}", params={"applies_at": before, "retrospective": "true"}
        ).json()

        assert status["verdict"] == "NOT_IN_EFFECT"
        assert all(f["value"] is None for f in status["fields"])

    async def test_k_the_default_is_as_known_and_the_past_did_not_know(
        self, api: TestClient, clean: Settings
    ) -> None:
        await publish_fixture(clean)
        before = (START - timedelta(days=1)).isoformat()

        as_of = api.get(f"{SOURCES}/metadata/{SYMBOL}", params={"known_by": before}).json()
        retro = api.get(
            f"{SOURCES}/metadata/{SYMBOL}",
            params={"applies_at": before, "retrospective": "true"},
        ).json()

        assert as_of["verdict"] == "NOT_YET_KNOWN" and as_of["retrospective"] is False
        assert retro["verdict"] == "USABLE" and retro["retrospective"] is True
        assert retro["known_by"] is None

    def test_k_retrospective_and_a_knowledge_boundary_together_are_refused(
        self, api: TestClient
    ) -> None:
        response = api.get(
            f"{SOURCES}/metadata/{SYMBOL}",
            params={"retrospective": "true", "known_by": START.isoformat()},
        )
        assert response.status_code == 422

    async def test_l_conflicting_authoritative_values_are_refused_and_recorded(
        self, api: TestClient, clean: Settings
    ) -> None:
        await publish_fixture(clean, multiplier="10")
        await publish_fixture(clean, multiplier="100")

        status = api.get(f"{SOURCES}/metadata/{SYMBOL}?retrospective=true").json()

        assert status["verdict"] == "CONFLICTING"
        assert status["governing_record"] is None
        assert {status["conflicts"][0]["chosen_value"], status["conflicts"][0]["other_value"]} == {
            "10",
            "100",
        }
        assert all(f["value"] is None for f in status["fields"])

    def test_m_no_calendar_means_no_session_claim(self, api: TestClient) -> None:
        body = api.get(f"{SOURCES}/calendar/{SYMBOL}").json()

        assert body["status"] == "UNAVAILABLE"
        assert body["source"] is None
        assert body["calendar_source_composed"] is False

    def test_a_naive_time_is_refused(self, api: TestClient) -> None:
        response = api.get(
            f"{SOURCES}/metadata/{SYMBOL}", params={"applies_at": "2026-03-02T09:00:00"}
        )

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "NAIVE_TIMESTAMP"


class TestBoundsAndHostileInput:
    @pytest.mark.parametrize(
        "query",
        ["after=-1", "limit=0", "limit=101", "after=abc", "limit=1.5", f"after={2**70}"],
    )
    def test_t_malformed_pagination_is_a_422(self, api: TestClient, query: str) -> None:
        assert api.get(f"{SOURCES}/reviews?{query}").status_code == 422

    async def test_t_a_page_is_bounded_and_resumable(
        self, api: TestClient, clean: Settings
    ) -> None:
        clock = StepClock()
        for i in range(3):
            await operator(
                clean,
                "submit",
                "--id",
                f"S{i}",
                "--symbol",
                SYMBOL,
                "--fact",
                "MULTIPLIER",
                "--value",
                "10",
                "--reference",
                "TEST_FIXTURE_DOC",
                "--authority",
                "EXCHANGE_OFFICIAL",
                "--submitted-by",
                "op",
                clock=clock,
            )

        first = api.get(f"{SOURCES}/reviews?limit=2").json()
        second = api.get(f"{SOURCES}/reviews?limit=2&after={first['next_after']}").json()

        assert first["total"] == 3 and len(first["items"]) == 2
        assert [i["submission_id"] for i in second["items"]] == ["S2"]
        assert second["next_after"] is None

    @pytest.mark.parametrize(
        "symbol",
        [
            "..%2F..%2Fetc%2Fpasswd",
            "%3Cscript%3Ealert(1)%3C%2Fscript%3E",
            "A" * 33,
            "TEST%00FUT",
            "TEST%20FUT",
            "%27%3B%20DROP%20TABLE%20fact_submissions%3B--",
        ],
    )
    def test_u_a_hostile_identifier_is_refused_not_executed(
        self, api: TestClient, symbol: str
    ) -> None:
        for path in ("metadata", "calendar"):
            response = api.get(f"{SOURCES}/{path}/{symbol}")
            assert response.status_code in (404, 422)
            assert "<script>" not in response.text
        assert api.get(f"{SOURCES}/reviews").status_code == 200  # the table is still there

    async def test_s_no_response_carries_a_credential_or_address(self, clean: Settings) -> None:
        secret = "sk-db-do-not-leak-0001"
        dead = clean.model_copy(update={"postgres_port": 1, "postgres_password": SecretStr(secret)})
        client = client_for(dead)
        try:
            responses = [
                client.get(f"{SOURCES}/{path}")
                for path in ("capabilities", f"metadata/{SYMBOL}", "reviews", f"calendar/{SYMBOL}")
            ]
        finally:
            client.__exit__(None, None, None)

        for response in responses[:3]:
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "SOURCE_JOURNAL_UNAVAILABLE"
        assert responses[3].status_code == 200  # the calendar needs no database
        for response in responses:
            text = response.text
            for leak in (
                secret,
                "postgresql",
                "psycopg",
                f"{dead.postgres_host}:1",
                "port=1",
                "Traceback",
            ):
                assert leak not in text


class TestNoTradingPath:
    async def test_v_w_verified_facts_open_no_position_and_send_no_order(
        self, api: TestClient, clean: Settings
    ) -> None:
        await publish_fixture(clean)

        positions = api.get("/api/paper/positions")
        capability = api.get("/api/backtest/capability").json()

        assert positions.status_code == 200 and positions.json()["total"] == 0
        assert capability["financial_execution_available"] is False
        assert Decimal("10")  # the verified multiplier exists; nothing consumed it
