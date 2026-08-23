"""Basis (section 32) and open-interest context (section 33).

All values are TEST_FIXTURE data.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.domain.futures.basis import (
    BasisContext,
    BasisPolicy,
    calculate_basis,
)
from app.domain.futures.open_interest import (
    OpenInterestContext,
    OpenInterestPolicy,
    read_open_interest,
)
from tests.factories_futures import FIXTURE_NOW, quote

# ----------------------------------------------------------------------
# Basis
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_futures_premium_hand_calculated() -> None:
    """106 futures against 105 spot: basis 1, ratio 1/105."""
    result = calculate_basis(Decimal("106"), Decimal("105"))
    assert result.context is BasisContext.PREMIUM
    assert result.basis == Decimal("1")
    assert result.basis_ratio is not None
    assert result.basis_ratio == pytest.approx(Decimal(1) / Decimal(105))


@pytest.mark.unit
def test_a_futures_discount_hand_calculated() -> None:
    result = calculate_basis(Decimal("104"), Decimal("105"))
    assert result.context is BasisContext.DISCOUNT
    assert result.basis == Decimal("-1")


@pytest.mark.unit
def test_an_exact_match_is_flat() -> None:
    result = calculate_basis(Decimal("105"), Decimal("105"))
    assert result.context is BasisContext.FLAT
    assert result.basis == Decimal("0")


@pytest.mark.unit
def test_the_ratio_is_a_fraction_and_the_percent_is_points() -> None:
    """The convention, pinned. A 2% premium is ratio 0.02 and percent 2.

    Confusing the two is the most reliable source of factor-of-100 errors in
    financial code, so both are exposed under names that say which is which.
    """
    result = calculate_basis(Decimal("102"), Decimal("100"))
    assert result.basis_ratio == Decimal("0.02")
    assert result.basis_percent == Decimal("2.00")


@pytest.mark.unit
def test_a_missing_spot_price_is_unavailable_not_flat() -> None:
    """FLAT would claim a measurement nobody took."""
    result = calculate_basis(Decimal("105"), None)
    assert result.context is BasisContext.UNAVAILABLE
    assert result.basis is None
    assert not result.is_available


@pytest.mark.unit
def test_a_missing_futures_price_is_unavailable() -> None:
    assert calculate_basis(None, Decimal("105")).context is BasisContext.UNAVAILABLE


@pytest.mark.unit
def test_a_zero_spot_leaves_the_ratio_undefined() -> None:
    result = calculate_basis(Decimal("105"), Decimal("0"))
    assert result.context is BasisContext.UNAVAILABLE
    assert result.basis == Decimal("105")
    assert result.basis_ratio is None


@pytest.mark.unit
def test_the_flat_tolerance_is_configurable_and_defaults_to_exact() -> None:
    near = (Decimal("105.01"), Decimal("105"))
    assert calculate_basis(*near).context is BasisContext.PREMIUM
    relaxed = BasisPolicy(flat_ratio_tolerance=Decimal("0.001"))
    assert calculate_basis(*near, relaxed).context is BasisContext.FLAT


@pytest.mark.unit
def test_a_negative_flat_tolerance_is_rejected() -> None:
    with pytest.raises(ValueError, match="flat_ratio_tolerance"):
        BasisPolicy(flat_ratio_tolerance=Decimal("-0.01"))


@pytest.mark.unit
def test_basis_never_becomes_a_direction() -> None:
    """Section 32: basis alone is not a directional prediction."""
    assert {member.value for member in BasisContext} == {
        "PREMIUM",
        "DISCOUNT",
        "FLAT",
        "UNAVAILABLE",
    }


@pytest.mark.unit
def test_annualised_basis_is_not_offered() -> None:
    """Deferred under section 118: it needs a verified expiry timestamp and a
    day-count convention, and 365/360/252 each give a different confident
    answer."""
    from app.domain.futures import basis as module

    assert not hasattr(module, "annualised_basis")
    assert not hasattr(module, "annualized_basis")
    assert "annualised" in module.__doc__.lower() if module.__doc__ else False


@pytest.mark.unit
def test_basis_is_reproducible() -> None:
    args = (Decimal("106"), Decimal("105"))
    assert calculate_basis(*args) == calculate_basis(*args)


# ----------------------------------------------------------------------
# Open interest
# ----------------------------------------------------------------------


LATER = FIXTURE_NOW + timedelta(minutes=15)


def reading(price_before: str, oi_before: str, price_after: str, oi_after: str, policy=None):  # type: ignore[no-untyped-def]
    return read_open_interest(
        quote(futures_price=price_before, open_interest=oi_before),
        quote(futures_price=price_after, open_interest=oi_after, observed_at=LATER),
        policy,
    )


@pytest.mark.unit
def test_price_up_open_interest_up_is_new_long_participation() -> None:
    result = reading("105", "1000", "107", "1200")
    assert result.context is OpenInterestContext.NEW_LONG_PARTICIPATION
    assert result.price_change == Decimal("2")
    assert result.open_interest_change == Decimal("200")


@pytest.mark.unit
def test_price_up_open_interest_down_is_short_covering() -> None:
    assert reading("105", "1000", "107", "800").context is OpenInterestContext.SHORT_COVERING


@pytest.mark.unit
def test_price_down_open_interest_up_is_new_short_participation() -> None:
    assert (
        reading("105", "1000", "103", "1200").context is OpenInterestContext.NEW_SHORT_PARTICIPATION
    )


@pytest.mark.unit
def test_price_down_open_interest_down_is_liquidation() -> None:
    assert reading("105", "1000", "103", "800").context is OpenInterestContext.LIQUIDATION


@pytest.mark.unit
def test_an_unchanged_price_is_not_forced_into_one_of_the_four() -> None:
    result = reading("105", "1000", "105", "1200")
    assert result.context is OpenInterestContext.UNCHANGED
    assert "price did not move" in result.reason
    assert not result.is_informative


@pytest.mark.unit
def test_unchanged_open_interest_is_not_forced_either() -> None:
    result = reading("105", "1000", "107", "1000")
    assert result.context is OpenInterestContext.UNCHANGED
    assert "open interest did not move" in result.reason


@pytest.mark.unit
def test_both_unchanged_is_reported_as_such() -> None:
    result = reading("105", "1000", "105", "1000")
    assert result.context is OpenInterestContext.UNCHANGED
    assert "neither" in result.reason


@pytest.mark.unit
def test_a_missing_open_interest_is_insufficient_data_not_unchanged() -> None:
    """Nothing happened and nobody looked are different statements."""
    result = read_open_interest(
        quote(futures_price="105", open_interest="1000"),
        quote(futures_price="107", observed_at=LATER),
    )
    assert result.context is OpenInterestContext.INSUFFICIENT_DATA
    assert result.open_interest_change is None


@pytest.mark.unit
def test_a_missing_price_is_insufficient_data() -> None:
    result = read_open_interest(
        quote(open_interest="1000"),
        quote(futures_price="107", open_interest="1200", observed_at=LATER),
    )
    assert result.context is OpenInterestContext.INSUFFICIENT_DATA
    assert result.price_change is None


@pytest.mark.unit
def test_tolerances_are_configurable_project_heuristics() -> None:
    tight = reading("105", "1000", "105.01", "1001")
    assert tight.context is OpenInterestContext.NEW_LONG_PARTICIPATION

    relaxed = reading(
        "105",
        "1000",
        "105.01",
        "1001",
        OpenInterestPolicy(price_tolerance=Decimal("0.05"), open_interest_tolerance=Decimal("10")),
    )
    assert relaxed.context is OpenInterestContext.UNCHANGED


@pytest.mark.unit
def test_negative_tolerances_are_rejected() -> None:
    with pytest.raises(ValueError, match="price_tolerance"):
        OpenInterestPolicy(price_tolerance=Decimal("-1"))
    with pytest.raises(ValueError, match="open_interest_tolerance"):
        OpenInterestPolicy(open_interest_tolerance=Decimal("-1"))


@pytest.mark.unit
def test_open_interest_context_never_becomes_a_direction() -> None:
    """Section 33: contextual interpretations, never absolute rules."""
    values = {member.value for member in OpenInterestContext}
    assert not (values & {"LONG", "SHORT", "BUY", "SELL"})


@pytest.mark.unit
def test_the_reading_is_reproducible() -> None:
    assert reading("105", "1000", "107", "1200") == reading("105", "1000", "107", "1200")
