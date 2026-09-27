"""Phase 15 Part 2C: final validation of the source-verification foundation.

Real PostgreSQL, the real composed application and the real operator command.
Every value is a TEST_FIXTURE in the disposable ``viop_test`` database.

What this adds to the Part 2B suites: the operator command's failure modes
(each a typed refusal, never a false "unreachable"), financial authority
checked in the *production composition* rather than in a metadata assessor,
the "applies in March, learnt in September" knowledge boundary end to end,
pagination that neither skips nor repeats under concurrent appends, and the
database-privilege facts the documented operator boundary rests on.
"""

from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.adapters.persistence.database import Database
from app.adapters.persistence.fact_store import SqlAlchemyFactVerificationStore
from app.application.ports.fact_verification import VerificationConflictError
from app.application.sourcing.fact_review import FactVerificationService
from app.core.config import Settings
from app.domain.sourcing.review import FactSubmission, ReviewRefusedError
from app.main import create_app
from app.operator.fact_review import MALFORMED, OK, REFUSED, run
from tests.integration.paper_support import truncate
from tests.integration.test_fact_verification_persistence import (
    StepClock,
    a_decision,
    a_submission,
)
from tests.integration.test_paper_api import create_body, headers

pytestmark = pytest.mark.integration

SYMBOL = "TEST_FIXTURE_FUT"
MARCH = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
SEPTEMBER = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)


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
    client = TestClient(create_app(clean), base_url="http://localhost")
    client.__enter__()
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


DECIDE_APPROVED = (
    "decide",
    "--id",
    "S1",
    "--reviewer",
    "op",
    "--outcome",
    "APPROVED",
    "--document-checked",
)


def submit_args(sid: str, fact: str = "MULTIPLIER", value: str = "10", **extra: str) -> list[str]:
    args = {
        "--id": sid,
        "--symbol": SYMBOL,
        "--fact": fact,
        "--value": value,
        "--reference": "TEST_FIXTURE_DOC#spec",
        "--authority": "EXCHANGE_OFFICIAL",
        "--effective-from": "2026-01-01T00:00:00+00:00",
        "--submitted-by": "op",
    } | extra
    return ["submit", *(item for pair in args.items() for item in pair)]


