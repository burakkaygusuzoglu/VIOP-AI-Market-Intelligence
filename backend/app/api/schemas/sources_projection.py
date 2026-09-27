"""Source-status domain objects to response schemas (Phase 15 Part 2B).

Values are exact decimal strings and ISO-8601 times; nothing is rounded,
converted to a float or defaulted to zero. A field this build cannot verify is
reported as such rather than omitted.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from app.api.schemas.sources import (
    CalendarStatusResponse,
    CategoryResponse,
    ChecksResponse,
    ConflictResponse,
    DecisionResponse,
    DeploymentResponse,
    FieldResponse,
    JournalCountsResponse,
    MetadataStatusResponse,
    RecordResponse,
    ReviewEntryResponse,
    ReviewPageResponse,
    SourceCapabilitiesResponse,
)
from app.application.ports.fact_verification import ReviewEntry, VerificationCounts
from app.application.sourcing.source_status import (
    CategoryState,
    MetadataStatus,
    SourceComposition,
)
from app.domain.sourcing.calendar import CalendarAnswer
from app.domain.sourcing.facts import ContractSourceRecord, SourceAuthority

__all__ = ["calendar_status", "capabilities", "metadata_status", "review_page"]

_ASSERTION: Literal["OPERATOR_ASSERTION_NOT_AUTHENTICATED"] = "OPERATOR_ASSERTION_NOT_AUTHENTICATED"


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def capabilities(
    composition: SourceComposition,
    rows: tuple[CategoryState, ...],
    counts: VerificationCounts,
    *,
    server_time: datetime,
) -> SourceCapabilitiesResponse:
    return SourceCapabilitiesResponse(
        deployment=DeploymentResponse(
            market_data_provider=composition.market_data_provider,
            real_provider_connected=False,
            simulated_market_data=composition.simulated_market_data,
            calendar_source_composed=composition.calendar_source_composed,
            verification_writes="LOCAL_OPERATOR_COMMAND_ONLY",
            reviewer_identity=_ASSERTION,
            financial_use_enabled=False,
        ),
        categories=[
            CategoryResponse(
                category=row.category.value,
                status=row.status.value,
                reason=row.reason,
                configured=row.configured,
                licensed=row.licensed,
                connected=row.connected,
                available=row.available,
                fresh=row.fresh,
                verified=row.verified,
                adapter_in_build=row.adapter_in_build,
            )
            for row in rows
        ],
        journal=JournalCountsResponse(
            submissions=counts.submissions,
            approved=counts.approved,
            rejected=counts.rejected,
            refused=counts.refused,
            records=counts.records,
        ),
        server_time=server_time.isoformat(),
    )


def _record_authority(
    authority: SourceAuthority,
) -> Literal["EXCHANGE_OFFICIAL", "LICENSED_PROVIDER"]:
    if authority is SourceAuthority.EXCHANGE_OFFICIAL:
        return "EXCHANGE_OFFICIAL"
    if authority is SourceAuthority.LICENSED_PROVIDER:
        return "LICENSED_PROVIDER"
    raise ValueError("a published record never comes from a non-authoritative source")


def _record(record: ContractSourceRecord) -> RecordResponse:
    contract = record.contract
    return RecordResponse(
        record_id=record.record_id,
        authority=_record_authority(record.authority),
        reference=record.reference,
        effective_from=record.effective_from.isoformat(),
        effective_until=_iso(record.effective_until),
        verified_at=record.verified_at.isoformat(),
        known_at=_iso(record.known_at),
        corrects=record.corrects,
        reviewed_by=record.reviewed_by,
        multiplier=_plain(contract.multiplier.value),
        tick_size=_plain(contract.tick_size.value),
        expiry_date=None
        if contract.expiry is None
        else contract.expiry.expiry_date.value.isoformat(),
    )


def metadata_status(status: MetadataStatus, *, server_time: datetime) -> MetadataStatusResponse:
    verdict = status.verdict
    checks = status.checks
    return MetadataStatusResponse(
        symbol=status.symbol,
        applies_at=status.applies_at.isoformat(),
        known_by=_iso(status.known_by),
        retrospective=status.retrospective,
        verdict=verdict.code.value,
        reason=verdict.reason,
        governing_record=None if verdict.record is None else verdict.record.record_id,
        fields=[
            FieldResponse(
                name=field.name,
                state=field.state.value,
                value=field.value,
                source=field.source,
                verified_at=_iso(field.verified_at),
            )
            for field in status.fields
        ],
        checks=ChecksResponse(
            source_claims_value=checks.source_claims_value,
            operator_examined_evidence=checks.operator_examined_evidence,
            source_authority_assessed=checks.source_authority_assessed,
            applicable_to_contract=checks.applicable_to_contract,
            applicable_at_market_time=checks.applicable_at_market_time,
            known_by_requested_time=checks.known_by_requested_time,
            current=checks.current,
            financial_use_enabled=False,
        ),
        conflicts=[
            ConflictResponse(
                fact=c.fact,
                chosen_record=c.chosen_record,
                chosen_value=c.chosen_value,
                other_record=c.other_record,
                other_value=c.other_value,
                resolution=c.resolution,
            )
            for c in status.conflicts
        ],
        superseded=list(verdict.superseded),
        records=[_record(record) for record in status.records],
        financial_use_enabled=False,
        server_time=server_time.isoformat(),
    )


def calendar_status(
    answer: CalendarAnswer, *, composed: bool, server_time: datetime
) -> CalendarStatusResponse:
    basis = answer.basis
    return CalendarStatusResponse(
        symbol=answer.symbol,
        at=answer.at.isoformat(),
        status=answer.status.value,
        reason=answer.reason,
        source=None if basis is None else basis.source,
        source_verified_at=None if basis is None else _iso(basis.as_of),
        calendar_source_composed=composed,
        server_time=server_time.isoformat(),
    )


def _entry(entry: ReviewEntry) -> ReviewEntryResponse:
    s = entry.submission
    value = s.value
    claimed = (
        None
        if value is None
        else _plain(value)
        if isinstance(value, Decimal)
        else value.isoformat()
    )
    d = entry.decision
    return ReviewEntryResponse(
        sequence=entry.sequence,
        submission_id=s.submission_id,
        symbol=s.symbol,
        fact=s.fact.value,
        claimed_value=claimed,
        reference=s.reference,
        authority=s.authority.value,
        effective_from=_iso(s.effective_from),
        effective_until=_iso(s.effective_until),
        submitted_by=s.submitted_by,
        submitted_at=s.submitted_at.isoformat(),
        origin=s.origin.value,
        corrects=s.corrects,
        recorded_at=entry.submitted_recorded_at.isoformat(),
        decision=None
        if d is None
        else DecisionResponse(
            reviewer=d.decision.reviewer,
            reviewer_identity=_ASSERTION,
            decided_at=d.decision.decided_at.isoformat(),
            outcome=d.decision.outcome.value,
            document_checked=d.decision.document_checked,
            note=d.decision.note,
            result=d.result.value,
            refusal_code=d.refusal_code,
            recorded_at=d.recorded_at.isoformat(),
        ),
    )


def review_page(
    entries: tuple[ReviewEntry, ...], total: int, *, limit: int, server_time: datetime
) -> ReviewPageResponse:
    return ReviewPageResponse(
        items=[_entry(entry) for entry in entries],
        total=total,
        next_after=entries[-1].sequence if len(entries) == limit else None,
        server_time=server_time.isoformat(),
    )
