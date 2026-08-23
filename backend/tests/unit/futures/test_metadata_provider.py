"""The contract metadata port and its manual adapter.

The central assertion of this module is negative: **no VIOP fact is hard-coded
anywhere in the source tree**, and the provider cannot manufacture one by
upgrading a verification status.

All contract values here are TEST_FIXTURE data.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.adapters.contract_metadata import (
    DuplicateContractError,
    ManualContractMetadataProvider,
)
from app.application.ports.contract_metadata import ContractMetadataProvider
from app.domain.common.verification import VerificationStatus
from app.domain.futures.validation import ContractIssueCode
from tests.factories_futures import contract, expiry_on, unverified, verified


@pytest.mark.unit
def test_the_manual_provider_satisfies_the_port() -> None:
    assert isinstance(ManualContractMetadataProvider(), ContractMetadataProvider)


@pytest.mark.unit
async def test_it_serves_exactly_what_was_registered() -> None:
    subject = contract(symbol="FIXTURE_A", multiplier=verified(100))
    provider = ManualContractMetadataProvider([subject])
    assert await provider.get_contract("FIXTURE_A") is subject


@pytest.mark.unit
async def test_an_unknown_symbol_returns_none_not_a_default_contract() -> None:
    """``None`` must never be a stand-in for a contract built from defaults."""
    provider = ManualContractMetadataProvider()
    assert await provider.get_contract("ANYTHING") is None


@pytest.mark.unit
async def test_it_starts_empty_with_no_contracts_shipped() -> None:
    """No production VIOP contract ships in this repository."""
    provider = ManualContractMetadataProvider()
    assert await provider.list_symbols() == ()


@pytest.mark.unit
async def test_provenance_survives_a_round_trip_untouched() -> None:
    """The one thing the adapter must never do: promote a status.

    A provider that turns UNVERIFIED into VERIFIED_CURRENT_FACT - because the
    value looked plausible, or because something downstream needed one -
    defeats the entire section 118 mechanism.
    """
    registered = contract(
        symbol="FIXTURE_B",
        multiplier=unverified(100),
        initial_margin=unverified(250),
    )
    provider = ManualContractMetadataProvider([registered])

    served = await provider.get_contract("FIXTURE_B")
    assert served is not None
    assert served.multiplier.status is VerificationStatus.UNVERIFIED
    assert served.initial_margin is not None
    assert served.initial_margin.status is VerificationStatus.UNVERIFIED
    assert served.authoritative_multiplier() is None


@pytest.mark.unit
def test_registration_reports_what_is_unverified_without_refusing_the_contract() -> None:
    provider = ManualContractMetadataProvider()
    issues = provider.register(contract(symbol="FIXTURE_C", multiplier=unverified(100)))
    assert ContractIssueCode.UNVERIFIED_MULTIPLIER in {issue.code for issue in issues}


@pytest.mark.unit
def test_a_structurally_impossible_contract_never_reaches_the_provider() -> None:
    from app.domain.futures.contract import ContractValidationError

    with pytest.raises(ContractValidationError):
        contract(multiplier=verified("-1"))


@pytest.mark.unit
def test_registering_a_symbol_twice_is_refused() -> None:
    """Silent replacement would make the in-force spec depend on import order."""
    provider = ManualContractMetadataProvider([contract(symbol="FIXTURE_D")])
    with pytest.raises(DuplicateContractError):
        provider.register(contract(symbol="FIXTURE_D"))


@pytest.mark.unit
async def test_unregister_removes_a_contract() -> None:
    provider = ManualContractMetadataProvider([contract(symbol="FIXTURE_E")])
    provider.unregister("FIXTURE_E")
    assert await provider.get_contract("FIXTURE_E") is None


@pytest.mark.unit
def test_the_verification_summary_shows_which_facts_are_trustworthy() -> None:
    provider = ManualContractMetadataProvider(
        [
            contract(
                symbol="FIXTURE_F",
                multiplier=verified(100),
                initial_margin=unverified(250),
                expiry=expiry_on(date(2026, 6, 30)),
            )
        ]
    )
    summary = provider.verification_summary()["FIXTURE_F"]
    assert summary["multiplier"] is VerificationStatus.VERIFIED_CURRENT_FACT
    assert summary["initial_margin"] is VerificationStatus.UNVERIFIED
    assert summary["expiry_date"] is VerificationStatus.VERIFIED_CURRENT_FACT


@pytest.mark.unit
async def test_symbols_are_listed_deterministically() -> None:
    provider = ManualContractMetadataProvider([contract(symbol="ZZZ"), contract(symbol="AAA")])
    assert await provider.list_symbols() == ("AAA", "ZZZ")


@pytest.mark.unit
def test_the_adapter_imports_nothing_that_could_reach_a_network() -> None:
    """Master spec section 120: no scraping, no broker interface, no secrets.

    Checked by parsing the module's imports rather than grepping its text -
    the docstring *explains* that credentials are not needed, and a naive
    substring search flags that explanation as a violation.
    """
    import ast

    root = Path(__file__).resolve().parents[3]
    source = root / (ManualContractMetadataProvider.__module__.replace(".", "/") + ".py")
    tree = ast.parse(source.read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden = {
        "http",
        "httpx",
        "requests",
        "urllib",
        "urllib3",
        "socket",
        "aiohttp",
        "selenium",
        "playwright",
        "ftplib",
        "smtplib",
    }
    assert not (imported & forbidden), imported & forbidden
    assert imported <= {"collections", "app", "__future__"}, imported


# ----------------------------------------------------------------------
# Section 118: no exchange fact is hard-coded anywhere in the source
# ----------------------------------------------------------------------


SOURCE_ROOT = Path(__file__).resolve().parents[3] / "app"


@pytest.mark.unit
def test_no_viop_instrument_name_appears_in_the_source_tree() -> None:
    """A contract code in the source would be a remembered exchange fact.

    The engines are instrument-agnostic: every specification arrives through
    the provider at runtime.
    """
    forbidden = ("XU030", "XU100", "BIST30", "BIST100", "F_XU030", "F_XU100")
    for path in SOURCE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8").upper()
        for needle in forbidden:
            assert needle not in text, f"{path.name} mentions {needle}"


@pytest.mark.unit
def test_no_module_defines_a_default_multiplier_tick_or_margin() -> None:
    """Section 118: these must arrive from a provider, never from a constant."""
    forbidden = (
        "DEFAULT_MULTIPLIER",
        "DEFAULT_TICK_SIZE",
        "DEFAULT_TICK_VALUE",
        "DEFAULT_MARGIN",
        "DEFAULT_INITIAL_MARGIN",
        "DEFAULT_COMMISSION",
        "STANDARD_MULTIPLIER",
        "TRADING_DAYS_PER_YEAR",
    )
    for path in SOURCE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8").upper()
        for needle in forbidden:
            assert needle not in text, f"{path.name} defines {needle}"


@pytest.mark.unit
def test_no_annualisation_constant_is_hard_coded() -> None:
    """252, 365 and 360 as bare day counts are calendar assumptions.

    Checked over the futures and risk packages, where such a constant would
    silently annualise something. The word may appear in prose explaining why
    it is absent - that is the point - so only assignments are searched.
    """
    import re

    pattern = re.compile(r"=\s*(252|365|360)\b")
    for package in ("futures", "risk"):
        for path in (SOURCE_ROOT / "domain" / package).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert not pattern.search(text), f"{path.name} assigns a calendar constant"


@pytest.mark.unit
def test_decimal_is_used_for_money_and_float_is_not() -> None:
    """No binary float may reach a monetary calculation."""
    for package in ("futures", "risk"):
        for path in (SOURCE_ROOT / "domain" / package).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "float(" not in text, f"{path.name} converts to float"
            assert ": float" not in text, f"{path.name} annotates a float"
