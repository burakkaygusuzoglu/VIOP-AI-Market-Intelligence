"""The operator review workflow over the durable journal (Phase 15 Parts 2A, 2B).

Part 2A wrote this workflow against an in-memory log. Part 2B replaces the log
with :class:`~app.application.ports.fact_verification.FactVerificationStore`,
an append-only PostgreSQL journal, and keeps the rules:

* a submission is journalled as a claim, never as a fact;
* a decision is judged by the domain's review boundary and journalled with
  its result - an approval, a rejection, or a refused approval with its code -
  in the same single write, so no approval exists without its evidence;
* a submission is decided once. The identical decision repeated (a retry) is
  answered from the journal; a different one is a conflict;
* a record is published only from decisions the journal holds as
  ``APPROVED``, re-judged from the journalled submission and decision rather
  than trusted from the caller, and only when every fact comes from one
  document for one period.

## Who can call this

Nothing reachable over HTTP. The only caller composed in this build is the
local operator command, :mod:`app.operator.fact_review`, run by someone with
shell and database access on the host. A reviewer name is that operator's
**assertion**, recorded as evidence; it is not authentication, and nothing in
the journal claims otherwise.

## What an approval does not do

It makes a fact verified. It does not make the fact usable by a financial
consumer: no risk, paper, backtest or shadow composition reads the journal,
and the source-status API reports financial use as not enabled.
"""

from __future__ import annotations

from app.application.ports.fact_verification import (
    FactVerificationStore,
    JournalledDecision,
    RecordEvidence,
    VerificationConflictError,
)
from app.application.ports.system import ClockPort
from app.domain.common.identity import canonical_symbol
from app.domain.sourcing.facts import ContractSourceRecord
from app.domain.sourcing.review import (
    ApprovedFact,
    FactSubmission,
    ReviewDecision,
    ReviewRefusedError,
    ReviewResult,
    judge,
    record_from_approved,
    same_decision,
)

__all__ = ["FIELD_LIMITS", "FactVerificationService"]

FIELD_LIMITS = {
    "submission_id": 64,
    "record_id": 64,
    "symbol": 64,
    "underlying_symbol": 64,
    "contract_name": 200,
    "reference": 500,
    "submitted_by": 80,
    "reviewer": 80,
    "note": 500,
}
"""The journal's column widths (migration 0009). Checked here so an over-long
value is a typed refusal, never a database error read as an outage."""


def _bounded(**values: str | None) -> None:
    for name, value in values.items():
        if value is not None and len(value) > FIELD_LIMITS[name]:
            raise ReviewRefusedError(
                "FIELD_TOO_LONG", f"{name} is longer than {FIELD_LIMITS[name]} characters"
            )


class FactVerificationService:
    def __init__(self, store: FactVerificationStore, clock: ClockPort) -> None:
        self._store = store
        self._clock = clock

    async def submit(self, submission: FactSubmission) -> bool:
        """Journal a claim. ``False`` if the identical claim was already held."""
        if submission.symbol != canonical_symbol(submission.symbol) or not submission.symbol:
            raise ReviewRefusedError(
                "SYMBOL_NOT_CANONICAL", "a submission names its contract exactly, trimmed"
            )
        _bounded(
            submission_id=submission.submission_id,
            symbol=submission.symbol,
            reference=submission.reference,
            submitted_by=submission.submitted_by,
            record_id=submission.corrects,
        )
        for moment in (
            submission.submitted_at,
            submission.effective_from,
            submission.effective_until,
        ):
            if moment is not None and moment.utcoffset() is None:
                raise ReviewRefusedError("NAIVE_TIMESTAMP", "submission times are timezone-aware")
        if (
            submission.effective_from is not None
            and submission.effective_until is not None
            and submission.effective_until <= submission.effective_from
        ):
            raise ReviewRefusedError(
                "EFFECTIVE_PERIOD_EMPTY", "the effective period ends before it starts"
            )
        now = self._clock.now()
        if submission.submitted_at > now:
            # An asserted time may be earlier than the journal's (a claim noted
            # before it was entered) but never later: a future-dated assertion
            # would read as fresher evidence than exists. Knowledge time is the
            # journal's own clock either way.
            raise ReviewRefusedError(
                "FUTURE_DATED", "a submission cannot be dated after this system's clock"
            )
        return await self._store.append_submission(submission, recorded_at=now)

    async def decide(self, decision: ReviewDecision) -> JournalledDecision:
        """Judge and journal ``decision``. A refused approval is journalled
        too - with its code - and returned, never raised past the journal."""
        _bounded(reviewer=decision.reviewer, note=decision.note)
        if decision.decided_at.utcoffset() is None:
            raise ReviewRefusedError("NAIVE_TIMESTAMP", "a decision time is timezone-aware")
        if decision.decided_at > self._clock.now():
            raise ReviewRefusedError(
                "FUTURE_DATED", "a decision cannot be dated after this system's clock"
            )
        entry = await self._store.entry(decision.submission_id)
        if entry is None:
            raise ReviewRefusedError("UNKNOWN_SUBMISSION", "no such submission is journalled")
        if entry.decision is not None:
            if same_decision(entry.decision.decision, decision):
                return entry.decision
            raise VerificationConflictError(
                "ALREADY_DECIDED", "the submission already has a different decision"
            )
        verdict = judge(entry.submission, decision)
        journalled, _ = await self._store.append_decision(
            decision, verdict, recorded_at=self._clock.now()
        )
        return journalled

    async def _approved(self, submission_id: str) -> ApprovedFact:
        entry = await self._store.entry(submission_id)
        if entry is None or entry.decision is None:
            raise VerificationConflictError(
                "NOT_APPROVED", f"submission {submission_id} has no journalled approval"
            )
        if entry.decision.result is not ReviewResult.APPROVED:
            raise VerificationConflictError(
                "NOT_APPROVED", f"submission {submission_id} was not approved"
            )
        # Re-judged from the journal rather than trusted: the stored result and
        # today's boundary must still agree.
        verdict = judge(entry.submission, entry.decision.decision)
        if verdict.approved is None:
            raise VerificationConflictError(
                "APPROVAL_NO_LONGER_HOLDS",
                f"submission {submission_id} would not be approved by the current boundary",
            )
        return verdict.approved

    async def publish(
        self,
        record_id: str,
        *,
        underlying_symbol: str,
        contract_name: str,
        multiplier: str,
        tick_size: str,
        expiry: str | None = None,
    ) -> ContractSourceRecord:
        """Publish a record from approved, journalled facts of one document."""
        _bounded(
            record_id=record_id,
            underlying_symbol=underlying_symbol,
            contract_name=contract_name,
        )
        record = record_from_approved(
            record_id,
            underlying_symbol=underlying_symbol,
            contract_name=contract_name,
            multiplier=await self._approved(multiplier),
            tick_size=await self._approved(tick_size),
            expiry=await self._approved(expiry) if expiry is not None else None,
        )
        await self._store.publish_record(
            record,
            RecordEvidence(multiplier=multiplier, tick_size=tick_size, expiry=expiry),
            recorded_at=self._clock.now(),
        )
        return record
