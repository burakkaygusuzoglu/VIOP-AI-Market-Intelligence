"""Contract model, validation, provenance, tick semantics and expiry.

**Every value in this file is TEST_FIXTURE data.** None of it describes a VIOP
contract; the multipliers and tick sizes are round numbers chosen for hand
calculation and must never be read as an exchange specification.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.domain.common.verification import VerificationStatus
from app.domain.futures.contract import (
    ContractState,
    ContractValidationError,
    FuturesQuote,
    SettlementType,
    UnsupportedValuationModelError,
    ValuationModel,
)
from app.domain.futures.validation import (
    ContractIssueCode,
    check_tick_grid,
    check_tick_value,
    contract_issues,
    contract_state,
    implied_tick_value,
    ticks_between,
)
from tests.factories_futures import contract, expiry_on, fact, quote, unverified, verified

NOW = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)


# ----------------------------------------------------------------------
# Metadata invariants
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("bad", ("0", "-1"))
def test_a_non_positive_multiplier_is_impossible(bad: str) -> None:
    with pytest.raises(ContractValidationError, match="multiplier"):
        contract(multiplier=verified(bad))


@pytest.mark.unit
@pytest.mark.parametrize("bad", ("0", "-0.05"))
def test_a_non_positive_tick_size_is_impossible(bad: str) -> None:
    with pytest.raises(ContractValidationError, match="tick_size"):
        contract(tick_size=verified(bad))


@pytest.mark.unit
def test_a_non_positive_tick_value_is_impossible() -> None:
    with pytest.raises(ContractValidationError, match="tick_value"):
        contract(tick_value=verified("0"))


@pytest.mark.unit
def test_a_non_positive_initial_margin_is_impossible() -> None:
    with pytest.raises(ContractValidationError, match="initial_margin"):
        contract(initial_margin=verified("0"))


@pytest.mark.unit
def test_a_negative_maintenance_margin_is_impossible() -> None:
    with pytest.raises(ContractValidationError, match="maintenance_margin"):
        contract(maintenance_margin=verified("-1"))


@pytest.mark.unit
def test_a_non_finite_multiplier_is_impossible() -> None:
    with pytest.raises(ContractValidationError, match="finite"):
        contract(multiplier=fact(Decimal("NaN")))


@pytest.mark.unit
def test_an_empty_symbol_is_impossible() -> None:
    with pytest.raises(ContractValidationError, match="symbol"):
        contract(symbol="   ")


@pytest.mark.unit
@pytest.mark.parametrize(("field", "bad"), (("open_interest", "-1"), ("volume", "-5")))
def test_negative_quote_quantities_are_impossible(field: str, bad: str) -> None:
    with pytest.raises(ContractValidationError, match=field):
        quote(**{field: bad})  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize("field", ("futures_price", "spot_price"))
def test_non_positive_quote_prices_are_impossible(field: str) -> None:
    with pytest.raises(ContractValidationError, match=field):
        quote(**{field: "0"})  # type: ignore[arg-type]


@pytest.mark.unit
def test_a_naive_observation_time_is_impossible() -> None:
    with pytest.raises(ContractValidationError, match="timezone-aware"):
        FuturesQuote(symbol="X", observed_at=datetime(2026, 3, 2, 12, 0))  # noqa: DTZ001


@pytest.mark.unit
def test_zero_open_interest_is_allowed() -> None:
    """Zero is a real reading; only negative is impossible."""
    assert quote(open_interest="0").open_interest == Decimal("0")


# ----------------------------------------------------------------------
# Provenance
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_an_unverified_fact_is_not_released_as_authoritative() -> None:
    subject = contract(multiplier=unverified(100))
    assert subject.multiplier.value == Decimal("100")
    assert subject.authoritative_multiplier() is None


@pytest.mark.unit
def test_a_verified_fact_is_released() -> None:
    assert contract(multiplier=verified(100)).authoritative_multiplier() == Decimal("100")


@pytest.mark.unit
@pytest.mark.parametrize(
    "status",
    (
        VerificationStatus.DEVELOPMENT_DEFAULT,
        VerificationStatus.TEST_FIXTURE,
        VerificationStatus.MOCK_DATA,
        VerificationStatus.UNVERIFIED,
    ),
)
def test_no_status_other_than_verified_counts_as_authoritative(
    status: VerificationStatus,
) -> None:
    subject = contract(multiplier=fact(100, status))
    assert subject.authoritative_multiplier() is None


@pytest.mark.unit
def test_missing_and_unverified_margin_are_distinguishable() -> None:
    """A gap in the data is not the same as a number somebody is unsure about."""
    missing = contract()
    unsure = contract(initial_margin=unverified(250))

    assert missing.initial_margin is None
    assert unsure.initial_margin is not None
    assert unsure.initial_margin.value == Decimal("250")
    assert missing.authoritative_initial_margin() is None
    assert unsure.authoritative_initial_margin() is None

    assert ContractIssueCode.MISSING_MARGIN in {i.code for i in contract_issues(missing)}
    assert ContractIssueCode.UNVERIFIED_MARGIN in {i.code for i in contract_issues(unsure)}


@pytest.mark.unit
def test_issues_report_unverified_facts_without_raising() -> None:
    """An unverified margin still leaves everything that does not need it usable."""
    issues = contract_issues(contract(multiplier=unverified(100)))
    codes = {issue.code for issue in issues}
    assert ContractIssueCode.UNVERIFIED_MULTIPLIER in codes
    assert any(issue.blocking for issue in issues)


@pytest.mark.unit
def test_a_fully_verified_and_fully_sourced_contract_reports_nothing() -> None:
    subject = contract(
        multiplier=verified(100),
        tick_size=verified("0.05"),
        initial_margin=verified(250),
        expiry=expiry_on(date(2026, 6, 30)),
    )
    assert contract_issues(subject) == ()


@pytest.mark.unit
def test_a_verified_fact_without_an_as_of_is_flagged_but_not_blocking() -> None:
    """'Current' cannot be evaluated without knowing when it was current.

    No freshness duration is invented - this does not decide anything is stale,
    which would need an exchange revision schedule. It requires only that the
    provenance exists so a human or a later phase can judge it.
    """
    subject = contract(multiplier=verified(100, as_of=None))
    issue = next(
        i for i in contract_issues(subject) if i.code is ContractIssueCode.UNDATED_VERIFIED_FACT
    )
    assert not issue.blocking
    assert "multiplier" in issue.message


@pytest.mark.unit
def test_a_verified_fact_with_no_source_is_blocking() -> None:
    """An unattributable claim of currency is a guess wearing the right status."""
    subject = contract(multiplier=fact(100, VerificationStatus.VERIFIED_CURRENT_FACT, source="  "))
    issue = next(
        i for i in contract_issues(subject) if i.code is ContractIssueCode.UNSOURCED_VERIFIED_FACT
    )
    assert issue.blocking


@pytest.mark.unit
def test_unverified_facts_are_not_checked_for_provenance_completeness() -> None:
    """They already carry a status telling the caller not to rely on them."""
    codes = {i.code for i in contract_issues(contract(multiplier=unverified(100)))}
    assert ContractIssueCode.UNDATED_VERIFIED_FACT not in codes
    assert ContractIssueCode.UNSOURCED_VERIFIED_FACT not in codes


# ----------------------------------------------------------------------
# Tick grid: report, never repair
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_price_on_the_grid_is_reported_as_on_grid() -> None:
    result = check_tick_grid(Decimal("105.05"), Decimal("0.05"))
    assert result.on_grid
    assert result.remainder == Decimal("0")


@pytest.mark.unit
def test_an_off_grid_price_is_reported_and_never_rounded() -> None:
    """105.037 must not silently become 105.05.

    A stop moved by a tick is a stop in a different place than the one the risk
    calculation used.
    """
    result = check_tick_grid(Decimal("105.037"), Decimal("0.05"))
    assert not result.on_grid
    assert result.price == Decimal("105.037")
    assert result.remainder == Decimal("0.037")
    assert result.nearest_lower == Decimal("105.000")
    assert result.nearest_upper == Decimal("105.050")


@pytest.mark.unit
def test_the_caller_is_given_both_neighbours_so_snapping_stays_explicit() -> None:
    result = check_tick_grid(Decimal("105.037"), Decimal("0.05"))
    assert result.nearest_lower < result.price < result.nearest_upper


@pytest.mark.unit
def test_a_non_positive_tick_size_cannot_be_checked_against() -> None:
    with pytest.raises(ValueError, match="tick_size"):
        check_tick_grid(Decimal("105"), Decimal("0"))


@pytest.mark.unit
def test_ticks_between_two_prices() -> None:
    assert ticks_between(Decimal("105.00"), Decimal("105.25"), Decimal("0.05")) == Decimal("5")


# ----------------------------------------------------------------------
# Tick value: cross-checked only where the identity holds
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_consistent_tick_value_passes() -> None:
    """0.05 x 100 = 5."""
    subject = contract(
        multiplier=verified(100), tick_size=verified("0.05"), tick_value=verified("5")
    )
    assert check_tick_value(subject) is None


@pytest.mark.unit
def test_an_inconsistent_tick_value_is_reported_as_blocking() -> None:
    subject = contract(
        multiplier=verified(100), tick_size=verified("0.05"), tick_value=verified("7")
    )
    issue = check_tick_value(subject)
    assert issue is not None
    assert issue.code is ContractIssueCode.TICK_VALUE_INCONSISTENT
    assert issue.blocking


@pytest.mark.unit
def test_an_absent_tick_value_produces_no_finding() -> None:
    assert check_tick_value(contract()) is None


@pytest.mark.unit
def test_the_identity_is_not_claimed_for_non_linear_valuation() -> None:
    """``tick_value == tick_size x multiplier`` is a *linear* fact, not a
    universal one; an inverse contract's tick value depends on price."""
    subject = contract(tick_value=verified("5"), valuation=ValuationModel.INVERSE)
    issue = check_tick_value(subject)
    assert issue is not None
    assert issue.code is ContractIssueCode.TICK_VALUE_UNCHECKABLE


