"""Contract facts bound to the document that states them (Phase 15 Part 2A).

## Why Part 1's choice was not enough

Part 1 chose between disagreeing sources by *verification time* - when
someone last checked. That is the wrong clock. A value checked yesterday from
a specification that stopped applying last month is recent and obsolete; a
value checked three weeks ago from the specification in force today is older
and correct. Section 118 asks for "the more recent **applicable** official
source", and applicability is a property of the document - the period it says
it governs - not of the moment somebody read it.

So a fact now travels in a :class:`ContractSourceRecord` that says:

* **which contract** it describes - exactly, never inferred from a symbol;
* **what kind of source** stated it (:class:`SourceAuthority`) - the exchange's
  own publication or a licensed provider; anything else cannot be relied on;
* **where** - a document reference a person can open and check;
* **the period it governs** - ``effective_from`` (required) and
  ``effective_until`` (exclusive, open-ended when absent);
* **when it was verified**, and by the review that approved it;
* **what it corrects** - a record that supersedes an earlier one names it.

## How a record is chosen

:func:`assess_fact_records` answers "what is the multiplier/tick size of
contract X *as of* ``applies_at``", evaluated at ``now``:

1. Records for another contract are refused outright.
2. Only records whose facts are verified current facts from an official or
   licensed source with a reference survive.
3. A record corrected by another surviving record is set aside, and the
   correction is carried in the verdict.
4. Only records whose effective period contains ``applies_at`` survive -
   however recently an inapplicable one was checked.
5. A surviving record whose verification is older than ``max_age`` at ``now``
   is stale: it may be right, but nobody has confirmed it recently enough.
6. Among what remains, the record with the latest ``effective_from`` wins -
   the newer governing document. The exchange outranks a licensed provider:
   a provider record that disagrees with an official one is never allowed to
   win, and when it claims a newer period than the exchange the disagreement
   is refused rather than resolved. Records with the same period and rank
   that disagree are refused. Every disagreement that was resolved is kept.

Verification time never decides between two records. It only decides
staleness.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.identity import same_instrument
from app.domain.futures.contract import ContractState, FuturesContract
from app.domain.futures.validation import contract_state

__all__ = [
    "ContractSourceRecord",
    "FactConflict",
    "FactVerdict",
    "FactVerdictCode",
    "SourceAuthority",
    "assess_fact_records",
]


@unique
class SourceAuthority(StrEnum):
    """Who published the document a record was taken from."""

    EXCHANGE_OFFICIAL = "EXCHANGE_OFFICIAL"
    """The exchange's own specification, notice or announcement."""

    LICENSED_PROVIDER = "LICENSED_PROVIDER"
    """A provider licensed to redistribute the exchange's reference data."""

    SECONDARY = "SECONDARY"
    """A broker page, an article, a spreadsheet. Useful to a person; never
    authoritative here."""

    UNKNOWN = "UNKNOWN"


_RANK = {SourceAuthority.EXCHANGE_OFFICIAL: 2, SourceAuthority.LICENSED_PROVIDER: 1}


@dataclass(frozen=True, slots=True)
class ContractSourceRecord:
    """One document's statement of a contract's facts, and its period."""

    record_id: str
    contract: FuturesContract
    authority: SourceAuthority
    reference: str
    """Document reference: a URL with section, a notice number, a file hash."""

    effective_from: datetime
    verified_at: datetime
    effective_until: datetime | None = None
    """Exclusive end of the governed period. ``None`` means none was stated."""

    corrects: str | None = None
    """``record_id`` of an earlier record this one corrects."""

    reviewed_by: str | None = None

    known_at: datetime | None = None
    """When this system first held the record (Part 2B): the audit time it
    was published to the verification journal. Distinct from the period it
    governs - a record may govern last March and be known only since today."""
    """The review that approved the record, for the audit trail."""

    def __post_init__(self) -> None:
        if not self.record_id.strip():
            raise ValueError("a source record has an identity")
        for name in ("effective_from", "verified_at", "effective_until", "known_at"):
            moment = getattr(self, name)
            if moment is not None and moment.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError("a record's effective period must not be empty")
        if self.corrects == self.record_id:
            raise ValueError("a record cannot correct itself")

    def applies_at(self, moment: datetime) -> bool:
        return self.effective_from <= moment and (
            self.effective_until is None or moment < self.effective_until
        )

    @property
    def is_authoritative(self) -> bool:
        facts = (self.contract.multiplier, self.contract.tick_size)
        return (
            self.authority in _RANK
            and bool(self.reference.strip())
            and all(fact.is_authoritative and fact.source.strip() for fact in facts)
        )


