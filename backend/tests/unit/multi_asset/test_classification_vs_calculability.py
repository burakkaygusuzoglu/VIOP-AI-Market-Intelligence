"""Three facts that must never stand in for one another (Phase 8.5 closeout).

1. **Classification** - what product class an instrument is, as established by
   the source that says so.
2. **Metadata availability** - whether the facts a calculation needs exist.
3. **Calculability** - whether those facts are authoritative enough to use.

And beside them, **capability** - what a product *type* can structurally have.

A Phase 8.5 draft derived (1) from (2) and (3): a verified multiplier and tick
size made the futures classification "verified", and an unverified tick size
made it "unverified". Review caught it. These tests pin the separation from
every direction, and pin the trust boundary that decides which product policy
is used at all.
"""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.routes.analysis import get_contract_metadata
from app.api.schemas.analysis import AnalysisRequestBody
from app.core.config import Settings
from app.domain.common.enums import Direction
from app.domain.common.verification import (
    UnverifiedFinancialFactError,
    VerificationStatus,
    VerifiedValue,
)
from app.domain.futures.contract import ContractExpiry, ContractValidationError, FuturesContract
from app.domain.futures.policy import FUTURES_CAPABILITIES, FuturesProductPolicy
from app.domain.futures.risk import assess_margin, calculate_contract_pnl, size_position
from app.domain.futures.validation import ContractIssueCode, contract_issues, contract_state
from app.domain.instrument import (
    AssetClass,
    InstrumentId,
    MarginFeasibility,
    Support,
    TickFeasibility,
)
from app.domain.risk.margin import RiskWarningCode
from app.domain.risk.sizing import AccountState, RiskMode, RiskPolicy, SizingOutcome
from app.main import create_app
from tests.unit.analysis_api.test_analysis_api import body

APP = Path(__file__).resolve().parents[3] / "app"
AS_OF = datetime(2026, 1, 1, tzinfo=UTC)
V = VerificationStatus
SYMBOL = "TEST_FIXTURE_FUT"


def fact[T](value: T, status: VerificationStatus = V.VERIFIED_CURRENT_FACT) -> VerifiedValue[T]:
    return VerifiedValue(value=value, status=status, source="fixture source", as_of=AS_OF)


def record(
    *,
    classification: VerificationStatus | None = None,
    multiplier: VerificationStatus = V.VERIFIED_CURRENT_FACT,
    tick: VerificationStatus = V.VERIFIED_CURRENT_FACT,
    margin: VerificationStatus | None = V.VERIFIED_CURRENT_FACT,
    expiry: ContractExpiry | None = None,
) -> FuturesContract:
    return FuturesContract(
        symbol=SYMBOL,
        underlying_symbol="TEST_FIXTURE_UNDERLYING",
        contract_name="fixture",
        multiplier=fact(Decimal("10"), multiplier),
        tick_size=fact(Decimal("0.25"), tick),
        initial_margin=fact(Decimal("5000"), margin) if margin is not None else None,
        expiry=expiry,
        classification=(
            fact(AssetClass.FUTURES, classification) if classification is not None else None
        ),
    )


ACCOUNT = AccountState(equity=Decimal("100000"))
BUDGET = RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("1000"))


def classification_of(item: FuturesContract) -> VerifiedValue[AssetClass]:
    return FuturesProductPolicy(item).instrument.asset_class


def sized(item: FuturesContract, entry: str = "120.00", stop: str = "118.00") -> Any:
    return size_position(Direction.LONG, Decimal(entry), Decimal(stop), item, ACCOUNT, BUDGET)


# ======================================================================
# 1. Classification is independent of calculability (review §2 A-F)
# ======================================================================


