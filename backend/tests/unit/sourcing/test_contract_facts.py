"""Contract facts by applicable period and authority (Phase 15 Part 2A, D-G).

Every record is TEST_FIXTURE-sourced; no value is a VIOP specification.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.application.ports.fact_verification import VerificationConflictError
from app.application.sourcing.fact_review import FactVerificationService
from app.application.sourcing.intelligence import VerifiedContractFacts
from app.domain.common.verification import VerificationStatus
from app.domain.sourcing.facts import (
    ContractSourceRecord,
    FactVerdictCode,
    SourceAuthority,
    assess_fact_records,
)
from app.domain.sourcing.limits import ProviderLimits
from app.domain.sourcing.review import (
    ContractFact,
    FactSubmission,
    ReviewDecision,
    ReviewOutcome,
    ReviewRefusedError,
    ReviewResult,
    SubmissionOrigin,
    record_from_approved,
    review,
)
from tests.unit.sourcing.memory_store import MemoryVerificationStore
from tests.unit.sourcing.support import NOW, contract, fact

pytestmark = pytest.mark.unit

MAX_AGE = timedelta(days=30)
SYMBOL = "TEST_FIXTURE_FUT"


def record(
    record_id: str = "R1",
    *,
    multiplier: str = "10",
    authority: SourceAuthority = SourceAuthority.EXCHANGE_OFFICIAL,
    effective_from: datetime = NOW - timedelta(days=200),
    effective_until: datetime | None = None,
    verified_at: datetime = NOW - timedelta(days=5),
    corrects: str | None = None,
    symbol: str = SYMBOL,
    status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT,
    reference: str = "TEST_FIXTURE_DOC#spec",
) -> ContractSourceRecord:
    return ContractSourceRecord(
        record_id=record_id,
        contract=contract(symbol, multiplier=fact(multiplier, status=status)),
        authority=authority,
        reference=reference,
        effective_from=effective_from,
        effective_until=effective_until,
        verified_at=verified_at,
        corrects=corrects,
    )


def assess(*records: ContractSourceRecord, applies_at: datetime = NOW, requested: str = SYMBOL):  # type: ignore[no-untyped-def]
    return assess_fact_records(requested, records, applies_at=applies_at, now=NOW, max_age=MAX_AGE)


class TestDWrongContractRefused:
    def test_a_record_for_another_contract_is_refused(self) -> None:
        verdict = assess(record(symbol="TEST_FIXTURE_OTHER"))

        assert verdict.code is FactVerdictCode.WRONG_CONTRACT
        assert verdict.contract is None

    def test_a_single_wrong_record_among_right_ones_refuses_the_lot(self) -> None:
        verdict = assess(record("R1"), record("R2", symbol="TEST_FIXTURE_OTHER"))

        assert verdict.code is FactVerdictCode.WRONG_CONTRACT


class TestEObsoletePeriodRejected:
    def test_a_recently_checked_obsolete_record_does_not_defeat_the_applicable_one(
        self,
    ) -> None:
        obsolete = record(
            "OBSOLETE",
            multiplier="100",
            effective_from=NOW - timedelta(days=400),
            effective_until=NOW - timedelta(days=30),
            verified_at=NOW - timedelta(hours=1),  # checked an hour ago
        )
        applicable = record(
            "APPLICABLE",
            effective_from=NOW - timedelta(days=30),
            verified_at=NOW - timedelta(days=20),  # checked three weeks ago
        )

        verdict = assess(obsolete, applicable)

        assert verdict.code is FactVerdictCode.USABLE
        assert verdict.record is applicable
        assert verdict.conflicts == ()  # the obsolete record never competed

    def test_only_an_obsolete_record_is_not_in_effect(self) -> None:
        verdict = assess(
            record(effective_until=NOW - timedelta(days=1), verified_at=NOW - timedelta(hours=1))
        )

        assert verdict.code is FactVerdictCode.NOT_IN_EFFECT

    def test_a_record_not_yet_in_effect_is_not_used_early(self) -> None:
        verdict = assess(record(effective_from=NOW + timedelta(days=1)))

        assert verdict.code is FactVerdictCode.NOT_IN_EFFECT

    def test_history_is_answered_by_the_record_that_governed_then(self) -> None:
        old = record(
            "OLD",
            multiplier="100",
            effective_from=NOW - timedelta(days=400),
            effective_until=NOW - timedelta(days=30),
        )
        new = record("NEW", effective_from=NOW - timedelta(days=30))

        then = assess(old, new, applies_at=NOW - timedelta(days=60))

        assert then.record is old

    def test_an_applicable_record_verified_too_long_ago_is_stale(self) -> None:
        verdict = assess(record(verified_at=NOW - MAX_AGE - timedelta(days=1)))

        assert verdict.code is FactVerdictCode.STALE


class TestExpiry:
    """Part 2C: an expired contract is never current, and history before the
    expiry is still answered by the record that governed it."""

    def expiring(self, expiry: date) -> ContractSourceRecord:
        return replace(record(), contract=contract(SYMBOL, expiry=expiry))

    def test_a_contract_past_its_verified_expiry_is_refused(self) -> None:
        verdict = assess(self.expiring((NOW - timedelta(days=1)).date()))

        assert verdict.code is FactVerdictCode.EXPIRED
        assert verdict.contract is None

    def test_the_period_before_expiry_is_still_answered(self) -> None:
        verdict = assess(
            self.expiring((NOW - timedelta(days=1)).date()), applies_at=NOW - timedelta(days=30)
        )

        assert verdict.code is FactVerdictCode.USABLE


class TestFOfficialVerifiedPreferred:
    def test_the_exchange_outranks_a_disagreeing_provider_for_the_same_period(self) -> None:
        exchange = record("EXCHANGE")
        vendor = record("VENDOR", multiplier="100", authority=SourceAuthority.LICENSED_PROVIDER)

        verdict = assess(vendor, exchange)

        assert verdict.record is exchange
        (conflict,) = verdict.conflicts
        assert (conflict.other_record, conflict.other_value) == ("VENDOR", "100")
        assert "outranks" in conflict.resolution

    @pytest.mark.parametrize("authority", [SourceAuthority.SECONDARY, SourceAuthority.UNKNOWN])
    def test_a_secondary_source_is_never_authoritative(self, authority: SourceAuthority) -> None:
        assert assess(record(authority=authority)).code is FactVerdictCode.NOT_AUTHORITATIVE

    @pytest.mark.parametrize(
        "status",
        [
            VerificationStatus.UNVERIFIED,
            VerificationStatus.TEST_FIXTURE,
            VerificationStatus.DEVELOPMENT_DEFAULT,
            VerificationStatus.MOCK_DATA,
        ],
    )
    def test_an_unverified_fact_is_not_authoritative(self, status: VerificationStatus) -> None:
        assert assess(record(status=status)).code is FactVerdictCode.NOT_AUTHORITATIVE

    def test_a_record_without_a_reference_is_not_authoritative(self) -> None:
        assert assess(record(reference=" ")).code is FactVerdictCode.NOT_AUTHORITATIVE

    def test_an_unverified_newer_record_does_not_displace_a_verified_one(self) -> None:
        verified = record("VERIFIED")
        guess = record(
            "GUESS",
            multiplier="999",
            status=VerificationStatus.UNVERIFIED,
            effective_from=NOW - timedelta(days=1),
        )

        verdict = assess(verified, guess)

        assert verdict.record is verified
        assert verdict.conflicts == ()

    def test_a_newer_official_period_governs_and_the_older_is_recorded(self) -> None:
        older = record("OLDER", multiplier="100", effective_from=NOW - timedelta(days=300))
        newer = record("NEWER", effective_from=NOW - timedelta(days=10))

        verdict = assess(older, newer)

        assert verdict.record is newer
        assert verdict.conflicts[0].resolution.startswith("the record with the later")


class TestGConflictsRecordedOrRefused:
    def test_same_period_same_rank_disagreement_is_refused(self) -> None:
        verdict = assess(record("A"), record("B", multiplier="100"))

        assert verdict.code is FactVerdictCode.CONFLICTING
        assert verdict.contract is None

    def test_a_provider_claiming_a_newer_period_than_the_exchange_is_refused(self) -> None:
        verdict = assess(
            record("EXCHANGE"),
            record(
                "VENDOR",
                multiplier="100",
                authority=SourceAuthority.LICENSED_PROVIDER,
                effective_from=NOW - timedelta(days=5),
            ),
        )

        assert verdict.code is FactVerdictCode.CONFLICTING
        assert "provider" in verdict.reason

    def test_a_correction_supersedes_the_record_it_names_and_says_so(self) -> None:
        wrong = record("WRONG", multiplier="100")
        fixed = record("FIXED", corrects="WRONG")

        verdict = assess(wrong, fixed)

        assert verdict.record is fixed
        assert verdict.superseded == ("WRONG",)
        assert verdict.conflicts == ()

    def test_two_records_with_one_identity_are_refused(self) -> None:
        assert assess(record("A"), record("A", multiplier="100")).code is (
            FactVerdictCode.CONFLICTING
        )

    def test_a_record_cannot_have_an_empty_period(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            record(effective_until=NOW - timedelta(days=200))


# ----------------------------------------------------------------------
# The operator review boundary (section 3 of the Part 2A brief)


def submission(
    fact_kind: ContractFact = ContractFact.MULTIPLIER,
    value: Decimal | date | None = Decimal("10"),
    *,
    submission_id: str = "S1",
    origin: SubmissionOrigin = SubmissionOrigin.MANUAL_ENTRY,
    reference: str = "TEST_FIXTURE_DOC#spec",
    authority: SourceAuthority = SourceAuthority.EXCHANGE_OFFICIAL,
    effective_from: datetime | None = NOW - timedelta(days=100),
) -> FactSubmission:
    return FactSubmission(
        submission_id=submission_id,
        symbol=SYMBOL,
        fact=fact_kind,
        value=value,
        reference=reference,
        authority=authority,
        effective_from=effective_from,
        submitted_by="test-operator",
        submitted_at=NOW - timedelta(hours=2),
        origin=origin,
    )


def decision(
    submission_id: str = "S1",
    *,
    outcome: ReviewOutcome = ReviewOutcome.APPROVED,
    document_checked: bool = True,
    decided_at: datetime = NOW - timedelta(hours=1),
    reviewer: str = "test-reviewer",
) -> ReviewDecision:
    return ReviewDecision(
        submission_id=submission_id,
        reviewer=reviewer,
        decided_at=decided_at,
        outcome=outcome,
        document_checked=document_checked,
    )


class TestTheReviewBoundary:
    def test_an_approval_is_a_verified_fact_citing_its_document_and_reviewer(self) -> None:
        approved = review(submission(), decision())

        assert approved is not None
        assert approved.verified.status is VerificationStatus.VERIFIED_CURRENT_FACT
        assert approved.verified.source == "TEST_FIXTURE_DOC#spec"
        assert approved.verified.as_of == NOW - timedelta(hours=1)
        assert "test-reviewer" in (approved.verified.note or "")

    def test_a_csv_import_never_becomes_verified(self) -> None:
        with pytest.raises(ReviewRefusedError) as caught:
            review(submission(origin=SubmissionOrigin.FILE_IMPORT), decision())
        assert caught.value.code == "FILE_IMPORT_NOT_VERIFIABLE"

    def test_an_official_looking_url_asserts_nothing_without_a_checked_document(self) -> None:
        official_looking = submission(reference="https://www.borsaistanbul.com/en/viop.pdf")

        with pytest.raises(ReviewRefusedError) as caught:
            review(official_looking, decision(document_checked=False))
        assert caught.value.code == "DOCUMENT_NOT_CHECKED"

    def test_an_official_looking_url_does_not_make_a_secondary_source_official(self) -> None:
        looks_official = submission(
            reference="https://www.borsaistanbul.com/en/viop.pdf",
            authority=SourceAuthority.SECONDARY,
        )

        with pytest.raises(ReviewRefusedError) as caught:
            review(looks_official, decision())
        assert caught.value.code == "NOT_AN_AUTHORITATIVE_SOURCE"

    @pytest.mark.parametrize(
        ("kwargs", "code"),
        [
            ({"reference": " "}, "NO_REFERENCE"),
            ({"effective_from": None}, "EFFECTIVE_DATE_MISSING"),
            ({"value": None}, "VALUE_MISSING"),
            ({"value": Decimal("0")}, "VALUE_NOT_POSITIVE"),
            ({"value": date(2099, 1, 1)}, "VALUE_TYPE"),
        ],
    )
    def test_incomplete_submissions_are_refused(self, kwargs: dict[str, object], code: str) -> None:
        with pytest.raises(ReviewRefusedError) as caught:
            review(submission(**kwargs), decision())  # type: ignore[arg-type]
        assert caught.value.code == code

    def test_a_decision_cannot_predate_its_submission(self) -> None:
        with pytest.raises(ReviewRefusedError) as caught:
            review(submission(), decision(decided_at=NOW - timedelta(days=1)))
        assert caught.value.code == "DECIDED_BEFORE_SUBMITTED"

    def test_a_rejection_produces_nothing(self) -> None:
        assert review(submission(), decision(outcome=ReviewOutcome.REJECTED)) is None

    def test_a_record_is_assembled_only_from_one_document(self) -> None:
        multiplier = review(submission(), decision())
        tick = review(
            submission(ContractFact.TICK_SIZE, Decimal("0.25"), submission_id="S2", reference="X"),
            decision("S2"),
        )
        assert multiplier is not None and tick is not None

        with pytest.raises(ReviewRefusedError) as caught:
            record_from_approved(
                "R1",
                underlying_symbol="TEST_FIXTURE_UNDERLYING",
                contract_name="fixture",
                multiplier=multiplier,
                tick_size=tick,
            )
        assert caught.value.code == "MIXED_SOURCES"

    def test_approved_facts_become_a_record_the_assessment_accepts(self) -> None:
        multiplier = review(submission(), decision())
        tick = review(
            submission(ContractFact.TICK_SIZE, Decimal("0.25"), submission_id="S2"),
            decision("S2"),
        )
        assert multiplier is not None and tick is not None

        built = record_from_approved(
            "R1",
            underlying_symbol="TEST_FIXTURE_UNDERLYING",
            contract_name="fixture",
            multiplier=multiplier,
            tick_size=tick,
        )

        verdict = assess(built)
        assert verdict.code is FactVerdictCode.USABLE
        assert built.reviewed_by == "test-reviewer"


class StepClock:
    def __init__(self, start: datetime = NOW) -> None:
        self.moment = start

    def now(self) -> datetime:
        self.moment += timedelta(seconds=1)
        return self.moment


def workflow() -> tuple[FactVerificationService, MemoryVerificationStore]:
    store = MemoryVerificationStore()
    return FactVerificationService(store, StepClock()), store


class TestTheAuditTrail:
    async def test_a_refused_approval_is_journalled_with_its_code_not_lost(self) -> None:
        service, store = workflow()
        await service.submit(submission(origin=SubmissionOrigin.FILE_IMPORT))

        journalled = await service.decide(decision())

        assert journalled.result is ReviewResult.REFUSED
        assert journalled.refusal_code == "FILE_IMPORT_NOT_VERIFIABLE"
        assert (await store.entry("S1")).decision == journalled  # type: ignore[union-attr]

    async def test_a_submission_is_decided_once_and_a_retry_is_idempotent(self) -> None:
        service, store = workflow()
        await service.submit(submission())
        first = await service.decide(decision())

        again = await service.decide(decision())
        with pytest.raises(VerificationConflictError) as caught:
            await service.decide(decision(outcome=ReviewOutcome.REJECTED))

        assert again == first
        assert caught.value.code == "ALREADY_DECIDED"
        assert store.writes == 2  # one submission, one decision

    async def test_an_unknown_or_different_duplicate_submission_is_refused(self) -> None:
        service, _ = workflow()
        with pytest.raises(ReviewRefusedError):
            await service.decide(decision())
        assert await service.submit(submission()) is True
        assert await service.submit(submission()) is False  # identical: idempotent
        with pytest.raises(VerificationConflictError):
            await service.submit(submission(value=Decimal("11")))

    async def test_a_symbol_is_journalled_exactly_as_named(self) -> None:
        service, _ = workflow()
        padded = replace(submission(), symbol=" TEST_FIXTURE_FUT")

        with pytest.raises(ReviewRefusedError) as caught:
            await service.submit(padded)
        assert caught.value.code == "SYMBOL_NOT_CANONICAL"

    async def test_publish_needs_journalled_approvals_and_records_knowledge_time(self) -> None:
        service, store = workflow()
        await service.submit(submission())
        await service.submit(
            submission(ContractFact.TICK_SIZE, Decimal("0.25"), submission_id="S2")
        )
        await service.decide(decision())

        with pytest.raises(VerificationConflictError) as caught:
            await service.publish(
                "R1", underlying_symbol="U", contract_name="n", multiplier="S1", tick_size="S2"
            )
        assert caught.value.code == "NOT_APPROVED"

        await service.decide(decision("S2"))
        await service.publish(
            "R1", underlying_symbol="U", contract_name="n", multiplier="S1", tick_size="S2"
        )
        (held,) = await store.records_for(SYMBOL, limit=10)
        assert held.known_at is not None and held.known_at >= held.verified_at


class TestPublishRefusesEveryNonApproval:
    """Part 2C: the service's own check, pinned. A rejected or refused decision
    is NOT_APPROVED at the application layer - before the re-judgement and the
    database's foreign keys, which stand behind it."""

    @pytest.mark.parametrize(
        ("origin", "outcome"),
        [
            (SubmissionOrigin.MANUAL_ENTRY, ReviewOutcome.REJECTED),
            (SubmissionOrigin.FILE_IMPORT, ReviewOutcome.APPROVED),
        ],
        ids=["rejected", "refused"],
    )
    async def test_publish_names_the_missing_approval(
        self, origin: SubmissionOrigin, outcome: ReviewOutcome
    ) -> None:
        service, store = workflow()
        await service.submit(submission(origin=origin))
        await service.submit(
            submission(ContractFact.TICK_SIZE, Decimal("0.25"), submission_id="S2")
        )
        await service.decide(decision(outcome=outcome))
        await service.decide(decision("S2"))

        with pytest.raises(VerificationConflictError) as caught:
            await service.publish(
                "R1", underlying_symbol="U", contract_name="n", multiplier="S1", tick_size="S2"
            )

        assert caught.value.code == "NOT_APPROVED"
        assert store.records == {}


