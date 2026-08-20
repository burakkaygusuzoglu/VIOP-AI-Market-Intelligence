"""Provenance rules for financial facts (master spec section 118)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.common.verification import (
    UnverifiedFinancialFactError,
    VerificationStatus,
    VerifiedValue,
)


@pytest.mark.unit
def test_only_verified_current_fact_is_authoritative() -> None:
    for status in VerificationStatus:
        expected = status is VerificationStatus.VERIFIED_CURRENT_FACT
        assert status.is_authoritative is expected


@pytest.mark.unit
def test_authoritative_value_is_released() -> None:
    multiplier = VerifiedValue[Decimal](
        value=Decimal("100"),
        status=VerificationStatus.VERIFIED_CURRENT_FACT,
        source="fixture: authoritative-source-placeholder",
        as_of=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert multiplier.require_authoritative("contract multiplier") == Decimal("100")


@pytest.mark.unit
@pytest.mark.parametrize(
    "status",
    [
        VerificationStatus.DEVELOPMENT_DEFAULT,
        VerificationStatus.TEST_FIXTURE,
        VerificationStatus.MOCK_DATA,
        VerificationStatus.UNVERIFIED,
    ],
)
def test_non_authoritative_value_cannot_reach_a_calculation(
    status: VerificationStatus,
) -> None:
    """A guessed or placeholder value must never be used as a market fact."""
    fact = VerifiedValue[Decimal](
        value=Decimal("100"),
        status=status,
        source="TEST_FIXTURE",
    )
    with pytest.raises(UnverifiedFinancialFactError) as error:
        fact.require_authoritative("contract multiplier")
    assert "contract multiplier" in str(error.value)
    assert status.value in str(error.value)


@pytest.mark.unit
def test_unknown_facts_are_marked_rather_than_guessed() -> None:
    unknown = VerifiedValue.unverified(
        source="no provider configured",
        note="initial margin requires an authoritative source",
    )
    assert unknown.value is None
    assert unknown.status is VerificationStatus.UNVERIFIED
    assert not unknown.is_authoritative


@pytest.mark.unit
def test_verified_value_is_immutable() -> None:
    fact = VerifiedValue[int](
        value=1,
        status=VerificationStatus.TEST_FIXTURE,
        source="TEST_FIXTURE",
    )
    with pytest.raises(AttributeError):
        fact.value = 2  # type: ignore[misc]
