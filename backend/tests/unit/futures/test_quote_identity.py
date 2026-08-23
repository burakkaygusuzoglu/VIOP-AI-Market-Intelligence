"""Contract/quote identity: metadata for A can never be paired with prices for B.

The failure this guards is silent and total. Pair one contract's multiplier and
margin with another's price and open interest, and every downstream number -
basis, P&L, exposure - is confidently wrong with nothing in the output to
suggest it. Splitting identity from observations made that pairing expressible,
so the pairing is checked.

All values are TEST_FIXTURE data.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.adapters.contract_metadata import ManualContractMetadataProvider
from app.domain.common.verification import VerificationStatus
from app.domain.futures.basis import BasisContext, calculate_contract_basis
from app.domain.futures.contract import QuoteMismatchError
from app.domain.futures.open_interest import (
    OpenInterestContext,
    read_contract_open_interest,
    read_open_interest,
)
from tests.factories_futures import FIXTURE_NOW, contract, quote, unverified, verified

LATER = FIXTURE_NOW + timedelta(minutes=15)


# ----------------------------------------------------------------------
# Matching
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_matching_contract_and_quote_compute_normally() -> None:
    subject = contract(symbol="FIXTURE_A")
    observation = quote(symbol="FIXTURE_A", futures_price="106", spot_price="105")
    result = calculate_contract_basis(subject, observation)
    assert result.context is BasisContext.PREMIUM
    assert result.basis == Decimal("1")


@pytest.mark.unit
def test_matching_quotes_read_open_interest_normally() -> None:
    subject = contract(symbol="FIXTURE_A")
    before = quote(symbol="FIXTURE_A", futures_price="105", open_interest="1000")
    after = quote(symbol="FIXTURE_A", futures_price="107", open_interest="1200", observed_at=LATER)
    reading = read_contract_open_interest(subject, before, after)
    assert reading.context is OpenInterestContext.NEW_LONG_PARTICIPATION


@pytest.mark.unit
def test_surrounding_whitespace_does_not_break_a_match() -> None:
    subject = contract(symbol="FIXTURE_A")
    observation = quote(symbol="  FIXTURE_A  ", futures_price="106", spot_price="105")
    assert calculate_contract_basis(subject, observation).context is BasisContext.PREMIUM


# ----------------------------------------------------------------------
# Mismatching
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_mismatched_quote_is_refused_by_the_basis_engine() -> None:
    subject = contract(symbol="FIXTURE_A")
    foreign = quote(symbol="FIXTURE_B", futures_price="106", spot_price="105")
    with pytest.raises(QuoteMismatchError) as excinfo:
        calculate_contract_basis(subject, foreign)
    assert excinfo.value.expected == "FIXTURE_A"
    assert excinfo.value.actual == "FIXTURE_B"


@pytest.mark.unit
def test_a_mismatched_quote_is_refused_by_the_open_interest_engine() -> None:
    subject = contract(symbol="FIXTURE_A")
    before = quote(symbol="FIXTURE_A", futures_price="105", open_interest="1000")
    foreign = quote(
        symbol="FIXTURE_B", futures_price="107", open_interest="1200", observed_at=LATER
    )
    with pytest.raises(QuoteMismatchError):
        read_contract_open_interest(subject, before, foreign)


@pytest.mark.unit
def test_two_quotes_of_different_instruments_are_refused() -> None:
    """A front-month roll would otherwise read as mass liquidation followed by
    mass new participation - a perfectly plausible reading of a change that
    never happened."""
    before = quote(symbol="FIXTURE_A", futures_price="105", open_interest="9000")
    after = quote(symbol="FIXTURE_B", futures_price="107", open_interest="200", observed_at=LATER)
    with pytest.raises(QuoteMismatchError):
        read_open_interest(before, after)


@pytest.mark.unit
def test_a_case_difference_is_a_mismatch_not_a_match() -> None:
    """No VIOP symbol rule is assumed - not even case folding.

    Any normalisation beyond whitespace would be an exchange convention this
    project has not verified.
    """
    subject = contract(symbol="FIXTURE_A")
    observation = quote(symbol="fixture_a", futures_price="106", spot_price="105")
    with pytest.raises(QuoteMismatchError):
        calculate_contract_basis(subject, observation)


@pytest.mark.unit
def test_the_error_names_both_instruments_and_the_operation() -> None:
    subject = contract(symbol="FIXTURE_A")
    foreign = quote(symbol="FIXTURE_B", futures_price="106", spot_price="105")
    with pytest.raises(QuoteMismatchError, match="basis calculation"):
        calculate_contract_basis(subject, foreign)


# ----------------------------------------------------------------------
# Missing and unknown contracts
# ----------------------------------------------------------------------


@pytest.mark.unit
async def test_a_provider_lookup_of_a_nonexistent_symbol_returns_none() -> None:
    provider = ManualContractMetadataProvider([contract(symbol="FIXTURE_A")])
    assert await provider.get_contract("FIXTURE_MISSING") is None


@pytest.mark.unit
async def test_a_missing_contract_cannot_be_paired_with_any_quote() -> None:
    """The type system carries this: there is no contract to pass."""
    provider = ManualContractMetadataProvider()
    subject = await provider.get_contract("FIXTURE_MISSING")
    assert subject is None


@pytest.mark.unit
async def test_provenance_survives_lookup_and_pairing() -> None:
    registered = contract(
        symbol="FIXTURE_C", multiplier=unverified(100), initial_margin=unverified(250)
    )
    provider = ManualContractMetadataProvider([registered])
    served = await provider.get_contract("FIXTURE_C")
    assert served is not None
    assert served.multiplier.status is VerificationStatus.UNVERIFIED

    observation = quote(symbol="FIXTURE_C", futures_price="106", spot_price="105")
    result = calculate_contract_basis(served, observation)
    assert result.context is BasisContext.PREMIUM
    assert served.multiplier.status is VerificationStatus.UNVERIFIED
    assert served.authoritative_multiplier() is None


@pytest.mark.unit
async def test_a_verified_contract_keeps_its_status_through_pairing() -> None:
    registered = contract(symbol="FIXTURE_D", multiplier=verified(100))
    provider = ManualContractMetadataProvider([registered])
    served = await provider.get_contract("FIXTURE_D")
    assert served is not None
    assert served.multiplier.status is VerificationStatus.VERIFIED_CURRENT_FACT
    assert served.authoritative_multiplier() == Decimal("100")
