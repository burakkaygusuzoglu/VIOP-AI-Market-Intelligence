"""What this deployment can honestly say about its external sources (Part 2B).

Read-only. Every answer here is built from what is *composed* - the providers,
grants, journal and calendar this process actually holds - not from which
Python interfaces exist. A port with no adapter is ``NOT_CONFIGURED``, never
``AVAILABLE``.

## Six words that are not synonyms

For each data category the matrix reports, separately: **configured** (a
provider offering it is composed), **licensed** (a valid grant covers it),
**connected**, **available** (delivering), **fresh** (inside its bound) and
**verified** (reviewed facts exist in the journal). Only the first five feed
the domain's staged :class:`CapabilityStatus`; "verified" is about journalled
contract facts and changes no capability.

## Metadata status separates the questions

Does a source claim a value; did an operator examine the document; is the
publisher authoritative; is the record for this contract; does it govern the
requested moment; had the system learnt it by then; is it still fresh - and,
separately, **may a financial consumer use it**. The last is always ``False``
in this build: no risk, paper, backtest or shadow composition reads the
journal, and the Phase 3 risk engine keeps its own veto regardless.

## As-of by default

A metadata question is answered *as known*: at ``known_by`` (default: now),
only records this system had journalled by then exist, and a later correction
does not reach back. A retrospective question - "what do we know today about
that period" - must be asked for explicitly and is labelled so.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum, unique
from typing import Literal

from app.application.ports.fact_verification import (
    FactVerificationStore,
    ReviewEntry,
    VerificationCounts,
)
from app.application.ports.session_calendar import SessionCalendarProvider
from app.application.sourcing.capabilities import capability_matrix
from app.domain.sourcing.calendar import CalendarAnswer
from app.domain.sourcing.capability import (
    CapabilityStatus,
    DataCategory,
    LicenceGrant,
    ProviderDeclaration,
    grant_refusal,
)
from app.domain.sourcing.facts import (
    ContractSourceRecord,
    FactConflict,
    FactVerdict,
    FactVerdictCode,
    assess_fact_records,
)

__all__ = [
    "CategoryState",
    "FieldState",
    "FieldStatus",
    "MetadataChecks",
    "MetadataStatus",
    "SourceComposition",
    "SourceStatusService",
]

MAX_RECORDS = 64
"""Records read for one contract. More is refused as a truncated answer."""


@dataclass(frozen=True, slots=True)
class SourceComposition:
    """What the composition root actually built. Nothing here is a request."""

    market_data_provider: str
    declaration: ProviderDeclaration | None = None
    grant: LicenceGrant | None = None
    connected: bool = False
    simulated_market_data: bool = False
    """Phase 13's local simulation of stored history is composed. It is not
    a market-data source and changes no capability."""

    calendar_source_composed: bool = False


@dataclass(frozen=True, slots=True)
class CategoryState:
    category: DataCategory
    status: CapabilityStatus
    reason: str
    configured: bool
    licensed: bool
    connected: bool
    available: bool
    fresh: bool
    verified: bool
    adapter_in_build: bool
    """Whether this build contains any real adapter for the category."""


@unique
class FieldState(StrEnum):
    VERIFIED = "VERIFIED"
    MISSING = "MISSING"
    """The governing record states nothing for this field."""
    NOT_REVIEWABLE = "NOT_REVIEWABLE"
    """This build's review boundary has no way to verify this field."""
    UNAVAILABLE = "UNAVAILABLE"
    """No record governs the requested moment, so no field is known."""


FieldName = Literal[
    "multiplier", "tick_size", "tick_value", "expiry_date", "initial_margin", "maintenance_margin"
]


@dataclass(frozen=True, slots=True)
class FieldStatus:
    name: FieldName
    state: FieldState
    value: str | None = None
    source: str | None = None
    verified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MetadataChecks:
    """The separate questions of Part 2B section 6, answered separately."""

    source_claims_value: bool
    operator_examined_evidence: bool
    source_authority_assessed: bool
    applicable_to_contract: bool
    applicable_at_market_time: bool
    known_by_requested_time: bool
    current: bool
    financial_use_enabled: bool


@dataclass(frozen=True, slots=True)
class MetadataStatus:
    symbol: str
    applies_at: datetime
    known_by: datetime | None
    verdict: FactVerdict
    fields: tuple[FieldStatus, ...]
    checks: MetadataChecks
    records: tuple[ContractSourceRecord, ...]
    conflicts: tuple[FactConflict, ...]

    @property
    def retrospective(self) -> bool:
        return self.known_by is None


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


