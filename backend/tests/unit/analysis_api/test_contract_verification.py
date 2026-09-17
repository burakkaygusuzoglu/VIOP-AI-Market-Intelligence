"""Unverified contract metadata must never drive a calculation (§10, §48.12).

A wrong multiplier produces a position size that looks completely normal. That
is exactly why master spec §118 exists, and why "the provider had a number" is
not the same as "the number is a current verified fact".

These tests exist because a mutation probe found the gap: replacing the
verification check with `contract is not None` left the whole suite green. No
test had ever supplied a contract that *existed but was unverified*, because the
default deployment composes no contract provider at all — so every risk path was
being exercised through the "no contract" branch.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.routes.analysis import get_contract_metadata
from app.core.config import Settings
from app.domain.common.verification import VerificationStatus, VerifiedValue
from app.domain.futures.contract import FuturesContract
from app.main import create_app
from tests.unit.analysis_api.test_analysis_api import body

SYMBOL = "TEST_FIXTURE_FUT"


def contract(status: VerificationStatus, *, margin: bool = False) -> FuturesContract:
    """A structurally valid contract whose facts carry ``status``.

    ``margin`` decides whether an initial margin is supplied. Without one the
    sizing engine can compute a risk-based maximum but cannot state a final
    allowance, which is a *third* outcome and not a refusal - see
    `test_verified_metadata_without_margin_is_undetermined`.
    """

    def fact(value: Decimal) -> VerifiedValue[Decimal]:
        return VerifiedValue(
            value=value,
            status=status,
            source="test fixture",
            as_of=datetime(2026, 1, 1, tzinfo=UTC),
        )

    return FuturesContract(
        symbol=SYMBOL,
        underlying_symbol=SYMBOL,
        contract_name="fixture contract",
        multiplier=fact(Decimal("10")),
        tick_size=fact(Decimal("0.25")),
        initial_margin=fact(Decimal("5000")) if margin else None,
    )


class StubContracts:
    """A `ContractMetadataProvider` that returns exactly what it was given.

    It preserves the status it was constructed with. A provider that promoted
    UNVERIFIED to VERIFIED_CURRENT_FACT would defeat the whole mechanism, so
    the stub deliberately cannot.
    """

    def __init__(self, item: FuturesContract | None) -> None:
        self._item = item

    async def get_contract(self, symbol: str) -> FuturesContract | None:
        return self._item if self._item is not None and symbol == self._item.symbol else None

    async def list_symbols(self) -> Sequence[str]:
        return [self._item.symbol] if self._item is not None else []


def make_client(provider: StubContracts) -> Iterator[TestClient]:
    settings = Settings(
        app_env="test",
        app_version="0.0.0-test",
        postgres_host="localhost",
        postgres_port=5432,
        postgres_user="viop",
        postgres_password=SecretStr("fixture-password"),  # TEST_FIXTURE value
        postgres_db="viop_test",
    )
    app = create_app(settings)
    app.dependency_overrides[get_contract_metadata] = lambda: provider
    with TestClient(app) as client:
        yield client


def sized_body() -> dict[str, object]:
    """Everything sizing needs except a trustworthy contract."""
    return body(
        symbol=SYMBOL,
        account={"equity": "100000", "used_margin": "0"},
        risk={"mode": "FIXED", "fixed_risk": "1000"},
        entry_price="120.00",
        stop_price="118.00",
    )


@pytest.mark.parametrize(
    "status",
    [
        VerificationStatus.UNVERIFIED,
        VerificationStatus.DEVELOPMENT_DEFAULT,
        VerificationStatus.TEST_FIXTURE,
        VerificationStatus.MOCK_DATA,
    ],
)
def test_unverified_metadata_blocks_sizing(status: VerificationStatus) -> None:
    """Every non-authoritative status refuses to size a position."""
    for client in make_client(StubContracts(contract(status))):
        payload = client.post("/api/analysis", json=sized_body()).json()

    assert payload["risk"]["available"] is False
    assert payload["risk"]["outcome"] == "UNAVAILABLE"
    assert payload["identity"]["contract_metadata_verified"] is False
    assert payload["risk"]["facts"] == []


def test_unverified_metadata_says_why() -> None:
    for client in make_client(StubContracts(contract(VerificationStatus.UNVERIFIED))):
        payload = client.post("/api/analysis", json=sized_body()).json()

    reasons = " ".join(payload["risk"]["unavailable_reasons"])
    assert "doğrulanmamış" in reasons.lower()


def test_the_unverified_contract_is_still_reported_with_its_status() -> None:
    """Shown, and shown as unverified.

    Hiding it would be worse: a user who supplied a symbol deserves to know the
    system found a specification for it and is refusing to trust it.
    """
    for client in make_client(StubContracts(contract(VerificationStatus.UNVERIFIED))):
        payload = client.post("/api/analysis", json=sized_body()).json()

    reported = payload["risk"]["contract"]
    assert reported is not None
    assert reported["verified"] is False
    assert reported["multiplier_status"] == "UNVERIFIED"


def test_verified_metadata_permits_a_real_sizing() -> None:
    """The other side of the same rule.

    Without this the parametrised test above would pass with a check that
    rejected everything, which is not the property being asserted.
    """
    verified = contract(VerificationStatus.VERIFIED_CURRENT_FACT, margin=True)
    for client in make_client(StubContracts(verified)):
        payload = client.post("/api/analysis", json=sized_body()).json()

    assert payload["identity"]["contract_metadata_verified"] is True
    assert payload["risk"]["available"] is True
    assert payload["risk"]["outcome"] in {"ALLOWED", "NOT_PERMITTED"}
    assert payload["risk"]["contract"]["verified"] is True


def test_a_verified_contract_produces_calculated_facts() -> None:
    verified = contract(VerificationStatus.VERIFIED_CURRENT_FACT, margin=True)
    for client in make_client(StubContracts(verified)):
        payload = client.post("/api/analysis", json=sized_body()).json()

    facts = {item["id"]: item for item in payload["risk"]["facts"]}
    assert "FACT-ALLOWED_CONTRACTS" in facts
    assert facts["FACT-ALLOWED_CONTRACTS"]["source"] == "CALCULATED"


def test_verified_metadata_without_margin_is_undetermined_not_refused() -> None:
    """A missing margin specification stops the calculation; it does not refuse.

    Found while writing these tests. The projection reported this case as
    NOT_PERMITTED, which tells a user their trade was rejected when in fact an
    input was missing - the same collapse §32 forbids between "0 contracts" and
    "no answer".
    """
    verified = contract(VerificationStatus.VERIFIED_CURRENT_FACT, margin=False)
    for client in make_client(StubContracts(verified)):
        payload = client.post("/api/analysis", json=sized_body()).json()

    risk = payload["risk"]
    assert risk["outcome"] == "UNDETERMINED"
    assert risk["available"] is False
    assert risk["unavailable_reasons"]
    assert not any(item["id"] == "FACT-ALLOWED_CONTRACTS" for item in risk["facts"])


def test_an_unknown_symbol_gets_no_contract_at_all() -> None:
    """No defaults. A symbol the provider never heard of has no specification."""
    for client in make_client(StubContracts(contract(VerificationStatus.VERIFIED_CURRENT_FACT))):
        payload = client.post("/api/analysis", json=body(symbol="SOME_OTHER_CODE")).json()

    assert payload["risk"]["contract"] is None
    assert payload["identity"]["contract_metadata_verified"] is False
