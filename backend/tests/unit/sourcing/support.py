"""Test doubles for the Phase 15 provider foundation.

Everything here is a **test double**. ``GrantedTestProvider`` declares a
real-exchange provenance so the grant gate can be exercised; the grants built
by ``fixture_grant`` carry ``VERIFIED_CURRENT_FACT`` evidence only because a test
constructs them - the source string says so, and a separate test proves the
application composes no grant at all. None of this is real provider access.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.domain.common.enums import Timeframe
from app.domain.common.verification import VerificationStatus, VerifiedValue
from app.domain.futures.contract import ContractExpiry, FuturesContract
from app.domain.live.events import StreamItem, StreamProvenance
from app.domain.sourcing.capability import DataCategory, Delivery, LicenceGrant
from tests.unit.live.support import RECEIVE_START

TEST_SOURCE = "TEST_FIXTURE: constructed by the Phase 15 test suite, not a real licence"
NOW = RECEIVE_START


class GrantedTestProvider:
    """A queue-backed provider that *declares* whatever provenance a test asks.

    Declaring is all it can do: whether a session accepts the declaration is
    the grant gate's decision, which is what these tests exercise.
    """

    provider_id = "TEST_DOUBLE_PROVIDER"

    def __init__(self, provenance: StreamProvenance) -> None:
        self._provenance = provenance
        self.queue: asyncio.Queue[StreamItem | None] = asyncio.Queue()

    @property
    def provenance(self) -> StreamProvenance:
        return self._provenance

    async def subscribe(
        self, symbol: str, timeframes: tuple[Timeframe, ...]
    ) -> GrantedTestProvider:
        return self

    async def items(self) -> AsyncIterator[StreamItem]:
        while True:
            item = await self.queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        return None

    def put(self, *items: StreamItem | None) -> None:
        for item in items:
            self.queue.put_nowait(item)


def fixture_grant(
    delivery: Delivery = Delivery.DELAYED,
    *,
    provider_id: str = GrantedTestProvider.provider_id,
    categories: frozenset[DataCategory] = frozenset({DataCategory.MARKET_DATA}),
    status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT,
    source: str = TEST_SOURCE,
    as_of: datetime | None = NOW - timedelta(days=1),
    valid_until: datetime | None = NOW + timedelta(days=30),
) -> LicenceGrant:
    return LicenceGrant(
        provider_id=provider_id,
        categories=categories,
        delivery=delivery,
        evidence=VerifiedValue(
            value="TEST-LICENCE-0001", status=status, source=source, as_of=as_of
        ),
        valid_until=valid_until,
    )


def fact(
    value: str,
    *,
    status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT,
    source: str = TEST_SOURCE,
    as_of: datetime | None = NOW - timedelta(days=2),
) -> VerifiedValue[Decimal]:
    return VerifiedValue(value=Decimal(value), status=status, source=source, as_of=as_of)


def contract(
    symbol: str = "TEST_FIXTURE_FUT",
    *,
    multiplier: VerifiedValue[Decimal] | None = None,
    tick_size: VerifiedValue[Decimal] | None = None,
    expiry: date | None = None,
) -> FuturesContract:
    """A contract record with explicit TEST_FIXTURE-sourced facts."""
    return FuturesContract(
        symbol=symbol,
        underlying_symbol="TEST_FIXTURE_UNDERLYING",
        contract_name="Test fixture contract - not a VIOP specification",
        multiplier=multiplier or fact("10"),
        tick_size=tick_size or fact("0.25"),
        expiry=None
        if expiry is None
        else ContractExpiry(
            expiry_date=VerifiedValue(
                value=expiry,
                status=VerificationStatus.VERIFIED_CURRENT_FACT,
                source=TEST_SOURCE,
                as_of=NOW - timedelta(days=2),
            )
        ),
    )


def reference(
    value: str = "TEST_FIXTURE_DOC#1",
    *,
    status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT,
    source: str = TEST_SOURCE,
    as_of: datetime | None = NOW - timedelta(days=2),
) -> VerifiedValue[str]:
    """A source reference as a fact. TEST_FIXTURE-sourced whatever its status."""
    return VerifiedValue(value=value, status=status, source=source, as_of=as_of)
