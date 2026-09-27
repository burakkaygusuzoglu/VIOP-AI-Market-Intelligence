"""How a contract fact becomes verified: an operator review (Phase 15 Part 2A).

Contract specifications are official web pages and PDFs, not an API, so for
most facts the realistic authoritative route is a person reading the official
document and recording what it says. This module is the boundary that makes
such a recording auditable - and the list of ways it refuses to.

## The review boundary

A :class:`FactSubmission` is a *claim*: somebody says document D states that
contract X's multiplier is V from date F. It carries no trust. A
:class:`ReviewDecision` is a named reviewer's statement that they opened the
referenced document and confirmed the value, the contract, the period and who
published it. Only :func:`review` turns the pair into an :class:`ApprovedFact`,
and it refuses when:

* the submission came from a **file import** (CSV or otherwise). A spreadsheet
  is a transcription; a transcription is re-keyed data, not a document. It may
  be *reviewed against* the document by resubmitting as a manual entry that
  names the document - it never becomes verified by being uploaded;
* there is **no document reference**, or the reviewer did not attest that
  they **opened and checked** it. A reference that looks official - an
  exchange domain, a PDF name - asserts nothing by itself; authority is what
  the reviewer states, never what the URL resembles;
* the stated authority is not the **exchange or a licensed provider**;
* the **effective date** is missing - without it, applicability cannot be
  judged (:mod:`app.domain.sourcing.facts`);
* the decision predates the submission, the reviewer is unnamed, or the
  submission has no value.

A rejection is a decision too, and is kept. Nothing is ever edited: a wrong
approved fact is corrected by a new submission that names the record it
corrects, and both stay in the log.

## What an approval produces

A ``VERIFIED_CURRENT_FACT`` whose ``source`` is the document reference and
whose ``as_of`` is the review time, plus the authority and period needed to
build a :class:`~app.domain.sourcing.facts.ContractSourceRecord`. It unlocks
nothing by itself: a record still passes :func:`assess_fact_records`, and a
usable record is still only metadata - risk approval, sizing and every Paper
gate stay independent.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.verification import VerificationStatus, VerifiedValue
from app.domain.futures.contract import ContractExpiry, FuturesContract
from app.domain.sourcing.facts import ContractSourceRecord, SourceAuthority

__all__ = [
    "ApprovedFact",
    "ContractFact",
    "FactSubmission",
    "ReviewDecision",
    "ReviewOutcome",
    "ReviewRefusedError",
    "ReviewResult",
    "ReviewVerdict",
    "SubmissionOrigin",
    "record_from_approved",
    "judge",
    "review",
]


@unique
class ContractFact(StrEnum):
    MULTIPLIER = "MULTIPLIER"
    TICK_SIZE = "TICK_SIZE"
    EXPIRY_DATE = "EXPIRY_DATE"


@unique
class SubmissionOrigin(StrEnum):
    MANUAL_ENTRY = "MANUAL_ENTRY"
    """Typed by an operator reading a named document."""

    FILE_IMPORT = "FILE_IMPORT"
    """Uploaded from a CSV or other file. Never verifiable as submitted."""


@unique
class ReviewOutcome(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@unique
class ReviewResult(StrEnum):
    """What a decision *did*, as the journal records it (Part 2B)."""

    APPROVED = "APPROVED"
    """The boundary accepted an approval: the fact is verified."""

    REJECTED = "REJECTED"
    """The reviewer rejected the submission."""

    REFUSED = "REFUSED"
    """The reviewer asked for an approval the boundary does not allow."""


class ReviewRefusedError(ValueError):
    """An approval was asked for that the review boundary does not allow."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True, slots=True)