class FactSource:
    def __init__(self, *records: ContractSourceRecord, fail: bool = False) -> None:
        self._records = records
        self._fail = fail

    async def symbols(self) -> Sequence[str]:
        return tuple({r.contract.symbol for r in self._records})

    async def records_for(self, symbol: str) -> Sequence[ContractSourceRecord]:
        if self._fail:
            raise ConnectionError("https://vendor.example/facts?token=sk-facts-do-not-leak")
        return self._records


class TestTheProviderBehindThePort:
    def gate(self, *sources: tuple[str, FactSource]) -> VerifiedContractFacts:
        return VerifiedContractFacts(
            sources,
            now=lambda: NOW,
            max_age=MAX_AGE,
            limits=ProviderLimits(max_fact_records=3),
        )

    async def test_only_a_usable_record_reaches_consumers(self) -> None:
        good = self.gate(("A", FactSource(record())))
        guess = self.gate(("A", FactSource(record(status=VerificationStatus.UNVERIFIED))))

        assert await good.get_contract(SYMBOL) is not None
        assert await guess.get_contract(SYMBOL) is None

    async def test_history_asks_for_the_period_not_today(self) -> None:
        gate = self.gate(("A", FactSource(record(effective_from=NOW - timedelta(days=10)))))

        verdict = await gate.assess(SYMBOL, applies_at=NOW - timedelta(days=60))
        assert verdict.code is FactVerdictCode.NOT_IN_EFFECT

    async def test_an_oversized_answer_is_refused_not_truncated(self) -> None:
        many = [record(f"R{i}") for i in range(4)]
        gate = self.gate(("A", FactSource(*many)))

        assert (await gate.assess(SYMBOL)).code is FactVerdictCode.MISSING

    async def test_a_failing_source_leaks_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        gate = self.gate(("BROKEN", FactSource(fail=True)), ("A", FactSource(record())))

        assert await gate.get_contract(SYMBOL) is not None
        assert "sk-facts-do-not-leak" not in caplog.text
        assert "vendor.example" not in caplog.text
