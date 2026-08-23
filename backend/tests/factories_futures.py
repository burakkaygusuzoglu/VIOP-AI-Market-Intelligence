"""Contract builders for Phase 3 tests.

**Every number produced here is TEST_FIXTURE data under master spec section
118.** None of it describes a VIOP contract, a real multiplier, a real tick
size or a real margin. The values are round numbers chosen to make hand
calculation easy, and they must never be read as, quoted as, or copied into
anything presenting itself as a current exchange specification.

The default verification status is ``TEST_FIXTURE`` for exactly that reason: a
test that needed ``VERIFIED_CURRENT_FACT`` has to ask for it explicitly, which
makes the pretence visible at the call site.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.domain.common.verification import VerificationStatus, VerifiedValue
from app.domain.futures.contract import (
    ContractExpiry,
    FuturesContract,
    FuturesQuote,
    SettlementType,
    ValuationModel,
)

FIXTURE_SYMBOL = "TEST_FIXTURE_FUT"
FIXTURE_SOURCE = "test fixture, not an exchange specification"
FIXTURE_NOW = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)


def fact(
    value: Decimal | str | int,
    status: VerificationStatus = VerificationStatus.TEST_FIXTURE,
    source: str = FIXTURE_SOURCE,
    as_of: datetime | None = None,
) -> VerifiedValue[Decimal]:
    """A ``Decimal`` fact with explicit provenance."""
    return VerifiedValue[Decimal](
        value=Decimal(str(value)), status=status, source=source, as_of=as_of
    )


def verified(
    value: Decimal | str | int, as_of: datetime | None = FIXTURE_NOW
) -> VerifiedValue[Decimal]:
    """A fact a test is treating **as if** verified.

    Used where the behaviour under test is what happens with a verified fact.
    The value is still invented; only the status is being simulated.

    It carries an ``as_of`` by default, because a claim of *current* fact with
    no date is itself a finding - pass ``as_of=None`` to exercise that.
    """
    return fact(value, VerificationStatus.VERIFIED_CURRENT_FACT, as_of=as_of)


def unverified(value: Decimal | str | int) -> VerifiedValue[Decimal]:
    return fact(value, VerificationStatus.UNVERIFIED)


def expiry_on(
    when: date,
    status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT,
    last_trading_time: datetime | None = None,
    last_trading_status: VerificationStatus = VerificationStatus.VERIFIED_CURRENT_FACT,
) -> ContractExpiry:
    return ContractExpiry(
        expiry_date=VerifiedValue[date](
            value=when, status=status, source=FIXTURE_SOURCE, as_of=FIXTURE_NOW
        ),
        last_trading_time=(
            None
            if last_trading_time is None
            else VerifiedValue[datetime](
                value=last_trading_time,
                status=last_trading_status,
                source=FIXTURE_SOURCE,
                as_of=FIXTURE_NOW,
            )
        ),
    )


def contract(
    *,
    symbol: str = FIXTURE_SYMBOL,
    multiplier: VerifiedValue[Decimal] | None = None,
    tick_size: VerifiedValue[Decimal] | None = None,
    tick_value: VerifiedValue[Decimal] | None = None,
    initial_margin: VerifiedValue[Decimal] | None = None,
    maintenance_margin: VerifiedValue[Decimal] | None = None,
    expiry: ContractExpiry | None = None,
    settlement: SettlementType | None = None,
    trading_session: str | None = None,
    valuation: ValuationModel = ValuationModel.LINEAR,
) -> FuturesContract:
    """A contract built entirely from fixture values.

    Multiplier and tick size default to *verified* fixture values because most
    tests are about the arithmetic rather than about provenance; the tests that
    are about provenance pass their own.
    """
    return FuturesContract(
        symbol=symbol,
        underlying_symbol="TEST_FIXTURE_SPOT",
        contract_name="TEST FIXTURE FUTURE",
        multiplier=multiplier if multiplier is not None else verified(100),
        tick_size=tick_size if tick_size is not None else verified("0.05"),
        tick_value=tick_value,
        initial_margin=initial_margin,
        maintenance_margin=maintenance_margin,
        expiry=expiry,
        settlement=(
            None
            if settlement is None
            else VerifiedValue[SettlementType](
                value=settlement,
                status=VerificationStatus.TEST_FIXTURE,
                source=FIXTURE_SOURCE,
            )
        ),
        trading_session=(
            None
            if trading_session is None
            else VerifiedValue[str](
                value=trading_session,
                status=VerificationStatus.TEST_FIXTURE,
                source=FIXTURE_SOURCE,
            )
        ),
        valuation=valuation,
    )


def quote(
    *,
    symbol: str = FIXTURE_SYMBOL,
    observed_at: datetime = FIXTURE_NOW,
    futures_price: Decimal | str | int | None = None,
    spot_price: Decimal | str | int | None = None,
    open_interest: Decimal | str | int | None = None,
    volume: Decimal | str | int | None = None,
) -> FuturesQuote:
    def to_decimal(value: Decimal | str | int | None) -> Decimal | None:
        return None if value is None else Decimal(str(value))

    return FuturesQuote(
        symbol=symbol,
        observed_at=observed_at,
        futures_price=to_decimal(futures_price),
        spot_price=to_decimal(spot_price),
        open_interest=to_decimal(open_interest),
        volume=to_decimal(volume),
    )
