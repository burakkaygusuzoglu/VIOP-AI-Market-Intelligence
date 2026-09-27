"""As-of knowledge, recorded conflicts, calendar audits and source status
(Phase 15 Part 2B; brief items J, K, L, M, N, O, P, Q, R).

Every record and calendar day is a TEST_FIXTURE built here.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from app.adapters.sourcing.unavailable_calendar import UnavailableSessionCalendar
from app.application.sourcing.source_status import (
    MAX_RECORDS,
    FieldState,
    SourceComposition,
    SourceStatusService,
)
from app.domain.common.verification import VerificationStatus
from app.domain.sourcing.calendar import (
    CalendarDay,
    DayKind,
    SessionInterval,
    SessionStatus,
    answer_from,
)
from app.domain.sourcing.capability import (
    CapabilityStatus,
    DataCategory,
    Delivery,
    ProviderDeclaration,
)
from app.domain.sourcing.facts import (
    ContractSourceRecord,
    FactVerdictCode,
    SourceAuthority,
    assess_fact_records,
)
from app.domain.sourcing.review import (
    FactSubmission,
    ReviewDecision,
    ReviewOutcome,
    ReviewResult,
    SubmissionOrigin,
    judge,
    same_claim,
    same_decision,
)
from tests.unit.sourcing.memory_store import MemoryVerificationStore
from tests.unit.sourcing.support import NOW, contract, fact, fixture_grant, reference
from tests.unit.sourcing.test_contract_facts import decision, submission

pytestmark = pytest.mark.unit

AGE = timedelta(days=30)
SYMBOL = "TEST_FIXTURE_FUT"
T1 = NOW - timedelta(days=10)
T2 = NOW - timedelta(days=2)


def record(
    record_id: str = "R1",
    *,
    multiplier: str = "10",
    known_at: datetime | None = T1,
    verified_at: datetime = T1,
    corrects: str | None = None,
    effective_from: datetime = NOW - timedelta(days=200),
    effective_until: datetime | None = None,
    authority: SourceAuthority = SourceAuthority.EXCHANGE_OFFICIAL,
    status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT,
    symbol: str = SYMBOL,
) -> ContractSourceRecord:
    return ContractSourceRecord(
        record_id=record_id,
        contract=contract(symbol, multiplier=fact(multiplier, status=status)),
        authority=authority,
        reference="TEST_FIXTURE_DOC#spec",
        effective_from=effective_from,
        effective_until=effective_until,
        verified_at=verified_at,
        corrects=corrects,
        known_at=known_at,
    )


def as_known(*records: ContractSourceRecord, known_by: datetime | None, applies_at=NOW):  # type: ignore[no-untyped-def]
    return assess_fact_records(
        SYMBOL, records, applies_at=applies_at, now=NOW, max_age=AGE, known_by=known_by
    )


class TestKHistoricalKnowledge:
    def test_a_record_the_system_did_not_yet_hold_does_not_exist(self) -> None:
        verdict = as_known(record(), known_by=T1 - timedelta(seconds=1))

        assert verdict.code is FactVerdictCode.NOT_YET_KNOWN
        assert verdict.contract is None

    def test_a_fact_valid_for_the_past_but_learnt_later_is_not_known_then(self) -> None:
        """Valid at market time is not known by that time."""
        learnt_late = record(effective_from=NOW - timedelta(days=400), known_at=T2)

        then = as_known(learnt_late, known_by=T1, applies_at=NOW - timedelta(days=300))
        retro = as_known(learnt_late, known_by=None, applies_at=NOW - timedelta(days=300))

        assert then.code is FactVerdictCode.NOT_YET_KNOWN
        assert retro.code is FactVerdictCode.USABLE

    def test_a_later_correction_does_not_rewrite_what_was_known_before_it(self) -> None:
        wrong = record("WRONG", multiplier="100", known_at=T1)
        fixed = record("FIXED", corrects="WRONG", known_at=T2, verified_at=T2)

        before = as_known(wrong, fixed, known_by=T2 - timedelta(seconds=1))
        after = as_known(wrong, fixed, known_by=NOW)

        assert before.record is wrong
        assert after.record is fixed
        assert after.superseded == ("WRONG",)

    def test_a_record_without_a_knowledge_time_is_never_known_as_of(self) -> None:
        assert as_known(record(known_at=None), known_by=NOW).code is FactVerdictCode.NOT_YET_KNOWN

    def test_staleness_is_judged_at_the_knowledge_boundary(self) -> None:
        old = record(verified_at=NOW - timedelta(days=100), known_at=NOW - timedelta(days=100))

        then = as_known(old, known_by=NOW - timedelta(days=90))
        now = as_known(old, known_by=NOW)

        assert then.code is FactVerdictCode.USABLE
        assert now.code is FactVerdictCode.STALE


class TestLConflictsAreRecordedWhenRefused:
    def test_same_period_disagreement_is_refused_and_both_values_kept(self) -> None:
        verdict = as_known(record("A"), record("B", multiplier="100"), known_by=None)

        assert verdict.code is FactVerdictCode.CONFLICTING
        assert verdict.contract is None
        (conflict,) = verdict.conflicts
        assert {conflict.chosen_value, conflict.other_value} == {"10", "100"}
        assert conflict.resolution.startswith("UNRESOLVED")

    def test_a_recently_checked_obsolete_record_still_loses(self) -> None:
        """Part 2A's correction, preserved (J)."""
        obsolete = record(
            "OBSOLETE",
            multiplier="100",
            effective_until=NOW - timedelta(days=30),
            verified_at=NOW - timedelta(hours=1),
            known_at=NOW - timedelta(hours=1),
        )
        applicable = record("APPLICABLE", effective_from=NOW - timedelta(days=30))

        assert as_known(obsolete, applicable, known_by=None).record is applicable