class TestClassificationFollowsItsOwnSource:
    def test_a_trusted_class_and_complete_metadata_calculate(self) -> None:
        """A. Both facts present and authoritative: trusted, and calculable."""
        item = record(classification=V.VERIFIED_CURRENT_FACT)

        assert classification_of(item).status is V.VERIFIED_CURRENT_FACT
        assert sized(item).outcome is SizingOutcome.ALLOWED

    @pytest.mark.parametrize("multiplier", [V.UNVERIFIED, V.TEST_FIXTURE, V.MOCK_DATA])
    def test_b_a_trusted_class_survives_an_unusable_multiplier(
        self, multiplier: VerificationStatus
    ) -> None:
        """B. The class is untouched; what needs the multiplier stops."""
        item = record(classification=V.VERIFIED_CURRENT_FACT, multiplier=multiplier)

        assert classification_of(item).status is V.VERIFIED_CURRENT_FACT
        assert sized(item).outcome is SizingOutcome.INVALID
        with pytest.raises(UnverifiedFinancialFactError):
            calculate_contract_pnl(item, Direction.LONG, Decimal("120"), Decimal("121"), 1)

    def test_b_a_multiplier_cannot_be_absent_from_a_futures_record(self) -> None:
        """B. "Missing" is not representable: the record refuses to exist without
        one, so an absent multiplier can never reach a calculation as zero."""
        fields = {field.name: field for field in dataclasses.fields(FuturesContract)}

        assert fields["multiplier"].default is dataclasses.MISSING

    @pytest.mark.parametrize("tick", [V.UNVERIFIED, V.DEVELOPMENT_DEFAULT])
    def test_c_a_trusted_class_survives_an_unverified_tick(self, tick: VerificationStatus) -> None:
        """C. The class is not silently changed; the tick-dependent allowance is."""
        item = record(classification=V.VERIFIED_CURRENT_FACT, tick=tick)
        sizing = sized(item)

        assert classification_of(item).status is V.VERIFIED_CURRENT_FACT
        assert sizing.tick_feasibility is TickFeasibility.UNVERIFIED
        assert sizing.outcome is SizingOutcome.UNDETERMINED
        assert sizing.allowed_contracts is None

    def test_c_a_trusted_class_survives_missing_margin(self) -> None:
        item = record(classification=V.VERIFIED_CURRENT_FACT, margin=None)

        assert classification_of(item).status is V.VERIFIED_CURRENT_FACT
        assert sized(item).outcome is SizingOutcome.UNDETERMINED

    def test_d_verified_numbers_do_not_establish_a_class(self) -> None:
        """D. Every numeric fact verified, no classification source: not verified."""
        item = record(classification=None)
        classification = classification_of(item)

        assert classification.value is AssetClass.FUTURES
        assert classification.status is V.UNVERIFIED
        assert not classification.is_authoritative
        assert "specification facts do not establish" in (classification.note or "")

    @pytest.mark.parametrize("claimed", [V.TEST_FIXTURE, V.MOCK_DATA, V.DEVELOPMENT_DEFAULT])
    def test_d_verified_numbers_do_not_upgrade_a_weak_classification(
        self, claimed: VerificationStatus
    ) -> None:
        item = record(classification=claimed)

        assert classification_of(item).status is claimed

    def test_d_calculability_is_unchanged_by_classification_status(self) -> None:
        """The sizing answer is the Phase 8 answer whether or not a class source exists.

        Gating calculations on a verified classification would change existing
        VİOP outputs, which this closeout does not do without human review.
        """
        with_source = sized(record(classification=V.VERIFIED_CURRENT_FACT))
        without = sized(record(classification=None))

        assert with_source == without

    def test_e_a_typed_futures_class_is_unverified(self) -> None:
        declared = InstrumentId.user_declared(SYMBOL, AssetClass.FUTURES)

        assert declared.asset_class.status is V.UNVERIFIED
        assert declared.asset_class.source == "user input"

    def test_f_a_futures_record_cannot_be_relabelled_as_another_class(self) -> None:
        """F. No record can claim equity while carrying futures arithmetic."""
        with pytest.raises(ContractValidationError, match="cannot be classified"):
            dataclasses.replace(record(), classification=fact(AssetClass.EQUITY))

    def test_a_classification_claiming_currency_without_a_source_is_blocking(self) -> None:
        """The classification is a fact like any other: an unsourced claim of
        verification is caught by the existing provenance checks."""
        item = dataclasses.replace(
            record(),
            classification=VerifiedValue(
                value=AssetClass.FUTURES, status=V.VERIFIED_CURRENT_FACT, source="  ", as_of=AS_OF
            ),
        )
        codes = {issue.code for issue in contract_issues(item) if issue.blocking}

        assert ContractIssueCode.UNSOURCED_VERIFIED_FACT in codes


# ======================================================================
# 2. Capability is not availability (review §3)
# ======================================================================


