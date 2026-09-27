"""The contract-fact verification journal against real PostgreSQL (Part 2B).

What only the real database can show: the schema matches the models; the
journal survives a restart exactly; UPDATE and DELETE are refused on every
table; PostgreSQL itself refuses an approval without evidence - even from a
writer that bypasses the application; a retry is idempotent; two conflicting
reviews racing for one submission produce exactly one; and an interrupted
transaction leaves nothing behind.

Every value is a TEST_FIXTURE in the disposable ``viop_test`` database.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from app.adapters.persistence import (  # noqa: F401  (registers every table)
    backtest_models,
    fact_models,
    journal_models,
    paper_models,
    replay_models,
    shadow_models,
)
from app.adapters.persistence.base import Base
from app.adapters.persistence.database import Database
from app.adapters.persistence.fact_store import SqlAlchemyFactVerificationStore
from app.application.ports.fact_verification import (
    RecordEvidence,
    VerificationConflictError,
)
from app.application.sourcing.fact_review import FactVerificationService
from app.core.config import Settings
from app.domain.sourcing.facts import FactVerdictCode, SourceAuthority, assess_fact_records
from app.domain.sourcing.review import (
    ContractFact,
    FactSubmission,
    ReviewDecision,
    ReviewOutcome,
    ReviewResult,
    SubmissionOrigin,
    judge,
)
from tests.integration.paper_support import truncate

pytestmark = pytest.mark.integration

SYMBOL = "TEST_FIXTURE_FUT"
START = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
REFERENCE = "TEST_FIXTURE_DOC#spec"


class StepClock:
    """Advances one second per reading, so every write has its own time."""

    def __init__(self, start: datetime = START) -> None:
        self.moment = start

    def now(self) -> datetime:
        self.moment += timedelta(seconds=1)
        return self.moment


def a_submission(
    submission_id: str = "S1",
    fact: ContractFact = ContractFact.MULTIPLIER,
    value: object = Decimal("10"),
    *,
    authority: SourceAuthority = SourceAuthority.EXCHANGE_OFFICIAL,
    origin: SubmissionOrigin = SubmissionOrigin.MANUAL_ENTRY,
    reference: str = REFERENCE,
    corrects: str | None = None,
    symbol: str = SYMBOL,
) -> FactSubmission:
    return FactSubmission(
        submission_id=submission_id,
        symbol=symbol,
        fact=fact,
        value=value,  # type: ignore[arg-type]
        reference=reference,
        authority=authority,
        effective_from=START - timedelta(days=100),
        submitted_by="test-operator",
        submitted_at=START,
        origin=origin,
        corrects=corrects,
    )


def a_decision(
    submission_id: str = "S1",
    *,
    outcome: ReviewOutcome = ReviewOutcome.APPROVED,
    checked: bool = True,
    reviewer: str = "test-reviewer",
    at: datetime | None = None,
) -> ReviewDecision:
    return ReviewDecision(
        submission_id=submission_id,
        reviewer=reviewer,
        decided_at=at or START + timedelta(hours=1),
        outcome=outcome,
        document_checked=checked,
    )


async def approved_pair(
    service: FactVerificationService,
    prefix: str = "",
    *,
    multiplier: str = "10",
    symbol: str = SYMBOL,
    corrects: str | None = None,
) -> tuple[str, str]:
    m, t = f"{prefix}M", f"{prefix}T"
    await service.submit(
        a_submission(m, value=Decimal(multiplier), symbol=symbol, corrects=corrects)
    )
    await service.submit(
        a_submission(t, ContractFact.TICK_SIZE, Decimal("0.25"), symbol=symbol, corrects=corrects)
    )
    await service.decide(a_decision(m, at=START + timedelta(hours=1)))
    await service.decide(a_decision(t, at=START + timedelta(hours=1)))
    return m, t


@pytest.fixture
async def database(migrated: Settings) -> AsyncIterator[Database]:
    db = Database(migrated.sqlalchemy_url)
    await truncate(db)
    try:
        yield db
    finally:
        await db.dispose()


@pytest.fixture
def clock() -> StepClock:
    return StepClock(START + timedelta(hours=2))


@pytest.fixture
def store(database: Database) -> SqlAlchemyFactVerificationStore:
    return SqlAlchemyFactVerificationStore(database)


@pytest.fixture
def service(store: SqlAlchemyFactVerificationStore, clock: StepClock) -> FactVerificationService:
    return FactVerificationService(store, clock)


class TestSchema:
    async def test_the_migration_matches_the_models_exactly(self, database: Database) -> None:
        async with database.engine.connect() as connection:
            diff = await connection.run_sync(
                lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata)
            )

        assert diff == []


class TestARestart:
    async def test_the_journal_and_records_survive_a_restart_exactly(
        self, service: FactVerificationService, migrated: Settings
    ) -> None:
        m, t = await approved_pair(service)
        await service.publish(
            "R1",
            underlying_symbol="TEST_FIXTURE_U",
            contract_name="fixture",
            multiplier=m,
            tick_size=t,
        )

        fresh = Database(migrated.sqlalchemy_url)
        try:
            reread = SqlAlchemyFactVerificationStore(fresh)
            entry = await reread.entry(m)
            (record,) = await reread.records_for(SYMBOL, limit=10)
            page, total = await reread.review_page(after=0, limit=10)
        finally:
            await fresh.dispose()

        assert entry is not None and entry.submission == a_submission(m)
        assert entry.decision is not None and entry.decision.result is ReviewResult.APPROVED
        assert record.contract.multiplier.value == Decimal("10")  # exact, never a float
        assert record.contract.tick_size.value == Decimal("0.25")
        assert record.reference == REFERENCE
        assert record.known_at is not None and record.known_at >= record.verified_at
        assert total == 2 and [e.submission.submission_id for e in page] == [m, t]


class TestBAppendOnly:
    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE fact_submissions SET reference = 'x'",
            "DELETE FROM fact_submissions",
            "UPDATE fact_review_decisions SET result = 'APPROVED'",
            "DELETE FROM fact_review_decisions",
            "UPDATE contract_fact_records SET reference = 'x'",
            "DELETE FROM contract_fact_records",
        ],
    )
    async def test_update_and_delete_are_refused_by_the_database(
        self, service: FactVerificationService, database: Database, statement: str
    ) -> None:
        m, t = await approved_pair(service)
        await service.publish(
            "R1", underlying_symbol="U", contract_name="n", multiplier=m, tick_size=t
        )

        with pytest.raises(DBAPIError, match="append-only"):
            async with database.engine.begin() as connection:
                await connection.execute(text(statement))


class TestCNoApprovalWithoutEvidence:
    async def test_a_record_citing_a_rejected_decision_is_refused_and_not_written(
        self, service: FactVerificationService, store: SqlAlchemyFactVerificationStore
    ) -> None:
        m, t = await approved_pair(service)
        await service.submit(a_submission("REJ", value=Decimal("99")))
        await service.decide(a_decision("REJ", outcome=ReviewOutcome.REJECTED))
        verdict = judge(a_submission(m), a_decision(m))
        assert verdict.approved is not None
        from app.domain.sourcing.review import record_from_approved

        record = record_from_approved(
            "R-FORGED",
            underlying_symbol="U",
            contract_name="n",
            multiplier=verdict.approved,
            tick_size=judge(
                a_submission(t, ContractFact.TICK_SIZE, Decimal("0.25")), a_decision(t)
            ).approved,  # type: ignore[arg-type]
        )

        with pytest.raises(VerificationConflictError) as caught:
            await store.publish_record(
                record,
                RecordEvidence(multiplier="REJ", tick_size=t),
                recorded_at=START + timedelta(days=1),
            )

        assert caught.value.code == "EVIDENCE_NOT_APPROVED"
        assert await store.records_for(SYMBOL, limit=10) == ()

    async def test_a_writer_bypassing_the_application_cannot_forge_an_approval(
        self, service: FactVerificationService, database: Database
    ) -> None:
        await service.submit(a_submission("S1"))

        with pytest.raises(DBAPIError, match="decision_approval_attested"):
            async with database.engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO fact_review_decisions (submission_id, reviewer, decided_at, "
                        "outcome, document_checked, note, result, refusal_code, recorded_at) "
                        "VALUES ('S1', 'x', now(), 'APPROVED', false, '', 'APPROVED', NULL, now())"
                    )
                )

    async def test_a_refusal_without_its_code_is_unrepresentable(
        self, service: FactVerificationService, database: Database
    ) -> None:
        await service.submit(a_submission("S1"))

        with pytest.raises(DBAPIError, match="decision_refusal_has_code"):
            async with database.engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO fact_review_decisions (submission_id, reviewer, decided_at, "
                        "outcome, document_checked, note, result, refusal_code, recorded_at) "
                        "VALUES ('S1', 'x', now(), 'APPROVED', true, '', 'REFUSED', NULL, now())"
                    )
                )

    async def test_an_interrupted_transaction_leaves_no_decision(
        self,
        service: FactVerificationService,
        database: Database,
        store: SqlAlchemyFactVerificationStore,
    ) -> None:
        await service.submit(a_submission("S1"))

        async with database.engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(
                text(
                    "INSERT INTO fact_review_decisions (submission_id, reviewer, decided_at, "
                    "outcome, document_checked, note, result, refusal_code, recorded_at) "
                    "VALUES ('S1', 'x', now(), 'APPROVED', true, '', 'APPROVED', NULL, now())"
                )
            )
            await transaction.rollback()  # the process "died" before commit

        entry = await store.entry("S1")
        assert entry is not None and entry.decision is None
        # And the real decision can still be made afterwards.
        assert (await service.decide(a_decision("S1"))).result is ReviewResult.APPROVED

    async def test_a_decision_for_an_unknown_submission_is_refused(
        self, store: SqlAlchemyFactVerificationStore
    ) -> None:
        with pytest.raises(VerificationConflictError) as caught:
            await store.append_decision(
                a_decision("NOPE"),
                judge(a_submission("NOPE"), a_decision("NOPE")),
                recorded_at=START,
            )
        assert caught.value.code == "UNKNOWN_SUBMISSION"

    async def test_a_refused_approval_is_journalled_and_cannot_be_published(
        self, service: FactVerificationService
    ) -> None:
        await service.submit(a_submission("CSV", origin=SubmissionOrigin.FILE_IMPORT))
        await service.submit(a_submission("T", ContractFact.TICK_SIZE, Decimal("0.25")))
        refused = await service.decide(a_decision("CSV"))
        await service.decide(a_decision("T"))

        with pytest.raises(VerificationConflictError) as caught:
            await service.publish(
                "R1", underlying_symbol="U", contract_name="n", multiplier="CSV", tick_size="T"
            )

        assert (refused.result, refused.refusal_code) == (
            ReviewResult.REFUSED,
            "FILE_IMPORT_NOT_VERIFIABLE",
        )
        assert caught.value.code == "NOT_APPROVED"

    async def test_a_correction_of_an_unknown_record_is_refused(
        self, service: FactVerificationService
    ) -> None:
        with pytest.raises(VerificationConflictError) as caught:
            await service.submit(a_submission("S1", corrects="NO_SUCH_RECORD"))
        assert caught.value.code == "UNKNOWN_CORRECTED_RECORD"


class TestDIdempotency:
    async def test_a_retried_submission_decision_and_publish_land_once(
        self, service: FactVerificationService, store: SqlAlchemyFactVerificationStore
    ) -> None:
        m, t = await approved_pair(service)
        assert await service.submit(a_submission(m)) is False
        again = await service.decide(a_decision(m, at=START + timedelta(hours=1)))
        await service.publish(
            "R1", underlying_symbol="U", contract_name="n", multiplier=m, tick_size=t
        )
        await service.publish(
            "R1", underlying_symbol="U", contract_name="n", multiplier=m, tick_size=t
        )

        counts = await store.counts()
        assert again.result is ReviewResult.APPROVED
        assert (counts.submissions, counts.approved, counts.records) == (2, 2, 1)

    async def test_different_content_under_a_held_identity_is_a_conflict(
        self, service: FactVerificationService
    ) -> None:
        await service.submit(a_submission("S1"))

        with pytest.raises(VerificationConflictError) as caught:
            await service.submit(a_submission("S1", value=Decimal("11")))
        assert caught.value.code == "SUBMISSION_ID_TAKEN"

    async def test_a_record_id_cannot_be_reused_for_another_record(
        self, service: FactVerificationService
    ) -> None:
        m, t = await approved_pair(service)
        m2, t2 = await approved_pair(service, "B", multiplier="100")
        await service.publish(
            "R1", underlying_symbol="U", contract_name="n", multiplier=m, tick_size=t
        )

        with pytest.raises(VerificationConflictError) as caught:
            await service.publish(
                "R1", underlying_symbol="U", contract_name="n", multiplier=m2, tick_size=t2
            )
        assert caught.value.code == "RECORD_ID_TAKEN"


class TestEConcurrency:
    async def test_conflicting_concurrent_reviews_produce_exactly_one(
        self, service: FactVerificationService, migrated: Settings
    ) -> None:
        await service.submit(a_submission("S1"))
        approve, reject = a_decision("S1"), a_decision("S1", outcome=ReviewOutcome.REJECTED)
        databases = [Database(migrated.sqlalchemy_url) for _ in range(2)]
        try:
            stores = [SqlAlchemyFactVerificationStore(db) for db in databases]
            results = await asyncio.gather(
                stores[0].append_decision(
                    approve, judge(a_submission("S1"), approve), recorded_at=START
                ),
                stores[1].append_decision(
                    reject, judge(a_submission("S1"), reject), recorded_at=START
                ),
                return_exceptions=True,
            )
            entry = await stores[0].entry("S1")
        finally:
            for db in databases:
                await db.dispose()

        won = [r for r in results if not isinstance(r, BaseException)]
        lost = [r for r in results if isinstance(r, BaseException)]
        assert len(won) == 1 and len(lost) == 1
        assert isinstance(lost[0], VerificationConflictError)
        assert lost[0].code == "ALREADY_DECIDED"
        assert entry is not None and entry.decision == won[0][0]

    async def test_identical_concurrent_reviews_write_once(
        self, service: FactVerificationService, store: SqlAlchemyFactVerificationStore
    ) -> None:
        await service.submit(a_submission("S1"))
        decision = a_decision("S1")
        verdict = judge(a_submission("S1"), decision)

        results = await asyncio.gather(
            *(store.append_decision(decision, verdict, recorded_at=START) for _ in range(5))
        )

        assert sum(1 for _, written in results if written) == 1
        assert (await store.counts()).approved == 1


class TestKKnowledgeTimeIsDurable:
    async def test_a_correction_published_later_does_not_reach_back(
        self, service: FactVerificationService, store: SqlAlchemyFactVerificationStore
    ) -> None:
        m, t = await approved_pair(service, multiplier="100")
        wrong = await service.publish(
            "WRONG", underlying_symbol="U", contract_name="n", multiplier=m, tick_size=t
        )
        m2, t2 = await approved_pair(service, "FIX", corrects="WRONG")
        await service.publish(
            "FIXED", underlying_symbol="U", contract_name="n", multiplier=m2, tick_size=t2
        )
        records = await store.records_for(SYMBOL, limit=10)
        by = {r.record_id: r for r in records}
        fixed_at, wrong_at = by["FIXED"].known_at, by["WRONG"].known_at
        assert fixed_at is not None and wrong_at is not None
        between = fixed_at - timedelta(microseconds=1)

        def at(known_by: datetime | None) -> Any:
            return assess_fact_records(
                SYMBOL,
                records,
                applies_at=START,
                now=START + timedelta(days=1),
                max_age=timedelta(days=30),
                known_by=known_by,
            )

        assert wrong.record_id == "WRONG"
        assert at(between).record.record_id == "WRONG"
        assert at(None).record.record_id == "FIXED"
        assert at(wrong_at - timedelta(microseconds=1)).code is FactVerdictCode.NOT_YET_KNOWN

    async def test_records_are_read_for_exactly_one_contract(
        self, service: FactVerificationService, store: SqlAlchemyFactVerificationStore
    ) -> None:
        m, t = await approved_pair(service, symbol="TEST_FIXTURE_A")
        await service.publish(
            "RA", underlying_symbol="U", contract_name="n", multiplier=m, tick_size=t
        )

        assert len(await store.records_for("TEST_FIXTURE_A", limit=10)) == 1
        for other in ("TEST_FIXTURE_B", "test_fixture_a", "TEST_FIXTURE_A%", "TEST_FIXTURE_"):
            assert await store.records_for(other, limit=10) == ()


class TestQueryBounds:
    async def test_reading_records_is_two_queries_however_many_records(
        self,
        service: FactVerificationService,
        store: SqlAlchemyFactVerificationStore,
        database: Database,
    ) -> None:
        for i in range(6):
            m, t = await approved_pair(service, f"P{i}")
            await service.publish(
                f"R{i}", underlying_symbol="U", contract_name="n", multiplier=m, tick_size=t
            )
        statements: list[str] = []

        def count(*args: Any) -> None:
            statements.append(str(args[2]))

        event.listen(database.engine.sync_engine, "before_cursor_execute", count)
        try:
            records = await store.records_for(SYMBOL, limit=100)
            page, _ = await store.review_page(after=0, limit=100)
        finally:
            event.remove(database.engine.sync_engine, "before_cursor_execute", count)

        assert len(records) == 6 and len(page) == 12
        selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
        assert len(selects) == 4  # records + evidence, count + page

    async def test_a_page_is_bounded_and_resumes_after_its_cursor(
        self, service: FactVerificationService, store: SqlAlchemyFactVerificationStore
    ) -> None:
        for i in range(5):
            await service.submit(a_submission(f"S{i}"))

        first, total = await store.review_page(after=0, limit=2)
        second, _ = await store.review_page(after=first[-1].sequence, limit=2)

        assert total == 5
        assert [e.submission.submission_id for e in first + second] == ["S0", "S1", "S2", "S3"]
