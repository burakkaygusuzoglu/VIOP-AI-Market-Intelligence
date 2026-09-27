"""Licence grants and capability stages (Phase 15 Part 2A, tests A, B, C, R, S).

A declaration, a grant's evidence, a configured adapter, a connection and a
delivery are separate stages; each is required before the next is asked.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from app.adapters.live.mock_stream import ManualClock, MockStreamProvider
from app.application.sourcing.capabilities import capability_matrix
from app.domain.common.verification import VerificationStatus
from app.domain.live.events import MarketCurrency, StreamProvenance, market_currency_of
from app.domain.sourcing.capability import (
    CapabilityStatus,
    DataCategory,
    Delivery,
    ProvenanceRefusedError,
    ProviderDeclaration,
    authorize_provenance,
    category_status,
    provenance_for,
)
from tests.unit.sourcing.support import NOW, GrantedTestProvider, fixture_grant
from tests.unit.sourcing.test_calendar_limits_settings import settings_from_env

pytestmark = pytest.mark.unit

APP = Path(__file__).resolve().parents[3] / "app"
AGE = timedelta(minutes=30)
PROVIDER = GrantedTestProvider.provider_id


def declared(
    delivery: Delivery = Delivery.DELAYED,
    categories: frozenset[DataCategory] = frozenset({DataCategory.MARKET_DATA}),
) -> ProviderDeclaration:
    return ProviderDeclaration(
        PROVIDER,
        categories,
        delivery,
        delay_seconds=900 if delivery is Delivery.DELAYED else None,
    )


def status(category: DataCategory = DataCategory.MARKET_DATA, **kwargs: object) -> CapabilityStatus:
    arguments: dict[str, object] = {
        "declaration": declared(),
        "grant": fixture_grant(Delivery.DELAYED),
        "connected": True,
        "last_delivery_at": NOW - timedelta(minutes=1),
        "max_age": AGE,
        "at": NOW,
    }
    arguments.update(kwargs)
    return category_status(category, **arguments)[0]  # type: ignore[arg-type]


class TestANoLicenceNoTrust:
    def test_a_configured_connected_delivering_provider_without_a_grant_is_not_licensed(
        self,
    ) -> None:
        assert status(grant=None) is CapabilityStatus.NOT_LICENSED

    @pytest.mark.parametrize(
        "grant",
        [
            fixture_grant(status=VerificationStatus.TEST_FIXTURE),
            fixture_grant(status=VerificationStatus.UNVERIFIED),
            fixture_grant(source=" "),
            fixture_grant(as_of=None),
            fixture_grant(valid_until=NOW),
            fixture_grant(provider_id="SOMEONE_ELSE"),
            fixture_grant(Delivery.REAL_TIME),
        ],
    )
    def test_defective_evidence_licenses_nothing(self, grant: object) -> None:
        assert status(grant=grant) is CapabilityStatus.NOT_LICENSED

    def test_a_market_data_grant_licenses_no_other_category(self) -> None:
        both = declared(categories=frozenset({DataCategory.MARKET_DATA, DataCategory.NEWS}))

        assert status(DataCategory.NEWS, declaration=both) is CapabilityStatus.NOT_LICENSED
        assert status(declaration=both) is CapabilityStatus.AVAILABLE

    def test_a_metadata_grant_is_not_metadata_authority(self) -> None:
        """Licensed to receive a vendor's metadata is not a verified fact; facts
        come only from reviewed, referenced records (tests D-G)."""
        import app.domain.sourcing.facts as facts

        assert "LicenceGrant" not in Path(facts.__file__).read_text(encoding="utf-8")


class TestBAForgedGrantCannotOpenProduction:
    def test_no_application_module_builds_a_grant_or_a_real_declaration(self) -> None:
        offenders = [
            path.relative_to(APP).as_posix()
            for path in APP.rglob("*.py")
            if any(
                marker in path.read_text(encoding="utf-8")
                for marker in ("LicenceGrant(", "ProviderDeclaration(", "grant=")
            )
            and path.name
            not in {"capability.py", "capabilities.py", "source_status.py", "session.py"}
        ]
        assert offenders == []
        # capabilities.py and source_status.py only forward a composition's
        # grant; the one composition (Part 2B) passes neither grant nor
        # declaration, so the forwarded grant is always None.
        callers = [
            path.name
            for path in APP.rglob("*.py")
            if "capability_matrix(" in path.read_text(encoding="utf-8")
            and path.name != "capabilities.py"
        ]
        assert callers == ["source_status.py"]
        main = (APP / "main.py").read_text(encoding="utf-8")
        composed = main[main.index("SourceComposition(") :]
        composed = composed[: composed.index(")")]
        assert "grant" not in composed and "declaration" not in composed

    @pytest.mark.parametrize(
        ("model_name", "valid"),
        [
            ("CreateLiveSessionBody", {"source_id": "RD-" + "a" * 32, "timeframes": ["5M"]}),
            ("LiveAnalysisBody", {}),
        ],
    )
    def test_the_live_api_request_models_refuse_a_smuggled_grant(
        self, model_name: str, valid: dict[str, object]
    ) -> None:
        """Part 2C: a *valid* body is accepted, and the same body carrying a
        grant, a provenance or a licence flag is refused for exactly that -
        so the refusal cannot be an unrelated missing field."""
        from pydantic import ValidationError

        import app.api.schemas.live as live

        model = getattr(live, model_name)
        model.model_validate(valid)
        forged = valid | {
            "grant": {"provider_id": PROVIDER},
            "provenance": "REAL_EXCHANGE_LIVE",
            "licensed": True,
        }
        with pytest.raises(ValidationError) as caught:
            model.model_validate(forged)
        assert {error["type"] for error in caught.value.errors()} == {"extra_forbidden"}

    def test_a_correct_looking_grant_for_another_provider_is_refused(self) -> None:
        with pytest.raises(ProvenanceRefusedError) as caught:
            authorize_provenance(
                StreamProvenance.REAL_EXCHANGE_LIVE,
                provider_id="PRODUCTION_PROVIDER",
                grant=fixture_grant(Delivery.REAL_TIME),
                at=NOW,
            )
        assert caught.value.code == "GRANT_FOR_ANOTHER_PROVIDER"


class TestCConfiguredIsNotConnected:
    def test_licensed_but_disconnected_is_unavailable(self) -> None:
        assert status(connected=False) is CapabilityStatus.UNAVAILABLE

    def test_connected_but_silent_is_unavailable(self) -> None:
        assert status(last_delivery_at=None) is CapabilityStatus.UNAVAILABLE

    def test_an_old_delivery_is_stale_not_available(self) -> None:
        assert status(last_delivery_at=NOW - AGE - timedelta(seconds=1)) is (CapabilityStatus.STALE)

    def test_only_every_stage_together_is_available(self) -> None:
        assert status() is CapabilityStatus.AVAILABLE

    def test_an_undeclared_category_is_not_configured(self) -> None:
        assert status(DataCategory.OPEN_INTEREST) is CapabilityStatus.NOT_CONFIGURED


class TestRAMockIsNeverExchangeLive:
    def test_the_shipped_mock_stream_is_simulated_history(self) -> None:
        mock = MockStreamProvider(script=(), clock=ManualClock(NOW))

        assert mock.provenance is StreamProvenance.SIMULATED_HISTORICAL_STREAM
        assert market_currency_of(mock.provenance) is MarketCurrency.HISTORICAL

    def test_a_simulated_declaration_asks_for_simulation_and_is_licensed_for_nothing(
        self,
    ) -> None:
        simulated = declared(Delivery.SIMULATED)

        assert provenance_for(simulated) is StreamProvenance.SIMULATED_HISTORICAL_STREAM
        assert (
            status(declaration=simulated, grant=fixture_grant(Delivery.SIMULATED))
            is CapabilityStatus.NOT_LICENSED
        )


class TestSProductionIsUnconfiguredByDefault:
    def test_the_default_setting_is_no_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert settings_from_env(monkeypatch).market_data_provider == "none"

    def test_the_production_matrix_is_every_category_not_configured(self) -> None:
        matrix = capability_matrix(
            declaration=None,
            grant=None,
            connected=False,
            last_delivery={},
            max_age=AGE,
            at=NOW,
        )

        assert [row.category for row in matrix] == list(DataCategory)
        assert {row.status for row in matrix} == {CapabilityStatus.NOT_CONFIGURED}
