"""Test composition can never become financial authority (Phase 12).

Phase 12's tests open thousands of simulated positions, and every one of them
is priced against a contract whose facts came from a fixture. That is fine -
and it is only fine because the path that supplies those facts does not exist
outside the test suite. This file holds that line.

Three things are being kept apart, and the distinction is the whole point:

**A. Verified product facts the financial engine requires.** A multiplier, a
tick size, a margin. `size_for_product` and the P&L engine refuse to compute on
anything whose `VerificationStatus` is not `VERIFIED_CURRENT_FACT`.

**B. Test-only composition that supplies controlled authoritative facts.** The
tests build a `ManualContractMetadataProvider` holding a contract whose values
carry `VERIFIED_CURRENT_FACT`. This is a *deliberate* lie, confined to the test
process: it is how a deterministic suite exercises engines that would otherwise
refuse to run at all. It is authoritative to the engine and to nobody else.

**C. `TEST_FIXTURE` provenance, which must never become authority.** A value
labelled `TEST_FIXTURE` is refused by the engines by construction. It is not a
weaker kind of fact; it is a non-fact wearing a label that says so.

The danger is not that (B) exists. It is that (B) could be reachable from a
running deployment through a setting, a request field, a symbol or something
the frontend sends. These tests assert it is not, and that a production
deployment with no provider refuses financial backtesting outright.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.common.verification import VerificationStatus

pytestmark = pytest.mark.unit

APP_ROOT = Path(__file__).resolve().parents[3] / "app"
PRODUCTION_FILES = sorted(APP_ROOT.rglob("*.py"))

BACKTEST_MODULES = sorted(
    [
        *(APP_ROOT / "domain" / "backtest").rglob("*.py"),
        *(APP_ROOT / "application" / "backtest").rglob("*.py"),
        APP_ROOT / "adapters" / "persistence" / "backtest_models.py",
        APP_ROOT / "adapters" / "persistence" / "backtest_store.py",
        APP_ROOT / "adapters" / "performance" / "backtest_source.py",
    ]
)


def source_of(paths: list[Path]) -> list[tuple[str, str]]:
    return [
        (path.relative_to(APP_ROOT).as_posix(), path.read_text(encoding="utf-8")) for path in paths
    ]


class TestNoShippedModuleBuildsAFixtureProduct:
    def test_the_backtest_modules_exist_and_are_being_checked(self) -> None:
        """A guard whose file list silently emptied would pass forever."""
        assert len(BACKTEST_MODULES) >= 8

    def test_no_backtest_module_imports_the_test_package(self) -> None:
        offenders = [
            name
            for name, text in source_of(BACKTEST_MODULES)
            if "import tests" in text or "from tests" in text
        ]
        assert offenders == []

    def test_no_backtest_module_constructs_a_fixture_contract(self) -> None:
        banned = (
            "paper_contract(",
            "factories_paper",
            "factories_futures",
            "factories_replay",
            "FakeProduct",
            "ScriptedStrategy",
        )
        offenders = [
            (name, needle)
            for name, text in source_of(BACKTEST_MODULES)
            for needle in banned
            if needle in text
        ]
        assert offenders == []

    def test_no_backtest_module_composes_a_manual_metadata_provider(self) -> None:
        """The provider that supplies controlled facts is test-only composition."""
        offenders = [
            name
            for name, text in source_of(BACKTEST_MODULES)
            if "ManualContractMetadataProvider" in text
        ]
        assert offenders == []

    def test_no_backtest_module_mints_a_verified_status(self) -> None:
        """Only a provider's own data may claim to be a current market fact."""
        offenders = [
            name for name, text in source_of(BACKTEST_MODULES) if "VERIFIED_CURRENT_FACT" in text
        ]
        assert offenders == []


class TestProductionNeverComposesAProvider:
    def test_the_composition_root_sets_no_product_resolver(self) -> None:
        """`None` is categorical here, not the false branch of a condition.

        Asserting that the ``= None`` line *exists* is not enough, and a
        mutation sweep proved it: appending a second assignment that hands out
        a real resolver leaves that line untouched and the test green. So what
        is checked is that ``product_resolver`` is assigned **exactly once**,
        and that the one assignment is ``None``.
        """
        main = (APP_ROOT / "main.py").read_text(encoding="utf-8")
        assignments = [
            line.strip()
            for line in main.splitlines()
            if "product_resolver" in line and "=" in line and not line.strip().startswith("#")
        ]

        assert assignments == ["application.state.product_resolver = None"]
        assert "ManualContractMetadataProvider" not in main

    def test_no_shipped_module_instantiates_the_manual_provider(self) -> None:
        """It is defined and exported. Nothing in `app/` ever builds one."""
        offenders = [
            name
            for name, text in source_of(PRODUCTION_FILES)
            if "ManualContractMetadataProvider(" in text
        ]
        assert offenders == []

    def test_no_setting_can_switch_metadata_composition_on(self) -> None:
        """There is no flag to find, so there is no flag to set by accident."""
        config = (APP_ROOT / "core" / "config.py").read_text(encoding="utf-8")
        for needle in (
            "fixture",
            "manual_contract",
            "contract_metadata",
            "product_resolver",
            "allow_unverified",
        ):
            assert needle not in config.lower()

    def test_the_resolver_dependency_accepts_only_a_real_resolver(self) -> None:
        """A value smuggled onto app state is type-checked before it is used."""
        routes = (APP_ROOT / "api" / "routes" / "paper.py").read_text(encoding="utf-8")

        assert "isinstance(resolver, ProductResolver)" in routes


class TestARunWithoutAProviderIsRefused:
    def test_the_runner_refuses_rather_than_defaults(self) -> None:
        """Read from the source, because the behaviour is a refusal *path*."""
        service = (APP_ROOT / "application" / "backtest" / "service.py").read_text(encoding="utf-8")

        assert "PRODUCT_METADATA_UNAVAILABLE" in service
        assert "reported with assumed specifications" in service

    def test_no_default_contract_exists_to_fall_back_to(self) -> None:
        offenders = [
            name for name, text in source_of(BACKTEST_MODULES) if "DEVELOPMENT_DEFAULT" in text
        ]
        assert offenders == []


class TestTheVocabularyItselfStillRefuses:
    @pytest.mark.parametrize(
        "status",
        [
            VerificationStatus.TEST_FIXTURE,
            VerificationStatus.MOCK_DATA,
            VerificationStatus.DEVELOPMENT_DEFAULT,
            VerificationStatus.UNVERIFIED,
        ],
    )
    def test_only_a_current_fact_is_authoritative(self, status: VerificationStatus) -> None:
        assert not status.is_authoritative

    def test_and_a_current_fact_is(self) -> None:
        assert VerificationStatus.VERIFIED_CURRENT_FACT.is_authoritative
