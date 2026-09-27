"""An in-memory ``FactVerificationStore`` for unit tests (Phase 15 Part 2B).

A test double that keeps the port's contract - append-only, idempotent on an
identical repeat, conflicting on different content, records only from
``APPROVED`` decisions - so application tests run without a database. The
PostgreSQL store is exercised against the same contract in
``tests/integration/test_fact_verification_persistence.py``; where the two
could differ, that suite is the authority.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from app.application.ports.fact_verification import (
    JournalledDecision,
    RecordEvidence,
    ReviewEntry,
    VerificationConflictError,
    VerificationCounts,
)
from app.domain.common.identity import canonical_symbol
from app.domain.sourcing.facts import ContractSourceRecord
from app.domain.sourcing.review import (
    FactSubmission,
    ReviewDecision,
    ReviewResult,
    ReviewVerdict,
    same_claim,
    same_decision,
)


class MemoryVerificationStore:
    def __init__(self) -> None:
        self.submissions: dict[str, tuple[int, FactSubmission, datetime]] = {}
        self.decisions: dict[str, JournalledDecision] = {}
        self.records: dict[str, tuple[int, ContractSourceRecord, RecordEvidence]] = {}
        self.writes = 0

    async def append_submission(self, submission: FactSubmission, *, recorded_at: datetime) -> bool:
        held = self.submissions.get(submission.submission_id)
        if held is not None:
            if not same_claim(held[1], submission):
                raise VerificationConflictError("SUBMISSION_ID_TAKEN", "taken")
            return False
        if submission.corrects is not None and submission.corrects not in self.records:
            raise VerificationConflictError("UNKNOWN_CORRECTED_RECORD", "unknown")
        self.writes += 1
        self.submissions[submission.submission_id] = (
            len(self.submissions) + 1,
            submission,
            recorded_at,
        )
        return True

    async def append_decision(
        self, decision: ReviewDecision, verdict: ReviewVerdict, *, recorded_at: datetime
    ) -> tuple[JournalledDecision, bool]:
        if decision.submission_id not in self.submissions:
            raise VerificationConflictError("UNKNOWN_SUBMISSION", "unknown")
        held = self.decisions.get(decision.submission_id)
        if held is not None:
            if not same_decision(held.decision, decision) or held.result is not verdict.result:
                raise VerificationConflictError("ALREADY_DECIDED", "decided")
            return held, False
        self.writes += 1
        journalled = JournalledDecision(decision, verdict.result, verdict.refusal_code, recorded_at)
        self.decisions[decision.submission_id] = journalled
        return journalled, True

    async def entry(self, submission_id: str) -> ReviewEntry | None:
        held = self.submissions.get(submission_id)
        if held is None:
            return None
        return ReviewEntry(held[0], held[1], held[2], self.decisions.get(submission_id))

    async def publish_record(
        self, record: ContractSourceRecord, evidence: RecordEvidence, *, recorded_at: datetime
    ) -> bool:
        for sid in (evidence.multiplier, evidence.tick_size, evidence.expiry):
            if sid is None:
                continue
            decision = self.decisions.get(sid)
            if decision is None or decision.result is not ReviewResult.APPROVED:
                raise VerificationConflictError("EVIDENCE_NOT_APPROVED", "not approved")
        if record.corrects is not None and record.corrects not in self.records:
            raise VerificationConflictError("UNKNOWN_CORRECTED_RECORD", "unknown")
        held = self.records.get(record.record_id)
        stored = replace(record, known_at=recorded_at)
        if held is not None:
            if replace(held[1], known_at=None) != replace(record, known_at=None):
                raise VerificationConflictError("RECORD_ID_TAKEN", "taken")
            return False
        self.writes += 1
        self.records[record.record_id] = (len(self.records) + 1, stored, evidence)
        return True

    async def records_for(self, symbol: str, *, limit: int) -> tuple[ContractSourceRecord, ...]:
        rows = sorted(self.records.values(), key=lambda item: item[0])
        return tuple(
            record for _, record, _ in rows if record.contract.symbol == canonical_symbol(symbol)
        )[:limit]

    async def review_page(self, *, after: int, limit: int) -> tuple[tuple[ReviewEntry, ...], int]:
        entries = [
            ReviewEntry(seq, sub, at, self.decisions.get(sid))
            for sid, (seq, sub, at) in self.submissions.items()
            if seq > after
        ]
        entries.sort(key=lambda entry: entry.sequence)
        return tuple(entries[:limit]), len(self.submissions)

    async def counts(self) -> VerificationCounts:
        results = [d.result for d in self.decisions.values()]
        return VerificationCounts(
            submissions=len(self.submissions),
            approved=results.count(ReviewResult.APPROVED),
            rejected=results.count(ReviewResult.REJECTED),
            refused=results.count(ReviewResult.REFUSED),
            records=len(self.records),
        )
