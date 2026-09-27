"""PostgreSQL contract-fact verification journal (Phase 15 Part 2B).

Every write is one ``INSERT ... ON CONFLICT DO NOTHING`` in its own
transaction. When the identity is already taken, the held row is read back and
compared: the identical entry is an idempotent repeat (a retry, a reconnect, a
second operator running the same command), anything else is a
:class:`VerificationConflictError`. Two concurrent, conflicting decisions for
one submission therefore produce exactly one row, and the loser is told so.

Integrity refusals from PostgreSQL - a decision for a submission that does not
exist, a record citing a decision that is not ``APPROVED``, a correction of an
unknown record - become typed conflicts named by their constraint, never a
driver message. Any other database failure becomes
:class:`VerificationStoreUnavailableError` with a fixed sentence.

Decimals go to a ``NUMERIC`` column without precision and come back as the same
``Decimal``; nothing passes through a float.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError

from app.adapters.persistence.database import Database
from app.adapters.persistence.fact_models import (
    ContractFactRecordRow,
    FactReviewDecisionRow,
    FactSubmissionRow,
)
from app.application.ports.fact_verification import (
    JournalledDecision,
    RecordEvidence,
    ReviewEntry,
    VerificationConflictError,
    VerificationCounts,
    VerificationStoreUnavailableError,
)
from app.domain.common.identity import canonical_symbol
from app.domain.sourcing.facts import ContractSourceRecord, SourceAuthority
from app.domain.sourcing.review import (
    ApprovedFact,
    ContractFact,
    FactSubmission,
    ReviewDecision,
    ReviewOutcome,
    ReviewResult,
    ReviewVerdict,
    SubmissionOrigin,
    judge,
    record_from_approved,
    same_claim,
    same_decision,
)

__all__ = ["SqlAlchemyFactVerificationStore"]

_CONSTRAINT_CODES = {
    "fk_fact_review_decisions_submission_id_fact_submissions": "UNKNOWN_SUBMISSION",
    "fk_record_multiplier_approved": "EVIDENCE_NOT_APPROVED",
    "fk_record_tick_size_approved": "EVIDENCE_NOT_APPROVED",
    "fk_record_expiry_approved": "EVIDENCE_NOT_APPROVED",
    "fk_contract_fact_records_corrects_contract_fact_records": "UNKNOWN_CORRECTED_RECORD",
    "fk_fact_submissions_corrects_contract_fact_records": "UNKNOWN_CORRECTED_RECORD",
}


_CHECK_CODES = {
    "submission_period_not_empty": "EFFECTIVE_PERIOD_EMPTY",
    "record_period_not_empty": "EFFECTIVE_PERIOD_EMPTY",
    "submission_symbol_canonical": "SYMBOL_NOT_CANONICAL",
    "record_symbol_canonical": "SYMBOL_NOT_CANONICAL",
    "record_known_after_verified": "VERIFIED_AFTER_KNOWN",
    "decision_approval_attested": "APPROVAL_NOT_ATTESTED",
    "decision_refusal_has_code": "REFUSAL_WITHOUT_CODE",
}
"""Check constraints, matched by name inside the database's (convention-
prefixed) constraint name. Each is a refusal of the entry, not an outage."""


def _code_of(error: IntegrityError) -> str:
    name = _constraint_of(error) or ""
    if name in _CONSTRAINT_CODES:
        return _CONSTRAINT_CODES[name]
    return next((code for key, code in _CHECK_CODES.items() if key in name), "INTEGRITY_REFUSED")


def _constraint_of(error: IntegrityError) -> str | None:
    diag = getattr(getattr(error, "orig", None), "diag", None)
    name = getattr(diag, "constraint_name", None)
    return name if isinstance(name, str) else None


@asynccontextmanager
async def _journal() -> AsyncIterator[None]:
    try:
        yield
    except IntegrityError as error:
        raise VerificationConflictError(
            _code_of(error), "the verification journal refused the entry"
        ) from None
    except DataError:
        # A value the column cannot hold - too long, out of range. The entry is
        # refused; the journal is not "unreachable".
        raise VerificationConflictError(
            "VALUE_OUT_OF_BOUNDS", "the verification journal refused a value it cannot hold"
        ) from None
    except SQLAlchemyError as error:
        raise VerificationStoreUnavailableError(
            "the verification journal is unreachable"
        ) from error


class SqlAlchemyFactVerificationStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    # ------------------------------------------------------------------
    # Writes

    async def append_submission(self, submission: FactSubmission, *, recorded_at: datetime) -> bool:
        values = _submission_row(submission) | {"recorded_at": recorded_at}
        statement = (
            insert(FactSubmissionRow)
            .values(values)
            .on_conflict_do_nothing(index_elements=["submission_id"])
            .returning(FactSubmissionRow.submission_id)
        )
        async with _journal(), self._database.session() as session:
            written = (await session.execute(statement)).scalar_one_or_none()
            await session.commit()
            if written is not None:
                return True
            held = await session.get(FactSubmissionRow, submission.submission_id)
        if held is None or not same_claim(_to_submission(held), submission):
            raise VerificationConflictError(
                "SUBMISSION_ID_TAKEN", "the submission id is held by a different claim"
            )
        return False

    async def append_decision(
        self, decision: ReviewDecision, verdict: ReviewVerdict, *, recorded_at: datetime
    ) -> tuple[JournalledDecision, bool]:
        values = {
            "submission_id": decision.submission_id,
            "reviewer": decision.reviewer,
            "decided_at": decision.decided_at,
            "outcome": decision.outcome.value,
            "document_checked": decision.document_checked,
            "note": decision.note,
            "result": verdict.result.value,
            "refusal_code": verdict.refusal_code,
            "recorded_at": recorded_at,
        }
        statement = (
            insert(FactReviewDecisionRow)
            .values(values)
            .on_conflict_do_nothing(index_elements=["submission_id"])
            .returning(FactReviewDecisionRow.submission_id)
        )
        async with _journal(), self._database.session() as session:
            written = (await session.execute(statement)).scalar_one_or_none()
            await session.commit()
            held = await session.get(FactReviewDecisionRow, decision.submission_id)
        if held is None:  # pragma: no cover - a committed row cannot vanish
            raise VerificationStoreUnavailableError("the verification journal is unreachable")
        journalled = _to_decision(held)
        if written is not None:
            return journalled, True
        if (
            not same_decision(journalled.decision, decision)
            or journalled.result is not verdict.result
        ):
            raise VerificationConflictError(
                "ALREADY_DECIDED", "the submission already has a different decision"
            )
        return journalled, False

    async def publish_record(
        self, record: ContractSourceRecord, evidence: RecordEvidence, *, recorded_at: datetime
    ) -> bool:
        contract = record.contract
        values = {
            "record_id": record.record_id,
            "symbol": contract.symbol,
            "underlying_symbol": contract.underlying_symbol,
            "contract_name": contract.contract_name,
            "authority": record.authority.value,
            "reference": record.reference,
            "effective_from": record.effective_from,
            "effective_until": record.effective_until,
            "verified_at": record.verified_at,
            "corrects": record.corrects,
            "reviewed_by": record.reviewed_by or "",
            "multiplier_submission": evidence.multiplier,
            "multiplier_result": ReviewResult.APPROVED.value,
            "tick_size_submission": evidence.tick_size,
            "tick_size_result": ReviewResult.APPROVED.value,
            "expiry_submission": evidence.expiry,
            "expiry_result": None if evidence.expiry is None else ReviewResult.APPROVED.value,
            "recorded_at": recorded_at,
        }
        statement = (
            insert(ContractFactRecordRow)
            .values(values)
            .on_conflict_do_nothing(index_elements=["record_id"])
            .returning(ContractFactRecordRow.record_id)
        )
        async with _journal(), self._database.session() as session:
            written = (await session.execute(statement)).scalar_one_or_none()
            await session.commit()
            if written is not None:
                return True
            held = await session.get(ContractFactRecordRow, record.record_id)
        comparable = {k: v for k, v in values.items() if k != "recorded_at"}
        if held is None or any(getattr(held, k) != v for k, v in comparable.items()):
            raise VerificationConflictError(
                "RECORD_ID_TAKEN", "the record id is held by a different record"
            )
        return False

    # ------------------------------------------------------------------
    # Reads

    async def entry(self, submission_id: str) -> ReviewEntry | None:
        statement = (
            select(FactSubmissionRow, FactReviewDecisionRow)
            .outerjoin(
                FactReviewDecisionRow,
                FactReviewDecisionRow.submission_id == FactSubmissionRow.submission_id,
            )
            .where(FactSubmissionRow.submission_id == submission_id)
        )
        async with _journal(), self._database.session() as session:
            row = (await session.execute(statement)).first()
        return None if row is None else _to_entry(row[0], row[1])

    async def review_page(self, *, after: int, limit: int) -> tuple[tuple[ReviewEntry, ...], int]:
        statement = (
            select(FactSubmissionRow, FactReviewDecisionRow)
            .outerjoin(
                FactReviewDecisionRow,
                FactReviewDecisionRow.submission_id == FactSubmissionRow.submission_id,
            )
            .where(FactSubmissionRow.sequence > after)
            .order_by(FactSubmissionRow.sequence)
            .limit(limit)
        )
        async with _journal(), self._database.session() as session:
            total = await session.scalar(select(func.count()).select_from(FactSubmissionRow))
            rows = (await session.execute(statement)).all()
        return tuple(_to_entry(s, d) for s, d in rows), int(total or 0)

    async def records_for(self, symbol: str, *, limit: int) -> tuple[ContractSourceRecord, ...]:
        """One query for the records and one for all their evidence - no N+1."""
        async with _journal(), self._database.session() as session:
            records = (
                (
                    await session.execute(
                        select(ContractFactRecordRow)
                        .where(ContractFactRecordRow.symbol == canonical_symbol(symbol))
                        .order_by(ContractFactRecordRow.sequence)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            ids = {
                sid
                for row in records
                for sid in (
                    row.multiplier_submission,
                    row.tick_size_submission,
                    row.expiry_submission,
                )
                if sid is not None
            }
            evidence: dict[str, tuple[FactSubmissionRow, FactReviewDecisionRow]] = {}
            if ids:
                for s, d in (
                    await session.execute(
                        select(FactSubmissionRow, FactReviewDecisionRow)
                        .join(
                            FactReviewDecisionRow,
                            FactReviewDecisionRow.submission_id == FactSubmissionRow.submission_id,
                        )
                        .where(FactSubmissionRow.submission_id.in_(ids))
                    )
                ).all():
                    evidence[s.submission_id] = (s, d)
        return tuple(_to_record(row, evidence) for row in records)

    async def counts(self) -> VerificationCounts:
        async with _journal(), self._database.session() as session:
            submissions = await session.scalar(select(func.count()).select_from(FactSubmissionRow))
            by_result = dict(
                (
                    await session.execute(
                        select(FactReviewDecisionRow.result, func.count()).group_by(
                            FactReviewDecisionRow.result
                        )
                    )
                )
                .tuples()
                .all()
            )
            records = await session.scalar(select(func.count()).select_from(ContractFactRecordRow))
        return VerificationCounts(
            submissions=int(submissions or 0),
            approved=int(by_result.get("APPROVED", 0)),
            rejected=int(by_result.get("REJECTED", 0)),
            refused=int(by_result.get("REFUSED", 0)),
            records=int(records or 0),
        )


# ----------------------------------------------------------------------
# Mapping


def _submission_row(submission: FactSubmission) -> dict[str, Any]:
    value = submission.value
    return {
        "submission_id": submission.submission_id,
        "symbol": submission.symbol,
        "fact": submission.fact.value,
        "value_decimal": value if isinstance(value, Decimal) else None,
        "value_date": None if isinstance(value, Decimal) else value,
        "reference": submission.reference,
        "authority": submission.authority.value,
        "effective_from": submission.effective_from,
        "effective_until": submission.effective_until,
        "submitted_by": submission.submitted_by,
        "submitted_at": submission.submitted_at,
        "origin": submission.origin.value,
        "corrects": submission.corrects,
    }


def _to_submission(row: FactSubmissionRow) -> FactSubmission:
    return FactSubmission(
        submission_id=row.submission_id,
        symbol=row.symbol,
        fact=ContractFact(row.fact),
        value=row.value_decimal if row.value_decimal is not None else row.value_date,
        reference=row.reference,
        authority=SourceAuthority(row.authority),
        effective_from=row.effective_from,
        submitted_by=row.submitted_by,
        submitted_at=row.submitted_at,
        origin=SubmissionOrigin(row.origin),
        effective_until=row.effective_until,
        corrects=row.corrects,
    )


def _to_decision(row: FactReviewDecisionRow) -> JournalledDecision:
    return JournalledDecision(
        decision=ReviewDecision(
            submission_id=row.submission_id,
            reviewer=row.reviewer,
            decided_at=row.decided_at,
            outcome=ReviewOutcome(row.outcome),
            document_checked=row.document_checked,
            note=row.note,
        ),
        result=ReviewResult(row.result),
        refusal_code=row.refusal_code,
        recorded_at=row.recorded_at,
    )


def _to_entry(submission: FactSubmissionRow, decision: FactReviewDecisionRow | None) -> ReviewEntry:
    return ReviewEntry(
        sequence=submission.sequence,
        submission=_to_submission(submission),
        submitted_recorded_at=submission.recorded_at,
        decision=None if decision is None else _to_decision(decision),
    )


def _approved(
    submission_id: str, evidence: dict[str, tuple[FactSubmissionRow, FactReviewDecisionRow]]
) -> ApprovedFact:
    """The fact a record cites, re-judged by the domain review boundary from
    the journalled claim and decision. The adapter never assigns a
    verification status itself: if the boundary would not approve what the
    journal holds, the record is refused rather than read."""
    s, d = evidence[submission_id]
    verdict = judge(_to_submission(s), _to_decision(d).decision)
    if verdict.approved is None:
        raise VerificationConflictError(
            "APPROVAL_NO_LONGER_HOLDS", "a record cites a decision the boundary would not approve"
        )
    return verdict.approved


def _to_record(
    row: ContractFactRecordRow,
    evidence: dict[str, tuple[FactSubmissionRow, FactReviewDecisionRow]],
) -> ContractSourceRecord:
    record = record_from_approved(
        row.record_id,
        underlying_symbol=row.underlying_symbol,
        contract_name=row.contract_name,
        multiplier=_approved(row.multiplier_submission, evidence),
        tick_size=_approved(row.tick_size_submission, evidence),
        expiry=(
            None if row.expiry_submission is None else _approved(row.expiry_submission, evidence)
        ),
    )
    return replace(record, known_at=row.recorded_at)