@unique
class FactVerdictCode(StrEnum):
    USABLE = "USABLE"
    MISSING = "MISSING"
    WRONG_CONTRACT = "WRONG_CONTRACT"
    NOT_AUTHORITATIVE = "NOT_AUTHORITATIVE"
    NOT_IN_EFFECT = "NOT_IN_EFFECT"
    """Authoritative records exist, but none governs the requested moment."""
    NOT_YET_KNOWN = "NOT_YET_KNOWN"
    """Records exist, but none had reached this system by ``known_by``."""
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    CONFLICTING = "CONFLICTING"


@dataclass(frozen=True, slots=True)
class FactConflict:
    """A disagreement that was resolved, and how - kept for the audit trail."""

    fact: str
    chosen_record: str
    chosen_value: str
    other_record: str
    other_value: str
    resolution: str


@dataclass(frozen=True, slots=True)
class FactVerdict:
    code: FactVerdictCode
    reason: str
    record: ContractSourceRecord | None = None
    conflicts: tuple[FactConflict, ...] = ()
    superseded: tuple[str, ...] = ()
    """Records set aside because a surviving record corrects them."""

    @property
    def usable(self) -> bool:
        return self.code is FactVerdictCode.USABLE

    @property
    def contract(self) -> FuturesContract | None:
        return self.record.contract if self.record is not None else None

    def __post_init__(self) -> None:
        if self.usable != (self.record is not None):
            raise ValueError("a usable verdict carries a record, and only a usable one")


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _facts(contract: FuturesContract) -> dict[str, str]:
    facts = {
        "multiplier": _plain(contract.multiplier.value),
        "tick_size": _plain(contract.tick_size.value),
    }
    if contract.expiry is not None and contract.expiry.expiry_date.is_authoritative:
        facts["expiry_date"] = contract.expiry.expiry_date.value.isoformat()
    return facts


def _refuse(
    code: FactVerdictCode,
    reason: str,
    *,
    superseded: tuple[str, ...] = (),
    conflicts: tuple[FactConflict, ...] = (),
) -> FactVerdict:
    return FactVerdict(code=code, reason=reason, superseded=superseded, conflicts=conflicts)


def _unresolved(
    fact: str,
    first: ContractSourceRecord,
    first_value: str,
    second: ContractSourceRecord,
    second_value: str,
) -> FactConflict:
    """A disagreement kept on a refusal. ``chosen_*`` is only the first side:
    nothing was chosen."""
    return FactConflict(
        fact=fact,
        chosen_record=first.record_id,
        chosen_value=first_value,
        other_record=second.record_id,
        other_value=second_value,
        resolution="UNRESOLVED: refused, no value chosen",
    )