class TestDurableVerdicts:
    def test_every_decision_has_a_recordable_result(self) -> None:
        approved = judge(submission(), decision())
        rejected = judge(submission(), decision(outcome=ReviewOutcome.REJECTED))
        refused = judge(submission(origin=SubmissionOrigin.FILE_IMPORT), decision())

        assert approved.result is ReviewResult.APPROVED and approved.approved is not None
        assert rejected.result is ReviewResult.REJECTED and rejected.approved is None
        assert (refused.result, refused.refusal_code) == (
            ReviewResult.REFUSED,
            "FILE_IMPORT_NOT_VERIFIABLE",
        )

    @pytest.mark.parametrize(
        "reviewer", ["admin", "root", "Borsa Istanbul", "SYSTEM", "exchange-official"]
    )
    def test_a_reviewer_name_confers_nothing(self, reviewer: str) -> None:
        """G: a forged or grand name is still only the operator's assertion; it
        cannot rescue an unchecked document or a secondary source."""
        unchecked = judge(submission(), decision(reviewer=reviewer, document_checked=False))
        secondary = judge(
            submission(authority=SourceAuthority.SECONDARY), decision(reviewer=reviewer)
        )

        assert unchecked.refusal_code == "DOCUMENT_NOT_CHECKED"
        assert secondary.refusal_code == "NOT_AN_AUTHORITATIVE_SOURCE"


class TestRetryIdentity:
    """Part 2C: a retried operator command restamps its time; nothing else may
    differ for it to count as the same claim or decision."""

    def test_only_the_asserted_time_may_differ(self) -> None:
        first = submission()
        later = replace(first, submitted_at=first.submitted_at + timedelta(hours=1))

        assert same_claim(first, later)
        assert not same_claim(first, replace(later, value=Decimal("11")))
        assert not same_claim(first, replace(later, reference="OTHER"))
        assert not same_claim(first, replace(later, authority=SourceAuthority.SECONDARY))

    def test_a_decision_retry_matches_only_on_everything_but_its_time(self) -> None:
        first = decision()
        later = replace(first, decided_at=first.decided_at + timedelta(minutes=5))

        assert same_decision(first, later)
        assert not same_decision(first, replace(later, outcome=ReviewOutcome.REJECTED))
        assert not same_decision(first, replace(later, document_checked=False))
        assert not same_decision(first, replace(later, reviewer="someone-else"))


# ----------------------------------------------------------------------
# Calendar audits (N, M)

DAY = date(2026, 9, 21)


def day(
    *,
    offset: timedelta = timedelta(hours=2),
    intervals: tuple[SessionInterval, ...] = (SessionInterval(time(10), time(12)),),
    kind: DayKind = DayKind.REGULAR,
) -> CalendarDay:
    return CalendarDay(
        venue="TEST_FIXTURE_VENUE",
        session_category="TEST_FIXTURE_CATEGORY",
        day=DAY,
        timezone_name="TEST_FIXTURE/Zone",
        utc_offset=offset,
        kind=kind,
        intervals=intervals,
        source=reference(),
        effective_from=date(2026, 1, 1),
    )