class SourceStatusService:
    def __init__(
        self,
        *,
        composition: SourceComposition,
        store: FactVerificationStore,
        calendar: SessionCalendarProvider,
        clock: Callable[[], datetime],
        metadata_max_age: timedelta,
        delivery_max_age: timedelta,
    ) -> None:
        self._composition = composition
        self._store = store
        self._calendar = calendar
        self._now = clock
        self._metadata_max_age = metadata_max_age
        self._delivery_max_age = delivery_max_age

    @property
    def composition(self) -> SourceComposition:
        return self._composition

    async def capabilities(self) -> tuple[tuple[CategoryState, ...], VerificationCounts]:
        now = self._now()
        c = self._composition
        counts = await self._store.counts()
        rows = []
        for item in capability_matrix(
            declaration=c.declaration,
            grant=c.grant,
            connected=c.connected,
            last_delivery={},
            max_age=self._delivery_max_age,
            at=now,
        ):
            configured = c.declaration is not None and item.category in c.declaration.categories
            licensed = configured and (
                c.declaration is not None
                and grant_refusal(
                    c.grant,
                    provider_id=c.declaration.provider_id,
                    category=item.category,
                    delivery=c.declaration.delivery,
                    at=now,
                )
                is None
            )
            rows.append(
                CategoryState(
                    category=item.category,
                    status=item.status,
                    reason=item.reason,
                    configured=configured,
                    licensed=licensed,
                    connected=configured and c.connected,
                    available=item.status is CapabilityStatus.AVAILABLE,
                    fresh=item.status is CapabilityStatus.AVAILABLE,
                    verified=item.category is DataCategory.CONTRACT_METADATA and counts.records > 0,
                    adapter_in_build=False,
                )
            )
        return tuple(rows), counts

    async def metadata(
        self,
        symbol: str,
        *,
        applies_at: datetime | None,
        known_by: datetime | None,
        retrospective: bool,
    ) -> MetadataStatus:
        """``retrospective`` must be asked for; otherwise the answer is as the
        system knew it at ``known_by`` (default now)."""
        now = self._now()
        applies = applies_at if applies_at is not None else now
        knowledge = None if retrospective else (known_by if known_by is not None else now)
        records = await self._store.records_for(symbol, limit=MAX_RECORDS + 1)
        if len(records) > MAX_RECORDS:
            records = records[:MAX_RECORDS]
            verdict = FactVerdict(
                code=FactVerdictCode.CONFLICTING,
                reason=f"more than {MAX_RECORDS} records exist; a partial read decides nothing",
            )
        else:
            verdict = assess_fact_records(
                symbol,
                records,
                applies_at=applies,
                now=now,
                max_age=self._metadata_max_age,
                known_by=knowledge,
            )
        return MetadataStatus(
            symbol=symbol,
            applies_at=applies,
            known_by=knowledge,
            verdict=verdict,
            fields=_fields(verdict),
            checks=_checks(records, verdict),
            records=records,
            conflicts=verdict.conflicts,
        )

    async def calendar(self, symbol: str, *, at: datetime | None) -> CalendarAnswer:
        return await self._calendar.session_at(symbol, at if at is not None else self._now())

    async def reviews(self, *, after: int, limit: int) -> tuple[tuple[ReviewEntry, ...], int]:
        return await self._store.review_page(after=after, limit=limit)


_NOT_REVIEWABLE = (
    FieldStatus("tick_value", FieldState.NOT_REVIEWABLE),
    FieldStatus("initial_margin", FieldState.NOT_REVIEWABLE),
    FieldStatus("maintenance_margin", FieldState.NOT_REVIEWABLE),
)
"""Facts the review boundary cannot verify in this build. Listed, never
omitted, so their absence is visible rather than read as zero."""


def _fields(verdict: FactVerdict) -> tuple[FieldStatus, ...]:
    contract = verdict.contract
    if not verdict.usable or contract is None:
        return (
            FieldStatus("multiplier", FieldState.UNAVAILABLE),
            FieldStatus("tick_size", FieldState.UNAVAILABLE),
            FieldStatus("expiry_date", FieldState.UNAVAILABLE),
            *_NOT_REVIEWABLE,
        )
    fields = [
        FieldStatus(
            "multiplier",
            FieldState.VERIFIED,
            value=_plain(contract.multiplier.value),
            source=contract.multiplier.source,
            verified_at=contract.multiplier.as_of,
        ),
        FieldStatus(
            "tick_size",
            FieldState.VERIFIED,
            value=_plain(contract.tick_size.value),
            source=contract.tick_size.source,
            verified_at=contract.tick_size.as_of,
        ),
    ]
    expiry = contract.expiry
    fields.append(
        FieldStatus("expiry_date", FieldState.MISSING)
        if expiry is None
        else FieldStatus(
            "expiry_date",
            FieldState.VERIFIED,
            value=expiry.expiry_date.value.isoformat(),
            source=expiry.expiry_date.source,
            verified_at=expiry.expiry_date.as_of,
        )
    )
    fields.extend(_NOT_REVIEWABLE)
    return tuple(fields)


_GOVERNS = {
    FactVerdictCode.USABLE,
    FactVerdictCode.STALE,
    FactVerdictCode.EXPIRED,
    FactVerdictCode.CONFLICTING,
}


def _checks(records: tuple[ContractSourceRecord, ...], verdict: FactVerdict) -> MetadataChecks:
    code = verdict.code
    held = bool(records)
    return MetadataChecks(
        source_claims_value=held,
        operator_examined_evidence=held,
        source_authority_assessed=any(record.is_authoritative for record in records),
        applicable_to_contract=held and code is not FactVerdictCode.WRONG_CONTRACT,
        applicable_at_market_time=code in _GOVERNS,
        known_by_requested_time=held and code is not FactVerdictCode.NOT_YET_KNOWN,
        current=code is FactVerdictCode.USABLE,
        financial_use_enabled=False,
    )