class FactSubmission:
    """A claim that a document states a fact. Carries no trust."""

    submission_id: str
    symbol: str
    fact: ContractFact
    value: Decimal | date | None
    reference: str
    authority: SourceAuthority
    effective_from: datetime | None
    submitted_by: str
    submitted_at: datetime
    origin: SubmissionOrigin
    effective_until: datetime | None = None
    corrects: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    submission_id: str
    reviewer: str
    decided_at: datetime
    outcome: ReviewOutcome
    document_checked: bool
    """The reviewer opened the referenced document and confirmed the value,
    the contract, the effective period and the publisher. Never defaulted."""

    note: str = ""


@dataclass(frozen=True, slots=True)
class ApprovedFact:
    submission: FactSubmission
    decision: ReviewDecision
    verified: VerifiedValue[Decimal | date]


def review(submission: FactSubmission, decision: ReviewDecision) -> ApprovedFact | None:
    """Apply ``decision``. ``None`` for a rejection; a refusal raises."""
    if decision.submission_id != submission.submission_id:
        raise ReviewRefusedError("WRONG_SUBMISSION", "the decision is for another submission")
    if not decision.reviewer.strip():
        raise ReviewRefusedError("REVIEWER_UNNAMED", "a review names its reviewer")
    for moment in (submission.submitted_at, decision.decided_at):
        if moment.utcoffset() is None:
            raise ReviewRefusedError("NAIVE_TIMESTAMP", "review times must be timezone-aware")
    if decision.decided_at < submission.submitted_at:
        raise ReviewRefusedError(
            "DECIDED_BEFORE_SUBMITTED", "a decision cannot predate its submission"
        )
    if decision.outcome is ReviewOutcome.REJECTED:
        return None

    if submission.origin is SubmissionOrigin.FILE_IMPORT:
        raise ReviewRefusedError(
            "FILE_IMPORT_NOT_VERIFIABLE",
            "an imported file is a transcription, not a document; it cannot be verified",
        )
    if not submission.reference.strip():
        raise ReviewRefusedError("NO_REFERENCE", "a verified fact names its document")
    if not decision.document_checked:
        raise ReviewRefusedError(
            "DOCUMENT_NOT_CHECKED",
            "the reviewer did not attest to checking the referenced document",
        )
    if submission.authority not in (
        SourceAuthority.EXCHANGE_OFFICIAL,
        SourceAuthority.LICENSED_PROVIDER,
    ):
        raise ReviewRefusedError(
            "NOT_AN_AUTHORITATIVE_SOURCE",
            "only the exchange or a licensed provider can be the source of a verified fact",
        )
    if submission.effective_from is None:
        raise ReviewRefusedError(
            "EFFECTIVE_DATE_MISSING", "without an effective date applicability is unknown"
        )
    if submission.value is None:
        raise ReviewRefusedError("VALUE_MISSING", "there is no value to verify")
    expected_type = date if submission.fact is ContractFact.EXPIRY_DATE else Decimal
    if not isinstance(submission.value, expected_type) or (
        expected_type is Decimal and isinstance(submission.value, datetime)
    ):
        raise ReviewRefusedError("VALUE_TYPE", f"{submission.fact} has the wrong value type")
    if isinstance(submission.value, Decimal) and not (
        submission.value.is_finite() and submission.value > 0
    ):
        raise ReviewRefusedError("VALUE_NOT_POSITIVE", f"{submission.fact} must be positive")

    return ApprovedFact(
        submission=submission,
        decision=decision,
        verified=VerifiedValue(
            value=submission.value,
            status=VerificationStatus.VERIFIED_CURRENT_FACT,
            source=submission.reference,
            as_of=decision.decided_at,
            note=f"submission {submission.submission_id} reviewed by {decision.reviewer}",
        ),
    )


def same_claim(first: FactSubmission, second: FactSubmission) -> bool:
    """Whether two submissions are one claim, retried (Part 2C).

    The asserted ``submitted_at`` is excluded: the operator command stamps it
    at run time, so a retry of the same command differs only there. The first
    journalled time stands; every other field must match exactly.
    """
    return replace(first, submitted_at=second.submitted_at) == second