@pytest.mark.unit
def test_implied_tick_value_is_derived_not_stored() -> None:
    subject = contract(multiplier=verified(100), tick_size=verified("0.05"))
    assert implied_tick_value(subject) == Decimal("5.00")
    assert subject.tick_value is None


@pytest.mark.unit
def test_implied_tick_value_needs_both_facts_verified() -> None:
    assert implied_tick_value(contract(multiplier=unverified(100))) is None


# ----------------------------------------------------------------------
# Valuation model: fail closed
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("model", (ValuationModel.INVERSE, ValuationModel.QUANTO))
def test_unsupported_valuation_is_refused_rather_than_approximated(
    model: ValuationModel,
) -> None:
    from app.domain.common.enums import Direction
    from app.domain.risk.pnl import calculate_contract_pnl

    subject = contract(valuation=model)
    with pytest.raises(UnsupportedValuationModelError):
        calculate_contract_pnl(subject, Direction.LONG, Decimal("105"), Decimal("110"), 1)


@pytest.mark.unit
def test_linear_valuation_is_the_default_and_is_supported() -> None:
    assert contract().valuation is ValuationModel.LINEAR
    contract().requires_linear_valuation("test")


# ----------------------------------------------------------------------
# Expiry
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_a_future_expiry_is_active() -> None:
    result = contract_state(contract(expiry=expiry_on(date(2026, 6, 30))), NOW)
    assert result.state is ContractState.ACTIVE
    assert result.days_to_expiry == 120