def ask(*days: CalendarDay, at: datetime) -> SessionStatus:
    return answer_from(
        days,
        symbol=SYMBOL,
        venue="TEST_FIXTURE_VENUE",
        session_category="TEST_FIXTURE_CATEGORY",
        at=at,
    ).status


AT = datetime.fromisoformat("2026-09-21T09:00:00+00:00")  # 11:00 at the fixture offset


class TestNCalendarAudits:
    def test_a_zero_length_interval_is_refused(self) -> None:
        with pytest.raises(ValueError):
            SessionInterval(time(10), time(10))

    def test_an_overnight_interval_is_refused_not_wrapped(self) -> None:
        with pytest.raises(ValueError, match="midnight"):
            SessionInterval(time(22), time(2))

    def test_overlapping_intervals_are_refused(self) -> None:
        with pytest.raises(ValueError, match="overlap"):
            day(
                intervals=(
                    SessionInterval(time(10), time(12)),
                    SessionInterval(time(11, 59), time(13)),
                )
            )

    def test_two_offsets_for_one_date_are_ambiguous_and_unavailable(self) -> None:
        """A transition day recorded twice - once per offset - is not guessed."""
        summer, winter = day(offset=timedelta(hours=3)), day(offset=timedelta(hours=2))

        assert ask(summer, winter, at=AT) is SessionStatus.UNAVAILABLE

    def test_a_late_differing_record_for_a_date_is_unavailable_not_chosen(self) -> None:
        corrected = day(kind=DayKind.EARLY_CLOSE, intervals=(SessionInterval(time(10), time(11)),))

        assert ask(day(), corrected, at=AT) is SessionStatus.UNAVAILABLE

    def test_an_unknown_date_is_unavailable(self) -> None:
        assert ask(day(), at=AT + timedelta(days=1)) is SessionStatus.UNAVAILABLE

    async def test_the_composed_calendar_never_claims_a_session(self) -> None:
        answer = await UnavailableSessionCalendar().session_at(SYMBOL, NOW)

        assert answer.status is SessionStatus.UNAVAILABLE
        assert answer.basis is None


# ----------------------------------------------------------------------
# The status service (O, P, Q, R)


def service(
    store: MemoryVerificationStore | None = None, **composition: object
) -> SourceStatusService:
    return SourceStatusService(
        composition=SourceComposition(market_data_provider="none", **composition),  # type: ignore[arg-type]
        store=store or MemoryVerificationStore(),
        calendar=UnavailableSessionCalendar(),
        clock=lambda: NOW,
        metadata_max_age=AGE,
        delivery_max_age=timedelta(minutes=5),
    )


async def seeded(*records: ContractSourceRecord) -> MemoryVerificationStore:
    store = MemoryVerificationStore()
    for item in records:
        store.records[item.record_id] = (len(store.records) + 1, item, None)  # type: ignore[assignment]
    return store


class TestOUnsupportedIsNotConfigured:
    async def test_the_production_composition_is_every_category_not_configured(self) -> None:
        rows, counts = await service().capabilities()

        assert [row.category for row in rows] == list(DataCategory)
        assert {row.status for row in rows} == {CapabilityStatus.NOT_CONFIGURED}
        for row in rows:
            assert not (row.configured or row.licensed or row.connected)
            assert not (row.available or row.fresh or row.adapter_in_build)
        assert counts.records == 0

    async def test_the_simulation_is_reported_but_is_not_market_data(self) -> None:
        simulated = service(simulated_market_data=True)
        rows, _ = await simulated.capabilities()

        assert simulated.composition.simulated_market_data
        market = next(row for row in rows if row.category is DataCategory.MARKET_DATA)
        assert market.status is CapabilityStatus.NOT_CONFIGURED


class TestPProviderPresenceIsNotALicence:
    async def test_a_declared_connected_provider_without_a_grant_is_not_licensed(self) -> None:
        declared = ProviderDeclaration(
            "TEST_DOUBLE_PROVIDER", frozenset({DataCategory.MARKET_DATA}), Delivery.REAL_TIME
        )
        rows, _ = await service(declaration=declared, connected=True).capabilities()

        market = next(row for row in rows if row.category is DataCategory.MARKET_DATA)
        assert market.configured and not market.licensed
        assert market.status is CapabilityStatus.NOT_LICENSED