class TestCapabilityIsNotAvailability:
    def test_supported_margin_coexists_with_missing_margin_and_no_calculation(self) -> None:
        item = record(classification=V.VERIFIED_CURRENT_FACT, margin=None)
        policy = FuturesProductPolicy(item)

        assert policy.capabilities.margin is Support.SUPPORTED  # the product type
        assert policy.margin_requirement().feasibility is MarginFeasibility.MISSING  # the facts
        sizing = sized(item)
        assert sizing.maximum_by_margin is None  # the calculation
        assert sizing.outcome is SizingOutcome.UNDETERMINED

        panel = assess_margin(item, ACCOUNT, Decimal("120"), 1, BUDGET)
        assert panel.required_margin is None
        assert RiskWarningCode.MARGIN_UNKNOWN in {warning.code for warning in panel.warnings}

    def test_supported_margin_with_unverified_margin_is_still_not_calculable(self) -> None:
        item = record(margin=V.UNVERIFIED)

        assert FuturesProductPolicy(item).capabilities.margin is Support.SUPPORTED
        assert FuturesProductPolicy(item).margin_requirement().per_unit is None
        assert sized(item).maximum_by_margin is None

    def test_supported_expiry_coexists_with_unknown_contract_state(self) -> None:
        item = record(expiry=None)
        state = contract_state(item, datetime(2026, 6, 1, tzinfo=UTC))

        assert FuturesProductPolicy(item).capabilities.expiry is Support.SUPPORTED
        assert state.state.value == "UNKNOWN"
        assert state.days_to_expiry is None

    def test_supported_expiry_with_unverified_date_is_still_unknown(self) -> None:
        item = record(expiry=ContractExpiry(expiry_date=fact(date(2026, 6, 30), V.UNVERIFIED)))

        assert contract_state(item, datetime(2026, 7, 5, tzinfo=UTC)).state.value == "UNKNOWN"

    def test_capabilities_do_not_read_metadata(self) -> None:
        """Identical for every metadata state - they describe the type, not the record."""
        states = [
            record(),
            record(margin=None),
            record(multiplier=V.UNVERIFIED, tick=V.UNVERIFIED, margin=V.UNVERIFIED),
            record(classification=V.VERIFIED_CURRENT_FACT),
        ]

        assert {FuturesProductPolicy(item).capabilities for item in states} == {
            FUTURES_CAPABILITIES
        }

    def test_the_policy_never_reads_its_capabilities_to_answer_availability(self) -> None:
        """Mechanical: availability comes from metadata, never from a capability."""
        tree = ast.parse((APP / "domain" / "futures" / "policy.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in {
                "margin_requirement",
                "price_increment_check",
                "point_value",
            }:
                assert "capabilities" not in ast.unparse(node), node.name

    def test_unknown_is_not_unsupported(self) -> None:
        values = [member.value for member in Support]

        assert values == ["SUPPORTED", "UNSUPPORTED", "UNKNOWN"]
        assert len(set(values)) == 3

    def test_margin_feasibility_keeps_missing_and_unverified_apart(self) -> None:
        """Availability (MISSING) and calculability (UNVERIFIED) stay distinct states."""
        missing = FuturesProductPolicy(record(margin=None)).margin_requirement()
        unverified = FuturesProductPolicy(record(margin=V.UNVERIFIED)).margin_requirement()

        assert missing.feasibility is MarginFeasibility.MISSING
        assert unverified.feasibility is MarginFeasibility.UNVERIFIED


# ======================================================================
# 3. The policy-dispatch trust boundary (review §4)
# ======================================================================


class Provider:
    """A server-side contract provider stand-in, holding exactly one record."""

    def __init__(self, item: FuturesContract | None) -> None:
        self._item = item

    async def get_contract(self, symbol: str) -> FuturesContract | None:
        return self._item if self._item is not None and symbol == self._item.symbol else None

    async def list_symbols(self) -> Sequence[str]:
        return [self._item.symbol] if self._item is not None else []


@pytest.fixture
def client_for() -> Iterator[Any]:
    clients: list[Any] = []

    def make(item: FuturesContract | None) -> TestClient:
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
        app.dependency_overrides[get_contract_metadata] = lambda: Provider(item)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def sizing_body(symbol: str, **extra: Any) -> dict[str, Any]:
    return body(
        symbol=symbol,
        account={"equity": "100000", "used_margin": "0"},
        risk={"mode": "FIXED", "fixed_risk": "1000"},
        entry_price="120.00",
        stop_price="118.00",
        **extra,
    )


class TestPolicyDispatchTrustBoundary:
    def test_the_request_has_nowhere_to_declare_a_product(self) -> None:
        assert set(AnalysisRequestBody.model_fields) == {
            "symbol",
            "datasets",
            "account",
            "risk",
            "entry_price",
            "stop_price",
        }
        assert AnalysisRequestBody.model_config.get("extra") == "forbid"

    @pytest.mark.parametrize(
        "declared",
        [
            {"asset_class": "FUTURES"},
            {"product_type": "FUTURES"},
            {"asset_class_status": "VERIFIED_CURRENT_FACT"},
            {"exchange": "VIOP"},
            {"locale": "tr-TR"},
        ],
    )
    def test_a_client_declaration_is_refused(
        self, client_for: Any, declared: dict[str, str]
    ) -> None:
        client = client_for(record())

        response = client.post("/api/analysis", json={**sizing_body(SYMBOL), **declared})

        assert response.status_code == 422

    @pytest.mark.parametrize(
        "symbol",
        ["F_XU0300626", "XU030_FUT", "BTCUSDT-PERP", "EURUSD", "THYAO", "TEST_FIXTURE_FUT "],
    )
    def test_symbol_text_alone_selects_no_policy(self, client_for: Any, symbol: str) -> None:
        """No record for the symbol: no policy, no classification, no size.

        The provider holds a record for a *different* symbol, so every one of
        these - future-looking suffix, perpetual suffix, FX pair, equity ticker,
        a near-miss - has nothing server-side to resolve to.
        """
        client = client_for(dataclasses.replace(record(), symbol="SOME_OTHER_CONTRACT"))

        risk = client.post("/api/analysis", json=sizing_body(symbol)).json()["risk"]

        assert risk["contract"] is None
        assert risk["available"] is False

    def test_no_provider_means_no_policy(self, client_for: Any) -> None:
        risk = client_for(None).post("/api/analysis", json=sizing_body(SYMBOL)).json()["risk"]

        assert risk["contract"] is None
        assert risk["available"] is False

    def test_a_server_record_is_the_only_route_to_a_policy(self, client_for: Any) -> None:
        risk = client_for(record()).post("/api/analysis", json=sizing_body(SYMBOL)).json()["risk"]

        assert risk["contract"]["symbol"] == SYMBOL
        assert risk["outcome"] == "ALLOWED"

    def test_the_api_reports_classification_provenance_independently(self, client_for: Any) -> None:
        """Verified numbers, no classification source: calculated, but UNVERIFIED."""
        unsourced = client_for(record()).post("/api/analysis", json=sizing_body(SYMBOL)).json()
        sourced = (
            client_for(record(classification=V.VERIFIED_CURRENT_FACT))
            .post("/api/analysis", json=sizing_body(SYMBOL))
            .json()
        )

        assert unsourced["risk"]["contract"]["asset_class_status"] == "UNVERIFIED"
        assert sourced["risk"]["contract"]["asset_class_status"] == "VERIFIED_CURRENT_FACT"
        assert unsourced["risk"]["outcome"] == sourced["risk"]["outcome"] == "ALLOWED"

    def test_a_policy_is_constructed_only_from_a_contract_record(self) -> None:
        """Mechanical: every construction site, and what it is built from."""
        sites: list[tuple[str, str]] = []
        for path in sorted(APP.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "FuturesProductPolicy"
                ):
                    sites.append((path.name, ast.unparse(node.args[0])))

        assert {name for name, _ in sites} == {
            "orchestrator.py",
            "analysis_projection.py",
            "risk.py",
            # Phase 9: the paper-trading product adapter. One site wraps the record
            # the server's contract provider returned; the other restores that same
            # record from the snapshot the server persisted when the position
            # opened. Neither can be reached with client input.
            "futures.py",
        }
        assert {argument for _, argument in sites} == {"contract", "record"}

    def test_the_paper_adapter_builds_policies_only_from_provider_or_stored_records(self) -> None:
        """Phase 9. The resolver reads the provider; the codec reads a server-written snapshot."""
        source = (APP / "adapters" / "products" / "futures.py").read_text(encoding="utf-8")
        resolve = source[
            source.index("async def resolve(") : source.index("class FuturesSnapshotCodec")
        ]
        restore = source[source.index("def restore(") :]

        assert "await self._provider.get_contract(symbol)" in resolve
        assert "return None if record is None else FuturesProductPolicy(record)" in resolve
        assert "record = FuturesContract(" in restore
        assert 'snapshot["contract"]' in restore

    def test_the_orchestrator_builds_a_policy_only_after_a_record_was_found(self) -> None:
        source = (APP / "application" / "analysis" / "orchestrator.py").read_text(encoding="utf-8")
        assess = source[source.index("def _assess_risk(") :]

        assert assess.index("contract is None") < assess.index("_product_policy(contract)")
        assert "contract = await _contract_for(symbol, contracts)" in source


# ======================================================================
# 4. Product vocabulary is terminology, not presentation (review §5)
# ======================================================================


class TestVocabularyIsTerminology:
    def test_vocabulary_is_short_ascii_domain_terms(self) -> None:
        words = FuturesProductPolicy(record()).vocabulary

        for term in dataclasses.astuple(words):
            assert term.isascii(), term
            assert len(term.split()) <= 2, term
            assert not any(mark in term for mark in ".!?:;"), term

    def test_it_matches_the_language_of_the_engine_reasons(self) -> None:
        """The risk engine's reasons were English audit text in Phase 3 and still are;
        Turkish user-facing copy is produced by the application and frontend."""
        reason = sized(record()).reason

        assert reason.isascii()
        assert "contract(s)" in reason