@pytest.mark.unit
def test_a_past_expiry_is_expired() -> None:
    result = contract_state(contract(expiry=expiry_on(date(2026, 2, 27))), NOW)
    assert result.state is ContractState.EXPIRED
    assert result.days_to_expiry == -3


@pytest.mark.unit
def test_the_expiry_day_itself_is_unknown_without_a_verified_last_trading_time() -> None:
    """The section 118 decision, and the point of this whole module.

    A contract stops trading at a specific moment on its final day. That moment
    is an exchange fact this project does not hold, so claiming the contract is
    dead - or alive - at noon would be inventing a session hour.
    """
    result = contract_state(contract(expiry=expiry_on(date(2026, 3, 2))), NOW)
    assert result.state is ContractState.UNKNOWN
    assert result.days_to_expiry == 0
    assert "inventing a session hour" in result.reason


@pytest.mark.unit
def test_a_verified_last_trading_time_makes_the_expiry_day_decidable() -> None:
    before = contract_state(
        contract(
            expiry=expiry_on(
                date(2026, 3, 2), last_trading_time=datetime(2026, 3, 2, 18, 0, tzinfo=UTC)
            )
        ),
        NOW,
    )
    after = contract_state(
        contract(
            expiry=expiry_on(
                date(2026, 3, 2), last_trading_time=datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
            )
        ),
        NOW,
    )
    assert before.state is ContractState.ACTIVE
    assert after.state is ContractState.EXPIRED


@pytest.mark.unit
def test_an_unverified_last_trading_time_does_not_decide_the_day() -> None:
    result = contract_state(
        contract(
            expiry=expiry_on(
                date(2026, 3, 2),
                last_trading_time=datetime(2026, 3, 2, 9, 0, tzinfo=UTC),
                last_trading_status=VerificationStatus.DEVELOPMENT_DEFAULT,
            )
        ),
        NOW,
    )
    assert result.state is ContractState.UNKNOWN


@pytest.mark.unit
def test_a_missing_expiry_is_unknown() -> None:
    result = contract_state(contract(), NOW)
    assert result.state is ContractState.UNKNOWN
    assert result.days_to_expiry is None


@pytest.mark.unit
def test_an_unverified_expiry_is_unknown() -> None:
    result = contract_state(
        contract(expiry=expiry_on(date(2026, 2, 27), VerificationStatus.DEVELOPMENT_DEFAULT)),
        NOW,
    )
    assert result.state is ContractState.UNKNOWN
    assert result.days_to_expiry is None


@pytest.mark.unit
def test_expiry_evaluation_requires_an_injected_aware_clock() -> None:
    """Never ``datetime.now()``: replay must evaluate at the historical moment."""
    with pytest.raises(ValueError, match="ClockPort"):
        contract_state(
            contract(expiry=expiry_on(date(2026, 6, 30))),
            datetime(2026, 3, 2, 12, 0),  # noqa: DTZ001
        )


@pytest.mark.unit
def test_days_to_expiry_is_not_a_stored_field() -> None:
    """Storing it would guarantee it goes stale."""
    from app.domain.futures.contract import FuturesContract

    assert "days_to_expiry" not in set(FuturesContract.__slots__)


@pytest.mark.unit
def test_settlement_and_session_carry_provenance_when_supplied() -> None:
    subject = contract(settlement=SettlementType.CASH, trading_session="fixture session")
    assert subject.settlement is not None
    assert subject.settlement.status is VerificationStatus.TEST_FIXTURE
    assert subject.trading_session is not None
    assert subject.trading_session.status is VerificationStatus.TEST_FIXTURE
