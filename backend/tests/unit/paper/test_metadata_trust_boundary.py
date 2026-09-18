"""Test contract metadata may never become production contract metadata.

Phase 9 closeout section 8. The lifecycle proofs need a contract whose
multiplier is treated as a verified fact, and they get one by declaring it so
at the call site in the test suite. That is only safe if there is no path from
a running deployment to such a fixture: not through a request field, a query
parameter, a symbol, an environment variable, a URL or a frontend flag.

These tests hold the boundary from both sides - what the composition root wires,
and what the request surface can reach.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes.paper import get_product_resolver
from app.api.schemas.paper import CreatePaperPositionBody
from app.core.config import Settings
from app.domain.common.verification import VerificationStatus
from app.main import create_app

APP_ROOT = Path(__file__).resolve().parents[3] / "app"
PRODUCTION_FILES = sorted(APP_ROOT.rglob("*.py"))


class TestOnlyOneStatusBuysTrust:
    def test_a_test_fixture_status_is_never_authoritative(self) -> None:
        assert VerificationStatus.VERIFIED_CURRENT_FACT.is_authoritative is True
        for status in VerificationStatus:
            if status is not VerificationStatus.VERIFIED_CURRENT_FACT:
                assert status.is_authoritative is False

    def test_the_fixture_contract_is_trusted_only_because_a_test_says_so(self) -> None:
        """The fixture's status is chosen in the test suite, never shipped."""
        from tests.factories_paper import paper_contract

        contract = paper_contract()
        assert contract.multiplier.status is VerificationStatus.VERIFIED_CURRENT_FACT
        source = inspect.getsourcefile(type(contract))
        assert source is not None and "tests" not in Path(source).parts
        # ...but the value itself comes from the test package.
        assert "tests" in Path(inspect.getsourcefile(paper_contract) or "").parts


class TestProductionComposesNoResolver:
    def test_the_composition_root_wires_no_product_resolver(self) -> None:
        app = create_app()
        with TestClient(app):
            assert app.state.product_resolver is None
            assert app.state.paper_store is not None
            assert app.state.product_codec is not None

    @pytest.mark.parametrize(
        "variable",
        [
            "PAPER_PRODUCT_RESOLVER",
            "PRODUCT_RESOLVER",
            "CONTRACT_METADATA_PROVIDER",
            "PAPER_TEST_FIXTURES",
            "USE_FIXTURE_METADATA",
            "APP_ENV",
            "PAPER_FIXTURE_CONTRACTS",
            "VIOP_CONTRACT_MULTIPLIER",
        ],
    )
    def test_no_environment_variable_switches_on_a_provider(
        self, monkeypatch: pytest.MonkeyPatch, variable: str
    ) -> None:
        monkeypatch.setenv(variable, "1" if variable != "APP_ENV" else "development")
        app = create_app()
        with TestClient(app) as client:
            assert app.state.product_resolver is None
            response = client.post(
                "/api/paper/positions",
                headers={"Idempotency-Key": "trust-boundary-key-0001"},
                json=_plan(),
            )
        # Refused, never created. Which refusal depends on whether this unit
        # environment can reach a database at all; the point is that no
        # environment variable produced a resolver. The runtime E2E proves the
        # deployed stack answers PRODUCT_METADATA_UNAVAILABLE.
        assert response.status_code in (422, 503)
        assert response.json()["detail"]["code"] in (
            "PRODUCT_METADATA_UNAVAILABLE",
            "PAPER_STORE_UNAVAILABLE",
        )

    def test_the_resolver_dependency_reads_application_state_and_nothing_else(self) -> None:
        parameters = list(inspect.signature(get_product_resolver).parameters)
        assert parameters == ["request"]
        body = inspect.getsource(get_product_resolver)
        assert "app.state" in body
        for reachable in ("query_params", "headers", "path_params", "json", "environ", "getenv"):
            assert reachable not in body

    def test_settings_expose_no_product_metadata_switch(self) -> None:
        fields = set(Settings.model_fields)
        for name in fields:
            assert "fixture" not in name
            assert "resolver" not in name
            assert "multiplier" not in name

    def test_no_request_field_can_carry_product_facts(self) -> None:
        fields = set(CreatePaperPositionBody.model_fields)
        for forbidden in (
            "multiplier",
            "tick_size",
            "point_value",
            "margin",
            "contract",
            "product",
            "resolver",
            "metadata",
            "asset_class",
            "provenance",
        ):
            assert forbidden not in fields
        assert CreatePaperPositionBody.model_config["extra"] == "forbid"


class TestProductionCodeCannotReachTheTestSuite:
    def test_no_production_module_imports_the_test_package(self) -> None:
        offenders = [
            path.relative_to(APP_ROOT).as_posix()
            for path in PRODUCTION_FILES
            if "import tests" in path.read_text(encoding="utf-8")
            or "from tests" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_no_production_module_builds_a_fixture_contract(self) -> None:
        """A shipped module may describe fixtures; none may construct one."""
        banned = ("paper_contract(", "factories_paper", "factories_futures", "FakeProduct")
        offenders = [
            (path.relative_to(APP_ROOT).as_posix(), name)
            for path in PRODUCTION_FILES
            for name in banned
            if name in path.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_no_production_module_hands_out_verified_status_for_contract_facts(self) -> None:
        """Only a provider's own data may claim VERIFIED_CURRENT_FACT."""
        allowed = {
            "domain/common/verification.py",  # defines the vocabulary
            "domain/analysis/evidence.py",  # reads a status, never assigns one
        }
        offenders = []
        for path in PRODUCTION_FILES:
            relative = path.relative_to(APP_ROOT).as_posix()
            if relative in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("#", '"', "'", "*")):
                    continue
                if "VerificationStatus.VERIFIED_CURRENT_FACT" in stripped and "=" in stripped:
                    offenders.append((relative, stripped[:80]))
        assert offenders == []


def _plan() -> dict[str, object]:
    return {
        "symbol": "TEST_FIXTURE_FUT",
        "direction": "LONG",
        "quantity": 2,
        "intended_entry": "100",
        "stop": "98",
        "targets": [{"price": "104", "quantity": 2}],
        "timeframe": "1H",
        "decision_time": "2026-03-02T10:00:00Z",
        "account": {"equity": "100000", "used_margin": "0"},
        "risk": {"mode": "FIXED", "fixed_risk": "1000"},
        "simulation": {
            "same_bar": "STOP_FIRST",
            "slippage_mode": "ZERO",
            "fee_mode": "NOT_MODELLED",
        },
    }
