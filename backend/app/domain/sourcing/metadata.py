"""Choosing contract metadata among sources, or refusing to (Phase 15 Part 1).

Master spec section 118 says a mutable contract fact must come from an
authoritative source, be marked ``UNVERIFIED`` when it cannot, and - when two
authoritative sources conflict - "record the conflict, prefer the more recent
applicable official source, do not silently choose a value, preserve
auditability". Once more than one metadata provider exists, somebody has to
apply that rule; this module is where it is applied, once, without naming a
vendor.

## What can make a record unusable

``MISSING``          no source has anything for the contract
``WRONG_CONTRACT``   a source answered with a record for another instrument
``NOT_AUTHORITATIVE`` the multiplier or tick size is not a verified current fact
``EXPIRED``          the contract's verified expiry has passed
``STALE``            the newest verification of a required fact is older than allowed
``CONFLICTING``      authoritative sources disagree

A usable answer names the source it came from. A refused disagreement carries
the conflicting values, so the refusal is auditable.

## Part 2A correction

Part 1 resolved a disagreement by choosing the most recently *verified*
source. The Part 2A audit found that wrong: verification time is when somebody
looked, not which document governs, so a recently checked obsolete value could
defeat an older but still applicable official one. A plain ``FuturesContract``
has no effective period, so this module can no longer resolve a disagreement
at all - it refuses it. Resolution by applicable period and source authority
lives in :mod:`app.domain.sourcing.facts`.

## What this never does

It never builds a contract from parts of several records, never infers a fact
from the symbol, never upgrades a status, and never treats a price feed as a
metadata source. A record that is not usable is not returned at all, so the
existing ``METADATA_UNAVAILABLE`` path downstream is what a caller sees.
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
    "MetadataCandidate",
    "MetadataConflict",
    "MetadataVerdict",
    "MetadataVerdictCode",
    "assess_contract_metadata",
]


@unique
class MetadataVerdictCode(StrEnum):
    USABLE = "USABLE"
    MISSING = "MISSING"
    WRONG_CONTRACT = "WRONG_CONTRACT"
    NOT_AUTHORITATIVE = "NOT_AUTHORITATIVE"
    EXPIRED = "EXPIRED"
    STALE = "STALE"
    CONFLICTING = "CONFLICTING"


@dataclass(frozen=True, slots=True)
class MetadataCandidate:
    """One source's answer: which source, and the record it gave."""

    source_id: str
    contract: FuturesContract


@dataclass(frozen=True, slots=True)
class MetadataConflict:
    """Two authoritative sources that disagreed, kept for the audit trail.
    ``chosen_*`` is simply the first side; since Part 2A nothing is chosen."""

    fact: str
    chosen_source: str
    chosen_value: str
    other_source: str
    other_value: str


@dataclass(frozen=True, slots=True)
class MetadataVerdict:
    code: MetadataVerdictCode
    reason: str
    contract: FuturesContract | None = None
    source_id: str | None = None
    conflicts: tuple[MetadataConflict, ...] = ()

    @property
    def usable(self) -> bool:
        return self.code is MetadataVerdictCode.USABLE

    def __post_init__(self) -> None:
        if self.usable != (self.contract is not None):
            raise ValueError("a usable verdict carries a contract, and only a usable one")


def _refuse(code: MetadataVerdictCode, reason: str) -> MetadataVerdict:
    return MetadataVerdict(code=code, reason=reason)


def _verified_at(contract: FuturesContract) -> datetime | None:
    """When the *least recently* verified required fact was verified."""
    dates = [fact.as_of for fact in (contract.multiplier, contract.tick_size)]
    if any(moment is None for moment in dates):
        return None
    return min(moment for moment in dates if moment is not None)


def _facts(contract: FuturesContract) -> dict[str, str]:
    facts = {
        "multiplier": _plain(contract.multiplier.value),
        "tick_size": _plain(contract.tick_size.value),
    }
    if contract.expiry is not None and contract.expiry.expiry_date.is_authoritative:
        facts["expiry_date"] = contract.expiry.expiry_date.value.isoformat()
    return facts


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def assess_contract_metadata(
    requested: str,
    candidates: tuple[MetadataCandidate, ...],
    *,
    at: datetime,
    max_age: timedelta,
) -> MetadataVerdict:
    """Decide whether one authoritative, current record exists for ``requested``."""
    if not candidates:
        return _refuse(MetadataVerdictCode.MISSING, f"no metadata source describes {requested}")

    for candidate in candidates:
        if not same_instrument(candidate.contract.symbol, requested):
            return _refuse(
                MetadataVerdictCode.WRONG_CONTRACT,
                f"source {candidate.source_id} answered for {candidate.contract.symbol}, "
                f"not {requested}",
            )

    authoritative = [
        candidate
        for candidate in candidates
        if candidate.contract.multiplier.is_authoritative
        and candidate.contract.tick_size.is_authoritative
        and candidate.contract.multiplier.source.strip()
        and candidate.contract.tick_size.source.strip()
    ]
    if not authoritative:
        return _refuse(
            MetadataVerdictCode.NOT_AUTHORITATIVE,
            "no source gives a sourced, verified current multiplier and tick size",
        )

    dated = [(candidate, _verified_at(candidate.contract)) for candidate in authoritative]
    undated = [candidate for candidate, moment in dated if moment is None]
    if len(undated) == len(dated):
        return _refuse(
            MetadataVerdictCode.NOT_AUTHORITATIVE,
            "a verified fact without a verification date cannot be judged current",
        )
    current = [(candidate, moment) for candidate, moment in dated if moment is not None]
    fresh = [(candidate, moment) for candidate, moment in current if at - moment <= max_age]
    if not fresh:
        return _refuse(
            MetadataVerdictCode.STALE,
            f"the newest verification is older than {int(max_age.total_seconds() // 86400)} days",
        )

    for candidate, _ in fresh:
        state = contract_state(candidate.contract, at)
        if state.state is ContractState.EXPIRED:
            return _refuse(
                MetadataVerdictCode.EXPIRED,
                f"{requested} expired according to source {candidate.source_id}",
            )

    fresh.sort(key=lambda item: item[1], reverse=True)
    chosen, _ = fresh[0]
    chosen_facts = _facts(chosen.contract)
    for other, _ in fresh[1:]:
        other_facts = _facts(other.contract)
        for fact, value in chosen_facts.items():
            if fact in other_facts and other_facts[fact] != value:
                # Part 2A audit: a record without an effective period cannot
                # say which of two disagreeing values *applies*, and the time
                # somebody last checked is not applicability - a recently
                # checked obsolete value would win. So nothing is chosen;
                # sources that can state their period go through
                # ``app.domain.sourcing.facts`` instead.
                return MetadataVerdict(
                    code=MetadataVerdictCode.CONFLICTING,
                    reason=(
                        f"sources {chosen.source_id} and {other.source_id} disagree on "
                        f"{fact}, and neither states the period it governs"
                    ),
                    conflicts=(
                        MetadataConflict(
                            fact=fact,
                            chosen_source=chosen.source_id,
                            chosen_value=value,
                            other_source=other.source_id,
                            other_value=other_facts[fact],
                        ),
                    ),
                )
    return MetadataVerdict(
        code=MetadataVerdictCode.USABLE,
        reason=f"verified by source {chosen.source_id}",
        contract=chosen.contract,
        source_id=chosen.source_id,
    )