class TestQMarketDataIsNotMetadataTrust:
    async def test_a_licensed_price_feed_leaves_contract_metadata_unconfigured(self) -> None:
        declared = ProviderDeclaration(
            "TEST_DOUBLE_PROVIDER", frozenset({DataCategory.MARKET_DATA}), Delivery.REAL_TIME
        )
        status = service(
            declaration=declared, grant=fixture_grant(Delivery.REAL_TIME), connected=True
        )
        rows, _ = await status.capabilities()
        metadata = await status.metadata(
            SYMBOL, applies_at=None, known_by=None, retrospective=False
        )

        by = {row.category: row for row in rows}
        assert by[DataCategory.MARKET_DATA].licensed
        assert by[DataCategory.CONTRACT_METADATA].status is CapabilityStatus.NOT_CONFIGURED
        assert metadata.verdict.code is FactVerdictCode.MISSING
        assert not metadata.checks.current


class TestMetadataStatus:
    async def test_verified_fields_carry_source_and_financial_use_stays_off(self) -> None:
        status = await service(await seeded(record())).metadata(
            SYMBOL, applies_at=None, known_by=None, retrospective=False
        )

        fields = {f.name: f for f in status.fields}
        assert status.verdict.code is FactVerdictCode.USABLE
        assert fields["multiplier"].state is FieldState.VERIFIED
        assert fields["multiplier"].value == "10"
        assert fields["tick_value"].state is FieldState.NOT_REVIEWABLE
        assert fields["initial_margin"].state is FieldState.NOT_REVIEWABLE
        assert fields["maintenance_margin"].value is None
        assert fields["expiry_date"].state is FieldState.MISSING  # never a zero or a guess
        assert status.checks.current and not status.checks.financial_use_enabled
        assert not status.retrospective

    async def test_unavailable_metadata_reports_no_value_at_all(self) -> None:
        status = await service().metadata(
            SYMBOL, applies_at=None, known_by=None, retrospective=False
        )

        assert {f.state for f in status.fields} <= {
            FieldState.UNAVAILABLE,
            FieldState.NOT_REVIEWABLE,
        }
        assert all(f.value is None for f in status.fields)

    async def test_as_of_is_the_default_and_retrospective_is_labelled(self) -> None:
        store = await seeded(record(known_at=NOW + timedelta(seconds=1)))
        default = await service(store).metadata(
            SYMBOL, applies_at=None, known_by=None, retrospective=False
        )
        retro = await service(store).metadata(
            SYMBOL, applies_at=None, known_by=None, retrospective=True
        )

        assert default.known_by == NOW and default.verdict.code is FactVerdictCode.NOT_YET_KNOWN
        assert retro.retrospective and retro.known_by is None

    async def test_another_contract_s_record_is_never_returned(self) -> None:
        store = await seeded(record(symbol="TEST_FIXTURE_OTHER"))
        status = await service(store).metadata(
            SYMBOL, applies_at=None, known_by=None, retrospective=True
        )

        assert status.records == ()
        assert status.verdict.code is FactVerdictCode.MISSING

    async def test_more_records_than_the_bound_decides_nothing(self) -> None:
        store = await seeded(*(record(f"R{i}") for i in range(MAX_RECORDS + 1)))
        status = await service(store).metadata(
            SYMBOL, applies_at=None, known_by=None, retrospective=True
        )

        assert status.verdict.code is FactVerdictCode.CONFLICTING
        assert len(status.records) == MAX_RECORDS

    async def test_a_journalled_fixture_status_is_never_authoritative(self) -> None:
        """R: a TEST_FIXTURE fact cannot be promoted by being stored."""
        store = await seeded(record(status=VerificationStatus.TEST_FIXTURE))
        status = await service(store).metadata(
            SYMBOL, applies_at=None, known_by=None, retrospective=True
        )

        assert status.verdict.code is FactVerdictCode.NOT_AUTHORITATIVE
        assert not status.checks.source_authority_assessed


def test_submission_and_decision_helpers_are_fixtures() -> None:
    assert isinstance(submission(), FactSubmission)
    assert isinstance(decision(), ReviewDecision)
    assert replace(submission(), value=Decimal("1")).value == Decimal("1")