def assess_fact_records(
    requested: str,
    records: tuple[ContractSourceRecord, ...],
    *,
    applies_at: datetime,
    now: datetime,
    max_age: timedelta,
    known_by: datetime | None = None,
) -> FactVerdict:
    """The record that governs ``requested`` at ``applies_at``, or a refusal.

    ``known_by`` (Part 2B) answers as the system could have at that moment:
    records it did not yet hold - including a later correction - do not
    exist, and staleness is judged then rather than now. ``None`` is the
    retrospective question, "what do we know today about that period", and a
    caller asking it must label the answer as retrospective.
    """
    if not records:
        return _refuse(FactVerdictCode.MISSING, f"no source record describes {requested}")
    for record in records:
        if not same_instrument(record.contract.symbol, requested):
            return _refuse(
                FactVerdictCode.WRONG_CONTRACT,
                f"record {record.record_id} describes {record.contract.symbol}, not {requested}",
            )
    if len({record.record_id for record in records}) != len(records):
        return _refuse(FactVerdictCode.CONFLICTING, "two records share one identity")

    judged_at = now
    if known_by is not None:
        judged_at = min(known_by, now)
        records = tuple(r for r in records if r.known_at is not None and r.known_at <= judged_at)
        if not records:
            return _refuse(
                FactVerdictCode.NOT_YET_KNOWN,
                f"no record for {requested} had reached this system by the requested moment",
            )

    authoritative = [record for record in records if record.is_authoritative]
    if not authoritative:
        return _refuse(
            FactVerdictCode.NOT_AUTHORITATIVE,
            "no record is a referenced, verified statement from the exchange or a "
            "licensed provider",
        )

    corrected = {record.corrects for record in authoritative if record.corrects is not None}
    superseded = tuple(sorted(r.record_id for r in authoritative if r.record_id in corrected))
    live = [record for record in authoritative if record.record_id not in corrected]

    applicable = [record for record in live if record.applies_at(applies_at)]
    if not applicable:
        return _refuse(
            FactVerdictCode.NOT_IN_EFFECT,
            f"no authoritative record governs {requested} at the requested moment",
            superseded=superseded,
        )

    fresh = [record for record in applicable if judged_at - record.verified_at <= max_age]
    if not fresh:
        return _refuse(
            FactVerdictCode.STALE,
            "every applicable record was verified longer ago than the allowed age",
            superseded=superseded,
        )

    for record in fresh:
        if contract_state(record.contract, applies_at).state is ContractState.EXPIRED:
            return _refuse(
                FactVerdictCode.EXPIRED,
                f"{requested} had expired at the requested moment according to "
                f"record {record.record_id}",
                superseded=superseded,
            )

    fresh.sort(key=lambda r: (_RANK[r.authority], r.effective_from), reverse=True)
    chosen = fresh[0]
    chosen_facts = _facts(chosen.contract)
    conflicts: list[FactConflict] = []
    for other in fresh[1:]:
        other_facts = _facts(other.contract)
        for fact, value in chosen_facts.items():
            if fact not in other_facts or other_facts[fact] == value:
                continue
            if _RANK[other.authority] == _RANK[chosen.authority]:
                if other.effective_from == chosen.effective_from:
                    return _refuse(
                        FactVerdictCode.CONFLICTING,
                        f"records {chosen.record_id} and {other.record_id} disagree on {fact} "
                        "for the same period; nothing says which governs",
                        superseded=superseded,
                        conflicts=(_unresolved(fact, chosen, value, other, other_facts[fact]),),
                    )
                resolution = "the record with the later effective period governs"
            elif other.effective_from > chosen.effective_from:
                return _refuse(
                    FactVerdictCode.CONFLICTING,
                    f"record {other.record_id} from a provider claims a newer period than "
                    f"exchange record {chosen.record_id} and disagrees on {fact}",
                    superseded=superseded,
                    conflicts=(_unresolved(fact, chosen, value, other, other_facts[fact]),),
                )
            else:
                resolution = "the exchange's record outranks a provider's"
            conflicts.append(
                FactConflict(
                    fact=fact,
                    chosen_record=chosen.record_id,
                    chosen_value=value,
                    other_record=other.record_id,
                    other_value=other_facts[fact],
                    resolution=resolution,
                )
            )
    reason = f"governed by record {chosen.record_id} ({chosen.authority.value})"
    if conflicts:
        reason += f"; {len(conflicts)} disagreement(s) recorded"
    return FactVerdict(
        code=FactVerdictCode.USABLE,
        reason=reason,
        record=chosen,
        conflicts=tuple(conflicts),
        superseded=superseded,
    )