def same_decision(first: ReviewDecision, second: ReviewDecision) -> bool:
    """Whether two decisions are one decision, retried; ``decided_at`` aside."""
    return replace(first, decided_at=second.decided_at) == second


@dataclass(frozen=True, slots=True)
class ReviewVerdict:
    result: ReviewResult
    refusal_code: str | None = None
    approved: ApprovedFact | None = None

    def __post_init__(self) -> None:
        if (self.result is ReviewResult.REFUSED) != (self.refusal_code is not None):
            raise ValueError("a refusal, and only a refusal, carries its code")
        if (self.result is ReviewResult.APPROVED) != (self.approved is not None):
            raise ValueError("an approval, and only an approval, carries the verified fact")


def judge(submission: FactSubmission, decision: ReviewDecision) -> ReviewVerdict:
    """:func:`review` as a total function: every decision has a recordable
    result, so a refused approval is journalled rather than lost."""
    try:
        approved = review(submission, decision)
    except ReviewRefusedError as refusal:
        return ReviewVerdict(ReviewResult.REFUSED, refusal_code=refusal.code)
    if approved is None:
        return ReviewVerdict(ReviewResult.REJECTED)
    return ReviewVerdict(ReviewResult.APPROVED, approved=approved)


def _decimal(fact: ApprovedFact, expected: ContractFact) -> VerifiedValue[Decimal]:
    value = fact.verified.value
    if fact.submission.fact is not expected or not isinstance(value, Decimal):
        raise ReviewRefusedError("WRONG_FACT", f"expected an approved {expected}")
    v = fact.verified
    return VerifiedValue(value=value, status=v.status, source=v.source, as_of=v.as_of, note=v.note)


def record_from_approved(
    record_id: str,
    *,
    underlying_symbol: str,
    contract_name: str,
    multiplier: ApprovedFact,
    tick_size: ApprovedFact,
    expiry: ApprovedFact | None = None,
) -> ContractSourceRecord:
    """A source record from approved facts that all come from one document.

    Facts approved from different documents, for different contracts or for
    different periods are not assembled into one record: that would be a
    contract built from parts, which no single source states.
    """
    parts = [multiplier, tick_size] + ([expiry] if expiry is not None else [])
    first = parts[0].submission
    for part in parts:
        s = part.submission
        if (s.symbol, s.reference, s.authority, s.effective_from, s.effective_until) != (
            first.symbol,
            first.reference,
            first.authority,
            first.effective_from,
            first.effective_until,
        ):
            raise ReviewRefusedError(
                "MIXED_SOURCES",
                "a record is assembled only from facts one document states for one period",
            )
    corrections = {part.submission.corrects for part in parts}
    if len(corrections) != 1:
        raise ReviewRefusedError("MIXED_CORRECTIONS", "the facts correct different records")
    assert first.effective_from is not None  # noqa: S101 - review() refuses otherwise

    contract_expiry = None
    if expiry is not None:
        day = expiry.verified.value
        if expiry.submission.fact is not ContractFact.EXPIRY_DATE or not isinstance(day, date):
            raise ReviewRefusedError("WRONG_FACT", "expected an approved expiry date")
        e = expiry.verified
        contract_expiry = ContractExpiry(
            expiry_date=VerifiedValue(
                value=day, status=e.status, source=e.source, as_of=e.as_of, note=e.note
            )
        )
    return ContractSourceRecord(
        record_id=record_id,
        contract=FuturesContract(
            symbol=first.symbol,
            underlying_symbol=underlying_symbol,
            contract_name=contract_name,
            multiplier=_decimal(multiplier, ContractFact.MULTIPLIER),
            tick_size=_decimal(tick_size, ContractFact.TICK_SIZE),
            expiry=contract_expiry,
        ),
        authority=first.authority,
        reference=first.reference,
        effective_from=first.effective_from,
        effective_until=first.effective_until,
        verified_at=min(part.decision.decided_at for part in parts),
        corrects=first.corrects,
        reviewed_by=", ".join(sorted({part.decision.reviewer for part in parts})),
    )