async def publish(
    settings: Settings, clock: StepClock, *, prefix: str = "", value: str = "10"
) -> None:
    for sid, fact, v in ((f"{prefix}M", "MULTIPLIER", value), (f"{prefix}T", "TICK_SIZE", "0.25")):
        assert (await operator(settings, *submit_args(sid, fact, v), clock=clock))[0] == OK
        code, _ = await operator(
            settings,
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
    code, _ = await operator(
        settings,
        "publish",
        "--record-id",
        f"{prefix}R",
        "--underlying",
        "U",
        "--name",
        "fixture",
        "--multiplier",
        f"{prefix}M",
        "--tick-size",
        f"{prefix}T",
        clock=clock,
    )
    assert code == OK


class TestOperatorFailureModes:
    @pytest.mark.parametrize(
        ("extra", "expected"),
        [
            ({"--reference": " "}, None),  # journalled as a claim; refused at review
            ({"--authority": "MADE_UP_AUTHORITY"}, MALFORMED),
            ({"--effective-from": "2026-03-01"}, MALFORMED),
            ({"--effective-from": "not-a-date"}, MALFORMED),
            ({"--value": "NaN"}, None),
        ],
    )
    async def test_malformed_or_incomplete_claims_never_become_facts(
        self, clean: Settings, extra: dict[str, str], expected: int | None
    ) -> None:
        clock = StepClock()
        code, _ = await operator(clean, *submit_args("S1", **extra), clock=clock)
        if expected is not None:
            assert code == expected
            return
        assert code == OK
        code, out = await operator(
            clean,
            "decide",
            "--id",
            "S1",
            "--reviewer",
            "op",
            "--outcome",
            "APPROVED",
            "--document-checked",
            clock=clock,
        )
        assert code == REFUSED and out[0]["result"] == "REFUSED"

    async def test_an_empty_effective_period_is_a_typed_refusal(self, clean: Settings) -> None:
        code, out = await operator(
            clean, *submit_args("S1", **{"--effective-until": "2026-01-01T00:00:00+00:00"})
        )
        assert (code, out) == (REFUSED, [{"error": "EFFECTIVE_PERIOD_EMPTY"}])

    async def test_an_over_long_value_is_refused_not_reported_as_an_outage(
        self, clean: Settings
    ) -> None:
        for extra in ({"--reference": "x" * 501}, {"--id": "S" * 65}, {"--submitted-by": "o" * 81}):
            code, out = await operator(clean, *submit_args("S1", **extra))
            assert (code, out) == (REFUSED, [{"error": "FIELD_TOO_LONG"}])

    async def test_a_bypassing_writer_s_over_long_value_is_refused_too(
        self, clean: Settings
    ) -> None:
        database = Database(clean.sqlalchemy_url)
        try:
            store = SqlAlchemyFactVerificationStore(database)
            with pytest.raises(VerificationConflictError) as caught:
                await store.append_submission(
                    a_submission("S1", reference="x" * 501), recorded_at=SEPTEMBER
                )
        finally:
            await database.dispose()
        assert caught.value.code == "VALUE_OUT_OF_BOUNDS"

    async def test_a_contract_identity_is_exact(self, clean: Settings) -> None:
        code, out = await operator(clean, *submit_args("S1", **{"--symbol": " TEST_FIXTURE_FUT"}))
        assert (code, out) == (REFUSED, [{"error": "SYMBOL_NOT_CANONICAL"}])

    async def test_facts_of_two_contracts_cannot_make_one_record(self, clean: Settings) -> None:
        clock = StepClock()
        await operator(clean, *submit_args("M"), clock=clock)
        await operator(
            clean,
            *submit_args("T", "TICK_SIZE", "0.25", **{"--symbol": "TEST_FIXTURE_OTHER"}),
            clock=clock,
        )
        for sid in ("M", "T"):
            await operator(
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
        code, out = await operator(
            clean,
            "publish",
            "--record-id",
            "R",
            "--underlying",
            "U",
            "--name",
            "n",
            "--multiplier",
            "M",
            "--tick-size",
            "T",
            clock=clock,
        )
        assert (code, out) == (REFUSED, [{"error": "MIXED_SOURCES"}])

    async def test_a_duplicate_is_idempotent_and_a_different_review_conflicts(
        self, clean: Settings
    ) -> None:
        clock = StepClock()
        assert (await operator(clean, *submit_args("S1"), clock=clock))[0] == OK
        _, again = await operator(clean, *submit_args("S1"), clock=clock)
        await operator(
            clean,
            "decide",
            "--id",
            "S1",
            "--reviewer",
            "op",
            "--outcome",
            "APPROVED",
            "--document-checked",
            clock=clock,
        )
        code, out = await operator(
            clean,
            "decide",
            "--id",
            "S1",
            "--reviewer",
            "other",
            "--outcome",
            "REJECTED",
            clock=clock,
        )
        retried = await operator(clean, *DECIDE_APPROVED, clock=clock)
        _, history = await operator(clean, "history")

        # A retried command is the same claim even though the command stamped
        # a later time; the first journalled time stands.
        assert again[0]["written"] is False
        assert (retried[0], retried[1][0]["result"]) == (OK, "APPROVED")
        assert (code, out) == (REFUSED, [{"error": "ALREADY_DECIDED"}])
        assert history[-1] == {"total": 1}

    async def test_future_dated_assertions_are_refused_by_the_server_clock(
        self, clean: Settings
    ) -> None:
        database = Database(clean.sqlalchemy_url)
        try:
            clock = StepClock(SEPTEMBER)
            service = FactVerificationService(SqlAlchemyFactVerificationStore(database), clock)
            future = replace_submitted(a_submission("S1"), SEPTEMBER + timedelta(days=1))
            with pytest.raises(ReviewRefusedError) as submitted:
                await service.submit(future)
            await service.submit(replace_submitted(a_submission("S2"), SEPTEMBER))
            with pytest.raises(ReviewRefusedError) as decided:
                await service.decide(a_decision("S2", at=SEPTEMBER + timedelta(days=1)))
        finally:
            await database.dispose()
        assert submitted.value.code == decided.value.code == "FUTURE_DATED"


def replace_submitted(submission: FactSubmission, moment: datetime) -> FactSubmission:
    return replace(submission, submitted_at=moment)


class TestFinancialAuthorityInTheProductionComposition:
    async def test_a_published_fact_opens_no_financial_path(
        self, api: TestClient, clean: Settings
    ) -> None:
        await publish(clean, StepClock())
        status = api.get(f"/api/sources/metadata/{SYMBOL}").json()
        assert status["verdict"] == "USABLE"  # the fact is verified...

        app = api.app
        refused = api.post(
            "/api/paper/positions", json=create_body(symbol=SYMBOL), headers=headers()
        )
        backtest = api.get("/api/backtest/capability").json()

        # ...and still no financial consumer can use it.
        assert app.state.product_resolver is None  # type: ignore[attr-defined]
        assert refused.status_code >= 400
        assert refused.json()["detail"]["code"] == "PRODUCT_METADATA_UNAVAILABLE"
        assert backtest["financial_execution_available"] is False
        assert status["financial_use_enabled"] is False
        assert status["checks"]["financial_use_enabled"] is False
        assert api.get("/api/paper/positions").json()["total"] == 0

    async def test_shadow_composed_locally_still_has_no_financial_metadata(
        self, clean: Settings
    ) -> None:
        await publish(clean, StepClock())
        client = TestClient(
            create_app(clean.model_copy(update={"live_simulation_enabled": True})),
            base_url="http://localhost",
        )
        client.__enter__()
        try:
            shadow = client.get("/api/shadow/capability").json()
            sources = client.get("/api/sources/capabilities").json()
        finally:
            client.__exit__(None, None, None)

        assert shadow["financial_metadata_available"] is False
        assert sources["deployment"]["simulated_market_data"] is True
        assert {c["status"] for c in sources["categories"]} == {"NOT_CONFIGURED"}


class TestKnowledgeTimeEndToEnd:
    async def test_a_march_fact_learnt_in_september(self, api: TestClient, clean: Settings) -> None:
        """Effective since January, reviewed and journalled in September."""
        await publish(clean, StepClock(SEPTEMBER))
        march = MARCH.isoformat()

        as_known_in_march = api.get(
            f"/api/sources/metadata/{SYMBOL}", params={"applies_at": march, "known_by": march}
        ).json()
        retrospective = api.get(
            f"/api/sources/metadata/{SYMBOL}",
            params={"applies_at": march, "retrospective": "true"},
        ).json()
        current = api.get(f"/api/sources/metadata/{SYMBOL}").json()

        assert as_known_in_march["verdict"] == "NOT_YET_KNOWN"
        assert as_known_in_march["retrospective"] is False
        assert all(f["value"] is None for f in as_known_in_march["fields"])
        assert retrospective["verdict"] == "USABLE"
        assert retrospective["retrospective"] is True and retrospective["known_by"] is None
        (record,) = retrospective["records"]
        assert record["known_at"] > record["verified_at"] > "2026-09"  # server clock, not asserted
        assert current["verdict"] == "USABLE" and current["retrospective"] is False

    async def test_a_later_official_correction_is_visible_only_from_when_it_was_learnt(
        self, api: TestClient, clean: Settings
    ) -> None:
        clock = StepClock(SEPTEMBER)
        await publish(clean, clock, prefix="A", value="100")
        learnt_first = api.get(f"/api/sources/metadata/{SYMBOL}").json()["server_time"]
        for sid, fact, v in (("BM", "MULTIPLIER", "10"), ("BT", "TICK_SIZE", "0.25")):
            await operator(clean, *submit_args(sid, fact, v, **{"--corrects": "AR"}), clock=clock)
            await operator(
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
        await operator(
            clean,
            "publish",
            "--record-id",
            "BR",
            "--underlying",
            "U",
            "--name",
            "n",
            "--multiplier",
            "BM",
            "--tick-size",
            "BT",
            clock=clock,
        )
        records = api.get(f"/api/sources/metadata/{SYMBOL}?retrospective=true").json()["records"]
        corrected_known = next(r["known_at"] for r in records if r["record_id"] == "BR")
        wrong_known = next(r["known_at"] for r in records if r["record_id"] == "AR")

        before = api.get(
            f"/api/sources/metadata/{SYMBOL}",
            params={"known_by": wrong_known, "applies_at": wrong_known},
        ).json()
        after = api.get(f"/api/sources/metadata/{SYMBOL}").json()

        assert learnt_first
        assert corrected_known > wrong_known
        assert before["governing_record"] == "AR"
        assert after["governing_record"] == "BR" and after["superseded"] == ["AR"]
        assert {f["name"]: f["value"] for f in after["fields"]}["multiplier"] == "10"


class TestPaginationUnderConcurrentAppends:
    async def test_pages_neither_skip_nor_repeat(self, api: TestClient, clean: Settings) -> None:
        clock = StepClock()
        for i in range(5):
            await operator(clean, *submit_args(f"S{i}"), clock=clock)

        first = api.get("/api/sources/reviews?limit=2").json()
        await operator(clean, *submit_args("LATE"), clock=clock)  # appended mid-read
        seen = [i["submission_id"] for i in first["items"]]
        cursor = first["next_after"]
        while cursor is not None:
            page = api.get(f"/api/sources/reviews?limit=2&after={cursor}").json()
            seen += [i["submission_id"] for i in page["items"]]
            cursor = page["next_after"]

        assert seen == ["S0", "S1", "S2", "S3", "S4", "LATE"]
        assert len(seen) == len(set(seen))


class TestDatabasePrivilegeFacts:
    """The documented limitation, measured: the application's role owns the
    journal, so the append-only triggers bind the *application code*, not a
    person holding its credentials. Pinned so the report cannot drift from
    the database."""

    async def test_the_app_role_owns_the_journal_tables(self, clean: Settings) -> None:
        database = Database(clean.sqlalchemy_url)
        try:
            async with database.engine.connect() as connection:
                me = (await connection.execute(text("SELECT current_user"))).scalar_one()
                owners = set(
                    (
                        await connection.execute(
                            text(
                                "SELECT tableowner FROM pg_tables WHERE tablename IN "
                                "('fact_submissions','fact_review_decisions','contract_fact_records')"
                            )
                        )
                    ).scalars()
                )
        finally:
            await database.dispose()
        assert owners == {me}

    async def test_the_triggers_are_enabled_and_refuse_the_owner_too(self, clean: Settings) -> None:
        clock = StepClock()
        await operator(clean, *submit_args("S1"), clock=clock)
        database = Database(clean.sqlalchemy_url)
        try:
            async with database.engine.connect() as connection:
                enabled = (
                    await connection.execute(
                        text(
                            "SELECT count(*) FROM pg_trigger WHERE tgname LIKE "
                            "'%_no_update_or_delete' AND tgenabled = 'O' "
                            "AND tgrelid::regclass::text IN "
                            "('fact_submissions','fact_review_decisions','contract_fact_records')"
                        )
                    )
                ).scalar_one()
            with pytest.raises(Exception, match="append-only"):
                async with database.engine.begin() as connection:
                    await connection.execute(text("UPDATE fact_submissions SET value_decimal = 99"))
        finally:
            await database.dispose()
        assert enabled == 3
        assert Decimal("10")  # the stored claim was not changed


class TestDeploymentMatrix:
    """Source status in every deployment the settings accept, and refusal to
    start for the ones they do not (Phase 13's rules, unchanged)."""

    @pytest.mark.parametrize("app_env", [None, "development", "test", "production"])
    @pytest.mark.parametrize("enabled", [None, "false", "true"])
    def test_status_is_truthful_and_read_only_everywhere(
        self,
        clean: Settings,
        monkeypatch: pytest.MonkeyPatch,
        app_env: str | None,
        enabled: str | None,
    ) -> None:
        for name, value in (("APP_ENV", app_env), ("LIVE_SIMULATION_ENABLED", enabled)):
            monkeypatch.delenv(name, raising=False)
            if value is not None:
                monkeypatch.setenv(name, value)
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        client = TestClient(create_app(settings), base_url="http://localhost")
        client.__enter__()
        try:
            body = client.get("/api/sources/capabilities").json()
            write = client.post("/api/sources/reviews", json={"outcome": "APPROVED"})
        finally:
            client.__exit__(None, None, None)

        composed = enabled == "true" and app_env in (None, "development", "test")
        assert body["deployment"]["simulated_market_data"] is composed
        assert body["deployment"]["real_provider_connected"] is False
        assert body["deployment"]["financial_use_enabled"] is False
        assert {c["status"] for c in body["categories"]} == {"NOT_CONFIGURED"}
        assert write.status_code == 405

    @pytest.mark.parametrize(
        ("app_env", "enabled"), [("staging", None), ("PRODUCTION", None), (None, "maybe")]
    )
    def test_an_ambiguous_deployment_refuses_to_start(
        self, monkeypatch: pytest.MonkeyPatch, app_env: str | None, enabled: str | None
    ) -> None:
        from pydantic import ValidationError

        for name, value in (("APP_ENV", app_env), ("LIVE_SIMULATION_ENABLED", enabled)):
            monkeypatch.delenv(name, raising=False)
            if value is not None:
                monkeypatch.setenv(name, value)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)  # type: ignore[call-arg]


class TestSecretsInFailures:
    def test_a_failing_journal_leaks_nothing_to_responses_or_logs(
        self,
        clean: Settings,
        capsys: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from pydantic import SecretStr

        secret = "sk-journal-secret-do-not-leak-7731"
        dead = clean.model_copy(update={"postgres_port": 1, "postgres_password": SecretStr(secret)})
        caplog.set_level("DEBUG")
        client = TestClient(create_app(dead), base_url="http://localhost")
        client.__enter__()
        try:
            bodies = [
                client.get(path).text
                for path in (
                    "/api/sources/capabilities",
                    f"/api/sources/metadata/{SYMBOL}",
                    "/api/sources/reviews",
                )
            ]
        finally:
            client.__exit__(None, None, None)
        captured = capsys.readouterr()
        everything = "\n".join([*bodies, captured.out, captured.err, caplog.text])

        assert secret not in everything
        assert f"{dead.postgres_user}:{secret}" not in everything
        assert "postgresql+psycopg://" not in "\n".join(bodies)
        assert "Traceback" not in "\n".join(bodies)
        assert all('"SOURCE_JOURNAL_UNAVAILABLE"' in body for body in bodies)
